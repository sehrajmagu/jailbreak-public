#!/usr/bin/env python3
"""Chat in the terminal with a bot from bots/, using its real model. See bots/README.md.

Keys come from environment variables only. Each message costs real money.

Defences work as in the live chat, using the same code in backend/app/engine/defences.py.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from bot_files import BOTS_DIR, HOSTS, PLACEHOLDER, ROOT, TODO, load_bot, load_models

sys.path.insert(0, str(ROOT / "backend"))
from app.engine.defences import Defences, allows, output_phrases, review_request, reviewer_prompt  # noqa: E402

MAX_OUTPUT_TOKENS = 16000
# Stops a model that keeps calling fake tools from spending tokens forever.
MAX_TOOL_ROUNDS = 5


class TurnFailed(Exception):
    """The model didn't produce a reply. The message is safe to print."""


def run_fake_tool(tool, arguments):
    for response in tool["fakeResponses"]:
        if all(arguments.get(key) == value for key, value in response["when"].items()):
            return response["result"]
    return tool["defaultResult"]


def input_schema(tool):
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in tool["parameters"].items()},
        "required": list(tool["parameters"]),
    }


def show_tool_call(name, arguments, result):
    print(f"  [fake tool {name}({json.dumps(arguments)}) returned {result}]")


class OpenAIChat:
    """OpenAI, and open-source models on OpenAI-compatible hosts."""

    def __init__(self, model, prompt, tools, key, base_url=None):
        from openai import OpenAI

        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model = model
        self.tools = {tool["name"]: tool for tool in tools}
        self.options = {"max_completion_tokens": MAX_OUTPUT_TOKENS}
        if base_url:
            # Many OpenAI-compatible hosts only know the older name.
            self.options = {"max_tokens": MAX_OUTPUT_TOKENS}
        if tools:
            self.options["tools"] = [
                {"type": "function", "function": {
                    "name": tool["name"], "description": tool["description"], "parameters": input_schema(tool),
                }}
                for tool in tools
            ]
        self.system = {"role": "system", "content": prompt}
        self.messages = []

    def reset(self):
        self.messages = []

    def replace_last_turn(self, text, reply):
        """Keeps only the blocked reply, as the live chat saves it."""
        del self.messages[self.turn_start:]
        self.messages += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]

    def drop_last_turn(self):
        """Forgets a turn whose reply was never checked, as the live chat hides a failed turn."""
        del self.messages[self.turn_start:]

    def send(self, text):
        """Returns (reply, input tokens, output tokens) for the whole turn."""
        start = self.turn_start = len(self.messages)
        self.tool_arguments = []
        self.messages.append({"role": "user", "content": text})
        input_tokens = output_tokens = 0
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                response = self.client.chat.completions.create(
                    model=self.model, messages=[self.system] + self.messages, **self.options
                )
                if response.usage:
                    input_tokens += response.usage.prompt_tokens
                    output_tokens += response.usage.completion_tokens
                message = response.choices[0].message
                if not message.tool_calls:
                    if getattr(message, "refusal", None):
                        raise TurnFailed(f"The model refused: {message.refusal}")
                    self.messages.append({"role": "assistant", "content": message.content or ""})
                    return message.content or "", input_tokens, output_tokens
                self.messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {"id": call.id, "type": "function",
                         "function": {"name": call.function.name, "arguments": call.function.arguments}}
                        for call in message.tool_calls
                    ],
                })
                for call in message.tool_calls:
                    result = self.call_tool(call.function.name, call.function.arguments)
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            raise TurnFailed(f"The model called tools more than {MAX_TOOL_ROUNDS} times in a row.")
        except BaseException:
            del self.messages[start:]
            raise

    def call_tool(self, name, raw_arguments):
        if name not in self.tools:
            return '{"error":"unknown_tool"}'
        try:
            arguments = json.loads(raw_arguments)
        except ValueError:
            arguments = {}
        result = run_fake_tool(self.tools[name], arguments if isinstance(arguments, dict) else {})
        self.tool_arguments.append(raw_arguments)
        show_tool_call(name, arguments, result)
        return result


