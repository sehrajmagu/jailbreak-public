"""Settings (from environment variables) and the provider registry.

Nothing here needs an API key by default: ``JUDGE_PROVIDER`` defaults to ``mock``.
"""

import os
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional

from engine.judge.providers.base import JudgeProvider
from engine.judge.providers.mock import MarkerMockProvider

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class JudgeSettings:
    provider: str = "mock"
    base_url: str = ""
    api_key: str = ""  # only ever read from the environment, never hardcoded
    model: str = ""
    timeout_seconds: float = 30.0
    max_context_tokens: int = 8000
    max_output_tokens: int = 200
    confirm_passes: bool = False  # optional second-pass check on PASS, off by default

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "JudgeSettings":
        env = os.environ if environ is None else environ
        confirm = env.get("JUDGE_CONFIRM_PASSES", "false").strip().lower()
        if confirm not in _TRUE and confirm not in _FALSE:
            raise ValueError("JUDGE_CONFIRM_PASSES must be true or false")
        return cls(
            provider=env.get("JUDGE_PROVIDER", "mock").strip().lower() or "mock",
            base_url=env.get("JUDGE_BASE_URL", ""),
            api_key=env.get("JUDGE_API_KEY", ""),
            model=env.get("JUDGE_MODEL", ""),
            timeout_seconds=_positive(env, "JUDGE_TIMEOUT_SECONDS", 30.0, float),
            max_context_tokens=_positive(env, "JUDGE_MAX_CONTEXT_TOKENS", 8000, int),
            max_output_tokens=_positive(env, "JUDGE_MAX_OUTPUT_TOKENS", 200, int),
            confirm_passes=confirm in _TRUE,
        )

    def __repr__(self) -> str:  # keep the API key out of logs
        return "JudgeSettings(provider=%r, model=%r, confirm_passes=%r)" % (
            self.provider,
            self.model,
            self.confirm_passes,
        )


def _positive(env: Mapping[str, str], name: str, default, cast: Callable):
    value = env.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        parsed = cast(value)
    except ValueError:
        raise ValueError("%s must be a number" % name)
    if parsed <= 0:
        raise ValueError("%s must be greater than zero" % name)
    return parsed


# ---------- provider registry ----------

ProviderFactory = Callable[[JudgeSettings], JudgeProvider]

def _openai_compatible(settings: JudgeSettings) -> JudgeProvider:
    # Imported here so the mock and the tests need no provider SDK.
    from engine.judge.providers.openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(settings)


_REGISTRY = {
    "mock": lambda settings: MarkerMockProvider(max_context_tokens=settings.max_context_tokens),
    "openai_compatible": _openai_compatible,
}  # type: Dict[str, ProviderFactory]


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Plug in a real provider (e.g. an OpenAI-compatible adapter) without touching the engine."""
    _REGISTRY[name.strip().lower()] = factory


def build_provider(settings: JudgeSettings) -> JudgeProvider:
    factory = _REGISTRY.get(settings.provider)
    if factory is None:
        raise ValueError(
            "Unknown JUDGE_PROVIDER %r. Available: %s. Register a real one with register_provider()."
            % (settings.provider, ", ".join(sorted(_REGISTRY)))
        )
    return factory(settings)
