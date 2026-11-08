"""Tests that scripts/import_bots.py keeps settings organisers manage.

Needs no database or keys, because it uses a small in-memory stand-in:
    python tests/test_import_bots.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import import_bots  # noqa: E402


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.rows = None

    def select(self, _):
        return self

    def upsert(self, rows, on_conflict):
        self.rows = rows
        self.keys = on_conflict.split(",")
        return self

    def execute(self):
        if self.rows is None:
            return SimpleNamespace(data=self.db.tables[self.table])
        stored = self.db.tables[self.table]
        for row in self.rows:
            match = next((s for s in stored if all(s[k] == row[k] for k in self.keys)), None)
            if match:
                match.update(row)
            else:
                stored.append(dict(row))
        return SimpleNamespace(data=self.rows)


class FakeDb:
    def __init__(self):
        self.tables = {"models": [], "bots": []}

    def table(self, name):
        return Query(self, name)


def check(name, passed):
    print(f"{'ok  ' if passed else 'FAIL'} {name}")
    if not passed:
        sys.exit(1)


def model(limit=1000, name="Model A"):
    return {
        "id": "m", "provider": "open", "display_name": name, "api_model": "real-id", "host": "groq",
        "organiser_cost_per_1k_input": 0, "organiser_cost_per_1k_output": 0,
        "token_limit_per_team": limit, "enabled": True,
    }


def bot(enabled=True, prompt="Guard {{SECRET}}.", description="First"):
    return {
        "level": 1, "label": "Bot A", "model": "m", "system_prompt": prompt, "check_type": "answer",
        "secret": "PW-1", "description": description, "goal": "Goal", "enabled": enabled, "tools": [],
        "defences": {"inputFilter": False, "outputFilter": False, "reviewerModel": None},
    }


db = FakeDb()

import_bots.import_models(db, [model(limit=1000)])
import_bots.import_bots(db, [bot(enabled=False)], [model()])
check("a new model takes its limit from the file", db.tables["models"][0]["token_limit_per_team"] == 1000)
check("a new bot takes enabled from the file", db.tables["bots"][0]["enabled"] is False)

# Existing legacy quota data and an admin-enabled bot survive re-import.
db.tables["models"][0]["token_limit_per_team"] = 250
db.tables["bots"][0]["enabled"] = True
import_bots.import_models(db, [model(limit=9999, name="Model A2")])
import_bots.import_bots(db, [bot(enabled=False, prompt="New {{SECRET}}.", description="Second")], [model()])
check("re-importing keeps a model's token limit", db.tables["models"][0]["token_limit_per_team"] == 250)
check("re-importing still updates a model's other fields", db.tables["models"][0]["display_name"] == "Model A2")
check("re-importing keeps a bot's enabled setting", db.tables["bots"][0]["enabled"] is True)
check("re-importing still updates a bot's instructions", db.tables["bots"][0]["system_prompt"] == "New {{SECRET}}.")
check("re-importing still updates a bot's description", db.tables["bots"][0]["description"] == "Second")
check("no rows are duplicated", len(db.tables["models"]) == 1 and len(db.tables["bots"]) == 1)

db.tables["bots"][0]["enabled"] = False
import_bots.import_bots(db, [bot(enabled=True)], [model()])
check("a disabled bot stays disabled even if its file says enabled", db.tables["bots"][0]["enabled"] is False)
# New model configs do not need the retired quota field at all.
without_quota = model()
without_quota.pop("token_limit_per_team")
new_db = FakeDb()
import_bots.import_models(new_db, [without_quota])
check("models without legacy quotas import", new_db.tables["models"][0]["token_limit_per_team"] == 0)
print("All import tests passed.")
