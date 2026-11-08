"""JudgeEngine: the provider-independent grading pipeline.

    load bot -> strip user turns -> build prompt -> size check -> provider call
        -> validate (shape, then evidence) -> optional PASS confirmation -> GradeResult

Rules this file enforces:
- A provider failure or unusable answer is a GradingError, never a PASS or a FAIL.
- A transcript that does not fit the context window is a GradingError
  (``context_too_large``). Nothing is ever truncated.
- Participant-facing text comes only from the fixed reason table.
- Logs hold request ids, codes and token counts, never transcripts or rubrics.
"""

import logging
import uuid
from typing import AbstractSet, List, Optional, Union

from pydantic import ValidationError

from engine.judge import errors
from engine.judge.bots import BotConfigSource
from engine.judge.errors import GradingError
from engine.judge.models import Conversation, GradeResult, JudgeBotConfig, ModelVerdict, ReasonCode, Usage
from engine.judge.prompt import build_system_prompt, build_user_message
from engine.judge.providers.base import (
    JudgeProvider,
    ProviderBadResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderRefused,
    ProviderRequest,
    ProviderTimeout,
    ProviderUnavailable,
)
from engine.judge.reasons import reason_text
from engine.judge.transcript import Turn, new_nonce, prepare_turns
from engine.judge.validator import InvalidModelOutput, validate_model_output

logger = logging.getLogger("engine.judge")

# Added to the user message when we re-ask after an unusable answer.
RETRY_SUFFIX = "\n\nYour previous reply was not valid. Reply with only the JSON object described above."

# One re-ask after a malformed answer, then give up with a retryable error.
MAX_ATTEMPTS = 2


class _Tally:
    """Running token total for one grade() call, including failed and confirmation calls."""

    def __init__(self) -> None:
        self.usage = Usage()

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.usage = self.usage.plus(Usage(input_tokens=input_tokens, output_tokens=output_tokens))


