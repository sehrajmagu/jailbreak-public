"""Streams from OpenAI, and from open-source models on OpenAI-compatible hosts such as Groq."""

import json
import os
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

import httpx
from openai import AsyncOpenAI

from app.engine.config import HOSTS, IDLE_TIMEOUT_SECONDS, BotConfig, max_output_tokens
from app.engine.events import CallEnd, TextEvent, ToolRequest, Usage
from app.engine.tools import input_schema

WHOLE_REPLY_TIMEOUT_SECONDS = 120


@lru_cache
def get_client(host: str) -> AsyncOpenAI:
    # Built on first use, because main.py loads .env after its imports. The SDK's
    # one retry happens before any reply arrives, so text is never sent twice.
    return AsyncOpenAI(
        api_key=os.environ[HOSTS[host]["key"]],
        base_url=HOSTS[host]["base_url"],
        max_retries=1,
        timeout=httpx.Timeout(IDLE_TIMEOUT_SECONDS, connect=10),
    )


class OpenAICompatibleAdapter:
    def __init__(self, config: BotConfig, client: Any = None) -> None:
        self.config = config
        self.client = client if client is not None else get_client(config.host)

    def build_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Saved tool calls become a request, its result, and then the reply."""
        messages: List[Dict[str, Any]] = []
        for message in history:
            if message["toolCalls"]:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{call['id']}",
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in message["toolCalls"]
                    ],
                })
                messages.extend(
                    {"role": "tool", "tool_call_id": f"call_{call['id']}", "content": call["result"]}
                    for call in message["toolCalls"]
                )
            messages.append({"role": message["role"], "content": message["content"]})
        return messages

    def add_round(
        self,
        messages: List[Dict[str, Any]],
        end: CallEnd,
        results: List[Tuple[ToolRequest, Dict[str, Any], str]],
    ) -> None:
        messages.append({
            "role": "assistant",
            "content": end.content or None,
            "tool_calls": [
                {
                    "id": request.id,
                    "type": "function",
                    "function": {"name": request.name, "arguments": json.dumps(arguments)},
                }
                for request, arguments, _ in results
            ],
        })
        messages.extend(
            {"role": "tool", "tool_call_id": request.id, "content": result}
            for request, _, result in results
        )

    def options(self) -> Dict[str, Any]:
        options: Dict[str, Any] = {"max_completion_tokens": max_output_tokens()}
        if self.config.host == "groq":
            # Reasoning can repeat the password or the prompt. Groq streams it by
            # default, so ask for none as well as never forwarding it below.
            options["extra_body"] = {"include_reasoning": False}
        if self.config.tools:
            options["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": input_schema(tool),
                    },
                }
                for tool in self.config.tools
            ]
        return options

    async def stream(
        self, messages: List[Dict[str, Any]]
    ) -> AsyncIterator[Union[TextEvent, ToolRequest, CallEnd]]:
        if self.config.host == "openai_compatible":
            async for event in self.whole_reply(messages):
                yield event
            return
        response = await self.client.chat.completions.create(
            model=self.config.api_model,
            messages=[{"role": "system", "content": self.config.system_prompt}] + messages,
            stream=True,
            stream_options={"include_usage": True},
            **self.options(),
        )
        text = ""
        calls: Dict[int, Dict[str, str]] = {}
        finish_reason = None
        refused = False
        usage = None
        async with response:
            async for chunk in response:
                # Groq reports usage twice per call, so keep the latest and never add.
                if chunk.usage:
                    usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                # The chunk holding only usage has no choices.
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                refused = refused or bool(getattr(delta, "refusal", None))
                # Only content reaches the team. delta.reasoning is deliberately ignored.
                if delta.content:
                    text += delta.content
                    yield TextEvent(delta.content)
                for part in delta.tool_calls or []:
                    call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    call["id"] = part.id or call["id"]
                    if part.function:
                        call["name"] = part.function.name or call["name"]
                        call["arguments"] += part.function.arguments or ""

        if finish_reason is None:
            raise RuntimeError("The stream ended before the model finished.")
        for event in self.end_of_call(finish_reason, refused, calls, usage, text):
            yield event

    async def whole_reply(
        self, messages: List[Dict[str, Any]]
    ) -> AsyncIterator[Union[TextEvent, ToolRequest, CallEnd]]:
        """Asks for the reply in one piece, because streaming from these hosts loses text.

        Cloudflare streams a token that looks like a number as a JSON number, such as
        {"content": 4}. LiteLLM then drops it and never frees the team's parallel slot,
        so numbers vanish from replies and the team's next message gets a 429.
        """
        response = await self.client.chat.completions.create(
            model=self.config.api_model,
            messages=[{"role": "system", "content": self.config.system_prompt}] + messages,
            # There are no bytes until the whole reply is ready, so allow longer than a stream.
            timeout=WHOLE_REPLY_TIMEOUT_SECONDS,
            **self.options(),
        )
        usage = None
        if response.usage:
            usage = Usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        if not response.choices:
            raise RuntimeError("The reply had no choices.")
        choice = response.choices[0]
        # Keep the same completion check as streaming, before exposing any text.
        if choice.finish_reason is None:
            raise RuntimeError("The reply ended without a finish reason.")
        message = choice.message
        # Only content reaches the team. message.reasoning is deliberately ignored.
        text = str(message.content).strip() if message.content is not None else ""
        if text:
            yield TextEvent(text)
        calls = {
            index: {"id": part.id, "name": part.function.name, "arguments": part.function.arguments}
            for index, part in enumerate(message.tool_calls or [])
        }
        refused = bool(getattr(message, "refusal", None))
        for event in self.end_of_call(choice.finish_reason, refused, calls, usage, text):
            yield event

    def end_of_call(
        self,
        finish_reason: Optional[str],
        refused: bool,
        calls: Dict[int, Dict[str, str]],
        usage: Optional[Usage],
        text: str,
    ) -> List[Union[ToolRequest, CallEnd]]:
        if refused or finish_reason == "content_filter":
            return [CallEnd("refusal", usage)]
        # A reply cut off by the output cap may hold half a tool call, so don't run it.
        if calls and finish_reason != "length":
            requests = [
                ToolRequest(call["id"], call["name"], call["arguments"])
                for _, call in sorted(calls.items())
            ]
            return [*requests, CallEnd("tool", usage, text)]
        return [CallEnd("length" if finish_reason == "length" else "stop", usage, text)]
