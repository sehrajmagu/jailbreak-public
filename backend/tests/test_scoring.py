"""Tests the answer function and scoring endpoints against a real Postgres.

Set TEST_DATABASE_URL to a local Postgres where you may create databases, then:
    pip install -r requirements-dev.txt
    python tests/test_scoring.py

It makes a throwaway database, adds stand-ins for what Supabase provides, loads
the SQL files, and drops it afterwards. Don't point it at Supabase.
"""

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.auth import get_session  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import AdminSession, TeamSession  # noqa: E402

SUPABASE_STAND_INS = """
create schema auth;
create table auth.users (id uuid primary key default gen_random_uuid());
do $$ begin
  create role anon nologin;
exception when duplicate_object then null; end $$;
do $$ begin
  create role authenticated nologin;
exception when duplicate_object then null; end $$;
do $$ begin
  create role service_role nologin bypassrls;
exception when duplicate_object then null; end $$;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
"""


class Query:
    """Runs the few Supabase client calls the router makes, using plain SQL."""

    def __init__(self, url, name):
        self.url, self.name = url, name
        self.params = None

    def select(self, _columns):
        return self

    def order(self, _column):
        return self

    def execute(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            if self.params is not None:
                p = self.params
                row = conn.execute(
                    "select submit_answer(%s::uuid, %s::uuid, %s, %s)",
                    (p["p_team_id"], p["p_bot_id"], p["p_answer"], p["p_max_wrong_answers"]),
                ).fetchone()
                return type("Response", (), {"data": row[0]})
            sql = {
                "leaderboard": "select id::text, name, total_points, rank from leaderboard order by rank, name",
                "scores": "select team_id::text from scores",
            }[self.name]
            cursor = conn.execute(sql)
            columns = [c.name for c in cursor.description]
            return type("Response", (), {"data": [dict(zip(columns, r)) for r in cursor.fetchall()]})


class FakeDb:
    def __init__(self, url):
        self.url = url

    def rpc(self, name, params):
        if name == "touch_team":
            # Recording team activity, which test_admin.py tests.
            return type("Touch", (), {"execute": lambda self: None})()
        query = Query(self.url, "rpc")
        query.params = params
        return query

    def table(self, name):
        return Query(self.url, name)


def run_tests(url):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SUPABASE_STAND_INS)
        conn.execute((BACKEND.parent / "supabase/schema.sql").read_text())
        conn.execute((BACKEND.parent / "supabase/scoring.sql").read_text())

        def one(sql, *args):
            return conn.execute(sql, args).fetchone()[0]

        def new_team(name, disabled=False):
            team = one("insert into auth.users default values returning id")
            conn.execute("insert into teams (id, name, disabled) values (%s, %s, %s)", (team, name, disabled))
            return str(team)

        conn.execute(
            "insert into levels (level_number, name, description, points_per_bot, required_bots)"
            " values (1, 'L1', 'd', 100, 3), (2, 'L2', 'd', 200, 3)"
        )
        conn.execute(
            "insert into models (id, provider, display_name, organiser_cost_per_1k_input,"
            " organiser_cost_per_1k_output, token_limit_per_team, enabled)"
            " values ('m', 'open', 'M', 0, 0, 1000, true)"
        )
        bots = [
            str(one(
                "insert into bots (level, label, display_order, model_id, system_prompt, check_type, secret)"
                " values (1, %s, %s, 'm', 'guard {{SECRET}}', 'answer', %s) returning id",
                f"Bot {i}", i, f"Secret-{i}",
            ))
            for i in range(6)
        ]
        judge_bot = str(one(
            "insert into bots (level, label, display_order, model_id, system_prompt, check_type, rubric)"
            " values (2, 'Bot J', 0, 'm', 'x', 'judge', 'r') returning id"
        ))
        locked_bot = str(one(
            "insert into bots (level, label, display_order, model_id, system_prompt, check_type, secret)"
            " values (2, 'Bot K', 1, 'm', 'guard {{SECRET}}', 'answer', 'Secret-K') returning id"
        ))
        admin = one("insert into auth.users default values returning id")
        conn.execute("insert into admin_users (id, display_name) values (%s, 'Org')", (admin,))

        app.dependency_overrides[get_db] = lambda: FakeDb(url)
        client = TestClient(app, raise_server_exceptions=False)

        def post(team, bot, answer):
            app.dependency_overrides[get_session] = lambda: TeamSession(team_id=team, team_name="T")
            return client.post(f"/api/bots/{bot}/answer", json={"answer": answer})

        def check(label, condition):
            print(("ok   " if condition else "FAIL ") + label)
            assert condition, label

        def wrong_count(team, bot):
            return one("select count(*) from answer_attempts where team_id=%s and bot_id=%s and not correct", team, bot)

        def completed(team, level=1):
            return one("select count(*) from level_completions where team_id=%s and level=%s and not revoked", team, level)

        # Wrong answers
        a = new_team("Wrong")
        r = post(a, bots[0], "nope")
        check("wrong answer reports the count and limit", r.status_code == 200 and r.json() == {
            "correct": False, "pointsAwarded": 0, "wrongAnswersUsed": 1, "maxWrongAnswers": 10,
        })
        check("comparison is case-sensitive", post(a, bots[0], "secret-0").json()["correct"] is False)
        check("no secret in the response", "Secret-0" not in post(a, bots[0], "x").text)

        # The cap
        for _ in range(7):
            post(a, bots[0], "nope")
        check("10 wrong answers recorded", wrong_count(a, bots[0]) == 10)
        r = post(a, bots[0], "Secret-0")
        check("correct answer refused at the cap", r.status_code == 409 and r.json()["error"]["code"] == "conflict")
        check("refused attempt isn't recorded", one("select count(*) from answer_attempts where team_id=%s", a) == 10)
        check("no points after the cap", one("select count(*) from scores where team_id=%s", a) == 0)
        check("cap is per bot", post(a, bots[1], "Secret-1").json()["correct"] is True)

        # Configurable cap
        os.environ["MAX_WRONG_ANSWERS"] = "1"
        c = new_team("Config")
        post(c, bots[0], "nope")
        check("cap comes from config", post(c, bots[0], "nope").status_code == 409)
        del os.environ["MAX_WRONG_ANSWERS"]

        # First pass only, and trimming
        b = new_team("First")
        r = post(b, bots[0], "  Secret-0\n")
        check("answer is trimmed and first pass scores", r.json()["correct"] is True and r.json()["pointsAwarded"] == 100)
        r = post(b, bots[0], "Secret-0")
        check("second pass scores nothing", r.json()["correct"] is True and r.json()["pointsAwarded"] == 0)
        check("one score row", one("select sum(points) from scores where team_id=%s", b) == 100)

        # Level completion
        check("not complete at 1 of 3", completed(b) == 0)
        post(b, bots[1], "Secret-1")
        check("not complete at 2 of 3", completed(b) == 0)
        post(b, bots[2], "Secret-2")
        check("complete at 3 of 3", completed(b) == 1)
        post(b, bots[3], "Secret-3")
        check("completed once only", one("select count(*) from level_completions where team_id=%s", b) == 1)

        # Revoked completion
        conn.execute(
            "update level_completions set revoked=true, revoked_by=%s, revoked_at=now() where team_id=%s",
            (admin, b),
        )
        check("revoked completion doesn't count", completed(b) == 0)
        post(b, bots[4], "Secret-4")
        check("a later solve doesn't restore it", completed(b) == 0 and one(
            "select revoked from level_completions where team_id=%s", b) is True)

        # Refusals
        check("judge bot refused", post(b, judge_bot, "x").status_code == 409)
        check("unknown bot refused", post(b, str(uuid.uuid4()), "x").status_code == 404)
        check("empty answer refused", post(b, bots[5], "   ").status_code == 422)
        check("too long answer refused", post(b, bots[5], "x" * 501).status_code == 422)
        check("disabled team refused", post(new_team("Off", disabled=True), bots[0], "x").status_code == 401)
        conn.execute("update levels set unlock_in_order=true where level_number=2")
        check("locked level refused", post(new_team("Locked"), locked_bot, "x").status_code == 403)
        conn.execute("update levels set unlock_in_order=false where level_number=2")
        app.dependency_overrides[get_session] = lambda: AdminSession(username="a")
        check("admin refused", client.post(f"/api/bots/{bots[0]}/answer", json={"answer": "x"}).status_code == 403)
        check("admin refused on leaderboard", client.get("/api/leaderboard").status_code == 403)

        # Simultaneous requests from one team
        def attempt(args):
            team, bot, answer = args
            with psycopg.connect(url, autocommit=True) as c2:
                return c2.execute("select submit_answer(%s, %s, %s)", (team, bot, answer)).fetchone()[0]

        with ThreadPoolExecutor(max_workers=16) as pool:
            t = new_team("Race wrong")
            list(pool.map(attempt, [(t, bots[0], "nope")] * 30))
            check("simultaneous wrong answers stop at 10", wrong_count(t, bots[0]) == 10)

            t = new_team("Race same")
            results = list(pool.map(attempt, [(t, bots[0], "Secret-0")] * 16))
            check("simultaneous correct answers score once", sum(r["points_awarded"] for r in results) == 100)

            for i in range(20):
                t = new_team(f"Race level {i}")
                list(pool.map(attempt, [(t, bots[n], f"Secret-{n}") for n in range(3)]))
                assert completed(t) == 1, "level completion missed"
            print("ok   simultaneous solves on different bots still complete the level")

        # Function access
        for role in ("anon", "authenticated"):
            conn.execute(f"set role {role}")
            try:
                conn.execute("select submit_answer(%s, %s, 'x')", (t, bots[0]))
                check(f"{role} can't call the function", False)
            except psycopg.errors.InsufficientPrivilege:
                print(f"ok   {role} can't call the function")
            finally:
                conn.execute("reset role")

        # Leaderboard
        conn.execute("update teams set disabled = true where name like 'Race%'")
        off = new_team("Hidden", disabled=True)
        conn.execute("insert into scores (team_id, bot_id, points, source) values (%s, %s, 9999, 'admin')", (off, bots[0]))
        zero = new_team("Zero")
        app.dependency_overrides[get_session] = lambda: TeamSession(team_id=b, team_name="First")
        r = client.get("/api/leaderboard")
        rows = r.json()
        check("leaderboard responds", r.status_code == 200)
        check("disabled teams excluded", not any(row["teamName"] in ("Hidden", "Off") for row in rows))
        check("highest score ranks first", rows[0]["teamName"] == "First" and rows[0]["rank"] == 1 and rows[0]["score"] == 500)
        check("solved count is right", rows[0]["solved"] == 5)
        check("own team marked once", [row["teamName"] for row in rows if row["isOwnTeam"]] == ["First"])
        check("zero score ranks last", rows[-1]["score"] == 0 and any(row["teamId"] == zero for row in rows))
        check("only allowed fields", set(rows[0]) == {"rank", "teamId", "teamName", "score", "solved", "isOwnTeam"})


def main():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        sys.exit("Set TEST_DATABASE_URL to a local Postgres. See the top of this file.")
    name = f"scoring_test_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(base)
    url = urlunsplit(parts._replace(path=f"/{name}"))
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"create database {name}")
    try:
        run_tests(url)
        print("All scoring tests passed.")
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"drop database {name} with (force)")


if __name__ == "__main__":
    main()
