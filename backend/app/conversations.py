"""Saves chat turns and maps stored conversations to API models.

The functions behind this are in supabase/conversations.sql and supabase/chat.sql.
A turn is saved in two steps around the model call. POST /messages calls start_turn,
which refuses rate limits and locked levels, leaving nothing
behind, and otherwise runs begin_turn. It saves the team's message and returns the
history to pass to the bot engine's run_chat, plus the IDs for the stream's `start`
event. app/turns.py then runs the model and always calls finish_turn, so that a
dropped connection still records the turn and its charge. It passes status
"failed" when the engine ends with `error`.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from postgrest.exceptions import APIError
from supabase import Client

from app.schemas import (
    AdminChatMessage,
    AdminConversation,
    AdminConversationSummary,
    AdminSubmission,
    ChatMessage,
    Conversation,
    ConversationSummary,
    Submission,
    SubmissionStatus,
    TeamSession,
    ToolCall,
)

REFUSALS = {
    "team_inactive": (401, "This team is not active."),
    "bot_not_found": (404, "Bot not found."),
    "conversation_not_found": (404, "Conversation not found."),
    "conversation_closed": (409, "This conversation was submitted and can't be continued."),
    "turn_in_progress": (409, "Wait for the bot to finish replying."),
    "turn_not_open": (409, "This turn has already finished."),
    "level_locked": (403, "Complete the previous level to unlock this one."),
    "model_unavailable": (503, "This bot's model isn't available yet. Try another bot."),
    "wrong_answers_used_up": (409, "You've used all your wrong answers for this bot, so its chat is closed."),
    "invalid_message": (422, "Enter a message of up to 8,000 characters."),
    "invalid_reply": (422, "The reply couldn't be saved."),
    "invalid_page": (422, "Invalid input."),
    # Submitting and grading, in supabase/judge.sql.
    "not_judge_bot": (409, "This bot is checked with the answer box."),
    "already_submitted": (409, "This conversation was already submitted."),
    "no_bot_replies": (422, "This conversation has no bot replies to submit."),
    "submission_not_found": (404, "Submission not found."),
    "submission_in_flight": (409, "This submission is already being graded."),
    # The organiser pages, in supabase/admin.sql.
    "team_not_found": (404, "Team not found."),
    "model_not_found": (404, "Model not found."),
    "level_not_found": (404, "Level not found."),
    "name_taken": (409, "A team with this name already exists."),
    "too_few_bots": (409, "A level needs at least as many enabled bots as it takes to complete it."),
    "invalid_name": (422, "Enter a team name of 1 to 60 characters."),
    "invalid_limit": (422, "The token limit must be a whole number of 0 or more."),
    "invalid_password": (422, "Enter a password of 8 to 72 characters."),
    "invalid_override": (422, "Invalid input."),
}


@dataclass(frozen=True)
class Turn:
    """A turn begin_turn has saved and finish_turn will complete."""

    conversation_id: str
    user_message: ChatMessage
    assistant_message_id: str
    # The run_chat messages from docs/INTERFACE.md, ending with this turn's message.
    history: List[Dict[str, Any]]


class RateLimited(HTTPException):
    """A 429 whose response tells the team when to retry. See main.py."""

    def __init__(self, retry_after_seconds: int, detail: str = "You're sending messages too quickly.") -> None:
        super().__init__(status_code=429, detail=detail)
        self.retry_after_seconds = retry_after_seconds


def database_error() -> HTTPException:
    # Database errors can quote internal details, so none of it reaches the team.
    return HTTPException(status_code=500, detail="Something went wrong. Please try again.")


def run_function(db: Client, function: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Runs one of the SQL functions and turns a refusal into an HTTP error."""
    try:
        result = db.rpc(function, params).execute().data
    except APIError:
        raise database_error()

    if result["status"] == "rate_limited":
        raise RateLimited(int(result["retry_after_seconds"]))
    if result["status"] == "too_many_logins":
        seconds = int(result["retry_after_seconds"])
        raise RateLimited(seconds, f"Too many sign-in attempts. Try again in {seconds} seconds.")
    if result["status"] != "ok":
        status, message = REFUSALS[result["status"]]
        raise HTTPException(status_code=status, detail=message)
    return result


def begin_turn(
    db: Client,
    session: TeamSession,
    bot_id: str,
    conversation_id: Optional[str],
    content: str,
) -> Turn:
    """Saves the team's message, without any of start_turn's checks."""
    return to_turn(run_function(db, "begin_turn", {
        "p_team_id": session.team_id,
        "p_bot_id": bot_id,
        "p_conversation_id": conversation_id,
        "p_content": content,
    }))


def start_turn(
    db: Client,
    session: TeamSession,
    bot_id: str,
    conversation_id: Optional[str],
    content: str,
    available_hosts: List[str],
    max_wrong_answers: int,
    rate_limit_messages: int,
    rate_limit_window_seconds: int,
    max_open_turns: int,
) -> Turn:
    """Checks the team may send this, then saves it. A null conversation_id starts a new one."""
    return to_turn(run_function(db, "start_turn", {
        "p_team_id": session.team_id,
        "p_bot_id": bot_id,
        "p_conversation_id": conversation_id,
        "p_content": content,
        "p_available_hosts": available_hosts,
        "p_max_wrong_answers": max_wrong_answers,
        "p_rate_limit_messages": rate_limit_messages,
        "p_rate_limit_window_seconds": rate_limit_window_seconds,
        "p_max_open_turns": max_open_turns,
    }))


