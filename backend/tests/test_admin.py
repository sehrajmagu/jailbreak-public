"""Tests the organiser pages' endpoints and last active times against a real Postgres.

Set TEST_DATABASE_URL to a local Postgres where you may create databases, then:
    pip install -r requirements-dev.txt
    python tests/test_admin.py

It makes a throwaway database, adds stand-ins for what Supabase provides, loads
the SQL files, and drops it afterwards. Don't point it at Supabase.
"""

import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.auth import get_session  # noqa: E402
from app.conversations import begin_turn, finish_turn  # noqa: E402
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

# team_pages.sql is loaded to check that organisers see the numbers teams see.
SQL_FILES = ["schema.sql", "scoring.sql", "conversations.sql", "team_pages.sql", "admin.sql"]

FUNCTIONS = {
    "admin_bot_status": "select admin_bot_status(gen_random_uuid(), gen_random_uuid())",
    "get_admin_overview": "select get_admin_overview()",
    "get_admin_teams": "select get_admin_teams()",
    "get_admin_team": "select get_admin_team(gen_random_uuid())",
    "get_admin_bots": "select get_admin_bots()",
    "get_admin_bot": "select get_admin_bot(gen_random_uuid())",
    "get_bot_stats": "select get_bot_stats()",
    "get_admin_models": "select get_admin_models()",
    "get_admin_submissions": "select get_admin_submissions(null, null, 1, 20)",
    "get_admin_prizes": "select get_admin_prizes()",
    # The same before and after, so the service role's call below writes nothing.
    "write_audit": "select write_audit(gen_random_uuid(), 'a', 'b', 'c', '{}', '{}')",
    "update_team": "select update_team(gen_random_uuid(), gen_random_uuid(), null, null)",
    "set_bot_enabled": "select set_bot_enabled(gen_random_uuid(), gen_random_uuid(), true)",
    "set_model_token_limit": "select set_model_token_limit(gen_random_uuid(), 'x', 1)",
    "set_level_override": "select set_level_override(gen_random_uuid(), gen_random_uuid(), 1, null)",
    "touch_team": "select touch_team(gen_random_uuid())",
    "the admin_teams view": "select * from admin_teams",
    "the admin_bots view": "select * from admin_bots",
    "the admin_submissions view": "select * from admin_submissions",
    "the admin_level_completions view": "select * from admin_level_completions",
}

TEAM_FIELDS = {
    "id", "name", "disabled", "tokensUsed", "attempted", "solved", "score", "rank",
    "lastActiveAt", "createdAt",
}
BOT_FIELDS = {
    "id", "label", "level", "model", "checkType", "points", "description", "goal",
    "systemPrompt", "winRubric", "secret", "defences", "enabled",
}
SUBMISSION_FIELDS = {
    "id", "conversationId", "botId", "level", "botLabel", "checkType", "status", "reason",
    "pointsAwarded", "createdAt", "gradedAt", "teamId", "teamName", "model",
}


class Rpc:
    """Runs a Supabase rpc call as plain SQL, passing arguments by name as PostgREST does."""

    def __init__(self, url, name, params):
        self.url, self.name, self.params = url, name, params

    def execute(self):
        args = ", ".join(f"{key} => %({key})s" for key in self.params)
        values = {
            key: Jsonb(value) if isinstance(value, (list, dict)) else value
            for key, value in self.params.items()
        }
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(f"select {self.name}({args})", values).fetchone()
        return type("Response", (), {"data": row[0]})


class FakeDb:
    def __init__(self, url):
        self.url = url

    def rpc(self, name, params):
        return Rpc(self.url, name, params)


