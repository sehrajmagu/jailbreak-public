import os
from collections import Counter
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError
from supabase import Client

from app.auth import require_team
from app.db import get_db
from app.schemas import AnswerRequest, AnswerResult, LeaderboardEntry, TeamSession

router = APIRouter(tags=["scoring"])

REFUSALS = {
    "team_inactive": (401, "This team is not active."),
    "bot_not_found": (404, "Bot not found."),
    "not_answer_bot": (409, "This bot is graded by the judge."),
    "level_locked": (403, "Complete the previous level to unlock this one."),
    "invalid_answer": (422, "Enter a password of up to 500 characters."),
    "cap_reached": (409, "You've used all your wrong answers for this bot."),
}


def database_error() -> HTTPException:
    # Database errors can quote internal details, so none of it reaches the team.
    return HTTPException(status_code=500, detail="Something went wrong. Please try again.")


@router.post("/bots/{id}/answer")
def submit_answer(
    id: UUID,
    body: AnswerRequest,
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> AnswerResult:
    try:
        result = db.rpc(
            "submit_answer",
            {
                "p_team_id": session.team_id,
                "p_bot_id": str(id),
                "p_answer": body.answer,
                "p_max_wrong_answers": int(os.environ.get("MAX_WRONG_ANSWERS", "10")),
            },
        ).execute().data
    except APIError:
        raise database_error()

    if result["status"] != "ok":
        status, message = REFUSALS[result["status"]]
        raise HTTPException(status_code=status, detail=message)
    return AnswerResult(**result)


@router.get("/leaderboard")
def get_leaderboard(
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> List[LeaderboardEntry]:
    try:
        rows = db.table("leaderboard").select("id,name,total_points,rank").order("rank").order("name").execute().data
        # The view has no solved count, which LeaderboardEntry needs.
        scores = db.table("scores").select("team_id").execute().data
    except APIError:
        raise database_error()

    solved = Counter(score["team_id"] for score in scores)
    return [
        LeaderboardEntry(
            rank=row["rank"],
            team_id=row["id"],
            team_name=row["name"],
            score=row["total_points"],
            solved=solved[row["id"]],
            is_own_team=row["id"] == session.team_id,
        )
        for row in rows
    ]
