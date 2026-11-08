"""run_chat: streams one turn of a bot's conversation, as in docs/INTERFACE.md."""

import asyncio
import json
import logging
import math
from dataclasses import replace
from typing import Any, AsyncIterator, Dict, List, Tuple

from app.engine.anthropic import AnthropicAdapter
from app.engine.config import HOSTS, BotConfig, load_config
from app.engine.defences import Defences, allows, review_request, reviewer_prompt
from app.engine.events import CallEnd, TextEvent, ToolRequest, Usage
from app.engine.openai_compatible import OpenAICompatibleAdapter
from app.engine.tools import UNKNOWN_TOOL_RESULT, clean_text, parse_arguments, run_fake_tool

logger = logging.getLogger(__name__)

# Stops a model that keeps calling fake tools from spending tokens forever. This is
# one first call and 5 tool rounds, matching MAX_TOOL_ROUNDS in scripts/playtest.py.
MAX_MODEL_CALLS = 6
# 4 characters per token undercounts Hindi.
CHARS_PER_TOKEN = 3

# Sent instead of a provider's refusal, whose wording is never forwarded. The model
# ran and tokens were spent, so this is a normal, charged turn.
REFUSAL_REPLY = "Sorry, I can't help with that."
# Reasoning counts towards the output cap, so a model can use it all and write nothing.
NO_REPLY = "Sorry, I lost my train of thought. Could you say that again?"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def message_tokens(message: Dict[str, Any]) -> int:
    calls = "".join(call["arguments"] + call["result"] for call in message["toolCalls"])
    return estimate_tokens(message["content"] + calls)


