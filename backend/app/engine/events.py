"""What every provider adapter yields, so the chat loop doesn't depend on any provider."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class TextEvent:
    text: str


@dataclass
class ToolRequest:
    id: str
    name: str
    # A JSON string, as the model wrote it.
    arguments: str


@dataclass
class CallEnd:
    # "stop", "tool", "length" or "refusal".
    stop_reason: str
    # None if the provider didn't report it, and the chat loop then estimates it.
    usage: Optional[Usage]
    # What the adapter needs in add_round to send this call back to the model.
    content: Any = None
