"""Turn a Conversation into the assistant-only, numbered transcript the judge reads.

Contract (docs/INTERFACE.md section 3): B removes every ``user`` message before the
judge model sees anything. Part A should already have done this; we do it again here
so the guarantee does not depend on A.
"""

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List

from engine.judge.models import Conversation, ToolCall


@dataclass
class Turn:
    """One assistant reply (text and/or tool calls). ``number`` starts at 1."""

    number: int
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)


def prepare_turns(conversation: Conversation) -> List[Turn]:
    """Keep only assistant messages that carry something, numbered 1..N in order.

    Empty assistant messages (no text, no tool calls) hold no evidence, so they are
    skipped. The numbers are the ones the judge may cite, and the validator later
    checks every cited number against this list.
    """
    turns = []  # type: List[Turn]
    for message in conversation.messages:
        if message.role != "assistant":
            continue
        if not message.content.strip() and not message.tool_calls:
            continue
        turns.append(Turn(number=len(turns) + 1, content=message.content, tool_calls=list(message.tool_calls)))
    return turns


def new_nonce() -> str:
    """Random per-call boundary token. Participants cannot predict it."""
    return secrets.token_hex(8)


def serialise_turns(turns: List[Turn]) -> str:
    """JSON text for the transcript.

    JSON escaping stops a reply from breaking out of its data block, and we also
    escape ``<`` so a reply cannot contain a fake ``</transcript-...>`` tag. Non-ASCII
    text (Hindi) is kept readable and compact rather than \\u-escaped.
    """
    payload = []  # type: List[Dict[str, Any]]
    for turn in turns:
        payload.append(
            {
                "turn": turn.number,
                "content": turn.content,
                "toolCalls": [
                    {"name": call.name, "arguments": call.arguments, "result": call.result}
                    for call in turn.tool_calls
                ],
            }
        )
    return json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")


def wrap_transcript(turns: List[Turn], nonce: str) -> str:
    return "<transcript-%s>\n%s\n</transcript-%s>" % (nonce, serialise_turns(turns), nonce)
