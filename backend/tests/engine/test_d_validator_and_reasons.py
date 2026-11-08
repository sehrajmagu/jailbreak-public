"""Stage D: output validation, evidence checks, reason mapping.

Unit tests (application logic only). They show our code rejects bad judge output, not
that any real model produces good output.
"""

import json

import pytest

from engine.judge.models import ReasonCode
from engine.judge.reasons import REASON_TEXT, reason_text
from engine.judge.validator import (
    MAX_OUTPUT_CHARS,
    InvalidModelOutput,
    check_evidence,
    parse_model_output,
    validate_model_output,
)

VALID = {1, 2, 3}


def out(verdict="PASS", code="OBJECTIVE_MET", turns=(2,), **extra):
    data = {"verdict": verdict, "code": code, "evidenceTurns": list(turns)}
    data.update(extra)
    return json.dumps(data)


def tag_of(text, valid=VALID):
    with pytest.raises(InvalidModelOutput) as info:
        validate_model_output(text, valid)
    return info.value.tag


# ---------- accepted ----------


def test_valid_pass_and_fail_are_accepted():
    assert validate_model_output(out(), VALID).verdict == "PASS"
    fail = validate_model_output(out("FAIL", "REFUSED", ()), VALID)
    assert fail.verdict == "FAIL" and fail.code is ReasonCode.REFUSED


def test_fail_may_cite_real_turns():
    assert validate_model_output(out("FAIL", "PARTIAL", (1, 2)), VALID).code is ReasonCode.PARTIAL


def test_single_markdown_fence_is_tolerated_but_content_stays_strict():
    assert validate_model_output("```json\n%s\n```" % out(), VALID).verdict == "PASS"
    assert tag_of("```json\n%s\n```" % out(reason="x")) == "schema"


# ---------- malformed shape ----------


def test_empty_and_whitespace_rejected():
    assert tag_of("") == "empty"
    assert tag_of("   \n ") == "empty"


@pytest.mark.parametrize("text", ["PASS", "not json at all", "{broken", "Sure! Here is the verdict: PASS"])
def test_non_json_rejected(text):
    assert tag_of(text) == "not_json"


def test_prose_around_json_rejected():
    assert tag_of("Here you go: " + out()) == "not_json"


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        '"PASS"',
        json.dumps({"code": "OBJECTIVE_MET", "evidenceTurns": [1]}),  # missing verdict
        json.dumps({"verdict": "PASS", "evidenceTurns": [1]}),  # missing code
        out("MAYBE"),  # unexpected verdict
        out("pass"),  # wrong case
        out("PASS", "SOMETHING_ELSE"),  # unknown code
        out(reason="The bot revealed the rubric"),  # no free-text field allowed
        out(verdict2="PASS"),  # extra field
        json.dumps({"verdict": "PASS", "code": "OBJECTIVE_MET", "evidenceTurns": "1"}),
        json.dumps({"verdict": "PASS", "code": "OBJECTIVE_MET", "evidenceTurns": [1.5]}),
    ],
)
def test_wrong_shape_rejected(text):
    assert tag_of(text) in {"schema", "not_json"}


def test_oversized_output_rejected():
    assert tag_of("x" * (MAX_OUTPUT_CHARS + 1)) == "too_long"


def test_model_may_not_emit_engine_only_code():
    assert tag_of(out("FAIL", "NO_BOT_REPLIES", ())) == "schema"


# ---------- evidence checks ----------


def test_pass_needs_objective_met_code():
    assert tag_of(out("PASS", "REFUSED", (1,))) == "evidence"


def test_pass_needs_at_least_one_cited_turn():
    assert tag_of(out("PASS", "OBJECTIVE_MET", ())) == "evidence"


def test_pass_citing_nonexistent_turn_rejected():
    assert tag_of(out("PASS", "OBJECTIVE_MET", (99,))) == "evidence"
    assert tag_of(out("PASS", "OBJECTIVE_MET", (0,))) == "evidence"
    assert tag_of(out("PASS", "OBJECTIVE_MET", (-1,))) == "evidence"


def test_one_bad_citation_among_good_ones_rejects_the_pass():
    assert tag_of(out("PASS", "OBJECTIVE_MET", (1, 99))) == "evidence"


def test_fail_cannot_claim_objective_met():
    assert tag_of(out("FAIL", "OBJECTIVE_MET", ())) == "evidence"


def test_fail_with_nonexistent_citation_rejected():
    assert tag_of(out("FAIL", "PARTIAL", (42,))) == "evidence"


def test_evidence_against_empty_valid_set_rejects_any_pass():
    assert tag_of(out(), valid=set()) == "evidence"


def test_check_evidence_is_independent_of_parsing():
    verdict = parse_model_output(out())
    check_evidence(verdict, {2})
    with pytest.raises(InvalidModelOutput):
        check_evidence(verdict, {1})


def test_error_message_never_contains_model_text():
    secret = "TOP-SECRET-RUBRIC-TEXT"
    with pytest.raises(InvalidModelOutput) as info:
        validate_model_output(out(reason=secret), VALID)
    assert secret not in str(info.value) and secret not in info.value.tag


# ---------- reason mapping ----------


def test_every_reason_code_has_a_fixed_sentence():
    assert set(REASON_TEXT) == set(ReasonCode)
    assert len(set(REASON_TEXT.values())) == len(REASON_TEXT)
    for code in ReasonCode:
        text = reason_text(code)
        assert text.endswith(".") and len(text) < 120


def test_reason_sentences_leak_nothing_and_have_no_placeholders():
    banned = ("rubric", "system prompt", "password", "defence", "defense", "judge", "{", "}")
    for text in REASON_TEXT.values():
        assert not any(word in text.lower() for word in banned)
