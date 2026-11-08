"""A real judge model, on any service with an OpenAI-style chat completions API.

Exception messages stay fixed: a raw provider error can repeat the prompt it was sent,
which holds the rubric and the transcript.
"""

from typing import Any, Optional
from urllib.parse import unquote, urlsplit

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

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


def retry_after(error: RateLimitError) -> Optional[int]:
    try:
        return max(1, int(float(error.response.headers.get("retry-after", ""))))
    except (TypeError, ValueError):
        return None


class OpenAICompatibleProvider(JudgeProvider):
    def __init__(self, settings: Any, http_client: Optional[httpx.Client] = None) -> None:
        if not settings.base_url or not settings.api_key or not settings.model:
            raise ValueError("JUDGE_BASE_URL, JUDGE_API_KEY and JUDGE_MODEL must all be set")
        # Fail at setup rather than queueing endless retries against an example URL.
        endpoint = urlsplit(settings.base_url)
        address = unquote(settings.base_url)
        if (endpoint.scheme not in ("http", "https") or not endpoint.hostname
                or any(char in address for char in "<>") or "REPLACE_WITH_" in address.upper()):
            raise ValueError("JUDGE_BASE_URL must be an HTTP(S) URL with all placeholders replaced")
        self.name = settings.model
        self.max_context_tokens = settings.max_context_tokens
        # The worker owns retries, so a retry can't spend twice behind its back.
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            response = self.client.chat.completions.create(
                model=self.name,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_completion_tokens=request.max_output_tokens,
                temperature=request.temperature,
            )
        except APITimeoutError:
            raise ProviderTimeout("timed out") from None
        except RateLimitError as error:
            raise ProviderRateLimited(retry_after(error)) from None
        except (APIConnectionError, APIStatusError):
            raise ProviderUnavailable("unavailable") from None

        if not response.choices:
            raise ProviderBadResponse("no choices")
        choice = response.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise ProviderRefused("refused")
        usage = response.usage
        return ProviderResponse(
            text=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
