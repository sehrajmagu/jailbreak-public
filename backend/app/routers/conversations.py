from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from supabase import Client

from app.auth import require_team
from app.conversations import run_function, to_conversation, to_summary
from app.db import get_db
from app.schemas import Conversation, ConversationSummary, TeamSession

router = APIRouter(tags=["conversations"])


@router.get("/bots/{id}/conversations")
def get_conversations(
    id: UUID,
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> List[ConversationSummary]:
    result = run_function(db, "get_team_conversations", {"p_team_id": session.team_id, "p_bot_id": str(id)})
    return [to_summary(row) for row in result["conversations"]]


@router.get("/conversations/{id}")
def get_conversation(
    id: UUID,
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> Conversation:
    result = run_function(db, "get_team_conversation", {"p_team_id": session.team_id, "p_conversation_id": str(id)})
    return to_conversation(result["conversation"])
