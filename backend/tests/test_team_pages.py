"""Tests the team page endpoints against a real Postgres.

Set TEST_DATABASE_URL to a local Postgres where you may create databases, then:
    pip install -r requirements-dev.txt
    python tests/test_team_pages.py

It makes a throwaway database, adds stand-ins for what Supabase provides, loads
the SQL files, and drops it afterwards. Don't point it at Supabase.
"""

import json
import os
import sys
import uuid
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

FUNCTIONS = [
    "team_bot_cards(gen_random_uuid())",
    "team_level_results(gen_random_uuid())",
    "get_team_me(gen_random_uuid())",
    "get_team_dashboard(gen_random_uuid())",
    "get_team_levels(gen_random_uuid())",
    "get_team_bot(gen_random_uuid(), gen_random_uuid(), 10)",
    "get_team_submissions(gen_random_uuid())",
]

# Everything in the hidden columns contains this, so one search finds any leak.
HIDDEN = "HIDDENVALUE"

# The fields in lib/types.ts. tokensUsed on the dashboard and wrongAnswersUsed on a
# bot card were asked for on top of those types.
PROFILE = {"id", "name", "tokensUsed"}
DASHBOARD = {
    "points", "rank", "totalTeams", "attempted", "solved", "remaining", "levelsCompleted",
    "totalLevels", "prizeEarned", "usageByModel", "tokensUsed",
}
USAGE = {"model", "tokensUsed"}
LEVEL = {
    "level", "name", "locked", "solved", "required", "total", "complete", "revoked",
    "prizeAmount", "prizeAwarded", "bots",
}
CARD = {"id", "level", "label", "model", "checkType", "points", "status", "wrongAnswersUsed"}
DETAIL = CARD | {"levelName", "description", "goal", "maxWrongAnswers"}
SUBMISSION = {
    "id", "conversationId", "botId", "level", "botLabel", "checkType", "status", "reason",
    "pointsAwarded", "createdAt", "gradedAt",
}


class Rpc:
    """Runs a Supabase rpc call as plain SQL, passing arguments by name as PostgREST does."""

    def __init__(self, url, name, params):
        self.url, self.name, self.params = url, name, params

    def execute(self):
        args = ", ".join(f"{key} => %({key})s" for key in self.params)
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(f"select {self.name}({args})", self.params).fetchone()
        return type("Response", (), {"data": row[0]})


class Table:
    """The few table calls the leaderboard endpoint makes."""

    def __init__(self, url, name):
        self.url, self.name, self.columns, self.orders = url, name, "*", []

    def select(self, columns):
        self.columns = columns
        return self

    def order(self, column):
        self.orders.append(column)
        return self

    def execute(self):
        sql = f"select {self.columns} from {self.name}"
        if self.orders:
            sql += " order by " + ", ".join(self.orders)
        with psycopg.connect(self.url, autocommit=True) as conn:
            cursor = conn.execute(sql)
            names = [column.name for column in cursor.description]
            rows = [
                {name: str(value) if isinstance(value, uuid.UUID) else value for name, value in zip(names, row)}
                for row in cursor.fetchall()
            ]
        return type("Response", (), {"data": rows})


class FakeDb:
    def __init__(self, url):
        self.url = url

    def rpc(self, name, params):
        return Rpc(self.url, name, params)

    def table(self, name):
        return Table(self.url, name)


def check(name, passed, detail=""):
    print(f"{'ok  ' if passed else 'FAIL'} {name}{f': {detail}' if not passed else ''}")
    if not passed:
        sys.exit(1)


