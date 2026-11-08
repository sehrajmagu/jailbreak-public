"""What the engine knows about a bot, loaded by ID from supabase/chat.sql."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.db import get_db

# The services that run models, as in scripts/bot_files.py.
HOSTS = {
    "openai": {"key": "OPENAI_API_KEY", "base_url": None},
    "anthropic": {"key": "ANTHROPIC_API_KEY", "base_url": None},
    "groq": {"key": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1"},
}

# A model that stops sending for this long counts as failed.
IDLE_TIMEOUT_SECONDS = 60


def available_hosts() -> List[str]:
    """The hosts with a key set. A bot on any other host can't take messages."""
    from app import gateway

    if gateway.enabled():
        return gateway.hosts()
    return [host for host, details in HOSTS.items() if os.environ.get(details["key"])]


def max_output_tokens() -> int:
    return int(os.environ.get("CHAT_MAX_OUTPUT_TOKENS", "2048"))


@dataclass(frozen=True)
class BotConfig:
    id: str
    level: int
    label: str
    model: str
    provider: str
    host: str
    api_model: str
    check_type: str
    context_limit_tokens: int
    # Secret, so these stay out of repr and any log line that prints a config.
    system_prompt: str = field(repr=False)
    tools: List[Dict[str, Any]] = field(repr=False)
    defences: Dict[str, Any] = field(repr=False)
    # What the output filter blocks, worked out by supabase/chat.sql. On an answer bot
    # with the filter on, it includes the password, though never labelled as such.
    output_filter_phrases: List[str] = field(default_factory=list, repr=False)
    # The reviewer model's model, provider, host and api_model, or None.
    reviewer: Optional[Dict[str, str]] = None


def load_config(bot_id: str) -> BotConfig:
    result = get_db().rpc("get_bot_config", {"p_bot_id": bot_id}).execute().data
    if result["status"] != "ok":
        raise LookupError("The bot wasn't found.")
    return BotConfig(
        id=result["id"],
        level=result["level"],
        label=result["label"],
        model=result["model"],
        provider=result["provider"],
        host=result["host"],
        api_model=result["api_model"],
        check_type=result["check_type"],
        context_limit_tokens=result["context_limit_tokens"],
        system_prompt=result["system_prompt"],
        tools=result["tools"],
        defences=result["defences"],
        output_filter_phrases=result.get("output_filter_phrases") or [],
        reviewer=result.get("reviewer"),
    )
