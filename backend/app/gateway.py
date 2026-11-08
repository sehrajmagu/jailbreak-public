"""Default LiteLLM boundary. Credentials never come from a browser or a bot file."""

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Optional, Tuple

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Written next to the team keys by `setup.py budget-reader`.
BUDGET_READER_FILE = "budget-reader-key"

# ContextVar isolates simultaneous teams and is inherited by the detached turn task.
# A client lives for one whole turn (including tool rounds and reviewer calls).
_client: ContextVar[Optional[AsyncOpenAI]] = ContextVar("gateway_client", default=None)


def enabled() -> bool:
    # Only an explicit opt-out can bypass team-budget enforcement.
    return os.environ.get("LITELLM_ENABLED", "true").strip().lower() != "false"


def budget_exceeded(error: Exception) -> bool:
    """Recognize only structured gateway budget errors, never message text or HTTP 429 alone.

    The SDK exposes the JSON error body for both rejected requests and stream errors.
    Requiring an active gateway client keeps direct-provider errors out of this path.
    """
    if _client.get() is None:
        return False
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return False
    details = body.get("error", body)
    return isinstance(details, dict) and details.get("type") == "budget_exceeded"


def routes() -> dict:
    # Read each time so adding routes/teams doesn't require restarting the app.
    return json.loads(Path(os.environ["LITELLM_ROUTES_FILE"]).read_text())


def hosts() -> list[str]:
    return sorted({route.split(":", 1)[0] for route in routes()})


@asynccontextmanager
async def team_scope(team_id: str):
    """Bind the *authenticated* team, never a team ID supplied in request content."""
    if not enabled():
        yield
        return
    keys = json.loads(Path(os.environ["LITELLM_TEAM_KEYS_FILE"]).read_text())
    key = keys.get(team_id)
    if not isinstance(key, str) or not key.startswith("sk-"):
        raise LookupError("This team has no gateway key.")
    # No SDK retry: retrying a partially billed call can spend money twice.
    async with AsyncOpenAI(
        api_key=key,
        base_url=os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1"),
        max_retries=0,
        timeout=httpx.Timeout(60, connect=10),
    ) as client:
        token = _client.set(client)
        try:
            yield
        finally:
            _client.reset(token)


def team_budget(team_id: str) -> Optional[Tuple[float, float]]:
    """(cap, spent) in dollars from LiteLLM, or None if it can't say right now.

    Team keys are refused on /key/info, so this uses a read-only key, never the master key.
    """
    if not enabled():
        return None
    try:
        keys_file = Path(os.environ["LITELLM_TEAM_KEYS_FILE"])
        team_key = json.loads(keys_file.read_text())[team_id]
        reader = keys_file.with_name(BUDGET_READER_FILE).read_text().strip()
        base = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1").rstrip("/").removesuffix("/v1")
        # The hash keeps the team's key out of URLs and access logs.
        response = httpx.get(
            f"{base}/key/info",
            params={"key": hashlib.sha256(team_key.encode()).hexdigest()},
            headers={"Authorization": f"Bearer {reader}"},
            timeout=5,
        )
        response.raise_for_status()
        info = response.json()["info"]
        return float(info["max_budget"]), float(info.get("spend") or 0)
    except (KeyError, OSError, TypeError, ValueError, httpx.HTTPError) as error:
        # Only the type: an error's text could echo a key.
        logger.warning("Couldn't read a team's budget from LiteLLM: %s", type(error).__name__)
        return None


def adapter(config):
    """Translate all providers through LiteLLM's OpenAI-compatible streaming API."""
    from app.engine.openai_compatible import OpenAICompatibleAdapter

    client = _client.get()
    if client is None:
        # Never fall back to an organiser key if context/configuration is missing.
        raise RuntimeError("A gateway team scope is required.")
    alias = routes().get(f"{config.host}:{config.api_model}")
    if not alias:
        raise LookupError("This model has no gateway route.")
    # Keep the original host for provider-specific options such as hiding reasoning.
    return OpenAICompatibleAdapter(replace(config, api_model=alias), client=client)
