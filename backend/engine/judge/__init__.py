"""LLM-as-a-Judge for Levels 2 to 5.

Entry point for Part A: ``grade(conversation, bot_id)``. See backend/engine/judge/README.md.

Part A calls ``configure(bot_source=...)`` once at startup, then ``grade()`` from its
background worker. ``grade()`` returns a GradeResult (``passed``, ``reason``, ``usage``;
``.to_contract_dict()`` gives the camelCase JSON) or raises GradingError, which carries no
verdict and must never be recorded as a failed submission.
"""

import threading
from typing import Mapping, Optional, Union

from engine.judge.bots import BotConfigSource
from engine.judge.config import JudgeSettings, build_provider
from engine.judge.engine import JudgeEngine
from engine.judge.errors import GradingError
from engine.judge.models import Conversation, GradeResult, JudgeBotConfig, Usage

__all__ = [
    "BotConfigSource",
    "Conversation",
    "GradeResult",
    "GradingError",
    "JudgeBotConfig",
    "JudgeEngine",
    "JudgeSettings",
    "Usage",
    "build_default_engine",
    "configure",
    "grade",
]

_lock = threading.Lock()
_engine = None  # type: Optional[JudgeEngine]


def build_default_engine(
    bot_source: BotConfigSource, environ: Optional[Mapping[str, str]] = None
) -> JudgeEngine:
    """Build an engine from JUDGE_* environment variables (mock provider if unset)."""
    settings = JudgeSettings.from_env(environ)
    return JudgeEngine(
        provider=build_provider(settings),
        bots=bot_source,
        confirm_passes=settings.confirm_passes,
        max_context_tokens=settings.max_context_tokens,
        max_output_tokens=settings.max_output_tokens,
    )


def configure(
    bot_source: Optional[BotConfigSource] = None,
    engine: Optional[JudgeEngine] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> JudgeEngine:
    """Set the engine used by ``grade()``. Pass a ready engine, or a bot source to build one."""
    global _engine
    if engine is None:
        if bot_source is None:
            raise ValueError("configure() needs a bot_source or an engine")
        engine = build_default_engine(bot_source, environ)
    with _lock:
        _engine = engine
    return engine


def grade(conversation: Union[Conversation, dict], bot_id: str) -> GradeResult:
    """Grade a judge-bot conversation. Raises GradingError (no verdict) if grading fails."""
    engine = _engine
    if engine is None:
        raise RuntimeError("engine.judge is not configured: call configure(bot_source=...) at startup")
    return engine.grade(conversation, bot_id)
