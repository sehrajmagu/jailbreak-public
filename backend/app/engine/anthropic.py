"""Streams from Anthropic. Tested with a fake transport only, because there's no key yet."""

import json
import os
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, List, Tuple, Union

import httpx
from anthropic import AsyncAnthropic

from app.engine.config import HOSTS, IDLE_TIMEOUT_SECONDS, BotConfig, max_output_tokens
from app.engine.events import CallEnd, TextEvent, ToolRequest, Usage
from app.engine.tools import input_schema, parse_arguments


@lru_cache
def get_client(host: str) -> AsyncAnthropic:
    # Built on first use, because main.py loads .env after its imports. The SDK's
    # one retry happens before any reply arrives, so text is never sent twice.
    return AsyncAnthropic(
        api_key=os.environ[HOSTS[host]["key"]],
        max_retries=1,
        timeout=httpx.Timeout(IDLE_TIMEOUT_SECONDS, connect=10),
    )


class AnthropicAdapter:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.client = get_client(config.host)

    def build_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Saved tool calls become a request, its result, and then the reply."""
        messages: List[Dict[str, Any]] = []
        for message in history:
            if message["toolCalls"]:
                messages.append({
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_{call['id']}",
                            "name": call["name"],
                            "input": parse_arguments(call["arguments"]),
                        }
                        for call in message["toolCalls"]
                    ],
                })
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": f"toolu_{call['id']}", "content": call["result"]}
                        for call in message["toolCalls"]
                    ],
                })
            # The API refuses an empty text block, as a reply with only tool calls would be.
            if message["content"].strip():
                messages.append({"role": message["role"], "content": message["content"]})
        return messages

    def add_round(
        self,
        messages: List[Dict[str, Any]],
        end: CallEnd,
        results: List[Tuple[ToolRequest, Dict[str, Any], str]],
    ) -> None:
        # The whole content goes back unchanged, because thinking blocks must be kept.
        messages.append({"role": "assistant", "content": end.content})
        messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": request.id, "content": result}
                for request, _, result in results
            ],
        })

    def options(self) -> Dict[str, Any]:
        # `thinking` is left out: Fable 5.1 always thinks, and refuses most settings for it.
        options: Dict[str, Any] = {"max_tokens": max_output_tokens()}
        if self.config.tools:
            options["tools"] = [
                {"name": tool["name"], "description": tool["description"], "input_schema": input_schema(tool)}
                for tool in self.config.tools
            ]
        return options

    async def stream(
        self, messages: List[Dict[str, Any]]
    ) -> AsyncIterator[Union[TextEvent, ToolRequest, CallEnd]]:
        async with self.client.messages.stream(
            model=self.config.api_model,
            system=self.config.system_prompt,
            messages=messages,
            **self.options(),
        ) as stream:
            async for event in stream:
                # Only text reaches the team. thinking_delta is deliberately ignored.
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield TextEvent(event.delta.text)
            final = await stream.get_final_message()

        usage = Usage(
            final.usage.input_tokens
            + (final.usage.cache_creation_input_tokens or 0)
            + (final.usage.cache_read_input_tokens or 0),
            final.usage.output_tokens,
        )
        # Checked first, because a refusal can cut a tool call off partway.
        if final.stop_reason == "refusal":
            yield CallEnd("refusal", usage)
            return
        if final.stop_reason == "max_tokens":
            yield CallEnd("length", usage)
            return
        calls = [block for block in final.content if block.type == "tool_use"]
        if calls:
            for block in calls:
                yield ToolRequest(block.id, block.name, json.dumps(block.input))
            yield CallEnd("tool", usage, final.content)
            return
        yield CallEnd("stop", usage)
