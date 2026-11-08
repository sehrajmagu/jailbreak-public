"""Tests that scripts/dev_seed.py fails loudly when rows don't get saved.

Needs no database or keys, because it uses a small in-memory stand-in:
    python tests/test_dev_seed.py
"""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from postgrest.exceptions import APIError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dev_seed  # noqa: E402


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.ids, self.rows = db, table, None, None

    def upsert(self, rows, on_conflict):
        self.rows = rows
        return self

    def select(self, _):
        return self

    def in_(self, _, ids):
        self.ids = ids
        return self

    def execute(self):
        if self.rows is None:
            rows = [row for row in self.db.tables[self.table] if row["id"] in self.ids]
            return SimpleNamespace(data=rows)
        if self.table in self.db.refuse:
            raise APIError({"message": "column is missing", "code": "42703"})
        if self.table not in self.db.drop:
            kept = [row for row in self.db.tables[self.table] if row["id"] not in [r["id"] for r in self.rows]]
            self.db.tables[self.table] = kept + self.rows
        returned = [] if self.table in self.db.return_nothing else self.rows
        return SimpleNamespace(data=returned)


class Admin:
    def __init__(self):
        self.users = []

    def list_users(self, page, per_page):
        return self.users if page == 1 else []

    def create_user(self, attributes):
        user = SimpleNamespace(id=uuid.uuid4(), email=attributes["email"])
        self.users.append(user)
        return SimpleNamespace(user=user)


class FakeDb:
    def __init__(self, drop=(), refuse=(), return_nothing=()):
        self.tables = {"teams": [], "admin_users": []}
        self.drop, self.refuse, self.return_nothing = drop, refuse, return_nothing
        self.auth = SimpleNamespace(admin=Admin())

    def table(self, name):
        return Query(self, name)


def check(name, passed):
    print(f"{'ok  ' if passed else 'FAIL'} {name}")
    if not passed:
        sys.exit(1)


def fails(db, kind, words):
    try:
        dev_seed.seed(db)
    except kind as e:
        return words in str(e) or words in getattr(e, "message", "")
    return False


db = FakeDb()
dev_seed.seed(db)
check("all 3 teams are saved", len(db.tables["teams"]) == 3)
check("the admin is saved", [row["display_name"] for row in db.tables["admin_users"]] == ["Dev Admin"])
check("4 users are created", len(db.auth.admin.users) == 4)
ids = sorted(row["id"] for row in db.tables["teams"])
dev_seed.seed(db)
check("re-running creates no new users", len(db.auth.admin.users) == 4)
check("re-running keeps the same teams", sorted(row["id"] for row in db.tables["teams"]) == ids)

check("rows that vanish after saving fail", fails(FakeDb(drop=("teams",)), dev_seed.SeedFailed, "isn't there after saving"))
check("an empty save result fails", fails(FakeDb(return_nothing=("teams",)), dev_seed.SeedFailed, "returned 0"))
check("a missing admin row fails", fails(FakeDb(drop=("admin_users",)), dev_seed.SeedFailed, "Dev Admin isn't there"))
check("a database error fails", fails(FakeDb(refuse=("teams",)), APIError, "column is missing"))
stopped = FakeDb(refuse=("teams",))
fails(stopped, APIError, "")
check("the admin isn't saved after the teams fail", stopped.tables["admin_users"] == [])
print("All dev seed tests passed.")
