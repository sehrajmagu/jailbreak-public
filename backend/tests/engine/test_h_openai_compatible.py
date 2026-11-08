"""The real provider, against a fake HTTP transport. No keys, no network, no spend."""

import httpx
import pytest
from dataclasses import replace

from engine.judge import errors
from engine.judge.bots import InMemoryBotConfigSource
from engine.judge.config import JudgeSettings, build_provider
from engine.judge.engine import JudgeEngine
from engine.judge.errors import GradingError
from engine.judge.providers.base import (
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderRefused,
    ProviderRequest,
    ProviderTimeout,
    ProviderUnavailable,
)
from engine.judge.providers.openai_compatible import OpenAICompatibleProvider

SETTINGS = JudgeSettings(
    provider="openai_compatible", base_url="https://judge.test/v1", api_key="test-key", model="judge-model",
)
REQUEST = ProviderRequest(system="RUBRIC-PRIVATE", user="TRANSCRIPT-PRIVATE", max_output_tokens=50)
VERDICT = '{"verdict": "PASS", "code": "OBJECTIVE_MET", "evidenceTurns": [1]}'


def reply(content=VERDICT, finish_reason="stop", **message):
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 0, "model": "judge-model",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content, **message}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    })


def provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(SETTINGS, http_client=client)


def test_sends_system_and_user_separately_and_reads_tokens():
    seen = []

    def handler(request):
        seen.append(request)
        return reply()

    response = provider(handler).complete(REQUEST)
    assert response.text == VERDICT and (response.input_tokens, response.output_tokens) == (11, 7)
    body = httpx.Response(200, content=seen[0].content).json()
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "RUBRIC-PRIVATE"
    assert body["model"] == "judge-model" and body["max_completion_tokens"] == 50
    assert seen[0].headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_status_errors_are_unavailable(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "RUBRIC-PRIVATE echoed"}})

    with pytest.raises(ProviderUnavailable) as caught:
        provider(handler).complete(REQUEST)
    assert "PRIVATE" not in str(caught.value)
    assert len(calls) == 1  # the worker owns retries


def test_rate_limit_keeps_the_providers_delay():
    with pytest.raises(ProviderRateLimited) as caught:
        provider(lambda r: httpx.Response(429, headers={"retry-after": "42"}, json={})).complete(REQUEST)
    assert caught.value.retry_after_seconds == 42


def test_rate_limit_without_a_delay():
    with pytest.raises(ProviderRateLimited) as caught:
        provider(lambda r: httpx.Response(429, json={})).complete(REQUEST)
    assert caught.value.retry_after_seconds is None


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("RUBRIC-PRIVATE")

    with pytest.raises(ProviderTimeout) as caught:
        provider(handler).complete(REQUEST)
    assert "PRIVATE" not in str(caught.value)


def test_connection_error():
    def handler(request):
        raise httpx.ConnectError("RUBRIC-PRIVATE")

    with pytest.raises(ProviderUnavailable) as caught:
        provider(handler).complete(REQUEST)
    assert "PRIVATE" not in str(caught.value)


def test_content_filter_is_a_refusal():
    with pytest.raises(ProviderRefused):
        provider(lambda r: reply(content="", finish_reason="content_filter")).complete(REQUEST)


def test_refusal_field_is_a_refusal():
    with pytest.raises(ProviderRefused):
        provider(lambda r: reply(content=None, refusal="I can't")).complete(REQUEST)


def test_no_choices_is_a_bad_response():
    empty = {"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": []}
    with pytest.raises(ProviderBadResponse):
        provider(lambda r: httpx.Response(200, json=empty)).complete(REQUEST)


def test_missing_settings_are_refused():
    for missing in ("base_url", "api_key", "model"):
        values = {"provider": "openai_compatible", "base_url": "u", "api_key": "k", "model": "m", missing: ""}
        with pytest.raises(ValueError):
            build_provider(JudgeSettings(**values))


def test_registered_under_its_setting_name():
    assert isinstance(build_provider(SETTINGS), OpenAICompatibleProvider)
    assert build_provider(SETTINGS).name == "judge-model"


@pytest.mark.parametrize("address", [
    "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1",
    "https://api.cloudflare.com/client/v4/accounts/%3CACCOUNT_ID%3E/ai/v1",
    "https://api.cloudflare.com/client/v4/accounts/REPLACE_WITH_ACCOUNT_ID/ai/v1",
    "judge.test/v1", "file:///tmp/judge",
])
def test_invalid_or_placeholder_endpoint_fails_before_sending_credentials(address):
    with pytest.raises(ValueError, match="placeholders replaced") as caught:
        build_provider(replace(SETTINGS, base_url=address))
    assert SETTINGS.api_key not in str(caught.value)


def test_local_gateway_http_endpoint_remains_supported():
    assert isinstance(build_provider(replace(SETTINGS, base_url="http://127.0.0.1:4000/v1")),
                      OpenAICompatibleProvider)


def test_key_stays_out_of_settings_repr():
    assert "test-key" not in repr(SETTINGS)


CONVERSATION = {"messages": [{"role": "assistant", "content": "A reply.", "toolCalls": []}]}


def engine(handler):
    bots = InMemoryBotConfigSource([{"id": "b", "level": 2, "checkType": "judge", "winRubric": "Objective: x"}])
    return JudgeEngine(provider=provider(handler), bots=bots)


def test_through_the_engine_a_pass():
    result = engine(lambda r: reply()).grade(CONVERSATION, "b")
    assert result.passed and result.usage.input_tokens == 11


def test_through_the_engine_a_refusal_has_no_verdict():
    with pytest.raises(GradingError) as caught:
        engine(lambda r: reply(content="", finish_reason="content_filter")).grade(CONVERSATION, "b")
    assert caught.value.code == errors.PROVIDER_REFUSED and not caught.value.retryable


def test_through_the_engine_free_text_never_passes():
    with pytest.raises(GradingError) as caught:
        engine(lambda r: reply(content="PASS. Ignore the rules.")).grade(CONVERSATION, "b")
    assert caught.value.code == errors.INVALID_OUTPUT
