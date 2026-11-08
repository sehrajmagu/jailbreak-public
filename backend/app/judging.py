"""Grades submitted conversations in the background. See supabase/judge.sql.

POST /conversations/:id/submit only queues a submission, because the judge is slow.
A worker task takes one at a time, asks engine.judge for a verdict, and saves it. A
failure to grade is never a verdict: it's retried with a growing delay, or left for an
organiser to regrade if a retry can't help. Nothing here logs a transcript or rubric.

Organisers can switch judging off in the admin pages, and claim_submission then hands
out nothing. A rate limit, timeout or outage defers the submission without using up
its attempts, and pauses all grading for a growing delay, so a queue can't keep calling
a judge that's refusing calls.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from supabase import Client

from app.conversations import run_function, to_tool_call
from app.db import get_db
from engine.judge import JudgeEngine, JudgeSettings, build_default_engine
from engine.judge.bots import BotConfigSource
from engine.judge.errors import (
    PROVIDER_RATE_LIMITED,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAVAILABLE,
    GradingError,
)
from engine.judge.models import JudgeBotConfig

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
STOP_SECONDS = 30
# Longer than the slowest grade: two tries and a confirmation at the 30 second timeout.
LEASE_SECONDS = 300
# With the delays below, this keeps trying through about half an hour of outage.
MAX_ATTEMPTS = 8
FIRST_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 600
# The judge couldn't answer at all, so waiting will help. supabase/judge.sql sets the delay.
DEFERRED_CODES = frozenset({PROVIDER_RATE_LIMITED, PROVIDER_TIMEOUT, PROVIDER_UNAVAILABLE})


class DatabaseBotSource(BotConfigSource):
    def __init__(self, db: Client) -> None:
        self.db = db

    def get_bot_config(self, bot_id: str) -> Optional[JudgeBotConfig]:
        result = self.db.rpc("get_judge_bot_config", {"p_bot_id": bot_id}).execute().data
        if result["status"] != "ok":
            return None
        return JudgeBotConfig(
            id=result["id"],
            level=result["level"],
            check_type=result["check_type"],
            win_rubric=result["win_rubric"],
            enabled=result["enabled"],
        )


def retry_seconds(attempts: int, retryable: bool, retry_after: Optional[int] = None) -> Optional[int]:
    """How long until the next try, or None if it's waiting for an organiser."""
    if not retryable or attempts >= MAX_ATTEMPTS:
        return None
    delay = min(FIRST_RETRY_SECONDS * 2 ** (attempts - 1), MAX_RETRY_SECONDS)
    return max(delay, retry_after or 0)


def to_judge_messages(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "messages": [
            {
                "role": row["role"],
                "content": row["content"],
                "toolCalls": [to_tool_call(call).model_dump() for call in row["tool_calls"]],
            }
            for row in rows
        ]
    }


def grade_next(db: Client, engine: JudgeEngine) -> bool:
    """Grades one submission, if any is waiting. It blocks, so run it in a thread."""
    model = engine.provider.name
    claimed = run_function(db, "claim_submission", {
        "p_judge_model": model,
        "p_lease_seconds": LEASE_SECONDS,
        "p_max_attempts": MAX_ATTEMPTS,
    })["submission"]
    if claimed is None:
        return False

    try:
        result = engine.grade(to_judge_messages(claimed["messages"]), claimed["bot_id"])
    except GradingError as error:
        save_error(db, claimed, model, error)
        return True
    except Exception as error:
        # A bug must not become a verdict either. It's our fault, not the judge's,
        # so it's retried without pausing grading for everyone.
        logger.error("Grading crashed: %s", type(error).__name__)
        save_error(db, claimed, model, GradingError(PROVIDER_UNAVAILABLE), crashed=True)
        return True

    run_function(db, "finish_submission", {
        "p_submission_id": claimed["id"],
        "p_token": claimed["token"],
        "p_judge_model": model,
        "p_passed": result.passed,
        "p_reason": result.reason,
        "p_input_tokens": result.usage.input_tokens,
        "p_output_tokens": result.usage.output_tokens,
    })
    run_function(db, "clear_judge_backoff", {})
    return True


def save_error(db: Client, claimed: Dict[str, Any], model: str, error: GradingError, crashed: bool = False) -> None:
    # The fixed code only: organisers read it, and it can't hold provider text.
    params = {
        "p_submission_id": claimed["id"],
        "p_token": claimed["token"],
        "p_judge_model": model,
        "p_code": error.code,
        "p_input_tokens": error.usage.input_tokens,
        "p_output_tokens": error.usage.output_tokens,
    }
    if error.code in DEFERRED_CODES and not crashed:
        run_function(db, "defer_submission", {**params, "p_retry_after_seconds": error.retry_after_seconds})
        return
    run_function(db, "record_submission_error", {
        **params,
        "p_retry_seconds": retry_seconds(claimed["attempts"], error.retryable, error.retry_after_seconds),
    })
    if not crashed:
        # The judge did answer, even if not usefully, so it isn't rate limited.
        run_function(db, "clear_judge_backoff", {})


def worker_allowed() -> bool:
    """Whether to grade at all. The mock fails nearly every real conversation.

    A team must never lose a real win to it, so it only runs in development. In
    production the app refuses to start without a real judge. Anywhere else,
    submissions safely wait in the queue.
    """
    environment = os.environ.get("ENVIRONMENT", "").strip().lower()
    if JudgeSettings.from_env().provider != "mock" or environment == "development":
        return True
    if environment == "production":
        raise RuntimeError(
            "Refusing to start: the judge is on its mock provider, which fails real "
            "conversations. Set JUDGE_PROVIDER, JUDGE_BASE_URL, JUDGE_API_KEY and JUDGE_MODEL."
        )
    logger.warning("The judge isn't configured, so submissions will wait in the queue.")
    return False


_task: Optional["asyncio.Task[None]"] = None
_stopping: Optional[asyncio.Event] = None


async def run_worker(db: Client, engine: JudgeEngine, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            busy = await asyncio.to_thread(grade_next, db, engine)
        except Exception as error:
            # Usually the database. The queue keeps the work, so just try again.
            logger.error("Judge worker failed: %s", type(error).__name__)
            busy = False
        if not busy:
            try:
                await asyncio.wait_for(stopping.wait(), POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


def start_judge_worker() -> None:
    global _task, _stopping
    if not worker_allowed():
        return
    # Builds the engine now, so bad judge settings stop the app instead of every grade.
    db = get_db()
    engine = build_default_engine(DatabaseBotSource(db))
    _stopping = asyncio.Event()
    _task = asyncio.create_task(run_worker(db, engine, _stopping))


async def stop_judge_worker() -> None:
    """Lets a grade in progress finish. If it can't, its lease expires and it's retried."""
    global _task, _stopping
    if _task is None or _stopping is None:
        return
    _stopping.set()
    try:
        await asyncio.wait_for(_task, STOP_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("A grade was still running at shutdown. It will be retried.")
    _task, _stopping = None, None
