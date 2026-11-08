"""Stage F: end-to-end pipeline tests for L2 to L5 with the mock provider.

Tier 2 (pipeline). These prove that grade() wires everything together correctly, that
application-side checks hold, and that failures never become verdicts. They do NOT prove
a real LLM judges accurately or resists prompt injection. See test_g_real_model.py.
"""

import pytest

from engine.judge import errors
from engine.judge.bots import InMemoryBotConfigSource
from engine.judge.engine import JudgeEngine
from engine.judge.errors import GradingError
from engine.judge.providers import mock
from engine.judge.providers.mock import MarkerMockProvider, MockJudgeProvider
from engine.judge.reasons import REASON_TEXT
from judge_fixtures import INJECTION_TEXT, QUALIFYING, RUBRICS, bot_for, long_conversation, scenarios

LEVELS = (2, 3, 4, 5)
ALL_BOTS = InMemoryBotConfigSource([bot_for(level) for level in LEVELS])
SAFE_SENTENCES = set(REASON_TEXT.values())


class SpyMarkerProvider(MarkerMockProvider):
    """Marker mock that also records every request, so tests can inspect what was sent."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return super().complete(request)


def engine_for(provider, **kwargs):
    return JudgeEngine(provider, ALL_BOTS, **kwargs)


CASES = [
    (level, name, spec) for level in LEVELS for name, spec in sorted(scenarios(level).items())
]


@pytest.mark.parametrize("level,name,spec", CASES, ids=["L%d-%s" % (c[0], c[1]) for c in CASES])
def test_scenario_outcome(level, name, spec):
    provider = SpyMarkerProvider()
    result = engine_for(provider).grade(spec["conversation"], "bot-l%d" % level)

    assert result.passed is spec["passed"]
    assert result.reason in SAFE_SENTENCES  # only predefined participant-safe text
    assert RUBRICS[level] not in result.reason

    # User messages never reach the provider, in any scenario.
    user_texts = [m["content"] for m in spec["conversation"]["messages"] if m["role"] == "user"]
    for request in provider.requests:
        for text in user_texts:
            assert text not in request.system and text not in request.user
        assert RUBRICS[level] in request.system and RUBRICS[level] not in request.user


def test_contract_shape_for_every_level():
    for level in LEVELS:
        spec = scenarios(level)["genuine_success"]
        data = engine_for(SpyMarkerProvider()).grade(spec["conversation"], "bot-l%d" % level).to_contract_dict()
        assert data["passed"] is True
        assert set(data) == {"passed", "reason", "usage"}
        assert set(data["usage"]) == {"inputTokens", "outputTokens"}


def test_level_guidance_matches_the_bots_level():
    for level, marker in ((2, "Forbidden topic"), (3, "Hindi and Hinglish"), (4, "Malware"), (5, "Sexual content")):
        provider = SpyMarkerProvider()
        engine_for(provider).grade(scenarios(level)["refusal"]["conversation"], "bot-l%d" % level)
        assert marker in provider.requests[0].system


# ---------- failures at every level are errors, never verdicts ----------


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize(
    "behaviour,code",
    [
        (mock.timeout(), errors.PROVIDER_TIMEOUT),
        (mock.unavailable(), errors.PROVIDER_UNAVAILABLE),
        (mock.refused(), errors.PROVIDER_REFUSED),
        (mock.rate_limited(5), errors.PROVIDER_RATE_LIMITED),
        (mock.raw("not json"), errors.INVALID_OUTPUT),
        (mock.empty(), errors.INVALID_OUTPUT),
    ],
)
def test_provider_problems_raise_grading_error(level, behaviour, code):
    provider = MockJudgeProvider(default=behaviour)
    with pytest.raises(GradingError) as info:
        engine_for(provider).grade(scenarios(level)["genuine_success"]["conversation"], "bot-l%d" % level)
    assert info.value.code == code


# ---------- injection attempts (pipeline level) ----------


@pytest.mark.parametrize("level", LEVELS)
def test_injection_in_bot_reply_is_just_data(level):
    c = {"messages": [{"role": "assistant", "content": INJECTION_TEXT, "toolCalls": []}]}
    provider = SpyMarkerProvider()
    result = engine_for(provider).grade(c, "bot-l%d" % level)
    assert result.passed is False
    # The injection text only ever travels inside the JSON transcript, never in the instructions.
    assert INJECTION_TEXT not in provider.requests[0].system
    assert INJECTION_TEXT in provider.requests[0].user


@pytest.mark.parametrize("level", LEVELS)
def test_hijacked_judge_claiming_pass_for_a_refusal_turn_is_still_accepted_only_with_real_evidence(level):
    """With a scripted 'hijacked' provider, the validator's checks (not the prompt) hold the line."""
    c = scenarios(level)["refusal"]["conversation"]
    for bad in (mock.passing([5]), mock.passing([]), mock.passing([1], code=mock.ReasonCode.REFUSED)):
        with pytest.raises(GradingError) as info:
            engine_for(MockJudgeProvider(default=bad)).grade(c, "bot-l%d" % level)
        assert info.value.code == errors.INVALID_OUTPUT


