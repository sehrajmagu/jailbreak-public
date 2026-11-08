import os
from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from app import gateway
from app.auth import require_team
from app.conversations import run_function, to_submission
from app.db import get_db
from app.schemas import (
    BotCard,
    BotDetail,
    DashboardSummary,
    LevelProgress,
    ModelUsage,
    Submission,
    TeamBudget,
    TeamProfile,
    TeamSession,
)

router = APIRouter(tags=["team"])


def to_card(row: Dict[str, Any]) -> BotCard:
    return BotCard(
        id=row["id"],
        level=row["level"],
        label=row["label"],
        model=row["model"],
        check_type=row["check_type"],
        points=row["points"],
        status=row["status"],
        wrong_answers_used=row["wrong_answers_used"],
    )


def to_level(row: Dict[str, Any]) -> LevelProgress:
    return LevelProgress(
        level=row["level"],
        name=row["name"],
        locked=row["locked"],
        solved=row["solved"],
        required=row["required"],
        total=row["total"],
        complete=row["complete"],
        revoked=row["revoked"],
        prize_amount=row["prize_amount"],
        prize_awarded=row["prize_awarded"],
        bots=[to_card(bot) for bot in row["bots"]],
    )


@router.get("/me")
def get_me(
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> TeamProfile:
    result = run_function(db, "get_team_me", {"p_team_id": session.team_id})
    return TeamProfile(id=result["id"], name=result["name"], tokens_used=result["tokens_used"])


@router.get("/me/budget")
def get_budget(session: TeamSession = Depends(require_team)) -> TeamBudget:
    budget = gateway.team_budget(session.team_id)
    if budget is None:
        raise HTTPException(status_code=503, detail="The team's budget is unavailable.")
    cap, spent = budget
    return TeamBudget(budget_usd=cap, spent_usd=spent, remaining_usd=round(max(cap - spent, 0), 6))


@router.get("/me/dashboard")
def get_dashboard(
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> DashboardSummary:
    result = run_function(db, "get_team_dashboard", {"p_team_id": session.team_id})
    return DashboardSummary(
        points=result["points"],
        rank=result["rank"],
        total_teams=result["total_teams"],
        attempted=result["attempted"],
        solved=result["solved"],
        remaining=result["remaining"],
        levels_completed=result["levels_completed"],
        total_levels=result["total_levels"],
        prize_earned=result["prize_earned"],
        tokens_used=result["tokens_used"],
        usage_by_model=[
            ModelUsage(
                model=row["model"],
                tokens_used=row["tokens_used"],
            )
            for row in result["usage_by_model"]
        ],
    )


@router.get("/levels")
def get_levels(
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> List[LevelProgress]:
    result = run_function(db, "get_team_levels", {"p_team_id": session.team_id})
    return [to_level(row) for row in result["levels"]]


@router.get("/bots/{id}")
def get_bot(
    id: str,
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> BotDetail:
    # An ID that can't exist gets the same answer as one that doesn't.
    try:
        bot_id = str(UUID(id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Bot not found.")
    result = run_function(db, "get_team_bot", {
        "p_team_id": session.team_id,
        "p_bot_id": bot_id,
        "p_max_wrong_answers": int(os.environ.get("MAX_WRONG_ANSWERS", "10")),
    })
    return BotDetail(
        id=result["id"],
        level=result["level"],
        label=result["label"],
        model=result["model"],
        check_type=result["check_type"],
        points=result["points"],
        status=result["bot_status"],
        level_name=result["level_name"],
        description=result["description"],
        goal=result["goal"],
        wrong_answers_used=result["wrong_answers_used"],
        max_wrong_answers=result["max_wrong_answers"],
    )


@router.get("/submissions")
def get_submissions(
    session: TeamSession = Depends(require_team),
    db: Client = Depends(get_db),
) -> List[Submission]:
    result = run_function(db, "get_team_submissions", {"p_team_id": session.team_id})
    return [to_submission(row) for row in result["submissions"]]
