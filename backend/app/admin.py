"""Maps the organiser pages' data to API models.

The functions behind this are in supabase/admin.sql. Each organiser change runs in
one of them, which also writes it to audit_log in the same transaction.
"""

from typing import Any, Dict, List

from app.schemas import (
    AdminBot,
    AdminLevelCompletion,
    AdminModel,
    AdminOverview,
    AdminPrizes,
    AdminTeam,
    AdminTeamDetail,
    AdminTeamPrizes,
    BotStat,
    LevelConfig,
    OverviewPoint,
    TeamBotProgress,
)

DEFENCE_LABELS = {"inputFilter": "Input filter", "outputFilter": "Output filter"}


def to_defences(defences: Dict[str, Any]) -> List[str]:
    # bots.defences is structured, but AdminBot.defences is a list of text. See docs/INTERFACE.md.
    labels = [label for key, label in DEFENCE_LABELS.items() if defences.get(key)]
    if defences.get("reviewerModel"):
        labels.append(f"Reviewer model: {defences['reviewerModel']}")
    return labels


def to_admin_team(row: Dict[str, Any]) -> AdminTeam:
    return AdminTeam(
        id=row["id"],
        name=row["name"],
        disabled=row["disabled"],
        tokens_used=row["tokens_used"],
        attempted=row["attempted"],
        solved=row["solved"],
        score=row["score"],
        rank=row["rank"],
        last_active_at=row["last_active_at"],
        created_at=row["created_at"],
    )


def to_team_progress(row: Dict[str, Any]) -> TeamBotProgress:
    return TeamBotProgress(
        bot_id=row["bot_id"],
        label=row["label"],
        level=row["level"],
        model=row["model"],
        status=row["status"],
        conversations=row["conversations"],
    )


def to_team_detail(row: Dict[str, Any]) -> AdminTeamDetail:
    return AdminTeamDetail(
        **to_admin_team(row).model_dump(),
        progress=[to_team_progress(bot) for bot in row["progress"]],
    )


def to_admin_bot(row: Dict[str, Any]) -> AdminBot:
    return AdminBot(
        id=row["id"],
        label=row["label"],
        level=row["level"],
        model=row["model"],
        check_type=row["check_type"],
        points=row["points"],
        description=row["description"],
        goal=row["goal"],
        system_prompt=row["system_prompt"],
        win_rubric=row["win_rubric"],
        secret=row["secret"],
        defences=to_defences(row["defences"]),
        enabled=row["enabled"],
    )


def to_admin_model(row: Dict[str, Any]) -> AdminModel:
    return AdminModel(model=row["model"])


def to_bot_stat(row: Dict[str, Any]) -> BotStat:
    return BotStat(
        bot_id=row["bot_id"],
        label=row["label"],
        level=row["level"],
        model=row["model"],
        enabled=row["enabled"],
        attempts=row["attempts"],
        teams_attempted=row["teams_attempted"],
        solves=row["solves"],
        solve_rate=row["solve_rate"],
        avg_attempts_to_solve=row["avg_attempts_to_solve"],
        tokens_used=row["tokens_used"],
    )


def to_overview(row: Dict[str, Any]) -> AdminOverview:
    return AdminOverview(
        total_teams=row["total_teams"],
        active_teams=row["active_teams"],
        tokens_consumed=row["tokens_consumed"],
        total_attempts=row["total_attempts"],
        total_solves=row["total_solves"],
        series=[
            OverviewPoint(
                date=point["date"],
                attempts=point["attempts"],
                solves=point["solves"],
                tokens_consumed=point["tokens_consumed"],
            )
            for point in row["series"]
        ],
    )


def to_level_completion(row: Dict[str, Any]) -> AdminLevelCompletion:
    return AdminLevelCompletion(
        level=row["level"],
        solved=row["solved"],
        required=row["required"],
        total=row["total"],
        earned=row["earned"],
        override=row["override"],
        complete=row["complete"],
        prize_awarded=row["prize_awarded"],
        completed_at=row["completed_at"],
    )


def to_prizes(row: Dict[str, Any]) -> AdminPrizes:
    return AdminPrizes(
        require_levels_in_order=row["require_levels_in_order"],
        levels=[
            LevelConfig(
                level=level["level"],
                name=level["name"],
                prize_amount=level["prize_amount"],
                required_bots=level["required_bots"],
                max_winners=level["max_winners"],
            )
            for level in row["levels"]
        ],
        teams=[
            AdminTeamPrizes(
                team_id=team["team_id"],
                team_name=team["team_name"],
                levels=[to_level_completion(level) for level in team["levels"]],
                prize_owed=team["prize_owed"],
            )
            for team in row["teams"]
        ],
        total_prize_owed=row["total_prize_owed"],
    )