def run_tests(url):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SUPABASE_STAND_INS)
        for name in SQL_FILES:
            conn.execute((BACKEND.parent / "supabase" / name).read_text())

        def one(sql, *args):
            return conn.execute(sql, args).fetchone()[0]

        def rows(sql, *args):
            return conn.execute(sql, args).fetchall()

        def new_user():
            return str(one("insert into auth.users default values returning id"))

        def new_team(name, disabled=False):
            team = new_user()
            conn.execute("insert into teams (id, name, disabled) values (%s, %s, %s)", (team, name, disabled))
            return team

        def new_bot(level, label, model, check_type="answer", enabled=True, defences=None):
            return str(one(
                "insert into bots (level, label, display_order, model_id, system_prompt, check_type,"
                " secret, rubric, enabled, defences)"
                " values (%s, %s, (select count(*) from bots where level = %s), %s, 'Prompt {{SECRET}}',"
                " %s, %s, %s, %s, %s) returning id",
                level, label, level, model, check_type,
                f"pw-{label}" if check_type == "answer" else None,
                "Rubric" if check_type == "judge" else None,
                enabled, Jsonb(defences or {}),
            ))

        conn.execute(
            "insert into levels (level_number, name, description, required_bots, points_per_bot,"
            " prize_amount, max_winners) values"
            " (1, 'L1', 'd', 3, 100, 1500, 2), (2, 'L2', 'd', 3, 200, 3000, null)"
        )
        conn.execute(
            "insert into models (id, provider, display_name, organiser_cost_per_1k_input,"
            " organiser_cost_per_1k_output, token_limit_per_team) values"
            " ('astra', 'openai', 'GPT Astra', 0, 0, 1000), ('terra', 'openai', 'GPT Terra', 0, 0, 2000),"
            " ('unused', 'open', 'Open LLM', 0, 0, 3000)"
        )
        bot_a = new_bot(1, "Bot A", "astra", defences={
            "inputFilter": True, "outputFilter": False, "reviewerModel": "GPT Terra"})
        bot_b = new_bot(1, "Bot B", "astra")
        bot_c = new_bot(1, "Bot C", "terra")
        bot_d = new_bot(1, "Bot D", "terra")
        bot_e = new_bot(1, "Bot E", "terra", enabled=False)
        judge_a = new_bot(2, "Bot A", "terra", check_type="judge")
        judge_b = new_bot(2, "Bot B", "terra", check_type="judge")
        new_bot(2, "Bot C", "terra", check_type="judge")

        admin_id = new_user()
        conn.execute("insert into admin_users (id, display_name) values (%s, 'Organiser')", (admin_id,))

        db = FakeDb(url)
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)

        def check(label, condition):
            print(("ok   " if condition else "FAIL ") + label)
            assert condition, label

        def session(team):
            return TeamSession(team_id=team, team_name="T")

        def as_team(team):
            app.dependency_overrides[get_session] = lambda: session(team)

        def as_admin():
            app.dependency_overrides[get_session] = lambda: AdminSession(username="Organiser", admin_id=admin_id)

        def answer(team, bot, text):
            return one("select submit_answer(%s, %s, %s)", team, bot, text)

        def turn(team, bot, conversation_id=None, tokens=(100, 20), status="complete"):
            started = begin_turn(db, session(team), bot, conversation_id, "Hi")
            finish_turn(db, session(team), started, "Hello", status, tokens[0], tokens[1], [])
            return started.conversation_id

        def audits():
            return rows("select admin_id::text, action, target_type, target_id, details from audit_log order by id")

        def audit_count():
            return one("select count(*) from audit_log")

        alpha = new_team("Alpha")
        beta = new_team("Beta")
        gamma = new_team("Gamma")
        delta = new_team("Delta", disabled=True)

        # Scores, through the real answer check. Alpha completes level 1 first, then Beta.
        answer(alpha, bot_a, "wrong")
        answer(alpha, bot_a, "pw-Bot A")
        answer(alpha, bot_a, "pw-Bot A")
        answer(alpha, bot_b, "pw-Bot B")
        answer(alpha, bot_c, "pw-Bot C")
        answer(beta, bot_a, "pw-Bot A")
        answer(beta, bot_b, "pw-Bot B")
        answer(beta, bot_c, "pw-Bot C")
        answer(gamma, bot_a, "pw-Bot A")
        answer(gamma, bot_b, "pw-Bot B")
        alpha_completed = one("select completed_at from level_completions where team_id = %s and level = 1", alpha)

        # Conversations: a charged turn, a failed one, and one for Beta.
        complete_conversation = turn(alpha, bot_a)
        turn(alpha, bot_a, tokens=(50, 0), status="failed")
        turn(beta, bot_a, tokens=(10, 10))

        # Judge submissions, written directly since the judge isn't built yet.
        passed_conversation = turn(alpha, judge_a, tokens=(1, 1))
        error_conversation = turn(alpha, judge_b, tokens=(1, 1))
        pending_conversation = turn(beta, judge_a, tokens=(1, 1))
        passed_submission = str(one(
            "insert into submissions (team_id, bot_id, conversation_id, status, reason, points_awarded, graded_at)"
            " values (%s, %s, %s, 'passed', 'Broke it', 200, now()) returning id",
            alpha, judge_a, passed_conversation,
        ))
        conn.execute(
            "insert into scores (team_id, bot_id, points, source, submission_id) values (%s, %s, 200, 'judge', %s)",
            (alpha, judge_a, passed_submission),
        )
        error_submission = str(one(
            "insert into submissions (team_id, bot_id, conversation_id, status, reason)"
            " values (%s, %s, %s, 'error', 'Judge timed out') returning id",
            alpha, judge_b, error_conversation,
        ))
        pending_submission = str(one(
            "insert into submissions (team_id, bot_id, conversation_id) values (%s, %s, %s) returning id",
            beta, judge_a, pending_conversation,
        ))

        as_admin()

        # ---------- Overview ----------
        r = client.get("/api/admin/overview")
        overview = r.json()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        check("overview responds", r.status_code == 200)
        check("overview has only its fields", set(overview) == {
            "totalTeams", "activeTeams", "tokensConsumed", "totalAttempts", "totalSolves", "series"})
        check("overview counts every team", overview["totalTeams"] == 4)
        check("no team active before any action", overview["activeTeams"] == 0)
        check("overview tokens are real spend, failed turns included",
              overview["tokensConsumed"] == 120 + 50 + 20 + 2 + 2 + 2)
        check("one attempt per conversation", overview["totalAttempts"] == 6)
        check("solves are scored team and bot pairs", overview["totalSolves"] == 9)
        check("series is the last 7 days, ending today", len(overview["series"]) == 7
              and overview["series"][-1]["date"] == today)
        check("series points have only their fields", set(overview["series"][0]) == {
            "date", "attempts", "solves", "tokensConsumed"})
        check("today's activity in the series", overview["series"][-1] == {
            "date": today, "attempts": 6, "solves": 9, "tokensConsumed": 196})
        check("earlier days are zero", all(
            p["attempts"] == p["solves"] == p["tokensConsumed"] == 0 for p in overview["series"][:-1]))

        # ---------- Teams ----------
        r = client.get("/api/admin/teams")
        teams = {t["name"]: t for t in r.json()}
        check("team list responds", r.status_code == 200 and len(teams) == 4)
        check("team list in creation order", [t["name"] for t in r.json()] == ["Alpha", "Beta", "Gamma", "Delta"])
        check("teams have only their fields", all(set(t) == TEAM_FIELDS for t in teams.values()))
        check("team tokens are charged tokens only", teams["Alpha"]["tokensUsed"] == 120 + 2 + 2)
        check("attempted counts bots tried by password or a complete turn", teams["Alpha"]["attempted"] == 5)
        check("solved counts scores", teams["Alpha"]["solved"] == 4 and teams["Gamma"]["solved"] == 2)
        check("score sums points", teams["Alpha"]["score"] == 500)
        check("rank from the leaderboard", teams["Alpha"]["rank"] == 1 and teams["Gamma"]["rank"] == 3)
        check("disabled team has no rank", teams["Delta"]["rank"] is None and teams["Delta"]["disabled"])
        check("last active starts empty", teams["Alpha"]["lastActiveAt"] is None)

        r = client.get(f"/api/admin/teams/{alpha}")
        detail = r.json()
        progress = {(p["level"], p["label"]): p for p in detail["progress"]}
        check("team detail responds", r.status_code == 200)
        check("team detail has the team fields and progress", set(detail) == TEAM_FIELDS | {"progress"})
        check("progress covers every bot, disabled ones too", len(detail["progress"]) == 8)
        check("progress fields", set(detail["progress"][0]) == {
            "botId", "label", "level", "model", "status", "conversations"})
        check("progress in level and display order", [(p["level"], p["label"]) for p in detail["progress"]] == [
            (1, "Bot A"), (1, "Bot B"), (1, "Bot C"), (1, "Bot D"), (1, "Bot E"),
            (2, "Bot A"), (2, "Bot B"), (2, "Bot C")])
        check("solved bot", progress[(1, "Bot A")]["status"] == "solved" and progress[(1, "Bot A")]["conversations"] == 2)
        check("attempted bot", progress[(2, "Bot B")]["status"] == "attempted")
        check("unattempted bot, never locked", progress[(1, "Bot D")]["status"] == "unattempted"
              and all(p["status"] != "locked" for p in detail["progress"]))
        check("progress shows the model's display name", progress[(1, "Bot C")]["model"] == "GPT Terra")
        check("unknown team", client.get(f"/api/admin/teams/{uuid.uuid4()}").status_code == 404)
        check("malformed team ID", client.get("/api/admin/teams/nope").status_code == 422)

        def patch_team(team, body):
            return client.patch(f"/api/admin/teams/{team}", json=body)

        r = patch_team(alpha, {"name": "  Alpha Prime \n"})
        check("rename responds with the team", r.status_code == 200 and set(r.json()) == TEAM_FIELDS)
        check("rename trims", r.json()["name"] == "Alpha Prime")
        check("rename is audited", audits()[-1] == (
            admin_id, "update_team", "team", alpha, {
                "before": {"name": "Alpha", "disabled": False},
                "after": {"name": "Alpha Prime", "disabled": False}}))
        before = audit_count()
        check("name taken, ignoring case", patch_team(alpha, {"name": "BETA"}).status_code == 409)
        check("empty name refused", patch_team(alpha, {"name": "   "}).status_code == 422)
        check("long name refused", patch_team(alpha, {"name": "x" * 61}).status_code == 422)
        check("refusals change nothing", audit_count() == before
              and one("select name from teams where id = %s", alpha) == "Alpha Prime")
        check("changing a name's case is allowed", patch_team(alpha, {"name": "alpha prime"}).status_code == 200)
        check("same name again isn't audited", patch_team(alpha, {"name": "alpha prime"}).status_code == 200
              and audit_count() == before + 1)
        check("empty change isn't audited", patch_team(alpha, {}).status_code == 200 and audit_count() == before + 1)
        r = patch_team(beta, {"disabled": True})
        check("disable", r.status_code == 200 and r.json()["disabled"] and r.json()["rank"] is None)
        check("disable is audited", audits()[-1][1:4] == ("update_team", "team", beta)
              and audits()[-1][4]["after"] == {"name": "Beta", "disabled": True})
        check("enable", patch_team(beta, {"disabled": False}).json()["disabled"] is False)
        check("unknown team refused", patch_team(uuid.uuid4(), {"disabled": True}).status_code == 404)
        check("bad body refused", patch_team(alpha, {"disabled": "maybe"}).status_code == 422)

        # ---------- Bots ----------
        r = client.get("/api/admin/bots")
        bots = r.json()
        check("bot list responds", r.status_code == 200 and len(bots) == 8)
        check("bots have only their fields", all(set(b) == BOT_FIELDS for b in bots))
        check("bots in level and display order", [b["id"] for b in bots][:5] == [bot_a, bot_b, bot_c, bot_d, bot_e])
        check("points come from the level", bots[0]["points"] == 100 and bots[5]["points"] == 200)
        check("answer bot has its password and no rubric", bots[0]["secret"] == "pw-Bot A" and bots[0]["winRubric"] == "")
        check("judge bot has its rubric and no password", bots[5]["winRubric"] == "Rubric" and bots[5]["secret"] == "")
        check("defences shown as text", bots[0]["defences"] == ["Input filter", "Reviewer model: GPT Terra"])
        check("no defences", bots[1]["defences"] == [])
        check("bot model is the display name", bots[0]["model"] == "GPT Astra")
        r = client.get(f"/api/admin/bots/{bot_a}")
        check("bot detail", r.status_code == 200 and r.json() == bots[0])
        check("unknown bot", client.get(f"/api/admin/bots/{uuid.uuid4()}").status_code == 404)
        check("malformed bot ID", client.get("/api/admin/bots/nope").status_code == 422)

        def set_enabled(bot, enabled):
            return client.patch(f"/api/admin/bots/{bot}", json={"enabled": enabled})

        r = set_enabled(bot_d, False)
        check("disable a bot", r.status_code == 200 and r.json()["enabled"] is False and set(r.json()) == BOT_FIELDS)
        check("bot change is audited", audits()[-1] == (
            admin_id, "set_bot_enabled", "bot", bot_d, {"before": {"enabled": True}, "after": {"enabled": False}}))
        before = audit_count()
        check("can't leave fewer enabled bots than a level needs", set_enabled(bot_c, False).status_code == 409)
        check("refused disable changes nothing", audit_count() == before
              and one("select enabled from bots where id = %s", bot_c))
        check("disabling a disabled bot is allowed", set_enabled(bot_e, False).status_code == 200)
        check("and isn't audited", audit_count() == before)
        check("enable a bot", set_enabled(bot_d, True).json()["enabled"] is True and audit_count() == before + 1)
        check("unknown bot refused", set_enabled(uuid.uuid4(), True).status_code == 404)

        # Two disables at once, with 4 enabled: only one may pass. A third connection
        # holds the level's bots so both calls start before either finishes.
        with psycopg.connect(url) as holder:
            holder.execute("select 1 from bots where level = 1 for update")
            with ThreadPoolExecutor(2) as pool:
                calls = [pool.submit(set_enabled, bot, False) for bot in (bot_c, bot_d)]
                deadline = time.time() + 10
                while one("select count(*) from pg_stat_activity where wait_event_type = 'Lock'") < 2:
                    assert time.time() < deadline, "the two calls never started"
                    time.sleep(0.05)
                holder.rollback()
                statuses = sorted(call.result().status_code for call in calls)
        check("simultaneous disables: one passes, one is refused", statuses == [200, 409])
        check("the level keeps enough enabled bots",
              one("select count(*) from bots where level = 1 and enabled") == 3)
        conn.execute("update bots set enabled = true where id in (%s, %s)", (bot_c, bot_d))

        # ---------- Models ----------
        r = client.get("/api/admin/models")
        check("model list", r.status_code == 200 and r.json() == [
            {"model": "GPT Astra"},
            {"model": "GPT Terra"},
            {"model": "Open LLM"},
        ])
        before = audit_count()
        r = client.patch("/api/admin/models/GPT%20Astra", json={"tokenLimitPerTeam": 5000})
        check("old quota endpoint is retired", r.status_code == 410)
        check("retired endpoint leaves data and audit unchanged",
              one("select token_limit_per_team from models where id = 'astra'") == 1000 and audit_count() == before)

        # ---------- Submissions ----------
        def submissions(**params):
            return client.get("/api/admin/submissions", params=params)

        r = submissions()
        page = r.json()
        items = page["items"]
        by_id = {s["id"]: s for s in items}
        attempts = rows("select id, correct from answer_attempts where team_id = %s and bot_id = %s order by id", alpha, bot_a)
        check("submission list responds", r.status_code == 200)
        check("submissions and password attempts together", page["total"] == 3 + 10)
        check("default page", page["page"] == 1 and page["pageSize"] == 20 and len(items) == 13)
        check("submissions have only their fields", all(set(s) == SUBMISSION_FIELDS for s in items))
        check("newest first", [s["createdAt"] for s in items] == sorted((s["createdAt"] for s in items), reverse=True))
        wrong, first_right, second_right = (by_id[f"answer-{a[0]}"] for a in attempts)
        check("password attempt has no conversation", wrong["conversationId"] is None and wrong["checkType"] == "answer")
        check("wrong password", wrong["status"] == "failed" and wrong["reason"] == "Incorrect password."
              and wrong["pointsAwarded"] == 0)
        check("first correct password scores", first_right["status"] == "passed"
              and first_right["reason"] == "Correct password." and first_right["pointsAwarded"] == 100)
        check("a repeat correct password scores nothing", second_right["pointsAwarded"] == 0)
        check("password attempts are graded at once", wrong["gradedAt"] == wrong["createdAt"])
        check("password attempt shows team and model", wrong["teamName"] == "alpha prime" and wrong["model"] == "GPT Astra")
        check("judge pass", by_id[passed_submission]["status"] == "passed"
              and by_id[passed_submission]["pointsAwarded"] == 200
              and by_id[passed_submission]["conversationId"] == passed_conversation)
        check("error shown as grading, with its reason, and no points yet",
              by_id[error_submission]["status"] == "grading"
              and by_id[error_submission]["reason"] == "Judge timed out"
              and by_id[error_submission]["pointsAwarded"] is None)
        check("pending", by_id[pending_submission]["status"] == "pending" and by_id[pending_submission]["gradedAt"] is None)

        grading = submissions(status="grading").json()
        check("grading filter includes errors", [s["id"] for s in grading["items"]] == [error_submission])
        check("pending filter", [s["id"] for s in submissions(status="pending").json()["items"]] == [pending_submission])
        check("passed filter covers both kinds", submissions(status="passed").json()["total"] == 1 + 9)
        check("failed filter", submissions(status="failed").json()["total"] == 1)
        beta_items = submissions(teamId=beta).json()
        check("team filter", beta_items["total"] == 4 and all(s["teamId"] == beta for s in beta_items["items"]))
        check("filters combine", submissions(teamId=beta, status="pending").json()["total"] == 1)
        second = submissions(page=2, pageSize=5).json()
        check("page 2", second["page"] == 2 and second["pageSize"] == 5
              and [s["id"] for s in second["items"]] == [s["id"] for s in items[5:10]])
        check("past the end is empty", submissions(page=9).json()["items"] == [])
        check("page 0 refused", submissions(page=0).status_code == 422)
        check("page size 101 refused", submissions(pageSize=101).status_code == 422)
        check("malformed team filter refused", submissions(teamId="nope").status_code == 422)
        check("error isn't a status teams know", submissions(status="error").status_code == 422)

        # ---------- Bot stats ----------
        r = client.get("/api/admin/bot-stats")
        stats = {(s["level"], s["label"]): s for s in r.json()}
        a_stats = stats[(1, "Bot A")]
        check("bot stats respond", r.status_code == 200 and len(stats) == 8)
        check("bot stats fields", set(a_stats) == {
            "botId", "label", "level", "model", "enabled", "attempts", "teamsAttempted", "solves",
            "solveRate", "avgAttemptsToSolve", "tokensUsed"})
        check("attempts are conversations", a_stats["attempts"] == 3)
        check("a password typed in without chatting counts as attempting", a_stats["teamsAttempted"] == 3)
        check("solves are scoring teams", a_stats["solves"] == 3)
        check("every solving team attempted, so the rate is at most 1", a_stats["solveRate"] == 1)
        check("average conversations per solving team", a_stats["avgAttemptsToSolve"] == 1)
        check("bot tokens are real spend", a_stats["tokensUsed"] == 120 + 50 + 20)
        check("unsolved bot", stats[(1, "Bot D")]["solves"] == 0 and stats[(1, "Bot D")]["solveRate"] == 0
              and stats[(1, "Bot D")]["avgAttemptsToSolve"] is None)
        check("partly solved bot", stats[(2, "Bot A")]["solveRate"] == 0.5
              and stats[(2, "Bot A")]["avgAttemptsToSolve"] == 1)
        check("disabled bot listed", stats[(1, "Bot E")]["enabled"] is False)

        # ---------- Prizes ----------
        def prizes():
            r = client.get("/api/admin/prizes")
            assert r.status_code == 200
            return r.json()

        def level_1(data, team):
            entry = next(t for t in data["teams"] if t["teamId"] == team)
            return entry["levels"][0]

        def put_override(team, level, override):
            return client.put(f"/api/admin/prizes/{team}/levels/{level}", json={"override": override})

        data = prizes()
        check("prizes fields", set(data) == {"requireLevelsInOrder", "levels", "teams", "totalPrizeOwed"})
        check("levels are always open", data["requireLevelsInOrder"] is False)
        check("level config", data["levels"][0] == {
            "level": 1, "name": "L1", "prizeAmount": 1500, "requiredBots": 3, "maxWinners": 2})
        check("every team listed, disabled ones too", [t["teamId"] for t in data["teams"]] == [alpha, beta, gamma, delta])
        check("team prize fields", set(data["teams"][0]) == {"teamId", "teamName", "levels", "prizeOwed"})
        check("level completion fields", set(level_1(data, alpha)) == {
            "level", "solved", "required", "total", "earned", "override", "complete", "prizeAwarded", "completedAt"})
        check("earned completion", level_1(data, alpha) | {"completedAt": None} == {
            "level": 1, "solved": 3, "required": 3, "total": 4, "earned": True, "override": None,
            "complete": True, "prizeAwarded": 1500, "completedAt": None})
        check("not yet earned", level_1(data, gamma)["earned"] is False and level_1(data, gamma)["complete"] is False
              and level_1(data, gamma)["completedAt"] is None)
        check("prizes owed", data["totalPrizeOwed"] == 3000)

        r = put_override(gamma, 1, "granted")
        data = r.json()
        check("grant responds with the prizes", r.status_code == 200 and set(data) == set(prizes()))
        check("granted level is complete", level_1(data, gamma)["override"] == "granted"
              and level_1(data, gamma)["complete"] and not level_1(data, gamma)["earned"])
        check("only the first max_winners teams win", level_1(data, gamma)["prizeAwarded"] == 0
              and level_1(data, beta)["prizeAwarded"] == 1500)
        check("grant is audited", audits()[-1][:4] == (admin_id, "set_level_override", "level_completion", f"{gamma}:1")
              and audits()[-1][4]["before"] is None and audits()[-1][4]["after"]["source"] == "admin_override")

        data = put_override(alpha, 1, "revoked").json()
        check("revoked level isn't complete", level_1(data, alpha)["override"] == "revoked"
              and not level_1(data, alpha)["complete"] and level_1(data, alpha)["earned"]
              and level_1(data, alpha)["prizeAwarded"] == 0)
        check("a revoked team frees its prize slot", level_1(data, gamma)["prizeAwarded"] == 1500)
        before = audit_count()
        put_override(alpha, 1, "revoked")
        check("revoking again isn't audited", audit_count() == before)

        data = put_override(alpha, 1, None).json()
        check("clearing goes back to what the solves earn", level_1(data, alpha)["override"] is None
              and level_1(data, alpha)["complete"])
        check("and keeps the original completion time", one(
            "select completed_at from level_completions where team_id = %s and level = 1", alpha) == alpha_completed)
        check("so the team gets its prize slot back", level_1(data, alpha)["prizeAwarded"] == 1500
              and level_1(data, gamma)["prizeAwarded"] == 0)
        check("clear is audited", audits()[-1][4]["before"]["revoked"] is True
              and audits()[-1][4]["after"]["source"] == "earned")

        data = put_override(gamma, 1, None).json()
        check("clearing a grant without the solves leaves it incomplete", not level_1(data, gamma)["complete"]
              and one("select count(*) from level_completions where team_id = %s and level = 1", gamma) == 0)
        before = audit_count()
        put_override(gamma, 1, None)
        check("clearing nothing isn't audited", audit_count() == before)

        put_override(gamma, 1, "revoked")
        answer(gamma, bot_c, "pw-Bot C")
        data = prizes()
        check("a revoked level stays revoked when the team scores again",
              level_1(data, gamma)["override"] == "revoked" and level_1(data, gamma)["earned"]
              and not level_1(data, gamma)["complete"])
        data = put_override(gamma, 1, None).json()
        third_score = one(
            "select max(s.awarded_at) from scores s join bots b on b.id = s.bot_id where s.team_id = %s and b.level = 1", gamma)
        check("clearing then completes it, when the third solve came in", level_1(data, gamma)["complete"]
              and one("select completed_at from level_completions where team_id = %s and level = 1", gamma) == third_score)

        data = put_override(beta, 1, "granted").json()
        check("granting an earned level keeps its prize slot", level_1(data, beta)["override"] == "granted"
              and level_1(data, beta)["prizeAwarded"] == 1500 and level_1(data, gamma)["prizeAwarded"] == 0)
        put_override(beta, 1, None)

        check("unknown override refused", put_override(alpha, 1, "maybe").status_code == 422)
        check("unknown team refused", put_override(uuid.uuid4(), 1, "granted").status_code == 404)
        check("unknown level refused", put_override(alpha, 9, "granted").status_code == 404)
        check("malformed team refused", client.put("/api/admin/prizes/nope/levels/1", json={"override": None}).status_code == 422)

        check("every audit row names the admin", all(row[0] == admin_id for row in audits()))

        # ---------- Agreement with the team pages ----------
        # Organisers must see the numbers teams see, above all what each team is owed.
        def agrees_with_team_pages():
            listed = {t["id"]: t for t in client.get("/api/admin/teams").json()}
            for entry in prizes()["teams"]:
                team = entry["teamId"]
                if listed[team]["disabled"]:
                    continue
                dashboard = one("select get_team_dashboard(%s)", team)
                results = {
                    level: (complete, prize)
                    for level, complete, prize in rows(
                        "select level, complete, prize_awarded from team_level_results(%s)", team)
                }
                if (listed[team]["attempted"], listed[team]["solved"]) != (dashboard["attempted"], dashboard["solved"]):
                    return False
                if entry["prizeOwed"] != dashboard["prize_earned"]:
                    return False
                if {level["level"]: (level["complete"], level["prizeAwarded"]) for level in entry["levels"]} != results:
                    return False
            return True

        check("teams and organisers see the same numbers", agrees_with_team_pages())

        epsilon = new_team("Epsilon")
        turn(epsilon, bot_d, tokens=(5, 0), status="failed")
        progress = {p["botId"]: p for p in client.get(f"/api/admin/teams/{epsilon}").json()["progress"]}
        check("a failed turn alone isn't an attempt", progress[bot_d]["status"] == "unattempted"
              and progress[bot_d]["conversations"] == 1)
        answer(epsilon, bot_d, "wrong")
        progress = {p["botId"]: p for p in client.get(f"/api/admin/teams/{epsilon}").json()["progress"]}
        check("a wrong password is an attempt", progress[bot_d]["status"] == "attempted")
        check("still the same numbers", agrees_with_team_pages())

        # Beta and Gamma complete level 1 at the same moment, after Alpha. With 2
        # winners, they share second place, so both are owed the prize.
        conn.execute(
            "update level_completions set completed_at = (select completed_at from level_completions"
            " where team_id = %s and level = 1) where team_id = %s and level = 1",
            (beta, gamma),
        )
        data = prizes()
        check("teams completing at the same moment share a place",
              level_1(data, beta)["prizeAwarded"] == 1500 and level_1(data, gamma)["prizeAwarded"] == 1500)
        check("the same numbers with a tie", agrees_with_team_pages())

        conn.execute("update levels set enabled = false where level_number = 2")
        data = prizes()
        check("a disabled level isn't listed", [level["level"] for level in data["levels"]] == [1]
              and all(len(team["levels"]) == 1 for team in data["teams"]))
        check("the same numbers with a level disabled", agrees_with_team_pages())
        conn.execute("update levels set enabled = true where level_number = 2")

        # ---------- Access ----------
        as_team(alpha)
        check("team refused on admin reads", client.get("/api/admin/teams").status_code == 403)
        check("team refused on admin changes", patch_team(alpha, {"disabled": True}).status_code == 403)
        check("team refused on prizes", put_override(alpha, 1, "granted").status_code == 403)

        # ---------- Last active ----------
        def last_active(team):
            return one("select last_active_at from teams where id = %s", team)

        client.get(f"/api/bots/{bot_a}/conversations")
        check("viewing a page isn't activity", last_active(alpha) is None)
        r = client.post(f"/api/bots/{bot_d}/answer", json={"answer": "guess"})
        check("an action records activity", r.status_code == 200 and last_active(alpha) is not None)
        first = last_active(alpha)
        client.post(f"/api/bots/{bot_d}/answer", json={"answer": "guess"})
        check("written at most once a minute", last_active(alpha) == first)
        conn.execute("update teams set last_active_at = now() - interval '2 minutes' where id = %s", (alpha,))
        client.post(f"/api/bots/{bot_d}/answer", json={"answer": "guess"})
        check("written again after a minute", last_active(alpha) > first)
        as_admin()
        check("the overview counts the active team", client.get("/api/admin/overview").json()["activeTeams"] == 1)
        check("the team list shows it", next(
            t for t in client.get("/api/admin/teams").json() if t["id"] == alpha)["lastActiveAt"] is not None)

        # Function access
        for role in ("anon", "authenticated"):
            for name, sql in FUNCTIONS.items():
                conn.execute(f"set role {role}")
                try:
                    conn.execute(sql)
                    check(f"{role} can't call {name}", False)
                except psycopg.errors.InsufficientPrivilege:
                    pass
                finally:
                    conn.execute("reset role")
            print(f"ok   {role} can't call any of the functions")

        # Re-running the file
        conn.execute((BACKEND.parent / "supabase/admin.sql").read_text())
        try:
            conn.execute("update models set display_name = 'GPT Astra' where id = 'terra'")
            check("model names stay unique after re-running", False)
        except psycopg.errors.UniqueViolation:
            print("ok   re-running is safe and model names stay unique")

        # The backend needs the service role's access even where the project's
        # default privileges don't give it, so the file grants it and re-running restores it.
        conn.execute("revoke all on admin_teams, admin_bots, admin_submissions, admin_level_completions from service_role")
        conn.execute((BACKEND.parent / "supabase/admin.sql").read_text())
        conn.execute("set role service_role")
        try:
            for name, sql in FUNCTIONS.items():
                conn.execute(sql)
            print("ok   the service role can call every function and read every view")
        except psycopg.errors.InsufficientPrivilege as error:
            check(f"the service role has access ({error})", False)
        finally:
            conn.execute("reset role")


def main():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        sys.exit("Set TEST_DATABASE_URL to a local Postgres. See the top of this file.")
    name = f"admin_test_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(base)
    url = urlunsplit(parts._replace(path=f"/{name}"))
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"create database {name}")
    try:
        run_tests(url)
        print("All admin tests passed.")
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"drop database {name} with (force)")


if __name__ == "__main__":
    main()
