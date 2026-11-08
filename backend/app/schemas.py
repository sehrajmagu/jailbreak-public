"""Pydantic models matching lib/types.ts. JSON fields are camelCase, as in that file.

Participant models must never gain fields for system prompts, passwords, rubrics,
defences or judge details. Those belong only on Admin* models.
"""

from typing import Generic, List, Literal, Optional, TypeVar, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from typing_extensions import Annotated

T = TypeVar("T")

CheckType = Literal["answer", "judge"]
BotStatus = Literal["locked", "unattempted", "attempted", "solved"]
SubmissionStatus = Literal["pending", "grading", "passed", "failed"]
ConversationResult = Literal[
    "pending", "grading", "passed", "failed", "not_submitted", "answer_bot"
]
LevelOverride = Literal["granted", "revoked"]
ModelName = Literal["Astra", "Terra", "Sol", "Luna", "Open-source LLM", "Fable 5.1"]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Paginated(ApiModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int


# ---------- Auth ----------


class TeamSession(ApiModel):
    role: Literal["team"] = "team"
    team_id: str
    team_name: str


class AdminSession(ApiModel):
    role: Literal["admin"] = "admin"
    username: str
    # admin_users.id, for recording who changed what. proxy.ts doesn't need it.
    admin_id: Optional[str] = None


Session = Union[TeamSession, AdminSession]


class TeamCredentials(ApiModel):
    team: str
    password: str


class AdminCredentials(ApiModel):
    username: str
    password: str


# ---------- Participant ----------


class TeamProfile(ApiModel):
    id: str
    name: str
    tokens_used: int


class ModelUsage(ApiModel):
    model: str
    tokens_used: int


class TeamBudget(ApiModel):
    budget_usd: float
    spent_usd: float
    remaining_usd: float


class DashboardSummary(ApiModel):
    points: int
    rank: Optional[int]
    total_teams: int
    attempted: int
    solved: int
    remaining: int
    levels_completed: int
    total_levels: int
    prize_earned: int
    # Not in lib/types.ts yet. The website ignores it.
    tokens_used: int
    usage_by_model: List[ModelUsage]


class BotCard(ApiModel):
    id: str
    level: int
    label: str
    # Any display name from the models table, as the admin conversation models take.
    # ModelName in lib/types.ts still holds placeholders until the real names are final.
    model: str
    check_type: CheckType
    points: int
    status: BotStatus
    # Not in lib/types.ts on BotCard yet. The website ignores it on the levels page.
    wrong_answers_used: int = 0


class BotDetail(BotCard):
    level_name: str
    description: str
    goal: str
    wrong_answers_used: int
    max_wrong_answers: int


class LevelProgress(ApiModel):
    level: int
    name: str
    locked: bool
    solved: int
    required: int
    total: int
    complete: bool
    revoked: bool
    prize_amount: int
    prize_awarded: int
    bots: List[BotCard]


class ToolCall(ApiModel):
    id: str
    name: str
    arguments: str
    result: str


class ChatMessage(ApiModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    tool_calls: List[ToolCall]
    tokens: int
    created_at: str


class Submission(ApiModel):
    id: str
    conversation_id: Optional[str]
    bot_id: str
    level: int
    bot_label: str
    check_type: CheckType
    status: SubmissionStatus
    reason: Optional[str]
    points_awarded: Optional[int]
    created_at: str
    graded_at: Optional[str]


class AnswerRequest(ApiModel):
    answer: str


class AnswerResult(ApiModel):
    correct: bool
    points_awarded: int
    wrong_answers_used: int
    max_wrong_answers: int


class ConversationSummary(ApiModel):
    id: str
    bot_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str
    submission_status: Optional[SubmissionStatus]


class Conversation(ConversationSummary):
    messages: List[ChatMessage]
    submission: Optional[Submission]


class SendMessageRequest(ApiModel):
    bot_id: str
    conversation_id: Optional[str]
    # begin_turn applies the real 8,000-character limit after trimming. This just
    # stops a huge body reaching the database.
    content: str = Field(max_length=16000)


class ChatStartEvent(ApiModel):
    type: Literal["start"] = "start"
    conversation_id: str
    user_message: ChatMessage
    assistant_message_id: str


class ChatToolCallEvent(ApiModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call: ToolCall


class ChatDeltaEvent(ApiModel):
    type: Literal["delta"] = "delta"
    text: str


class ChatDoneEvent(ApiModel):
    type: Literal["done"] = "done"
    message: ChatMessage


class ChatErrorEvent(ApiModel):
    type: Literal["error"] = "error"
    code: Literal["bot_unavailable", "budget_exceeded"] = "bot_unavailable"


ChatEvent = Annotated[
    Union[ChatStartEvent, ChatToolCallEvent, ChatDeltaEvent, ChatDoneEvent, ChatErrorEvent],
    Field(discriminator="type"),
]


class LeaderboardEntry(ApiModel):
    rank: int
    team_id: str
    team_name: str
    score: int
    solved: int
    is_own_team: bool


# ---------- Admin only ----------


class BotInput(ApiModel):
    label: str
    level: int
    model: ModelName
    check_type: CheckType
    points: int
    description: str
    goal: str
    system_prompt: str
    win_rubric: str
    secret: str
    defences: List[str]
    enabled: bool


# The admin models below take any model name, not only ModelName, because they
# show the display names stored in the models table. ModelName in lib/types.ts
# still holds placeholders until the real names are final.


class AdminBot(BotInput):
    id: str
    model: str


class AdminModel(ApiModel):
    model: str


class SetBotEnabledRequest(ApiModel):
    enabled: bool


class AdminJudging(ApiModel):
    """Organisers only. Teams never see whether judging is on."""

    enabled: bool
    # Set while grading waits out a judge rate limit or outage.
    paused_until: Optional[str]
    updated_at: Optional[str]
    # Submitted and still to be graded, including ones waiting to retry.
    waiting: int
    # Stuck on an error a retry can't fix. Fix the cause, then regrade them.
    needs_organiser: int


class SetJudgingRequest(ApiModel):
    enabled: bool


class AdminTeam(ApiModel):
    id: str
    name: str
    disabled: bool
    tokens_used: int
    attempted: int
    solved: int
    score: int
    rank: Optional[int]
    last_active_at: Optional[str]
    created_at: str


class TeamBotProgress(ApiModel):
    bot_id: str
    label: str
    level: int
    model: str
    status: BotStatus
    conversations: int


class AdminTeamDetail(AdminTeam):
    progress: List[TeamBotProgress]


class CreateTeamInput(ApiModel):
    name: str
    password: str


class UpdateTeamInput(ApiModel):
    name: Optional[str] = None
    disabled: Optional[bool] = None


class ResetPasswordResult(ApiModel):
    temporary_password: str


class AdminSubmission(Submission):
    team_id: str
    team_name: str
    model: str


class AdminConversationSummary(ConversationSummary):
    team_id: str
    team_name: str
    bot_label: str
    level: int
    model: str
    tokens: int
    result: ConversationResult


class AdminChatMessage(ChatMessage):
    status: Literal["complete", "failed"]


class AdminConversation(AdminConversationSummary):
    messages: List[AdminChatMessage]
    verdict: Optional[AdminSubmission]


class ConversationFilters(ApiModel):
    team_id: Optional[UUID] = None
    bot_id: Optional[UUID] = None
    model: Optional[str] = None
    result: Optional[ConversationResult] = None
    page: Optional[int] = Field(default=None, ge=1)
    page_size: Optional[int] = Field(default=None, ge=1, le=100)


class SubmissionFilters(ApiModel):
    status: Optional[SubmissionStatus] = None
    team_id: Optional[UUID] = None
    page: Optional[int] = Field(default=None, ge=1)
    page_size: Optional[int] = Field(default=None, ge=1, le=100)


class OverviewPoint(ApiModel):
    date: str
    attempts: int
    solves: int
    tokens_consumed: int


class AdminOverview(ApiModel):
    total_teams: int
    active_teams: int
    tokens_consumed: int
    total_attempts: int
    total_solves: int
    series: List[OverviewPoint]


class BotStat(ApiModel):
    bot_id: str
    label: str
    level: int
    model: str
    enabled: bool
    attempts: int
    teams_attempted: int
    solves: int
    solve_rate: float
    avg_attempts_to_solve: Optional[float]
    tokens_used: int


class LevelConfig(ApiModel):
    level: int
    name: str
    prize_amount: int
    required_bots: int
    max_winners: Optional[int]


class AdminLevelCompletion(ApiModel):
    level: int
    solved: int
    required: int
    total: int
    earned: bool
    override: Optional[LevelOverride]
    complete: bool
    prize_awarded: int
    completed_at: Optional[str]


class AdminTeamPrizes(ApiModel):
    team_id: str
    team_name: str
    levels: List[AdminLevelCompletion]
    prize_owed: int


class AdminPrizes(ApiModel):
    require_levels_in_order: bool
    levels: List[LevelConfig]
    teams: List[AdminTeamPrizes]
    total_prize_owed: int


class SetLevelOverrideRequest(ApiModel):
    override: Optional[LevelOverride]