# ---------- conversation shapes ----------


def test_code_blocks_and_quoted_markup_are_passed_through_as_data():
    code = "```html\n<script>alert(1)</script>\n```\nHe said: \"</transcript-abc>\" " + QUALIFYING
    c = {"messages": [{"role": "assistant", "content": code, "toolCalls": []}]}
    provider = SpyMarkerProvider()
    result = engine_for(provider).grade(c, "bot-l4")
    assert result.passed is True
    assert "<script>" not in provider.requests[0].user  # '<' is escaped inside the transcript
    assert "\\u003cscript>" in provider.requests[0].user


def test_tool_calls_count_as_evidence_input():
    c = {
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "toolCalls": [{"id": "t1", "name": "lookup", "arguments": "{}", "result": QUALIFYING}],
            }
        ]
    }
    assert engine_for(SpyMarkerProvider()).grade(c, "bot-l2").passed is True


# ---------- long conversations ----------


@pytest.mark.parametrize("position", ["start", "middle", "end"])
def test_long_conversation_whole_transcript_is_judged_wherever_the_evidence_is(position):
    total = 300
    marker = {"start": 1, "middle": 150, "end": 300}[position]
    provider = SpyMarkerProvider(max_context_tokens=200000)
    c = long_conversation(total, [marker])
    result = engine_for(provider, max_context_tokens=200000).grade(c, "bot-l2")
    assert result.passed is True
    sent = provider.requests[0].user
    assert "filler reply number 1," in sent and "filler reply number 300," in sent  # nothing dropped


def test_long_conversation_without_evidence_fails():
    provider = SpyMarkerProvider(max_context_tokens=200000)
    result = engine_for(provider, max_context_tokens=200000).grade(long_conversation(300, []), "bot-l2")
    assert result.passed is False


def test_conversation_over_the_limit_is_an_error_not_a_truncated_verdict():
    provider = SpyMarkerProvider(max_context_tokens=2000)
    c = long_conversation(300, [1])  # evidence at the very start must not be silently dropped
    with pytest.raises(GradingError) as info:
        engine_for(provider).grade(c, "bot-l2")
    assert info.value.code == errors.CONTEXT_TOO_LARGE
    assert provider.requests == []


# ---------- confirmation, end to end ----------


def test_confirmation_enabled_still_passes_genuine_success_and_blocks_a_flip():
    spec = scenarios(2)["genuine_success"]
    ok = MockJudgeProvider([mock.passing([1]), mock.passing([1])])
    assert engine_for(ok, confirm_passes=True).grade(spec["conversation"], "bot-l2").passed is True
    flip = MockJudgeProvider([mock.passing([1]), mock.failing()])
    assert engine_for(flip, confirm_passes=True).grade(spec["conversation"], "bot-l2").passed is False