def run_tests(url):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SUPABASE_STAND_INS)
        conn.execute((BACKEND.parent / "supabase/schema.sql").read_text())
        conn.execute((BACKEND.parent / "supabase/scoring.sql").read_text())
        conn.execute((BACKEND.parent / "supabase/conversations.sql").read_text())
        conn.execute((BACKEND.parent / "supabase/team_pages.sql").read_text())

        def one(sql, *args):
            return conn.execute(sql, args).fetchone()[0]

        def new_team(name, disabled=False):
            team = one("insert into auth.users default values returning id")
            conn.execute("insert into teams (id, name, disabled) values (%s, %s, %s)", (team, name, disabled))
            return str(team)

        def new_bot(level, label, model, order, check_type="judge", enabled=True):
            return str(one(
                "insert into bots (level, label, display_order, model_id, description, goal, system_prompt,"
                " defences, tools, check_type, secret, rubric, enabled)"
                " values (%s, %s, %s, %s, 'About it', 'Beat it', %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)"
                " returning id",
                level, label, order, model, f"prompt {HIDDEN}", json.dumps({"note": HIDDEN}),
                json.dumps([{"name": HIDDEN}]), check_type, f"{HIDDEN}-{level}-{label}",
                f"rubric {HIDDEN}", enabled,
            ))

        session = {}
        app.dependency_overrides[get_session] = lambda: session["value"]
        app.dependency_overrides[get_db] = lambda: FakeDb(url)
        client = TestClient(app)

        def as_team(team_id):
            session["value"] = TeamSession(team_id=team_id, team_name="ignored")

        def get(path):
            response = client.get("/api" + path)
            if response.status_code != 200:
                raise AssertionError(f"{path} gave {response.status_code}: {response.text}")
            return response.json()

        # ---------- Access ----------

        for role in ("anon", "authenticated"):
            for function in FUNCTIONS:
                call = f"select * from {function}"
                conn.execute(f"set role {role}")
                try:
                    conn.execute(call)
                    allowed = True
                except psycopg.errors.InsufficientPrivilege:
                    allowed = False
                conn.execute("reset role")
                check(f"{role} can't call {function.split('(')[0]}", not allowed)

        session["value"] = AdminSession(username="a")
        for path in ("/me", "/me/dashboard", "/levels", "/submissions", f"/bots/{uuid.uuid4()}"):
            check(f"an admin gets 403 on {path.split('/')[1]}", client.get("/api" + path).status_code == 403)

        # ---------- An empty database ----------

        empty = new_team("Empty")
        as_team(empty)
        check("getMe works with nothing else in the database", get("/me") == {"id": empty, "name": "Empty", "tokensUsed": 0})
        dashboard = get("/me/dashboard")
        check("getDashboard is all zeros and empty lists", dashboard == {
            "points": 0, "rank": 1, "totalTeams": 1, "attempted": 0, "solved": 0, "remaining": 0,
            "levelsCompleted": 0, "totalLevels": 0, "prizeEarned": 0, "tokensUsed": 0, "usageByModel": [],
        }, dashboard)
        check("getLevels is empty", get("/levels") == [])
        check("getSubmissions is empty", get("/submissions") == [])
        check("getLeaderboard has the one team", [row["teamName"] for row in get("/leaderboard")] == ["Empty"])
        check("getBot is 404 with no bots", client.get(f"/api/bots/{uuid.uuid4()}").status_code == 404)
        conn.execute("delete from teams where id = %s", (empty,))

        # ---------- A real event ----------

        for level, name in enumerate(("Password", "Forbidden topic", "Hindi", "Malware", "Explicit"), 1):
            conn.execute(
                "insert into levels (level_number, name, description, points_per_bot, required_bots)"
                " values (%s, %s, 'd', %s, 3)",
                (level, name, level * 100),
            )
        conn.execute(
            "insert into models (id, provider, display_name, api_model, host, organiser_cost_per_1k_input,"
            " organiser_cost_per_1k_output, token_limit_per_team, enabled) values"
            " ('m-a', 'open', 'Model A', %s, %s, 0, 0, 1000, true),"
            " ('m-b', 'openai', 'Model B', %s, %s, 0, 0, 500, true),"
            " ('m-off', 'openai', 'Model Off', null, null, 0, 0, 500, false)",
            (f"api-{HIDDEN}", f"host-{HIDDEN}", f"api-{HIDDEN}", f"host-{HIDDEN}"),
        )
        answer_bots = [
            new_bot(1, f"Bot {label}", "m-b" if i else "m-a", i, "answer") for i, label in enumerate("ABCD")
        ]
        judge_a = new_bot(2, "Bot A", "m-a", 0)
        judge_b = new_bot(2, "Bot B", "m-b", 1)
        hidden_bot = new_bot(2, "Bot H", "m-a", 2, enabled=False)

        alpha = new_team("Alpha")
        beta = new_team("Beta")
        new_team("Gone", disabled=True)
        as_team(alpha)

        def answer(team, bot, guess):
            conn.execute(
                "select submit_answer(%s, %s, %s)", (team, bot, guess)
            )

        # A wrong guess, then a right one, then the right one again for no more points.
        answer(alpha, answer_bots[0], f"guess-{HIDDEN}")
        answer(alpha, answer_bots[0], f"{HIDDEN}-1-Bot A")
        answer(alpha, answer_bots[0], f"{HIDDEN}-1-Bot A")
        answer(alpha, answer_bots[1], "wrong")
        answer(beta, answer_bots[3], "beta-wrong")

        turn = one("select begin_turn(%s, %s, null, 'hello')", alpha, judge_a)
        one(
            "select finish_turn(%s, %s, %s, %s, 'hi', 'complete', 300, 100)",
            alpha, turn["conversation_id"], turn["user_message"]["id"], turn["assistant_message_id"],
        )
        failed = one("select begin_turn(%s, %s, null, 'hello')", alpha, judge_b)
        one(
            "select finish_turn(%s, %s, %s, %s, 'broke', 'failed', 900, 900)",
            alpha, failed["conversation_id"], failed["user_message"]["id"], failed["assistant_message_id"],
        )
        beta_turn = one("select begin_turn(%s, %s, null, 'hello')", beta, judge_b)
        one(
            "select finish_turn(%s, %s, %s, %s, 'hi', 'complete', 400, 100)",
            beta, beta_turn["conversation_id"], beta_turn["user_message"]["id"], beta_turn["assistant_message_id"],
        )
        conn.execute(
            "insert into submissions (team_id, bot_id, conversation_id, status, reason)"
            " values (%s, %s, %s, 'error', %s)",
            (alpha, judge_a, turn["conversation_id"], f"judge fault {HIDDEN}"),
        )

        me = get("/me")
        check("getMe has only the fields in lib/types.ts", set(me) == PROFILE)
        check("getMe counts charged tokens only", me == {"id": alpha, "name": "Alpha", "tokensUsed": 400}, me)

        dashboard = get("/me/dashboard")
        check("getDashboard has the expected fields", set(dashboard) == DASHBOARD)
        check("points come from this team's scores", dashboard["points"] == 100)
        check("rank is first", dashboard["rank"] == 1)
        check("disabled teams aren't counted", dashboard["totalTeams"] == 2)
        check("solved bots count", dashboard["solved"] == 1)
        check("attempted counts guesses, chats and solved bots, not failed turns", dashboard["attempted"] == 3, dashboard)
        check("remaining counts visible bots not solved", dashboard["remaining"] == 5)
        check("all 5 levels are counted", dashboard["totalLevels"] == 5)
        check("total tokens are on the dashboard", dashboard["tokensUsed"] == 400)
        usage = dashboard["usageByModel"]
        check("each usage row has the expected fields", all(set(row) == USAGE for row in usage))
        check("usage is in model order, without unused turned-off models", usage == [
            {"model": "Model A", "tokensUsed": 400},
            {"model": "Model B", "tokensUsed": 0},
        ], usage)

        levels = get("/levels")
        check("all 5 levels are returned in order", [row["level"] for row in levels] == [1, 2, 3, 4, 5])
        check("each level has the expected fields", all(set(row) == LEVEL for row in levels))
        check("no level is ever locked", not any(row["locked"] for row in levels))
        check("an empty level has no bots", levels[2]["bots"] == [] and levels[2]["total"] == 0)
        check("cards have the expected fields", all(set(c) == CARD for row in levels for c in row["bots"]))
        first = levels[0]["bots"]
        check("bots are in order", [c["label"] for c in first] == ["Bot A", "Bot B", "Bot C", "Bot D"])
        check("statuses are per team", [c["status"] for c in first] == ["solved", "attempted", "unattempted", "unattempted"])
        check("wrong answers are per team", [c["wrongAnswersUsed"] for c in first] == [1, 1, 0, 0])
        check("points come from the level", first[0]["points"] == 100 and levels[1]["bots"][0]["points"] == 200)
        second = levels[1]
        check("disabled bots are hidden", hidden_bot not in [c["id"] for c in second["bots"]] and second["total"] == 2)
        check("a chat counts as attempted", second["bots"][0]["status"] == "attempted")
        check("a failed turn alone doesn't", second["bots"][1]["status"] == "unattempted")
        check("the level's solved count is right", levels[0]["solved"] == 1 and levels[0]["required"] == 3)

        # ---------- One bot ----------

        bot = get(f"/bots/{answer_bots[0]}")
        check("getBot has the expected fields", set(bot) == DETAIL)
        check("getBot shows the brief", bot["description"] == "About it" and bot["goal"] == "Beat it")
        check("getBot shows the level and model", bot["levelName"] == "Password" and bot["model"] == "Model A")
        check("getBot shows solved", bot["status"] == "solved")
        check("getBot shows the wrong-answer count", (bot["wrongAnswersUsed"], bot["maxWrongAnswers"]) == (1, 10))
        check("bot details contain no token lock flag", "tokenLimitReached" not in bot)
        check("a judge bot has no wrong answers", get(f"/bots/{judge_a}")["wrongAnswersUsed"] == 0)
        for label, bad in (("disabled", hidden_bot), ("unknown", str(uuid.uuid4())), ("malformed", "not-a-uuid")):
            check(f"a {label} bot is 404", client.get(f"/api/bots/{bad}").status_code == 404)
        os.environ["MAX_WRONG_ANSWERS"] = "4"
        check("the limit follows the setting", get(f"/bots/{answer_bots[0]}")["maxWrongAnswers"] == 4)
        del os.environ["MAX_WRONG_ANSWERS"]

        # ---------- Submissions ----------

        submissions = get("/submissions")
        check("getSubmissions merges judge and password rows", len(submissions) == 5, len(submissions))
        check("each has the expected fields", all(set(row) == SUBMISSION for row in submissions))
        times = [row["createdAt"] for row in submissions]
        check("they're newest first", times == sorted(times, reverse=True))
        judge = [row for row in submissions if row["checkType"] == "judge"]
        check("an error shows as grading", judge[0]["status"] == "grading")
        check("the reason stays null until graded", judge[0]["reason"] is None)
        check("points stay null until graded", judge[0]["pointsAwarded"] is None)
        attempts = sorted(
            (row for row in submissions if row["checkType"] == "answer" and row["botId"] == answer_bots[0]),
            key=lambda row: (row["createdAt"], int(row["id"].split("-")[1])),
        )
        check("attempts have no conversation", all(row["conversationId"] is None for row in attempts))
        check("attempts show their result", [row["status"] for row in attempts] == ["failed", "passed", "passed"])
        check("only the first correct answer earns points", [row["pointsAwarded"] for row in attempts] == [0, 100, 0])
        check("attempts are graded at once", all(row["gradedAt"] and row["reason"] for row in attempts))
        conn.execute(
            "update submissions set status = 'passed', reason = 'Well done.', points_awarded = 200,"
            " graded_at = now() where team_id = %s",
            (alpha,),
        )
        graded = [row for row in get("/submissions") if row["checkType"] == "judge"][0]
        check("a graded submission shows its reason and points", (graded["reason"], graded["pointsAwarded"]) == ("Well done.", 200))

        # ---------- A team only sees its own data ----------

        as_team(beta)
        check("Beta sees its own name", get("/me")["name"] == "Beta")
        check("Beta's tokens are its own", get("/me")["tokensUsed"] == 500)
        dashboard = get("/me/dashboard")
        check("Beta has no points", dashboard["points"] == 0 and dashboard["solved"] == 0)
        check("Beta's usage is its own", [row["tokensUsed"] for row in dashboard["usageByModel"]] == [0, 500])
        first = get("/levels")[0]["bots"]
        check("Beta doesn't see Alpha's progress", [c["status"] for c in first] == ["unattempted", "unattempted", "unattempted", "attempted"])
        check("Beta doesn't see Alpha's wrong answers", get(f"/bots/{answer_bots[1]}")["wrongAnswersUsed"] == 0)
        own = get("/submissions")
        check("Beta sees only its own attempt", len(own) == 1 and own[0]["botId"] == answer_bots[3])
        check("a team ID in the URL is ignored", client.get(f"/api/me?teamId={alpha}&team_id={alpha}").json()["name"] == "Beta")

        # ---------- Secrets ----------

        everything = ""
        for team in (alpha, beta):
            as_team(team)
            bots = [row["id"] for level in get("/levels") for row in level["bots"]]
            for path in ["/me", "/me/dashboard", "/levels", "/submissions", "/leaderboard"] + [f"/bots/{b}" for b in bots]:
                everything += client.get("/api" + path).text
        check("hidden values never appear in any response", HIDDEN not in everything)
        for word in ("systemPrompt", "system_prompt", "secret", "rubric", "winRubric", "defences", "apiModel", "api_model", "host"):
            check(f"no response has a {word} field", f'"{word}"' not in everything)
        conn.execute("update submissions set reason = %s, graded_at = null, status = 'grading' where team_id = %s", (f"x {HIDDEN}", alpha))
        as_team(alpha)
        check("an ungraded reason never appears", HIDDEN not in client.get("/api/submissions").text)

        # ---------- Levels and prizes ----------

        as_team(alpha)
        def level_one():
            return get("/levels")[0]

        check("an incomplete level earns nothing", (level_one()["complete"], level_one()["prizeAwarded"]) == (False, 0))
        conn.execute("insert into level_completions (team_id, level, completed_at) values (%s, 1, now() - interval '1 hour')", (alpha,))
        check("a completed level shows its prize", (level_one()["complete"], level_one()["prizeAwarded"]) == (True, 1500))
        dashboard = get("/me/dashboard")
        check("the dashboard counts it", (dashboard["levelsCompleted"], dashboard["prizeEarned"]) == (1, 1500))

        conn.execute("insert into level_completions (team_id, level, completed_at) values (%s, 1, now() - interval '2 hours')", (beta,))
        conn.execute("update levels set max_winners = 1 where level_number = 1")
        check("a winner limit stops later teams winning", level_one()["prizeAwarded"] == 0 and level_one()["complete"])
        conn.execute("update levels set max_winners = null where level_number = 1")

        admin = one("insert into auth.users default values returning id")
        conn.execute("insert into admin_users (id, display_name) values (%s, 'A')", (admin,))
        conn.execute(
            "update level_completions set revoked = true, revoked_by = %s, revoked_at = now() where team_id = %s",
            (admin, alpha),
        )
        check("a revoked level isn't complete", (level_one()["complete"], level_one()["revoked"], level_one()["prizeAwarded"]) == (False, True, 0))
        dashboard = get("/me/dashboard")
        check("the dashboard ignores revoked levels", (dashboard["levelsCompleted"], dashboard["prizeEarned"]) == (0, 0))
        check("no level locks, even with the in-order setting", not any(
            row["locked"] for row in (conn.execute("update levels set unlock_in_order = true"), get("/levels"))[1]
        ))
        conn.execute("update levels set unlock_in_order = false")

        # ---------- Legacy quota values are ignored ----------

        conn.execute("update models set token_limit_per_team = 400 where id = 'm-a'")
        bot = get(f"/bots/{judge_a}")
        check("legacy limit does not close bot details", "tokenLimitReached" not in bot)
        check("other models aren't affected", "tokenLimitReached" not in get(f"/bots/{judge_b}"))
        conn.execute("update models set token_limit_per_team = 300 where id = 'm-a'")
        check("usage is preserved without quota fields", get("/me/dashboard")["usageByModel"][0] == {"model": "Model A", "tokensUsed": 400})

        # ---------- Ranks and ties ----------

        conn.execute("delete from credit_ledger")
        carol, dave, erin, frank = (new_team(name) for name in ("Carol", "Dave", "Erin", "Frank"))
        conn.execute("delete from scores")
        when = "2026-01-01 10:00:00+00"
        conn.execute(
            "insert into scores (team_id, bot_id, points, source, awarded_at) values"
            " (%s, %s, 300, 'admin', %s), (%s, %s, 300, 'admin', %s),"
            " (%s, %s, 300, 'admin', %s), (%s, %s, 100, 'admin', %s)",
            (carol, judge_a, when, dave, judge_a, when, erin, judge_a, "2026-01-01 11:00:00+00", frank, judge_a, when),
        )
        as_team(carol)
        board = {row["teamName"]: row["rank"] for row in get("/leaderboard")}
        check("the same points at the same time share a rank", board["Carol"] == board["Dave"] == 1, board)
        check("the rank after a tie is skipped", board["Erin"] == 3, board)
        check("the earlier team wins on the same points", board["Erin"] > board["Carol"], board)
        check("lower points rank lower", board["Frank"] == 4, board)
        check("teams with no points tie last", board["Alpha"] == board["Beta"] == 5, board)
        check("the board marks only your own team", [row["teamName"] for row in get("/leaderboard") if row["isOwnTeam"]] == ["Carol"])
        check("the dashboard rank agrees", get("/me/dashboard")["rank"] == 1)
        as_team(erin)
        check("the dashboard agrees after a tie", get("/me/dashboard")["rank"] == 3)
        as_team(alpha)
        check("the dashboard agrees for a tie at zero", get("/me/dashboard")["rank"] == 5)

        # ---------- Disabled teams and re-running ----------

        conn.execute("update teams set disabled = true where id = %s", (frank,))
        as_team(frank)
        for path in ("/me", "/me/dashboard", "/levels", "/submissions", f"/bots/{judge_a}"):
            check(f"a disabled team gets 401 on {path.split('/')[1]}", client.get("/api" + path).status_code == 401)

        conn.execute((BACKEND.parent / "supabase/team_pages.sql").read_text())
        as_team(alpha)
        check("re-running the SQL file is safe", get("/me")["name"] == "Alpha")
        conn.execute("set role anon")
        try:
            conn.execute("select get_team_me(gen_random_uuid())")
            still_allowed = True
        except psycopg.errors.InsufficientPrivilege:
            still_allowed = False
        conn.execute("reset role")
        check("access stays closed after re-running", not still_allowed)


def main():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        sys.exit("Set TEST_DATABASE_URL to a local Postgres. See the top of this file.")
    parts = urlsplit(base)
    name = f"team_pages_test_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    try:
        run_tests(urlunsplit(parts._replace(path=f"/{name}")))
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'drop database "{name}" with (force)')
    print("All team page tests passed.")


if __name__ == "__main__":
    main()
