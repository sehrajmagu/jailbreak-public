"""Runs a chat turn in its own task, so a client that disconnects can't avoid the charge.

Usage only arrives at the end of a model's stream, and Starlette can cancel a response
when its client goes away. So the response only reads from a queue, and this task reads
the stream to the end and always calls finish_turn. The queue is unbounded, so a dead
client can't stall it.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from fastapi import HTTPException
from supabase import Client

from app.gateway import team_scope
from app.conversations import Turn, finish_turn
from app.engine import run_chat
from app.schemas import (
    ChatDeltaEvent,
    ChatDoneEvent,
    ChatErrorEvent,
    ChatStartEvent,
    ChatToolCallEvent,
    TeamSession,
    ToolCall,
)

logger = logging.getLogger(__name__)

# Well under the 10 minutes after which begin_turn treats a turn as abandoned.
TURN_TIMEOUT_SECONDS = 300
# The most finish_turn accepts. Past this, the text is dropped but the stream is still
# read so usage arrives, and what's saved is exactly what the team saw.
MAX_REPLY_CHARS = 32000
SAVE_ATTEMPTS = 3
SAVE_RETRY_SECONDS = 0.5
DRAIN_SECONDS = 30

# Holds a reference to each task, or it could be garbage-collected mid-turn.
_running: "set[asyncio.Task[None]]" = set()


@dataclass
class TurnResult:
    """What the engine has produced so far. A turn only completes on `done`."""

    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    status: Literal["complete", "failed"] = "failed"
    input_tokens: int = 0
    output_tokens: int = 0
    error_code: Literal["bot_unavailable", "budget_exceeded"] = "bot_unavailable"

    def read_usage(self, event: Dict[str, Any]) -> None:
        self.input_tokens = event["usage"]["inputTokens"]
        self.output_tokens = event["usage"]["outputTokens"]


def to_line(event: Any) -> str:
    # Events are built only from schemas.py, so `usage` can't reach the browser.
    return event.model_dump_json(by_alias=True) + "\n"


async def read_engine(bot_id: str, turn: Turn, queue: "asyncio.Queue[Optional[str]]", result: TurnResult) -> None:
    async for event in run_chat(bot_id, turn.history):
        if event["type"] == "delta":
            room = MAX_REPLY_CHARS - len(result.content)
            text = event["text"][:room]
            if text:
                result.content += text
                await queue.put(to_line(ChatDeltaEvent(text=text)))
        elif event["type"] == "tool_call":
            call = event["toolCall"]
            tool_call = ToolCall(id=call["id"], name=call["name"], arguments=call["arguments"], result=call["result"])
            result.tool_calls.append(tool_call)
            await queue.put(to_line(ChatToolCallEvent(tool_call=tool_call)))
        elif event["type"] == "done":
            result.read_usage(event)
            result.status = "complete"
        elif event["type"] == "error":
            result.read_usage(event)
            if event.get("code") == "budget_exceeded":
                result.error_code = "budget_exceeded"


async def save_turn(db: Client, session: TeamSession, turn: Turn, result: TurnResult) -> str:
    """Saves the turn, and returns the stream's last event."""
    for attempt in range(1, SAVE_ATTEMPTS + 1):
        try:
            message = await asyncio.to_thread(
                finish_turn, db, session, turn, result.content, result.status,
                result.input_tokens, result.output_tokens, result.tool_calls,
            )
        except Exception as error:
            # A refusal won't change on a retry, but a database fault might.
            refused = isinstance(error, HTTPException) and error.status_code < 500
            if attempt < SAVE_ATTEMPTS and not refused:
                await asyncio.sleep(SAVE_RETRY_SECONDS)
                continue
            # Enough to reconcile by hand. Never log the text, which may hold a password.
            logger.error(
                "Couldn't save a turn: %s. Team %s, conversation %s, message %s, status %s, tokens %s in, %s out.",
                type(error).__name__, session.team_id, turn.conversation_id, turn.assistant_message_id,
                result.status, result.input_tokens, result.output_tokens,
            )
            return to_line(ChatErrorEvent(code=result.error_code))
        if result.status == "complete":
            return to_line(ChatDoneEvent(message=message))
        return to_line(ChatErrorEvent(code=result.error_code))
    raise AssertionError("unreachable")


async def run_turn(
    db: Client, session: TeamSession, bot_id: str, turn: Turn, queue: "asyncio.Queue[Optional[str]]"
) -> None:
    await queue.put(to_line(ChatStartEvent(
        conversation_id=turn.conversation_id,
        user_message=turn.user_message,
        assistant_message_id=turn.assistant_message_id,
    )))
    result = TurnResult()
    try:
        # All calls, including reviewers, draw from the authenticated team budget.
        async with team_scope(session.team_id):
            await asyncio.wait_for(read_engine(bot_id, turn, queue, result), TURN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        # The server is shutting down. Record what's known, or the turn is left to
        # the abandoned turn handling in supabase/conversations.sql.
        result.status = "failed"
        await queue.put(await save_turn(db, session, turn, result))
        await queue.put(None)
        raise
    except Exception as error:
        result.status = "failed"
        logger.error("Turn failed: %s", type(error).__name__)
    last_event = await save_turn(db, session, turn, result)
    await queue.put(last_event)
    await queue.put(None)


def start_turn_task(db: Client, session: TeamSession, bot_id: str, turn: Turn) -> "asyncio.Queue[Optional[str]]":
    """Starts the turn and returns the queue its stream lines arrive on, ending with None."""
    queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
    task = asyncio.create_task(run_turn(db, session, bot_id, turn, queue))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return queue


async def read_queue(queue: "asyncio.Queue[Optional[str]]") -> AsyncIterator[str]:
    while True:
        line = await queue.get()
        if line is None:
            return
        yield line


async def drain_turns() -> None:
    """On shutdown, lets running turns finish briefly, then cancels what's left."""
    if not _running:
        return
    _, unfinished = await asyncio.wait(set(_running), timeout=DRAIN_SECONDS)
    for task in unfinished:
        task.cancel()
    if unfinished:
        await asyncio.gather(*unfinished, return_exceptions=True)