class AnthropicChat:
    def __init__(self, model, prompt, tools, key):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=key)
        self.model = model
        self.prompt = prompt
        self.tools = {tool["name"]: tool for tool in tools}
        self.options = {}
        if tools:
            self.options["tools"] = [
                {"name": tool["name"], "description": tool["description"], "input_schema": input_schema(tool)}
                for tool in tools
            ]
        self.messages = []

    def reset(self):
        self.messages = []

    def replace_last_turn(self, text, reply):
        """Keeps only the blocked reply, as the live chat saves it."""
        del self.messages[self.turn_start:]
        self.messages += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]

    def drop_last_turn(self):
        """Forgets a turn whose reply was never checked, as the live chat hides a failed turn."""
        del self.messages[self.turn_start:]

    def send(self, text):
        """Returns (reply, input tokens, output tokens) for the whole turn."""
        start = self.turn_start = len(self.messages)
        self.tool_arguments = []
        self.messages.append({"role": "user", "content": text})
        input_tokens = output_tokens = 0
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                response = self.client.messages.create(
                    model=self.model, max_tokens=MAX_OUTPUT_TOKENS, system=self.prompt,
                    messages=self.messages, **self.options,
                )
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                if response.stop_reason == "refusal":
                    raise TurnFailed("The provider's safety system declined this message, and the turn was dropped.")
                # The whole content goes back, because thinking blocks must be kept.
                self.messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    reply = "".join(block.text for block in response.content if block.type == "text")
                    return reply, input_tokens, output_tokens
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        results.append({
                            "type": "tool_result", "tool_use_id": block.id,
                            "content": self.call_tool(block.name, block.input),
                        })
                self.messages.append({"role": "user", "content": results})
            raise TurnFailed(f"The model called tools more than {MAX_TOOL_ROUNDS} times in a row.")
        except BaseException:
            del self.messages[start:]
            raise

    def call_tool(self, name, arguments):
        if name not in self.tools:
            return '{"error":"unknown_tool"}'
        result = run_fake_tool(self.tools[name], arguments)
        self.tool_arguments.append(json.dumps(arguments))
        show_tool_call(name, arguments, result)
        return result


def need_env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name} in your terminal first. Don't save it in this repo.")
    return value


def open_chat(model, prompt, tools):
    if model["api_model"] == TODO:
        sys.exit(f"{model['display_name']} has api_model {TODO} in bots/models.yaml, so it can't run yet.")
    if model["host"] not in HOSTS:
        sys.exit("This model uses a gateway-only host. Test it through the website/LiteLLM, not a direct provider client.")
    host = HOSTS[model["host"]]
    key = need_env(host["key"])
    if model["host"] == "anthropic":
        return AnthropicChat(model["api_model"], prompt, tools, key)
    return OpenAIChat(model["api_model"], prompt, tools, key, host["base_url"])


def words(listed):
    return f"{len(listed)} word" + ("" if len(listed) == 1 else "s")


def defences_note(defences):
    on = []
    if defences["inputFilter"]:
        on.append(f"input filter ({words(defences['inputFilter'])})")
    if defences["outputFilter"] is True:
        on.append("output filter (the password)")
    elif defences["outputFilter"]:
        on.append(f"output filter ({words(defences['outputFilter'])}, and the password on an answer bot)")
    if defences["reviewerModel"]:
        on.append(f"reviewer model ({defences['reviewerModel']})")
    if not on:
        return "Defences: none turned on in this file."
    return f"Defences: {', '.join(on)}. They work as in the live chat."


