"""Tier 3: real-model evaluation harness. SKIPPED BY DEFAULT.

This is the only tier that can say anything about judging accuracy or injection
resistance against a real LLM. It needs three things the mock tests do not:

1. A real provider registered with ``register_provider`` (none ships yet) and the
   JUDGE_* environment variables set.
2. JUDGE_REAL_MODEL_TESTS=1.
3. A labelled corpus written and approved by the organisers, in the JSON format below.
   The synthetic fixtures cannot be reused: a real model has no reason to react to
   ``[[SYNTHETIC:...]]`` markers.

Corpus format (JUDGE_REAL_MODEL_CORPUS=path/to/corpus.json):

    [{"name": "...", "winRubric": "...", "level": 2,
      "conversation": {"messages": [...]}, "expectedPassed": true}, ...]
"""

import json
import os

import pytest

from engine.judge import build_default_engine
from engine.judge.bots import InMemoryBotConfigSource

pytestmark = pytest.mark.real_model


def _load_corpus():
    path = os.environ.get("JUDGE_REAL_MODEL_CORPUS", "")
    if not path or not os.path.exists(path):
        pytest.skip("set JUDGE_REAL_MODEL_CORPUS to an organiser-approved corpus file")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_real_model_agrees_with_the_labelled_corpus():
    corpus = _load_corpus()
    bots = InMemoryBotConfigSource(
        [
            {"id": "case-%d" % i, "level": case["level"], "checkType": "judge", "winRubric": case["winRubric"]}
            for i, case in enumerate(corpus)
        ]
    )
    engine = build_default_engine(bots)
    wrong = []
    for i, case in enumerate(corpus):
        result = engine.grade(case["conversation"], "case-%d" % i)
        if result.passed != case["expectedPassed"]:
            wrong.append(case["name"])
    assert not wrong, "real model disagreed with labels on: %s" % ", ".join(wrong)
