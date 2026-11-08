from uuid import UUID

from fastapi import APIRouter, Depends
from supabase import Client

from app.auth import require_team
from app.conversations import run_function, to_submission
from app.db import get_db
from app.schemas import Submission, TeamSession

# Take the team from the session, using Depends(require_team), and never from the request.
router = APIRouter(tags=["team"], dependencies=[Depends(require_team)])


@router.post("/conversations/{id}/submit")
def submit_conversation(
    id: UUID,
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> Submission:
    """Queues the conversation and returns at once. The judge worker grades it later."""
    result = run_function(db, "submit_conversation", {"p_team_id": session.team_id, "p_conversation_id": str(id)})
    return to_submission(result["submission"])
