"""Mock judge providers for development and tests. They never call a model.

IMPORTANT: these exist to test the software around the model (parsing, validation,
error handling, wiring). They do NOT measure judging accuracy, and they say nothing
about how a real LLM resists prompt injection. That needs the opt-in real-model tests.
"""

import json
import re
import threading
from typing import Callable, Iterable, List, Optional, Sequence, Union

from engine.judge.models import ReasonCode
from engine.judge.providers.base import (
    JudgeProvider,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderRefused,
    ProviderRequest,
    ProviderResponse,
    ProviderTimeout,
    ProviderUnavailable,
)

Behaviour = Callable[[ProviderRequest], Union[str, ProviderResponse]]


def _verdict_json(verdict: str, code: ReasonCode, evidence_turns: Iterable[int]) -> str:
    return json.dumps({"verdict": verdict, "code": code.value, "evidenceTurns": list(evidence_turns)})


# ---------- scripted behaviours ----------


def passing(evidence_turns: Sequence[int] = (1,), code: ReasonCode = ReasonCode.OBJECTIVE_MET) -> Behaviour:
    return lambda request: _verdict_json("PASS", code, evidence_turns)


def failing(code: ReasonCode = ReasonCode.NOT_ESTABLISHED, evidence_turns: Sequence[int] = ()) -> Behaviour:
    return lambda request: _verdict_json("FAIL", code, evidence_turns)


def raw(text: str) -> Behaviour:
    """Return exactly this text, e.g. invalid JSON or JSON with the wrong shape."""
    return lambda request: text


def empty() -> Behaviour:
    return raw("")


def _raiser(exc: Exception) -> Behaviour:
    def behave(request: ProviderRequest) -> str:
        raise exc

    return behave


def timeout() -> Behaviour:
    return _raiser(ProviderTimeout("timeout"))


def unavailable() -> Behaviour:
    return _raiser(ProviderUnavailable("unavailable"))


def refused() -> Behaviour:
    return _raiser(ProviderRefused("refused"))


def rate_limited(retry_after_seconds: Optional[int] = None) -> Behaviour:
    return _raiser(ProviderRateLimited(retry_after_seconds))


def bad_response() -> Behaviour:
    return _raiser(ProviderBadResponse("bad response"))


def _approx_tokens(text: str) -> int:
    return len(text) // 4


class MockJudgeProvider(JudgeProvider):
    """Plays back scripted behaviours, one per call, and records every request.

    Tests can assert on ``provider.requests`` to check exactly what the engine sent
    (for example, that no user message ever reached the provider).
    """

    def __init__(
        self,
        behaviours: Optional[Iterable[Behaviour]] = None,
        default: Optional[Behaviour] = None,
        name: str = "mock",
        max_context_tokens: int = 8000,
    ) -> None:
        self.name = name
        self.max_context_tokens = max_context_tokens
        self._queue: List[Behaviour] = list(behaviours or [])
        self._default = default
        self._lock = threading.Lock()
        self.requests: List[ProviderRequest] = []

    def queue(self, *behaviours: Behaviour) -> "MockJudgeProvider":
        with self._lock:
            self._queue.extend(behaviours)
        return self

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        with self._lock:
            self.requests.append(request)
            if self._queue:
                behaviour = self._queue.pop(0)
            elif self._default is not None:
                behaviour = self._default
            else:
                raise AssertionError("MockJudgeProvider was called but no behaviour is scripted")
        result = behaviour(request)
        if isinstance(result, ProviderResponse):
            return result
        return ProviderResponse(
            text=result,
            input_tokens=_approx_tokens(request.system + request.user),
            output_tokens=_approx_tokens(result),
        )


# ---------- marker-based mock (plumbing only) ----------

QUALIFYING_MARKER = "[[SYNTHETIC:QUALIFYING]]"
PARTIAL_MARKER = "[[SYNTHETIC:PARTIAL]]"
REFUSAL_MARKER = "[[SYNTHETIC:REFUSAL]]"

# The engine wraps the transcript as <transcript-NONCE>\n<JSON>\n</transcript-NONCE>.
# The JSON escapes "<", so message text can never fake the closing tag.
_TRANSCRIPT_RE = re.compile(r"<transcript-([0-9a-f]+)>\n(.*?)\n</transcript-\1>", re.DOTALL)


class MarkerMockProvider(JudgeProvider):
    """Looks for synthetic marker strings in the transcript instead of judging.

    This is NOT a judge. It lets the whole pipeline run end to end with no model:
    it answers PASS only when an assistant turn contains ``[[SYNTHETIC:QUALIFYING]]``.
    Real fixtures never contain real harmful content, only these labelled markers.
    """

    def __init__(self, name: str = "mock-marker", max_context_tokens: int = 8000) -> None:
        self.name = name
        self.max_context_tokens = max_context_tokens

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        match = _TRANSCRIPT_RE.search(request.user)
        if match is None:
            raise ProviderBadResponse("no transcript found")
        try:
            turns = json.loads(match.group(2))
        except ValueError:
            raise ProviderBadResponse("transcript is not JSON")

        def turns_with(marker: str) -> List[int]:
            found = []
            for turn in turns:
                blob = json.dumps(turn, ensure_ascii=False)
                if marker in blob:
                    found.append(int(turn["turn"]))
            return found

        qualifying = turns_with(QUALIFYING_MARKER)
        if qualifying:
            text = _verdict_json("PASS", ReasonCode.OBJECTIVE_MET, qualifying[:20])
        elif turns_with(PARTIAL_MARKER):
            text = _verdict_json("FAIL", ReasonCode.PARTIAL, [])
        elif turns_with(REFUSAL_MARKER):
            text = _verdict_json("FAIL", ReasonCode.REFUSED, [])
        else:
            text = _verdict_json("FAIL", ReasonCode.NOT_ESTABLISHED, [])
        return ProviderResponse(
            text=text,
            input_tokens=_approx_tokens(request.system + request.user),
            output_tokens=_approx_tokens(text),
        )
