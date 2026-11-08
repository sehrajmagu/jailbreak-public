"""The JudgeProvider interface.

A provider sends one prompt to a model and returns the raw text plus token
counts. It does NOT parse or validate the answer: the engine does that, so
validation is the same for every provider and a mock can return broken output.
"""

import math
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field


class ProviderRequest(BaseModel):
    system: str
    user: str
    max_output_tokens: int = Field(default=200, gt=0)
    temperature: float = 0.0
    request_id: str = ""


class ProviderResponse(BaseModel):
    text: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ProviderError(Exception):
    """Base class for provider failures. Messages must never contain prompt text."""


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    """Network error, 5xx, bad credentials: the provider could not answer."""


class ProviderRateLimited(ProviderError):
    def __init__(self, retry_after_seconds: Optional[int] = None) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


class ProviderRefused(ProviderError):
    """The provider's own safety filter declined to process the content."""


class ProviderBadResponse(ProviderError):
    """The provider replied, but not in a shape we can read."""


class JudgeProvider(ABC):
    #: Short name for private logs and for Part A's judge_runs.judge_model.
    name = "provider"
    #: Total context window of the model, in tokens (prompt + output).
    max_context_tokens = 8000

    @abstractmethod
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Send one request. Raise a ProviderError subclass on failure."""

    def estimate_tokens(self, text: str) -> int:
        """Conservative token estimate, used only to decide if a transcript fits.

        Two characters per token overestimates for English and is closer to
        reality for Devanagari, which tokenises poorly. Real providers may override.
        """
        return int(math.ceil(len(text) / 2.0))
