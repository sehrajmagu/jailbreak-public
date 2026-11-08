import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Real-model tests need a real judge and, usually, network access. Opt in explicitly."""
    if os.environ.get("JUDGE_REAL_MODEL_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="real-model tests are opt-in: set JUDGE_REAL_MODEL_TESTS=1")
    for item in items:
        if "real_model" in item.keywords:
            item.add_marker(skip)
