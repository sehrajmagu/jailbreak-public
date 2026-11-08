import os
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from supabase import Client

from app.auth import require_team
from app.conversations import REFUSALS, start_turn
from app.db import get_db
from app.engine.config import available_hosts
from app.schemas import SendMessageRequest, TeamSession
from app.turns import read_queue, start_turn_task

router = APIRouter(tags=["chat"])


def parse_id(value: str, refusal: str) -> str:
    # A bad ID would otherwise reach Postgres and come back as a 500.
    try:
        return str(UUID(value))
    except ValueError:
        status, message = REFUSALS[refusal]
        raise HTTPException(status_code=status, detail=message)


@router.post("/messages", response_model=None)
async def send_message(
    body: SendMessageRequest,
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> StreamingResponse:
    """Streams schemas.ChatEvent objects as application/x-ndjson.

    Refusals are normal JSON errors, before any stream starts, and save nothing. The
    model call runs in its own task: see app/turns.py.
    """
    bot_id = parse_id(body.bot_id, "bot_not_found")
    conversation_id: Optional[str] = None
    if body.conversation_id is not None:
        conversation_id = parse_id(body.conversation_id, "conversation_not_found")
    # Postgres can't store NUL in text.
    if "\x00" in body.content:
        status, message = REFUSALS["invalid_message"]
        raise HTTPException(status_code=status, detail=message)

    # The Supabase client is synchronous, and would block every other stream here.
    turn = await run_in_threadpool(
        start_turn,
        db,
        session,
        bot_id,
        conversation_id,
        body.content,
        available_hosts(),
        int(os.environ.get("MAX_WRONG_ANSWERS", "10")),
        int(os.environ.get("CHAT_RATE_LIMIT_MESSAGES", "6")),
        int(os.environ.get("CHAT_RATE_LIMIT_WINDOW_SECONDS", "30")),
        int(os.environ.get("CHAT_MAX_OPEN_TURNS", "3")),
    )
    queue = start_turn_task(db, session, bot_id, turn)
    return StreamingResponse(
        read_queue(queue),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