def to_turn(result: Dict[str, Any]) -> Turn:
    return Turn(
        conversation_id=result["conversation_id"],
        user_message=to_message(result["user_message"]),
        assistant_message_id=str(result["assistant_message_id"]),
        history=[
            {
                "role": row["role"],
                "content": row["content"],
                "toolCalls": [to_tool_call(tool_call).model_dump(by_alias=True) for tool_call in row["tool_calls"]],
            }
            for row in result["history"]
        ],
    )


def finish_turn(
    db: Client,
    session: TeamSession,
    turn: Turn,
    content: str,
    status: Literal["complete", "failed"],
    input_tokens: int,
    output_tokens: int,
    tool_calls: List[ToolCall],
) -> ChatMessage:
    """Saves the reply and, when it's complete, charges its tokens to the team.

    The tokens are the engine's `usage` for the whole turn. The returned message
    is the one to send in the stream's `done` event.
    """
    result = run_function(db, "finish_turn", {
        "p_team_id": session.team_id,
        "p_conversation_id": turn.conversation_id,
        "p_user_message_id": int(turn.user_message.id),
        "p_assistant_message_id": int(turn.assistant_message_id),
        "p_content": content,
        "p_status": status,
        "p_input_tokens": input_tokens,
        "p_output_tokens": output_tokens,
        "p_tool_calls": [
            {
                "name": tool_call.name,
                "arguments": parse_json(tool_call.arguments),
                "result": parse_json(tool_call.result) if tool_call.result else None,
            }
            for tool_call in tool_calls
        ],
    })
    return to_message(result["message"])


# ---------- Mapping ----------


def parse_json(text: str) -> Any:
    """Tool arguments and results arrive as JSON strings and are stored as JSONB.

    Text that isn't valid JSON is stored as a JSON string, so it's never lost.
    """
    try:
        return json.loads(text, parse_constant=reject_constant)
    except ValueError:
        return text


def reject_constant(name: str) -> None:
    # NaN and Infinity are valid in Python's JSON but not in JSONB.
    raise ValueError(name)


def to_json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def to_tool_call(row: Dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=str(row["id"]),
        name=row["name"],
        arguments=to_json_text(row["arguments"]),
        result=to_json_text(row["result"]),
    )


def to_message(row: Dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        id=str(row["id"]),
        role=row["role"],
        content=row["content"],
        tool_calls=[to_tool_call(tool_call) for tool_call in row["tool_calls"]],
        tokens=row["tokens"],
        created_at=row["created_at"],
    )


def to_team_status(status: Optional[str]) -> Optional[SubmissionStatus]:
    # Teams see anything not yet graded as grading. An error is retried, and showing
    # "pending" would tell a team when organisers have switched judging off.
    return "grading" if status in ("error", "pending") else status


def to_admin_status(status: Optional[str]) -> Optional[SubmissionStatus]:
    # Organisers have no "error" status either: its reason says what went wrong.
    return "grading" if status == "error" else status


def to_submission(row: Dict[str, Any]) -> Submission:
    return Submission(
        id=row["id"],
        conversation_id=row["conversation_id"],
        bot_id=row["bot_id"],
        level=row["level"],
        bot_label=row["bot_label"],
        check_type=row["check_type"],
        status=to_team_status(row["status"]),
        # Until it's graded, reason may hold error text, and points default to 0.
        reason=row["reason"] if row["graded_at"] else None,
        points_awarded=row["points_awarded"] if row["graded_at"] else None,
        created_at=row["created_at"],
        graded_at=row["graded_at"],
    )


def to_summary(row: Dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"],
        bot_id=row["bot_id"],
        title=row["title"],
        message_count=row["message_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        submission_status=to_team_status(row["submission_status"]),
    )


def to_conversation(row: Dict[str, Any]) -> Conversation:
    return Conversation(
        **to_summary(row).model_dump(),
        messages=[to_message(message) for message in row["messages"]],
        submission=to_submission(row["submission"]) if row["submission"] else None,
    )


# ---------- Admin mapping ----------


def to_admin_message(row: Dict[str, Any]) -> AdminChatMessage:
    return AdminChatMessage(**to_message(row).model_dump(), status=row["status"])


def to_admin_submission(row: Dict[str, Any]) -> AdminSubmission:
    submission = to_submission(row).model_dump()
    # Organisers see the reason before grading ends, since it may explain an error.
    submission["reason"] = row["reason"]
    submission["status"] = to_admin_status(row["status"])
    return AdminSubmission(
        **submission,
        team_id=row["team_id"],
        team_name=row["team_name"],
        model=row["model"],
    )


def to_admin_summary(row: Dict[str, Any]) -> AdminConversationSummary:
    summary = to_summary(row).model_dump()
    summary["submission_status"] = to_admin_status(row["submission_status"])
    return AdminConversationSummary(
        **summary,
        team_id=row["team_id"],
        team_name=row["team_name"],
        bot_label=row["bot_label"],
        level=row["level"],
        model=row["model"],
        tokens=row["tokens"],
        result=row["result"],
    )


def to_admin_conversation(row: Dict[str, Any]) -> AdminConversation:
    return AdminConversation(
        **to_admin_summary(row).model_dump(),
        messages=[to_admin_message(message) for message in row["messages"]],
        verdict=to_admin_submission(row["verdict"]) if row["verdict"] else None,
    )