class JudgeEngine:
    def __init__(
        self,
        provider: JudgeProvider,
        bots: BotConfigSource,
        confirm_passes: bool = False,
        max_context_tokens: Optional[int] = None,
        max_output_tokens: int = 200,
    ) -> None:
        self.provider = provider
        self.bots = bots
        self.confirm_passes = confirm_passes
        self.max_output_tokens = max_output_tokens
        limit = provider.max_context_tokens
        self.max_context_tokens = min(limit, max_context_tokens) if max_context_tokens else limit

    # ---------- public ----------

    def grade(self, conversation: Union[Conversation, dict], bot_id: str) -> GradeResult:
        request_id = uuid.uuid4().hex[:12]
        tally = _Tally()
        try:
            result = self._grade(conversation, bot_id, request_id, tally)
        except GradingError as exc:
            if exc.usage.input_tokens == 0 and exc.usage.output_tokens == 0:
                exc.usage = tally.usage
            logger.warning("judge %s: error code=%s retryable=%s", request_id, exc.code, exc.retryable)
            raise
        logger.info(
            "judge %s: passed=%s tokens_in=%d tokens_out=%d",
            request_id,
            result.passed,
            result.usage.input_tokens,
            result.usage.output_tokens,
        )
        return result

    # ---------- pipeline ----------

    def _grade(self, conversation, bot_id: str, request_id: str, tally: _Tally) -> GradeResult:
        conv = self._coerce_conversation(conversation)
        bot = self._load_bot(bot_id)

        turns = prepare_turns(conv)  # user messages are dropped here
        if not turns:
            return self._result(ReasonCode.NO_BOT_REPLIES, False, tally)

        nonce = new_nonce()
        system = build_system_prompt(bot.win_rubric, bot.level, nonce)
        user = build_user_message(turns, nonce)
        self._check_fits(system, user)

        valid = frozenset(t.number for t in turns)
        verdict = self._ask(system, user, valid, request_id, tally)

        if verdict.verdict == "PASS" and self.confirm_passes:
            verdict = self._confirm(bot, turns, verdict, request_id, tally)

        return self._result(verdict.code, verdict.verdict == "PASS", tally)

    def _coerce_conversation(self, conversation) -> Conversation:
        if isinstance(conversation, Conversation):
            return conversation
        try:
            return Conversation.model_validate(conversation)
        except ValidationError:
            raise GradingError(errors.INVALID_INPUT, "The conversation is not in the expected format.")

    def _load_bot(self, bot_id: str) -> JudgeBotConfig:
        bot = self.bots.get_bot_config(bot_id)
        if bot is None:
            raise GradingError(errors.BOT_NOT_FOUND, "No such bot.")
        if bot.check_type != "judge":
            raise GradingError(errors.NOT_A_JUDGE_BOT, "This bot is not graded by the judge.")
        if not bot.win_rubric.strip():
            raise GradingError(errors.RUBRIC_MISSING, "This bot has no win rubric.")
        return bot

    def _check_fits(self, system: str, user: str) -> None:
        needed = (
            self.provider.estimate_tokens(system)
            + self.provider.estimate_tokens(user + RETRY_SUFFIX)
            + self.max_output_tokens
        )
        if needed > self.max_context_tokens:
            # Never truncate: a dropped turn could be the one that decides the verdict.
            raise GradingError(
                errors.CONTEXT_TOO_LARGE,
                "The conversation is too long for the judge's context window.",
            )

    # ---------- one provider round trip, with one re-ask on bad output ----------

    def _ask(
        self,
        system: str,
        user: str,
        valid_turns: AbstractSet[int],
        request_id: str,
        tally: _Tally,
    ) -> ModelVerdict:
        last_tag = "unknown"
        for attempt in range(MAX_ATTEMPTS):
            request = ProviderRequest(
                system=system,
                user=user if attempt == 0 else user + RETRY_SUFFIX,
                max_output_tokens=self.max_output_tokens,
                temperature=0.0,
                request_id="%s-%d" % (request_id, attempt),
            )
            text = self._call(request, tally)
            try:
                return validate_model_output(text, valid_turns)
            except InvalidModelOutput as exc:
                last_tag = exc.tag
                logger.warning("judge %s: unusable model output (%s), attempt %d", request_id, exc.tag, attempt + 1)
        raise GradingError(
            errors.INVALID_OUTPUT,
            "The judge did not return a usable answer (%s)." % last_tag,
            usage=tally.usage,
        )

    def _call(self, request: ProviderRequest, tally: _Tally) -> str:
        try:
            response = self.provider.complete(request)
        except ProviderTimeout:
            raise GradingError(errors.PROVIDER_TIMEOUT, "The judge timed out.", usage=tally.usage)
        except ProviderRateLimited as exc:
            raise GradingError(
                errors.PROVIDER_RATE_LIMITED,
                "The judge is rate limited.",
                usage=tally.usage,
                retry_after_seconds=exc.retry_after_seconds,
            )
        except ProviderRefused:
            raise GradingError(errors.PROVIDER_REFUSED, "The judge declined to process this content.", usage=tally.usage)
        except ProviderBadResponse:
            raise GradingError(errors.INVALID_OUTPUT, "The judge returned an unreadable response.", usage=tally.usage)
        except ProviderUnavailable:
            raise GradingError(errors.PROVIDER_UNAVAILABLE, "The judge is unavailable.", usage=tally.usage)
        except ProviderError:
            raise GradingError(errors.PROVIDER_UNAVAILABLE, "The judge failed.", usage=tally.usage)
        except Exception as exc:  # a bug in a provider must still never become a verdict
            logger.error("judge %s: provider crashed (%s)", request.request_id, type(exc).__name__)
            raise GradingError(errors.PROVIDER_UNAVAILABLE, "The judge failed.", usage=tally.usage)
        tally.add(response.input_tokens, response.output_tokens)
        return response.text

    # ---------- optional second pass ----------

    def _confirm(
        self,
        bot: JudgeBotConfig,
        turns: List[Turn],
        first: ModelVerdict,
        request_id: str,
        tally: _Tally,
    ) -> ModelVerdict:
        """Re-check a PASS on the cited turns plus their neighbours, with a fresh nonce.

        Both calls must agree. A disagreement is a FAIL with the second call's code.
        """
        wanted = set()
        for number in first.evidence_turns:
            wanted.update((number - 1, number, number + 1))
        subset = [t for t in turns if t.number in wanted]  # original numbers and order kept
        nonce = new_nonce()
        system = build_system_prompt(bot.win_rubric, bot.level, nonce)
        user = build_user_message(subset, nonce)
        self._check_fits(system, user)
        return self._ask(system, user, frozenset(t.number for t in subset), request_id + "-confirm", tally)

    # ---------- result ----------

    @staticmethod
    def _result(code: ReasonCode, passed: bool, tally: _Tally) -> GradeResult:
        return GradeResult(passed=passed, reason=reason_text(code), usage=tally.usage)