def review(models, defences, reply, tool_arguments):
    """Asks the reviewer model, as the live chat does. Returns (allowed, tokens in, tokens out)."""
    model = next(model for model in models if model["id"] == defences["reviewerModel"])
    reviewer = open_chat(model, reviewer_prompt(defences["reviewerNote"]), [])
    calls = [{"name": "tool", "arguments": arguments} for arguments in tool_arguments]
    try:
        verdict, input_tokens, output_tokens = reviewer.send(review_request(reply, calls))
    except TurnFailed:
        # A reviewer refusal blocks, as in the live chat.
        return False, 0, 0
    except Exception as error:
        # Only the error's type: the request held the reply, which may hold the password.
        raise TurnFailed(
            f"The reviewer model failed ({type(error).__name__}), so this reply was dropped, as the live chat would."
        ) from None
    return allows(verdict), input_tokens, output_tokens


def find_bot(name):
    path = Path(name)
    return path if path.is_file() else BOTS_DIR / name


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bot", help="a file in bots/, such as level1-bot-a.yaml")
    path = find_bot(parser.parse_args().bot)

    models, errors = load_models()
    if not errors:
        bot, errors = load_bot(path, models)
    if errors:
        print(f"Fix these in {path.name} first:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    model = next(model for model in models if model["id"] == bot["model"])
    prompt = bot["system_prompt"]
    if bot["check_type"] == "answer":
        prompt = prompt.replace(PLACEHOLDER, bot["secret"])
    chat = open_chat(model, prompt, bot["tools"])
    defences = bot["defences"]
    guard = Defences.build(defences, output_phrases(defences, bot.get("secret")))

    context_limit = bot.get("context_limit_tokens", 8000)
    used = 0
    print(f"Level {bot['level']} {bot['label']} on {model['display_name']} ({model['host']}: {model['api_model']})")
    if bot["check_type"] == "answer":
        print(f"{PLACEHOLDER} is filled in with this bot's password.")
    print(f"Fake tools: {', '.join(tool['name'] for tool in bot['tools']) or 'none'}.")
    print(defences_note(defences))
    if defences["reviewerModel"]:
        reviewer_model = next(model for model in models if model["id"] == defences["reviewerModel"])
        if reviewer_model["api_model"] == TODO:
            sys.exit(f"The reviewer, {reviewer_model['display_name']}, has api_model {TODO}, so this bot can't run yet.")
    print("Type a message. /reset starts over, /quit leaves.\n")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reset":
            chat.reset()
            used = 0
            print("Started over.\n")
            continue
        if guard.blocks_input(text):
            # Not sent to the model, and not kept in the history it sees.
            print(f"bot> {guard.blocked_reply}")
            print("[defence: the input filter blocked this message. The model wasn't called | 0 tokens]\n")
            continue
        sent = False
        try:
            reply, input_tokens, output_tokens = chat.send(text)
            sent = True
            blocked_by = None
            if guard.blocks_output(reply, chat.tool_arguments):
                blocked_by = "the output filter"
            elif guard.has_reviewer:
                allowed, review_in, review_out = review(models, defences, reply, chat.tool_arguments)
                input_tokens += review_in
                output_tokens += review_out
                if not allowed:
                    blocked_by = "the reviewer model"
            if blocked_by:
                chat.replace_last_turn(text, guard.blocked_reply)
                reply = guard.blocked_reply
        except KeyboardInterrupt:
            print("\nStopped that message.\n")
            continue
        except TurnFailed as e:
            if sent:
                chat.drop_last_turn()
            print(f"[{e}]\n")
            continue
        except Exception as e:
            if sent:
                # The reply was never checked, so neither it nor the error text is shown.
                chat.drop_last_turn()
                print(f"[A defence failed ({type(e).__name__}), so this message was dropped.]\n")
                continue
            print(f"[The provider call failed, so this message was dropped: {type(e).__name__}: {e}]\n")
            continue

        used += input_tokens + output_tokens
        print(f"bot> {reply}")
        if blocked_by:
            # Which one, never what the model wrote: it may hold the password.
            print(f"[defence: {blocked_by} blocked the model's reply, so the team sees the blocked reply]")
        print(f"[tokens: {input_tokens} in, {output_tokens} out | conversation tokens: {used:,}]")
        if input_tokens > context_limit:
            print(f"[this turn's input is over the bot's {context_limit:,} context limit. The playtest doesn't trim history]")
        print()


if __name__ == "__main__":
    main()