def trim_history(messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Keeps whole turns, newest first, and always the newest message.

    Every turn starts with the team's message, so what's kept starts with one too.
    """
    turns: List[List[Dict[str, Any]]] = []
    for message in messages:
        if message["role"] == "user":
            turns.append([message])
        elif turns:
            turns[-1].append(message)

    kept: List[List[Dict[str, Any]]] = []
    used = 0
    for turn in reversed(turns):
        size = sum(message_tokens(message) for message in turn)
        if kept and used + size > limit:
            break
        kept.insert(0, turn)
        used += size
    return [message for turn in kept for message in turn]


def drop_blocked_turns(messages: List[Dict[str, Any]], defences: Defences) -> List[Dict[str, Any]]:
    """Leaves out earlier turns whose team message the input filter blocks.

    Those turns are saved, with the blocked reply, so a later message would otherwise
    hand the blocked text to the model after all.
    """
    kept: List[Dict[str, Any]] = []
    skipping = False
    for message in messages:
        if message["role"] == "user":
            skipping = defences.blocks_input(message["content"])
        if not skipping:
            kept.append(message)
    return kept


def open_adapter(config: BotConfig) -> Any:
    from app import gateway

    if gateway.enabled():
        return gateway.adapter(config)
    if config.host not in HOSTS:
        raise LookupError("This host requires the gateway.")
    if config.host == "anthropic":
        return AnthropicAdapter(config)
    return OpenAICompatibleAdapter(config)


def estimate_usage(config: BotConfig, messages: List[Dict[str, Any]], output: str) -> Usage:
    """For a provider that ended without reporting usage: charge an estimate, not zero."""
    logger.warning("No usage reported for bot %s, so estimating it.", config.id)
    sent = config.system_prompt + json.dumps(messages, default=str)
    return Usage(estimate_tokens(sent), estimate_tokens(output))


def run_tool(config: BotConfig, request: ToolRequest) -> Tuple[Dict[str, Any], str, bool]:
    """Returns the arguments, the result, and whether it's a real entry in the bot's file."""
    arguments = parse_arguments(request.arguments)
    tool = next((tool for tool in config.tools if tool["name"] == request.name), None)
    if tool is None:
        return arguments, UNKNOWN_TOOL_RESULT, False
    return arguments, run_fake_tool(tool, arguments), True


def error_event(usage: Usage, code: str = "bot_unavailable") -> Dict[str, Any]:
    return {"type": "error", "code": code, "usage": usage_json(usage)}


def usage_json(usage: Usage) -> Dict[str, int]:
    return {"inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens}


def delta_event(text: str) -> Dict[str, Any]:
    return {"type": "delta", "text": text}


async def review(config: BotConfig, defences: Defences, text: str, tool_calls: List[Dict[str, str]], usage: Usage) -> bool:
    """Asks the reviewer model about a finished reply. True lets it through.

    The reviewer sees only the reply and the bot's reviewer note: never the bot's
    prompt, password or rubric. Its tokens are added to the turn's usage. A refusal,
    an empty answer or anything but ALLOW blocks the reply. A failed call raises, so
    the turn fails and nothing is shown.
    """
    if not config.reviewer:
        raise LookupError("The reviewer model wasn't found.")
    reviewer = replace(
        config,
        model=config.reviewer["model"],
        provider=config.reviewer["provider"],
        host=config.reviewer["host"],
        api_model=config.reviewer["api_model"],
        system_prompt=reviewer_prompt(defences.reviewer_note),
        tools=[],
        defences={},
        output_filter_phrases=[],
        reviewer=None,
    )
    adapter = open_adapter(reviewer)
    request = [{"role": "user", "content": review_request(text, tool_calls), "toolCalls": []}]
    messages = adapter.build_messages(request)
    verdict = ""
    end = None
    async for event in adapter.stream(messages):
        if isinstance(event, TextEvent):
            verdict += event.text
        elif isinstance(event, CallEnd):
            end = event
    if end is None:
        raise RuntimeError("The reviewer ended without finishing the call.")
    usage.add(end.usage or estimate_usage(reviewer, messages, verdict))
    return end.stop_reason != "refusal" and allows(verdict)


async def run_chat(bot_id: str, messages: List[Dict[str, Any]]) -> AsyncIterator[Dict[str, Any]]:
    """Yields start, then tool_call and delta events, then one done or error.

    With an output filter or reviewer, nothing is sent until the whole reply has
    passed, and a blocked reply is replaced by the bot's blocked reply. A blocked turn
    still ends with done and its real usage. If a defence fails, the turn ends with
    error and the held-back reply is never sent.
    """
    yield {"type": "start"}
    usage = Usage()
    try:
        config = await asyncio.to_thread(load_config, bot_id)
        defences = Defences.build(config.defences, config.output_filter_phrases)

        # The model never sees a blocked message, so nothing is spent.
        if defences.blocks_input(messages[-1]["content"]):
            logger.info("Input filter blocked a message for bot %s.", config.id)
            yield delta_event(defences.blocked_reply)
            yield {"type": "done", "usage": usage_json(usage)}
            return

        adapter = open_adapter(config)
        history = trim_history(drop_blocked_turns(messages, defences), config.context_limit_tokens)
        provider_messages = adapter.build_messages(history)

        # Held back until checked, when a defence has to read the whole reply first.
        held: List[Dict[str, Any]] = []

        def send(event: Dict[str, Any]) -> List[Dict[str, Any]]:
            if defences.checks_output:
                held.append(event)
                return []
            return [event]

        wrote_text = False
        for call_number in range(1, MAX_MODEL_CALLS + 1):
            requests: List[ToolRequest] = []
            end = None
            output = ""
            async for event in adapter.stream(provider_messages):
                if isinstance(event, TextEvent):
                    text = clean_text(event.text)
                    if text:
                        wrote_text = True
                        output += text
                        for ready in send(delta_event(text)):
                            yield ready
                elif isinstance(event, ToolRequest):
                    requests.append(event)
                    output += event.arguments
                elif isinstance(event, CallEnd):
                    end = event
            if end is None:
                raise RuntimeError("The adapter ended without finishing the call.")
            usage.add(end.usage or estimate_usage(config, provider_messages, output))

            if end.stop_reason == "refusal":
                # A refusal can follow text that has already streamed.
                for ready in send(delta_event(f"\n\n{REFUSAL_REPLY}" if wrote_text else REFUSAL_REPLY)):
                    yield ready
                break
            if not requests or call_number == MAX_MODEL_CALLS:
                if not wrote_text:
                    for ready in send(delta_event(NO_REPLY)):
                        yield ready
                break

            results = []
            for request in requests:
                arguments, result, known = run_tool(config, request)
                results.append((request, arguments, result))
                # A name the model made up isn't one of this bot's tools, so it's only
                # answered, and never shown to the team or saved.
                if known:
                    for ready in send({
                        "type": "tool_call",
                        "toolCall": {
                            "id": request.id,
                            "name": request.name,
                            "arguments": json.dumps(arguments),
                            "result": result,
                        },
                    }):
                        yield ready
            adapter.add_round(provider_messages, end, results)

        if defences.checks_output:
            text = "".join(event["text"] for event in held if event["type"] == "delta")
            tool_calls = [event["toolCall"] for event in held if event["type"] == "tool_call"]
            blocked_by = None
            if defences.blocks_output(text, [call["arguments"] for call in tool_calls]):
                blocked_by = "Output filter"
            elif defences.has_reviewer and not await review(config, defences, text, tool_calls, usage):
                blocked_by = "Reviewer model"
            if blocked_by:
                logger.info("%s blocked a reply for bot %s.", blocked_by, config.id)
                held = [delta_event(defences.blocked_reply)]
            for event in held:
                yield event

        yield {"type": "done", "usage": usage_json(usage)}
    except Exception as error:
        # A raw provider error can repeat the prompt, so log only its class, status and
        # code. Groq's failed_generation can hold the model's raw output. Anything held
        # back for a defence is dropped.
        logger.error(
            "Model call failed: %s %s %s",
            type(error).__name__, getattr(error, "status_code", ""), getattr(error, "code", "") or "",
        )
        from app import gateway

        code = "budget_exceeded" if gateway.budget_exceeded(error) else "bot_unavailable"
        yield error_event(usage, code)
