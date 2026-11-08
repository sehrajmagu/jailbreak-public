"""Fake tools, ported from scripts/playtest.py. Nothing runs: the bot gets the file's text."""

import json
import math
from typing import Any, Dict

UNKNOWN_TOOL_RESULT = '{"error":"unknown_tool"}'


def run_fake_tool(tool: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    for response in tool["fakeResponses"]:
        if all(arguments.get(key) == value for key, value in response["when"].items()):
            return response["result"]
    return tool["defaultResult"]


def input_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in tool["parameters"].items()},
        "required": list(tool["parameters"]),
    }


def clean_text(text: str) -> str:
    """Removes what Postgres rejects in text and JSONB: NUL, and unpaired surrogates."""
    return text.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8")


def clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if isinstance(value, dict):
        return {clean_text(key): clean_value(item) for key, item in value.items()}
    # 1e999 parses as infinity, which JSONB can't hold.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def parse_arguments(text: str) -> Dict[str, Any]:
    """Reads a model's tool arguments, which it controls and may have malformed.

    A team can coax a model into writing "\\u0000", which only becomes a NUL once
    parsed, so cleaning the raw text wouldn't be enough to save it.
    """
    try:
        arguments = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    return clean_value(arguments) if isinstance(arguments, dict) else {}
