import logging
import secrets
from typing import Any, Dict, List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
from supabase_auth.errors import AuthError, AuthWeakPasswordError
from typing_extensions import Annotated

from app.admin import (
    to_admin_bot,
    to_admin_model,
    to_admin_team,
    to_bot_stat,
    to_overview,
    to_prizes,
    to_team_detail,
)
from app.auth import require_admin
from app.conversations import (
    REFUSALS,
    database_error,
    run_function,
    to_admin_conversation,
    to_admin_submission,
    to_admin_summary,
)
from app.db import get_db
from app.routers import not_implemented
from app.schemas import (
    AdminBot,
    AdminConversation,
    AdminConversationSummary,
    AdminModel,
    AdminOverview,
    AdminPrizes,
    AdminSession,
    AdminSubmission,
    AdminTeam,
    AdminTeamDetail,
    BotInput,
    BotStat,
    ConversationFilters,
    CreateTeamInput,
    Paginated,
    ResetPasswordResult,
    AdminJudging,
    SetBotEnabledRequest,
    SetJudgingRequest,
    SetLevelOverrideRequest,
    SubmissionFilters,
    UpdateTeamInput,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# What Supabase Auth takes: its default minimum, and the most its hashing can use.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72


def invalid_password() -> HTTPException:
    status, message = REFUSALS["invalid_password"]
    return HTTPException(status_code=status, detail=message)


@router.get("/overview")
def get_admin_overview(db: Client = Depends(get_db)) -> AdminOverview:
    return to_overview(run_function(db, "get_admin_overview", {}))


@router.get("/teams")
def get_admin_teams(db: Client = Depends(get_db)) -> List[AdminTeam]:
    return [to_admin_team(row) for row in run_function(db, "get_admin_teams", {})["teams"]]


@router.get("/teams/{id}")
def get_admin_team(id: UUID, db: Client = Depends(get_db)) -> AdminTeamDetail:
    return to_team_detail(run_function(db, "get_admin_team", {"p_team_id": str(id)})["team"])


@router.post("/teams")
def create_team(
    body: CreateTeamInput,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminTeam:
    if len(body.password) < MIN_PASSWORD_LENGTH or len(body.password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise invalid_password()
    try:
        # Teams sign in by name, so this address is only Supabase Auth's identifier.
        # example.com is reserved for examples, so it can never reach a real person.
        user = db.auth.admin.create_user({
            "email": f"team-{uuid4().hex}@example.com",
            "password": body.password,
            "email_confirm": True,
            "app_metadata": {"role": "team"},
        }).user
    except AuthWeakPasswordError:
        raise invalid_password()
    except AuthError as error:
        logger.error("Couldn't create a team's account: %s", type(error).__name__)
        raise database_error()

    try:
        result = run_function(db, "create_team", {
            "p_admin_id": session.admin_id,
            "p_team_id": str(user.id),
            "p_name": body.name,
        })
    except Exception:
        # Such as a name that's taken. Don't leave an account with no team behind.
        db.auth.admin.delete_user(str(user.id))
        raise
    return to_admin_team(result["team"])


@router.patch("/teams/{id}")
def update_team(
    id: UUID,
    body: UpdateTeamInput,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminTeam:
    result = run_function(db, "update_team", {
        "p_admin_id": session.admin_id,
        "p_team_id": str(id),
        "p_name": body.name,
        "p_disabled": body.disabled,
    })
    return to_admin_team(result["team"])


@router.post("/teams/{id}/reset-password")
def reset_team_password(
    id: UUID,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> ResetPasswordResult:
    # Checked first, because the reset below can't be undone once it's run.
    run_function(db, "get_admin_team", {"p_team_id": str(id)})
    password = secrets.token_urlsafe(12)
    try:
        db.auth.admin.update_user_by_id(str(id), {"password": password})
    except AuthError as error:
        logger.error("Couldn't reset a team's password: %s", type(error).__name__)
        raise database_error()
    run_function(db, "record_password_reset", {"p_admin_id": session.admin_id, "p_team_id": str(id)})
    return ResetPasswordResult(temporary_password=password)


@router.get("/conversations")
def get_admin_conversations(
    filters: Annotated[ConversationFilters, Query()],
    db: Client = Depends(get_db),
) -> Paginated[AdminConversationSummary]:
    page = filters.page or 1
    page_size = filters.page_size or 20
    result = run_function(db, "get_admin_conversations", {
        "p_team_id": str(filters.team_id) if filters.team_id else None,
        "p_bot_id": str(filters.bot_id) if filters.bot_id else None,
        "p_model": filters.model,
        "p_result": filters.result,
        "p_page": page,
        "p_page_size": page_size,
    })
    return Paginated[AdminConversationSummary](
        items=[to_admin_summary(row) for row in result["items"]],
        total=result["total"],
        page=page,
        page_size=page_size,
    )


@router.get("/conversations/{id}")
def get_admin_conversation(id: UUID, db: Client = Depends(get_db)) -> AdminConversation:
    result = run_function(db, "get_admin_conversation", {"p_conversation_id": str(id)})
    return to_admin_conversation(result["conversation"])


@router.get("/submissions")
def get_admin_submissions(
    filters: Annotated[SubmissionFilters, Query()],
    db: Client = Depends(get_db),
) -> Paginated[AdminSubmission]:
    page = filters.page or 1
    page_size = filters.page_size or 20
    result = run_function(db, "get_admin_submissions", {
        "p_status": filters.status,
        "p_team_id": str(filters.team_id) if filters.team_id else None,
        "p_page": page,
        "p_page_size": page_size,
    })
    return Paginated[AdminSubmission](
        items=[to_admin_submission(row) for row in result["items"]],
        total=result["total"],
        page=page,
        page_size=page_size,
    )


@router.post("/submissions/{id}/regrade")
def regrade_submission(
    id: str,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminSubmission:
    # Password attempts share this list, with IDs like answer-12.
    if id.startswith("answer-"):
        raise HTTPException(status_code=409, detail="Answer checks aren't graded by the judge.")
    try:
        submission_id = str(UUID(id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Submission not found.")
    result = run_function(db, "regrade_submission", {"p_admin_id": session.admin_id, "p_submission_id": submission_id})
    return to_admin_submission(result["submission"])


def to_judging(row: Dict[str, Any]) -> AdminJudging:
    return AdminJudging(
        enabled=row["enabled"],
        paused_until=row["paused_until"],
        updated_at=row["updated_at"],
        waiting=row["waiting"],
        needs_organiser=row["needs_organiser"],
    )


@router.get("/judging")
def get_judging(db: Client = Depends(get_db)) -> AdminJudging:
    return to_judging(run_function(db, "get_judging", {}))


@router.put("/judging")
def set_judging(
    body: SetJudgingRequest,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminJudging:
    """Turns judging on or off at once, with no restart. Off still accepts submissions."""
    return to_judging(run_function(db, "set_judging", {"p_admin_id": session.admin_id, "p_enabled": body.enabled}))


@router.get("/bot-stats")
def get_bot_stats(db: Client = Depends(get_db)) -> List[BotStat]:
    return [to_bot_stat(row) for row in run_function(db, "get_bot_stats", {})["stats"]]


@router.get("/bots")
def get_admin_bots(db: Client = Depends(get_db)) -> List[AdminBot]:
    return [to_admin_bot(row) for row in run_function(db, "get_admin_bots", {})["bots"]]


@router.get("/bots/{id}")
def get_admin_bot(id: UUID, db: Client = Depends(get_db)) -> AdminBot:
    return to_admin_bot(run_function(db, "get_admin_bot", {"p_bot_id": str(id)})["bot"])


@router.post("/bots")
def create_bot(body: BotInput) -> AdminBot:
    not_implemented()


@router.put("/bots/{id}")
def update_bot(id: str, body: BotInput) -> AdminBot:
    not_implemented()


@router.patch("/bots/{id}")
def set_bot_enabled(
    id: UUID,
    body: SetBotEnabledRequest,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminBot:
    result = run_function(db, "set_bot_enabled", {
        "p_admin_id": session.admin_id,
        "p_bot_id": str(id),
        "p_enabled": body.enabled,
    })
    return to_admin_bot(result["bot"])


@router.get("/models")
def get_admin_models(db: Client = Depends(get_db)) -> List[AdminModel]:
    return [to_admin_model(row) for row in run_function(db, "get_admin_models", {})["models"]]


# The model is named by its display name, as AdminModel shows it.
@router.patch("/models/{model}")
def update_model(
    model: str,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminModel:
    # Old clients get an explicit refusal rather than changing an unused allowance.
    raise HTTPException(status_code=410, detail="Token quotas were removed. Manage dollar budgets in LiteLLM.")


@router.get("/prizes")
def get_admin_prizes(db: Client = Depends(get_db)) -> AdminPrizes:
    return to_prizes(run_function(db, "get_admin_prizes", {}))


@router.put("/prizes/{team_id}/levels/{level}")
def set_level_override(
    team_id: UUID,
    level: int,
    body: SetLevelOverrideRequest,
    session: AdminSession = Depends(require_admin),
    db: Client = Depends(get_db),
) -> AdminPrizes:
    return to_prizes(run_function(db, "set_level_override", {
        "p_admin_id": session.admin_id,
        "p_team_id": str(team_id),
        "p_level": level,
        "p_override": body.override,
    }))
