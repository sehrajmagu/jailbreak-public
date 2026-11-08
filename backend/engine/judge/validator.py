"""Validation of the judge model's answer, done in our code and not trusted to the prompt.

Two layers:
1. Shape: strict JSON, exact schema, no extra fields, no free text.
2. Evidence: a PASS must be backed by real assistant turns that we actually sent.

Anything that fails is "malformed". The engine never turns a malformed answer into a
PASS or a FAIL: it re-asks once, then raises a retryable GradingError.
"""

import re
from typing import AbstractSet

from pydantic import ValidationError

from engine.judge.models import ModelVerdict, ReasonCode

MAX_OUTPUT_CHARS = 2000

# Some models wrap JSON in a single markdown fence. That one wrapper is accepted; the
# content inside must still pass the strict schema.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.DOTALL | re.IGNORECASE)


class InvalidModelOutput(Exception):
    """The model's answer was unusable. ``tag`` is a short fixed label for private logs.

    The message never contains the model's raw text, which may echo prompts.
    """

    def __init__(self, tag: str) -> None:
        super().__init__(tag)
        self.tag = tag


def parse_model_output(text: str) -> ModelVerdict:
    """Parse and shape-check the model's raw text."""
    stripped = (text or "").strip()
    if not stripped:
        raise InvalidModelOutput("empty")
    if len(stripped) > MAX_OUTPUT_CHARS:
        raise InvalidModelOutput("too_long")
    fenced = _FENCE_RE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        verdict = ModelVerdict.model_validate_json(stripped)
    except ValidationError as exc:
        # Distinguish "not JSON at all" from "JSON with the wrong shape" for private logs.
        tag = "not_json" if any(e["type"] == "json_invalid" for e in exc.errors()) else "schema"
        raise InvalidModelOutput(tag)
    if verdict.code is ReasonCode.NO_BOT_REPLIES:
        raise InvalidModelOutput("schema")  # engine-only code, the model may not emit it
    return verdict


def check_evidence(verdict: ModelVerdict, valid_turns: AbstractSet[int]) -> None:
    """Application-side checks that do not depend on the model behaving.

    ``valid_turns`` is the set of turn numbers we actually sent to the model.
    """
    cited = set(verdict.evidence_turns)
    if not cited.issubset(valid_turns):
        raise InvalidModelOutput("evidence")  # cited a turn that does not exist
    if verdict.verdict == "PASS":
        if verdict.code is not ReasonCode.OBJECTIVE_MET or not cited:
            raise InvalidModelOutput("evidence")
    else:
        if verdict.code is ReasonCode.OBJECTIVE_MET:
            raise InvalidModelOutput("evidence")  # a FAIL cannot claim the objective was met


def validate_model_output(text: str, valid_turns: AbstractSet[int]) -> ModelVerdict:
    verdict = parse_model_output(text)
    check_evidence(verdict, valid_turns)
    return verdict
