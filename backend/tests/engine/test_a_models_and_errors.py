"""Stage A: models, errors and provider interface. Unit tests (application logic only)."""

import pytest
from pydantic import ValidationError

from engine.judge import errors
from engine.judge.errors import GradingError
from engine.judge.models import (
    Conversation,
    GradeResult,
    JudgeBotConfig,
    ModelVerdict,
    ReasonCode,
    Usage,
)
from engine.judge.providers.base import JudgeProvider, ProviderRequest, ProviderResponse


def test_conversation_parses_contract_example():
    raw = {
        "messages": [
            {"role": "user", "content": "hi", "toolCalls": []},
            {
                "role": "assistant",
                "content": "Done.",
                "toolCalls": [{"id": "tc-1", "name": "lookup_record", "arguments": "{}", "result": "{}"}],
            },
        ]
    }
    conv = Conversation.model_validate(raw)
    assert conv.messages[1].tool_calls[0].name == "lookup_record"


def test_null_tool_result_is_not_required():
    conv = Conversation.model_validate(
        {"messages": [{"role": "assistant", "content": "x", "toolCalls": [{"id": "1", "name": "t", "arguments": "{}"}]}]}
    )
    assert conv.messages[0].tool_calls[0].result == ""


def test_unknown_role_rejected():
    with pytest.raises(ValidationError):
        Conversation.model_validate({"messages": [{"role": "system", "content": "x"}]})


def test_grade_result_serialises_to_contract_shape():
    result = GradeResult(passed=True, reason="ok", usage=Usage(input_tokens=540, output_tokens=35))
    assert result.to_contract_dict() == {
        "passed": True,
        "reason": "ok",
        "usage": {"inputTokens": 540, "outputTokens": 35},
    }


def test_usage_rejects_negative_and_adds():
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1, output_tokens=0)
    total = Usage(input_tokens=1, output_tokens=2).plus(Usage(input_tokens=3, output_tokens=4))
    assert (total.input_tokens, total.output_tokens) == (4, 6)


def test_bot_config_ignores_secret_fields():
    cfg = JudgeBotConfig.model_validate(
        {
            "id": "bot-1",
            "level": 2,
            "checkType": "judge",
            "winRubric": "r",
            "enabled": True,
            "systemPrompt": "TOP SECRET",
            "defences": {"inputFilter": True},
        }
    )
    assert not hasattr(cfg, "system_prompt")
    assert "TOP SECRET" not in cfg.model_dump_json()


def test_model_verdict_accepts_camel_case_json():
    v = ModelVerdict.model_validate_json('{"verdict":"PASS","code":"OBJECTIVE_MET","evidenceTurns":[2]}')
    assert v.evidence_turns == [2] and v.code is ReasonCode.OBJECTIVE_MET


@pytest.mark.parametrize(
    "payload",
    [
        '{"verdict":"MAYBE","code":"OBJECTIVE_MET","evidenceTurns":[1]}',  # bad verdict
        '{"verdict":"PASS","code":"WHATEVER","evidenceTurns":[1]}',  # bad code
        '{"verdict":"PASS","code":"OBJECTIVE_MET","evidenceTurns":[1],"reason":"free text"}',  # extra field
        '{"code":"OBJECTIVE_MET","evidenceTurns":[1]}',  # missing verdict
        '{"verdict":"pass","code":"OBJECTIVE_MET","evidenceTurns":[1]}',  # wrong case
    ],
)
def test_model_verdict_is_strict(payload):
    with pytest.raises(ValidationError):
        ModelVerdict.model_validate_json(payload)


def test_grading_error_retryability():
    assert GradingError(errors.PROVIDER_TIMEOUT).retryable is True
    assert GradingError(errors.INVALID_OUTPUT).retryable is True
    assert GradingError(errors.PROVIDER_REFUSED).retryable is False
    assert GradingError(errors.CONTEXT_TOO_LARGE).retryable is False


def test_grading_error_rejects_unknown_code_and_carries_usage():
    with pytest.raises(ValueError):
        GradingError("made_up")
    err = GradingError(errors.PROVIDER_RATE_LIMITED, usage=Usage(input_tokens=5), retry_after_seconds=30)
    assert err.to_dict() == {
        "code": "provider_rate_limited",
        "retryable": True,
        "retryAfterSeconds": 30,
        "usage": {"inputTokens": 5, "outputTokens": 0},
    }


def test_provider_is_abstract_and_estimates_conservatively():
    with pytest.raises(TypeError):
        JudgeProvider()  # type: ignore[abstract]

    class Dummy(JudgeProvider):
        def complete(self, request):
            return ProviderResponse(text="x")

    d = Dummy()
    assert d.estimate_tokens("a" * 100) == 50
    assert d.complete(ProviderRequest(system="s", user="u")).text == "x"
