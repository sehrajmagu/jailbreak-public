"""Typed input and output models for the judge.

JSON fields are camelCase, to match docs/INTERFACE.md and lib/types.ts. Part B
deliberately does not import from ``app.schemas`` (Part A), so the two parts stay
independent.
"""

from enum import Enum
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---------- grade() input ----------


class ToolCall(ApiModel):
    id: str
    name: str
    arguments: str
    result: str = ""


class ConversationMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = ""
    tool_calls: List[ToolCall] = Field(default_factory=list)


class Conversation(ApiModel):
    messages: List[ConversationMessage] = Field(default_factory=list)


# ---------- grade() output ----------


class Usage(ApiModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class GradeResult(ApiModel):
    """The contract in docs/INTERFACE.md section 3, unchanged."""

    passed: bool
    reason: str
    usage: Usage

    def to_contract_dict(self) -> Dict[str, Any]:
        """camelCase JSON-ready dict: {passed, reason, usage: {inputTokens, outputTokens}}."""
        return self.model_dump(by_alias=True)


# ---------- bot configuration (least privilege) ----------


class JudgeBotConfig(ApiModel):
    """The only BotConfig fields the judge reads.

    Extra fields (systemPrompt, defences, ...) are ignored on purpose: the judge
    must never see a bot's system prompt or password.
    """

    id: str
    level: int
    check_type: Literal["answer", "judge"]
    win_rubric: str = ""
    enabled: bool = True


# ---------- what the judge model must emit ----------


class ReasonCode(str, Enum):
    OBJECTIVE_MET = "OBJECTIVE_MET"
    REFUSED = "REFUSED"
    DISCUSSED_NOT_PRODUCED = "DISCUSSED_NOT_PRODUCED"
    PARTIAL = "PARTIAL"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NO_BOT_REPLIES = "NO_BOT_REPLIES"


class ModelVerdict(BaseModel):
    """Strict schema for the judge model's JSON. Unknown fields are rejected.

    There is no free-text field. The model can only pick an enum code and cite
    turn numbers, so even a hijacked judge cannot write the rubric, the system
    prompt or a password into a team-facing reason.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    verdict: Literal["PASS", "FAIL"]
    code: ReasonCode
    evidence_turns: List[int] = Field(default_factory=list, max_length=20)
