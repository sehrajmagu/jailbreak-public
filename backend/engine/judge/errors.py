"""GradingError: the "error with no verdict" from docs/INTERFACE.md.

Part A must never turn a GradingError into a ``failed`` submission. ``retryable``
tells A whether retrying later is sensible.
"""

from typing import Optional

from engine.judge.models import Usage

# Retryable: a later attempt may succeed.
PROVIDER_TIMEOUT = "provider_timeout"
PROVIDER_UNAVAILABLE = "provider_unavailable"
PROVIDER_RATE_LIMITED = "provider_rate_limited"
INVALID_OUTPUT = "invalid_output"

# Not retryable: needs a human or a config change.
PROVIDER_REFUSED = "provider_refused"
CONTEXT_TOO_LARGE = "context_too_large"
RUBRIC_MISSING = "rubric_missing"
NOT_A_JUDGE_BOT = "not_a_judge_bot"
BOT_NOT_FOUND = "bot_not_found"
INVALID_INPUT = "invalid_input"

RETRYABLE_CODES = frozenset(
    {PROVIDER_TIMEOUT, PROVIDER_UNAVAILABLE, PROVIDER_RATE_LIMITED, INVALID_OUTPUT}
)
ALL_CODES = RETRYABLE_CODES | frozenset(
    {PROVIDER_REFUSED, CONTEXT_TOO_LARGE, RUBRIC_MISSING, NOT_A_JUDGE_BOT, BOT_NOT_FOUND, INVALID_INPUT}
)


class GradingError(Exception):
    """Grading failed and there is no verdict.

    The message is a fixed, safe string. Never put transcript text, rubric text or
    raw provider output in it: raw details are logged privately by the caller.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        usage: Optional[Usage] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        if code not in ALL_CODES:
            raise ValueError("Unknown GradingError code: %s" % code)
        super().__init__(message or code)
        self.code = code
        self.retryable = code in RETRYABLE_CODES
        self.usage = usage if usage is not None else Usage()
        self.retry_after_seconds = retry_after_seconds

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "retryable": self.retryable,
            "retryAfterSeconds": self.retry_after_seconds,
            "usage": self.usage.model_dump(by_alias=True),
        }
