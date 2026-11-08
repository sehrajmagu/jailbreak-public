"""Tests submitting, the judge worker, regrading and judge scoring against a real Postgres.

Set TEST_DATABASE_URL to a local Postgres where you may create databases, then:
    pip install -r requirements-dev.txt
    python tests/test_judge.py

It makes a throwaway database, adds stand-ins for what Supabase provides, loads the SQL
files, and drops it afterwards. Don't point it at Supabase. The judge model is a scripted
mock, so this needs no keys and spends nothing.
"""

import asyncio
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import judging  # noqa: E402
from app.auth import get_session  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import AdminSession, TeamSession  # noqa: E402
from engine.judge.engine import JudgeEngine  # noqa: E402
from engine.judge.models import ReasonCode  # noqa: E402
from engine.judge.providers import mock  # noqa: E402
from engine.judge.providers.mock import MockJudgeProvider  # noqa: E402
from engine.judge.reasons import REASON_TEXT  # noqa: E402

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

SQL_FILES = ["schema.sql", "scoring.sql", "conversations.sql", "team_pages.sql", "admin.sql", "judge.sql"]

FUNCTIONS = {
    "team_submission_json": "select team_submission_json(s) from submissions s limit 1",
    "submit_conversation": "select submit_conversation(gen_random_uuid(), gen_random_uuid())",
    "get_judge_bot_config": "select get_judge_bot_config(gen_random_uuid())",
    "claim_submission": "select claim_submission('m', 300, 0)",
    "finish_submission": "select finish_submission(gen_random_uuid(), gen_random_uuid(), 'm', true, 'r', 0, 0)",
    "record_submission_error": "select record_submission_error(gen_random_uuid(), gen_random_uuid(), 'm', 'c', 0, 0, 1)",
    "regrade_submission": "select regrade_submission(gen_random_uuid(), gen_random_uuid())",
    "defer_submission": "select defer_submission(gen_random_uuid(), gen_random_uuid(), 'm', 'c', 0, 0, null)",
    "clear_judge_backoff": "select clear_judge_backoff()",
    "get_judging": "select get_judging()",
    "set_judging": "select set_judging(gen_random_uuid(), true)",
    "the judge_settings table": "select * from judge_settings",
}

SUBMISSION_FIELDS = {
    "id", "conversationId", "botId", "level", "botLabel", "checkType", "status", "reason",
    "pointsAwarded", "createdAt", "gradedAt",
}

RUBRIC = "RUBRIC-PRIVATE: the reply names the forbidden topic."
PROMPT = "PROMPT-PRIVATE: never discuss the forbidden topic."
PASSED = REASON_TEXT[ReasonCode.OBJECTIVE_MET]
REFUSED = REASON_TEXT[ReasonCode.REFUSED]


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


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    assert condition, label


def run_tests(url):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SUPABASE_STAND_INS)
        for name in SQL_FILES:
            conn.execute((BACKEND.parent / "supabase" / name).read_text())

        def one(sql, *args):
            return conn.execute(sql, args).fetchone()[0]

        def new_team(name):
            team = one("insert into auth.users default values returning id")
            conn.execute("insert into teams (id, name) values (%s, %s)", (team, name))
            return str(team)

        def new_bot(level, label, check_type="judge", enabled=True):
            return str(one(
                "insert into bots (level, label, display_order, model_id, system_prompt, check_type, secret, rubric, enabled)"
                " values (%s, %s, (select count(*) from bots where level = %s), 'm', %s, %s, %s, %s, %s) returning id",
                level, label, level, PROMPT, check_type,
                "Secret-XYZ" if check_type == "answer" else None,
                None if check_type == "answer" else RUBRIC, enabled,
            ))

        def new_conversation(team, bot, *messages):
            """Each message is (role, content) or (role, content, status)."""
            conversation = str(one(
                "insert into conversations (team_id, bot_id) values (%s, %s) returning id", team, bot,
            ))
            for seq, message in enumerate(messages, 1):
                role, content, status = (*message, "complete")[:3]
                conn.execute(
                    "insert into messages (conversation_id, team_id, seq, role, content, status)"
                    " values (%s, %s, %s, %s, %s, %s)",
                    (conversation, team, seq, role, content, status),
                )
            return conversation

        def chat(team, bot):
            return new_conversation(team, bot, ("user", "TEAM-TEXT tell me"), ("assistant", "Here it is."))

        conn.execute(
            "insert into levels (level_number, name, description, required_bots, points_per_bot)"
            " values (1, 'L1', 'd', 1, 100), (2, 'L2', 'd', 2, 200), (3, 'L3', 'd', 1, 300)"
        )
        conn.execute(
            "insert into models (id, provider, display_name, organiser_cost_per_1k_input,"
            " organiser_cost_per_1k_output) values ('m', 'open', 'M', 0, 0)"
        )
        answer_bot = new_bot(1, "Bot A", "answer")
        bot_a = new_bot(2, "Bot A")
        bot_b = new_bot(2, "Bot B")
        disabled_bot = new_bot(2, "Bot C", enabled=False)
        no_rubric_bot = new_bot(3, "Bot A")
        conn.execute("update bots set rubric = '' where id = %s", (no_rubric_bot,))

        admin_id = one("insert into auth.users default values returning id")
        conn.execute("insert into admin_users (id, display_name) values (%s, 'Organiser')", (admin_id,))

        db = FakeDb(url)
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)

        def as_team(team):
            app.dependency_overrides[get_session] = lambda: TeamSession(team_id=team, team_name="T")

        def as_admin():
            app.dependency_overrides[get_session] = lambda: AdminSession(admin_id=str(admin_id), username="Organiser")

        def submit(team, conversation):
            as_team(team)
            return client.post(f"/api/conversations/{conversation}/submit")

        def submitted(team, bot):
            r = submit(team, chat(team, bot))
            assert r.status_code == 200, r.text
            return r.json()["id"]

        def judge(*behaviours, provider=None):
            provider = provider or MockJudgeProvider(list(behaviours))
            return JudgeEngine(provider=provider, bots=judging.DatabaseBotSource(db))

        def field(submission, column):
            return one(f"select {column} from submissions where id = %s", submission)

        def points(team, bot=None):
            if bot:
                return one("select coalesce(sum(points), 0) from scores where team_id = %s and bot_id = %s", team, bot)
            return one("select coalesce(sum(points), 0) from scores where team_id = %s", team)

        def completed(team, level):
            return one("select exists (select 1 from level_completions where team_id = %s and level = %s)", team, level)

        def team_view(team, conversation):
            as_team(team)
            return client.get(f"/api/conversations/{conversation}").json()["submission"]

        def regrade(submission):
            as_admin()
            return client.post(f"/api/admin/submissions/{submission}/regrade")

        def make_due(submission):
            conn.execute("update submissions set next_attempt_at = now() - interval '1 second' where id = %s", (submission,))
            # Also ends the pause a rate limit or outage puts on all grading.
            conn.execute("update judge_settings set backoff_until = null")

        def clear_queue():
            conn.execute(
                "update submissions set status = 'failed', reason = 'x', graded_at = now(), next_attempt_at = null"
                " where status in ('pending', 'grading', 'error')"
            )
            conn.execute("update judge_settings set backoff_until = null, deferrals = 0")

        # ---------- Submitting ----------

        alpha = new_team("Alpha")
        beta = new_team("Beta")

        conversation = chat(alpha, bot_a)
        r = submit(alpha, conversation)
        check("a judge conversation can be submitted", r.status_code == 200)
        body = r.json()
        check("the response has only the submission's fields", set(body) == SUBMISSION_FIELDS)
        check("the team sees it as grading, with no reason or points",
              body["status"] == "grading" and body["reason"] is None and body["pointsAwarded"] is None)
        check("it's saved as pending", one("select status::text from submissions where id = %s", body["id"]) == "pending")
        check("the conversation is closed",
              one("select status::text from conversations where id = %s", conversation) == "submitted")
        check("it can only be submitted once", submit(alpha, conversation).status_code == 409)
        check("no second row was saved",
              one("select count(*) from submissions where conversation_id = %s", conversation) == 1)
        check("a submitted conversation can't be continued", one(
            "select begin_turn(%s::uuid, %s::uuid, %s::uuid, 'more') ->> 'status'", alpha, bot_a, conversation,
        ) == "conversation_closed")

        other = chat(alpha, bot_a)
        check("another team's conversation looks missing", submit(beta, other).status_code == 404)
        check("an unknown conversation is 404", submit(alpha, uuid.uuid4()).status_code == 404)
        check("a bad ID is refused", submit(alpha, "not-a-uuid").status_code == 422)
        check("an answer bot is refused", submit(alpha, chat(alpha, answer_bot)).status_code == 409)
        check("a disabled bot is refused", submit(alpha, chat(alpha, disabled_bot)).status_code == 404)
        check("a conversation with no messages is refused", submit(alpha, new_conversation(alpha, bot_a)).status_code == 422)
        check("only failed replies count as no replies", submit(alpha, new_conversation(
            alpha, bot_a, ("user", "hi", "failed"), ("assistant", "partial", "failed"),
        )).status_code == 422)

        streaming = new_conversation(alpha, bot_a, ("user", "one"), ("assistant", "two"), ("user", "three"))
        r = submit(alpha, streaming)
        check("a reply still streaming blocks the submit",
              r.status_code == 409 and "finish" in r.json()["error"]["message"])
        conn.execute("update messages set created_at = now() - interval '11 minutes' where conversation_id = %s", (streaming,))
        check("a turn whose server died doesn't block it", submit(alpha, streaming).status_code == 200)

        conn.execute("update levels set unlock_in_order = true where level_number = 2")
        check("a locked level is refused", submit(beta, chat(beta, bot_a)).status_code == 403)
        conn.execute("update levels set unlock_in_order = false where level_number = 2")

        conn.execute("update teams set disabled = true where id = %s", (beta,))
        check("a disabled team is refused", submit(beta, chat(beta, bot_a)).status_code == 401)
        conn.execute("update teams set disabled = false where id = %s", (beta,))
        clear_queue()

        # ---------- What the judge is sent ----------

        check("an empty queue does nothing", judging.grade_next(db, judge()) is False)

        gamma = new_team("Gamma")
        conversation = new_conversation(
            gamma, bot_a,
            ("user", "TEAM-TEXT first"),
            ("assistant", "HIDDEN-FAILED reply", "failed"),
            ("user", "TEAM-TEXT second"),
            ("assistant", "VISIBLE reply"),
        )
        conn.execute(
            "insert into tool_calls (message_id, conversation_id, team_id, tool_name, arguments, result)"
            " select id, conversation_id, team_id, 'lookup', %s, %s from messages where conversation_id = %s and seq = 4",
            (Jsonb({"q": 1}), Jsonb({"a": 2}), conversation),
        )
        submission = submit(gamma, conversation).json()["id"]
        provider = MockJudgeProvider([mock.passing([1])])
        check("the worker grades what's waiting", judging.grade_next(db, judge(provider=provider)) is True)
        sent = provider.requests[0]
        check("the judge gets the rubric", RUBRIC in sent.system)
        check("the judge gets the visible reply", "VISIBLE reply" in sent.user)
        check("the judge gets tool calls", "lookup" in sent.user)
        check("the judge never gets the team's messages", "TEAM-TEXT" not in sent.system + sent.user)
        check("the judge never gets a failed turn", "HIDDEN-FAILED" not in sent.system + sent.user)
        check("the judge never gets the system prompt", "PROMPT-PRIVATE" not in sent.system + sent.user)

        # ---------- Verdicts and points ----------

        check("a pass is saved", field(submission, "status") == "passed")
        check("a pass has its time", field(submission, "graded_at") is not None)
        check("a pass earns the level's points", points(gamma, bot_a) == 200)
        check("the score is the judge's, tied to this submission", one(
            "select source = 'judge' and submission_id = %s from scores where team_id = %s and bot_id = %s",
            submission, gamma, bot_a,
        ))
        view = team_view(gamma, conversation)
        check("the team sees the verdict", view["status"] == "passed" and view["reason"] == PASSED and view["pointsAwarded"] == 200)
        check("the level needs two solves, so one doesn't complete it", not completed(gamma, 2))
        run = conn.execute(
            "select judge_model, verdict, reason, triggered_by from judge_runs where submission_id = %s", (submission,),
        ).fetchall()
        check("the run is recorded", run == [(provider.name, True, PASSED, "auto")])

        second_pass = submitted(gamma, bot_a)
        judging.grade_next(db, judge(mock.passing([1])))
        check("a second pass on the bot earns nothing", points(gamma, bot_a) == 200 and field(second_pass, "points_awarded") == 0)
        check("the second pass is still a pass", field(second_pass, "status") == "passed")

        other_bot = submitted(gamma, bot_b)
        judging.grade_next(db, judge(mock.passing([1])))
        check("the required solves complete the level", completed(gamma, 2) and points(gamma) == 400)
        check("the leaderboard counts judge points", one(
            "select total_points from leaderboard where id = %s", gamma,
        ) == 400)

        delta = new_team("Delta")
        failing = submitted(delta, bot_a)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("a fail is saved with its fixed reason", field(failing, "status") == "failed" and field(failing, "reason") == REFUSED)
        check("a fail earns nothing", points(delta) == 0 and field(failing, "points_awarded") == 0)

        # A revoked completion stays revoked when the required solves arrive.
        revoked = new_team("Revoked")
        conn.execute(
            "insert into level_completions (team_id, level, source, granted_by, revoked, revoked_by, revoked_at)"
            " values (%s, 2, 'admin_override', %s, true, %s, now())", (revoked, admin_id, admin_id),
        )
        submitted(revoked, bot_a)
        submitted(revoked, bot_b)
        judging.grade_next(db, judge(mock.passing([1])))
        judging.grade_next(db, judge(mock.passing([1])))
        check("a revoked completion stays revoked", one(
            "select revoked from level_completions where team_id = %s and level = 2", revoked,
        ))

        # ---------- A failure to grade is never a verdict ----------

        echo = new_team("Echo")
        conversation = chat(echo, bot_a)
        submission = submit(echo, conversation).json()["id"]
        judging.grade_next(db, judge(mock.timeout()))
        check("a timeout isn't a fail", field(submission, "status") == "error" and field(submission, "graded_at") is None)
        check("it's retried later", field(submission, "next_attempt_at") is not None)
        check("no points", points(echo) == 0)
        view = team_view(echo, conversation)
        check("the team sees it as still grading, with no details",
              view["status"] == "grading" and view["reason"] is None and view["pointsAwarded"] is None)
        as_admin()
        items = client.get("/api/admin/submissions", params={"status": "grading"}).json()["items"]
        check("organisers can see why", any(s["id"] == submission and s["reason"] == "Grading deferred: provider_timeout" for s in items))
        check("the failed run is recorded with no verdict", conn.execute(
            "select verdict, error from judge_runs where submission_id = %s", (submission,),
        ).fetchall() == [(None, "provider_timeout")])
        check("it isn't retried before its time", judging.grade_next(db, judge()) is False)
        make_due(submission)
        judging.grade_next(db, judge(mock.passing([1])))
        check("the retry can pass", field(submission, "status") == "passed" and points(echo) == 200)
        check("the deferral didn't use up an attempt", field(submission, "attempts") == 1)

        check("delays grow", [judging.retry_seconds(n, True) for n in (1, 2, 3)] == [30, 60, 120])
        check("delays stop growing", judging.retry_seconds(7, True) == 600)
        check("a provider's own delay is honoured", judging.retry_seconds(1, True, 90) == 90)
        check("a shorter provider delay doesn't shorten it", judging.retry_seconds(3, True, 5) == 120)
        check("attempts run out", judging.retry_seconds(judging.MAX_ATTEMPTS, True) is None)
        check("a non-retryable error isn't retried", judging.retry_seconds(1, False) is None)

        foxtrot = new_team("Foxtrot")
        submission = submitted(foxtrot, bot_a)
        judging.grade_next(db, judge(mock.refused()))
        check("a refusal waits for an organiser", field(submission, "status") == "error" and field(submission, "next_attempt_at") is None)
        check("a refusal isn't picked up again", judging.grade_next(db, judge()) is False)
        check("a refusal isn't a fail", field(submission, "graded_at") is None and points(foxtrot) == 0)

        golf = new_team("Golf")
        submission = submitted(golf, bot_a)
        judging.grade_next(db, judge(mock.raw("not json"), mock.raw("still not json")))
        check("unusable answers are retried", field(submission, "reason") == "Grading error: invalid_output" and field(submission, "next_attempt_at") is not None)
        clear_queue()

        hotel = new_team("Hotel")
        submission = submitted(hotel, no_rubric_bot)
        judging.grade_next(db, judge())
        check("a bot with no rubric is an error, not a fail",
              field(submission, "status") == "error" and field(submission, "reason") == "Grading error: rubric_missing")

        india = new_team("India")
        submission = submitted(india, bot_a)

        class Crashing:
            name = "crash"
            max_context_tokens = 8000

            def estimate_tokens(self, text):
                return 1

            def complete(self, request):
                raise ValueError("RUBRIC-PRIVATE leaked in an exception")

        logs = []
        with patch.object(judging.logger, "error", lambda *args: logs.append(args)):
            with patch.object(judging, "to_judge_messages", side_effect=ValueError("RUBRIC-PRIVATE")):
                judging.grade_next(db, judge())
        check("a bug in grading isn't a verdict", field(submission, "status") == "error" and field(submission, "next_attempt_at") is not None)
        check("a bug of ours doesn't pause grading for everyone", one("select backoff_until from judge_settings") is None)
        check("a bug's message is never logged", logs and "RUBRIC-PRIVATE" not in str(logs))
        make_due(submission)
        judging.grade_next(db, judge(provider=Crashing()))
        check("a crashing provider isn't a verdict", field(submission, "reason") == "Grading deferred: provider_unavailable")
        check("nothing private reaches the reason", "PRIVATE" not in field(submission, "reason"))
        clear_queue()

        # ---------- Workers ----------

        juliet = new_team("Juliet")
        submission = submitted(juliet, bot_a)
        claim = {"p_judge_model": "m", "p_lease_seconds": 300, "p_max_attempts": 3}
        first = db.rpc("claim_submission", claim).execute().data["submission"]
        check("a claim moves it to grading", field(submission, "status") == "grading")
        check("nobody else can claim it", db.rpc("claim_submission", claim).execute().data["submission"] is None)

        conn.execute("update submissions set grading_started_at = now() - interval '10 minutes' where id = %s", (submission,))
        second = db.rpc("claim_submission", claim).execute().data["submission"]
        check("a dead worker's claim is taken over", second and second["id"] == submission and second["token"] != first["token"])

        finish = {
            "p_submission_id": submission, "p_judge_model": "m", "p_passed": True, "p_reason": PASSED,
            "p_input_tokens": 1, "p_output_tokens": 1,
        }
        late = db.rpc("finish_submission", {**finish, "p_token": first["token"]}).execute().data
        check("the old worker's verdict is ignored", late["saved"] is False and points(juliet) == 0)
        late = db.rpc("record_submission_error", {
            "p_submission_id": submission, "p_token": first["token"], "p_judge_model": "m", "p_code": "provider_timeout",
            "p_input_tokens": 0, "p_output_tokens": 0, "p_retry_seconds": 1,
        }).execute().data
        check("so is its error", late["saved"] is False and field(submission, "status") == "grading")
        check("the current worker's verdict counts",
              db.rpc("finish_submission", {**finish, "p_token": second["token"]}).execute().data["saved"] and points(juliet) == 200)
        check("a verdict can't be saved twice",
              db.rpc("finish_submission", {**finish, "p_token": second["token"]}).execute().data["saved"] is False)

        kilo = new_team("Kilo")
        submission = submitted(kilo, bot_a)
        db.rpc("claim_submission", claim).execute()
        conn.execute("update submissions set attempts = 3, grading_started_at = now() - interval '10 minutes' where id = %s", (submission,))
        check("a claim out of attempts isn't taken", db.rpc("claim_submission", claim).execute().data["submission"] is None)
        check("it waits for an organiser",
              field(submission, "status") == "error" and field(submission, "reason") == "Grading error: worker_lost")

        lima = new_team("Lima")
        for _ in range(8):
            submitted(lima, bot_a)
        with ThreadPoolExecutor(8) as pool:
            claims = list(pool.map(lambda _: db.rpc("claim_submission", claim).execute().data["submission"], range(8)))
        ids = [c["id"] for c in claims if c]
        check("simultaneous workers never share a submission", len(ids) == len(set(ids)))
        clear_queue()
        conn.execute("update submissions set grading_token = null where team_id = %s", (lima,))

        mike = new_team("Mike")
        for _ in range(6):
            submitted(mike, bot_a)
        with ThreadPoolExecutor(6) as pool:
            list(pool.map(lambda _: judging.grade_next(db, judge(mock.passing([1]))), range(6)))
        check("simultaneous passes pay once", points(mike) == 200 and one(
            "select count(*) from submissions where team_id = %s and points_awarded > 0", mike,
        ) == 1)
        check("all of them were graded", one(
            "select count(*) from submissions where team_id = %s and status = 'passed'", mike,
        ) == 6)

        # ---------- Regrading ----------

        november = new_team("November")
        conversation = chat(november, bot_a)
        submission = submit(november, conversation).json()["id"]
        check("a submission waiting in the queue can't be regraded", regrade(submission).status_code == 409)
        judging.grade_next(db, judge(mock.passing([1])))
        as_team(november)
        check("a team can't regrade", client.post(f"/api/admin/submissions/{submission}/regrade").status_code == 403)
        check("the refused regrade changed nothing", field(submission, "status") == "passed")

        r = regrade(submission)
        check("an organiser can regrade", r.status_code == 200 and r.json()["status"] == "pending")
        check("the response has no reason or points yet", r.json()["reason"] is None and r.json()["pointsAwarded"] is None)
        check("it's audited", one(
            "select count(*) from audit_log where action = 'regrade_submission' and target_id = %s and admin_id = %s",
            submission, admin_id,
        ) == 1)
        check("points stay until the new verdict", points(november) == 200)
        check("the regrade can't be stacked", regrade(submission).status_code == 409)
        judging.grade_next(db, judge(mock.passing([1])))
        check("pass to pass doesn't pay twice", points(november) == 200 and field(submission, "points_awarded") == 200)
        check("the run says it was a regrade", one(
            "select triggered_by from judge_runs where submission_id = %s order by id desc limit 1", submission,
        ) == "admin_regrade")

        regrade(submission)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("pass to fail takes the points back", points(november) == 0 and field(submission, "points_awarded") == 0)
        regrade(submission)
        judging.grade_next(db, judge(mock.passing([1])))
        check("fail to pass pays once", points(november) == 200 and field(submission, "points_awarded") == 200)

        check("a password attempt can't be regraded", regrade("answer-1").status_code == 409)
        check("an unknown submission is 404", regrade(uuid.uuid4()).status_code == 404)
        check("a bad ID is 404", regrade("nonsense").status_code == 404)

        # The points move to the team's next pass, and the first holder's are gone.
        oscar = new_team("Oscar")
        first_pass = submitted(oscar, bot_a)
        judging.grade_next(db, judge(mock.passing([1])))
        later_pass = submitted(oscar, bot_a)
        judging.grade_next(db, judge(mock.passing([1])))
        regrade(first_pass)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("the points move to the next pass", points(oscar) == 200 and field(later_pass, "points_awarded") == 200)
        check("the score now names that pass", str(one(
            "select submission_id from scores where team_id = %s and bot_id = %s", oscar, bot_a,
        )) == later_pass)
        regrade(first_pass)
        judging.grade_next(db, judge(mock.passing([1])))
        check("passing again doesn't pay twice", points(oscar) == 200 and field(first_pass, "points_awarded") == 0)

        # A pass that earned nothing has no points to take back.
        regrade(first_pass)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("failing a pass that held nothing changes nothing", points(oscar) == 200)

        # An earned completion goes with the solve that earned it.
        papa = new_team("Papa")
        solve_a = submitted(papa, bot_a)
        judging.grade_next(db, judge(mock.passing([1])))
        submitted(papa, bot_b)
        judging.grade_next(db, judge(mock.passing([1])))
        check("two solves complete the level", completed(papa, 2))
        regrade(solve_a)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("losing a solve removes the earned completion", not completed(papa, 2) and points(papa) == 200)
        regrade(solve_a)
        judging.grade_next(db, judge(mock.passing([1])))
        check("earning it back restores the completion", completed(papa, 2))

        # An organiser's grant isn't the worker's to remove.
        quebec = new_team("Quebec")
        conn.execute(
            "insert into level_completions (team_id, level, source, granted_by) values (%s, 2, 'admin_override', %s)",
            (quebec, admin_id),
        )
        solve = submitted(quebec, bot_a)
        judging.grade_next(db, judge(mock.passing([1])))
        regrade(solve)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("an organiser's grant stays", completed(quebec, 2))

        # A score from an organiser or a password stays when a judge pass is failed.
        romeo = new_team("Romeo")
        conn.execute("insert into scores (team_id, bot_id, points, source) values (%s, %s, 50, 'admin')", (romeo, bot_a))
        solve = submitted(romeo, bot_a)
        judging.grade_next(db, judge(mock.passing([1])))
        check("an existing score isn't replaced", points(romeo) == 50 and field(solve, "points_awarded") == 0)
        regrade(solve)
        judging.grade_next(db, judge(mock.failing(ReasonCode.REFUSED)))
        check("an organiser's score isn't taken back", points(romeo) == 50)

        # An error can be rescued.
        sierra = new_team("Sierra")
        stuck = submitted(sierra, bot_a)
        judging.grade_next(db, judge(mock.refused()))
        r = regrade(stuck)
        check("an organiser can regrade an error", r.status_code == 200 and field(stuck, "attempts") == 0)
        judging.grade_next(db, judge(mock.passing([1])))
        check("the rescued submission scores", field(stuck, "status") == "passed" and points(sierra) == 200)

        # ---------- The judging switch ----------

        clear_queue()
        conn.execute("update judge_settings set backoff_until = null, deferrals = 0")
        as_admin()
        r = client.get("/api/admin/judging")
        check("judging starts on", r.status_code == 200 and r.json()["enabled"] is True)
        check("the switch has only its fields",
              set(r.json()) == {"enabled", "pausedUntil", "updatedAt", "waiting", "needsOrganiser"})

        switch_team = new_team("Switch")
        as_team(switch_team)
        check("a team can't read the switch", client.get("/api/admin/judging").status_code == 403)
        check("a team can't turn judging off", client.put("/api/admin/judging", json={"enabled": False}).status_code == 403)
        check("so it's still on", one("select enabled from judge_settings"))
        app.dependency_overrides.pop(get_session)
        check("signed out can't read it", client.get("/api/admin/judging").status_code == 401)
        check("signed out can't change it", client.put("/api/admin/judging", json={"enabled": False}).status_code == 401)
        check("so it's still on after that", one("select enabled from judge_settings"))

        def set_judging(enabled):
            as_admin()
            return client.put("/api/admin/judging", json={"enabled": enabled})

        audits = lambda: one("select count(*) from audit_log where action = 'set_judging'")
        r = set_judging(False)
        check("an organiser can turn judging off", r.status_code == 200 and r.json()["enabled"] is False)
        check("it's saved", one("select enabled from judge_settings") is False)
        check("turning it off is audited", one(
            "select details from audit_log where action = 'set_judging' order by id desc limit 1",
        ) == {"before": {"enabled": True}, "after": {"enabled": False}} and audits() == 1)
        set_judging(False)
        check("the same state again isn't audited", audits() == 1)
        check("a bad value is refused", set_judging("sometimes").status_code == 422 and one("select enabled from judge_settings") is False)

        # Off: submissions are accepted and wait, and nothing reaches the judge.
        waiting = [submitted(switch_team, bot_a) for _ in range(3)]
        check("submissions are accepted while off", all(field(s, "status") == "pending" for s in waiting))
        idle = MockJudgeProvider([mock.passing([1])] * 10)
        check("the worker takes nothing while off", judging.grade_next(db, judge(provider=idle)) is False)
        check("nothing is sent to the judge", idle.requests == [])
        conn.execute(
            "update submissions set status = 'grading', grading_started_at = now() - interval '1 hour',"
            " grading_token = gen_random_uuid(), attempts = 1 where id = %s", (waiting[0],),
        )
        check("a dead worker's claim isn't taken while off", judging.grade_next(db, judge(provider=idle)) is False)
        conn.execute(
            "update submissions set status = 'pending', grading_started_at = null, grading_token = null, attempts = 0"
            " where id = %s", (waiting[0],),
        )

        async def worker_while_off():
            stopping = asyncio.Event()
            with patch.object(judging, "POLL_SECONDS", 0.01):
                task = asyncio.create_task(judging.run_worker(db, judge(provider=idle), stopping))
                await asyncio.sleep(0.3)
                stopping.set()
                await asyncio.wait_for(task, 5)

        asyncio.run(worker_while_off())
        check("the background worker sends nothing while off", idle.requests == [])
        check("nothing is lost or failed while off", [field(s, "status") for s in waiting] == ["pending"] * 3)
        check("no runs are recorded while off", one(
            "select count(*) from judge_runs where submission_id = any(%s::uuid[])", waiting,
        ) == 0)
        conversation = one("select conversation_id from submissions where id = %s", waiting[0])
        view = team_view(switch_team, conversation)
        check("the team sees grading while off", view["status"] == "grading")
        as_admin()
        check("organisers see them waiting", client.get("/api/admin/judging").json()["waiting"] == 3)
        items = client.get("/api/admin/submissions", params={"teamId": switch_team}).json()["items"]
        check("organisers see the real status", {s["status"] for s in items} == {"pending"})

        # On: the queue drains in order, each graded exactly once.
        r = set_judging(True)
        check("an organiser can turn judging back on", r.status_code == 200 and r.json()["enabled"] is True and audits() == 2)
        drain = MockJudgeProvider([mock.passing([1])] * 10)
        graded = 0
        while judging.grade_next(db, judge(provider=drain)):
            graded += 1
        check("switching on grades every waiting submission", graded == 3 and len(drain.requests) == 3)
        check("each is graded once", one(
            "select count(*) from judge_runs where submission_id = any(%s::uuid[])", waiting,
        ) == 3)
        order = [str(row[0]) for row in conn.execute(
            "select id from submissions where id = any(%s::uuid[]) order by graded_at", (waiting,),
        ).fetchall()]
        check("in the order they were submitted", order == waiting)
        check("all of them passed", [field(s, "status") for s in waiting] == ["passed"] * 3)
        check("and paid once", points(switch_team) == 200)
        check("a second drain finds nothing", judging.grade_next(db, judge(provider=drain)) is False and len(drain.requests) == 3)

        # ---------- Rate limits defer instead of failing ----------

        limited_team = new_team("Limited")
        queue = [submitted(limited_team, bot_a) for _ in range(3)]
        limited = MockJudgeProvider([mock.rate_limited()] * 10)
        check("a rate limit is handled", judging.grade_next(db, judge(provider=limited)) is True)
        check("it isn't a fail", field(queue[0], "status") == "error" and field(queue[0], "graded_at") is None)
        check("it says it was deferred", field(queue[0], "reason") == "Grading deferred: provider_rate_limited")
        check("it waits to be retried", field(queue[0], "next_attempt_at") is not None)
        check("it didn't use an attempt", field(queue[0], "attempts") == 0)
        delay = one("select extract(epoch from backoff_until - now()) from judge_settings")
        check("grading pauses for about 30 seconds", 25 < delay <= 30)
        check("the rest of the queue isn't sent while paused", judging.grade_next(db, judge(provider=limited)) is False)
        check("so a busy queue makes one call, not one per submission", len(limited.requests) == 1)
        view = team_view(limited_team, one("select conversation_id from submissions where id = %s", queue[0]))
        check("the team sees grading, with no details", view["status"] == "grading" and view["reason"] is None)
        as_admin()
        check("organisers see the pause", client.get("/api/admin/judging").json()["pausedUntil"] is not None)

        async def worker_rate_limited():
            stopping = asyncio.Event()
            conn.execute("update judge_settings set backoff_until = null")
            with patch.object(judging, "POLL_SECONDS", 0.01):
                task = asyncio.create_task(judging.run_worker(db, judge(provider=spin), stopping))
                await asyncio.sleep(0.5)
                stopping.set()
                await asyncio.wait_for(task, 5)

        spin = MockJudgeProvider([mock.rate_limited()] * 100)
        asyncio.run(worker_rate_limited())
        check("a rate-limited worker doesn't spin: one call in half a second", len(spin.requests) == 1)

        delays = []
        for _ in range(7):
            conn.execute("update judge_settings set backoff_until = null")
            conn.execute("update submissions set next_attempt_at = now() - interval '1 second' where status = 'error' and team_id = %s", (limited_team,))
            judging.grade_next(db, judge(mock.rate_limited()))
            delays.append(round(one("select extract(epoch from backoff_until - now()) from judge_settings")))
        check("the delay grows and stops at 10 minutes", delays == [120, 240, 480, 600, 600, 600, 600])
        check("many deferrals never fail anything", one(
            "select count(*) from submissions where team_id = %s and status in ('failed', 'passed')", limited_team,
        ) == 0)
        check("nor use up attempts, so they never need an organiser", one(
            "select count(*) from submissions where team_id = %s and status = 'error'"
            " and (next_attempt_at is null or attempts > 0)", limited_team,
        ) == 0 and field(queue[0], "status") == "error")
        check("each deferral is recorded with no verdict", one(
            "select count(*) from judge_runs where submission_id = any(%s::uuid[]) and verdict is null"
            " and error = 'provider_rate_limited'", queue,
        ) == 9)

        conn.execute("update judge_settings set backoff_until = null, deferrals = 0")
        make_due(queue[0])
        judging.grade_next(db, judge(mock.rate_limited(300)))
        delay = one("select extract(epoch from backoff_until - now()) from judge_settings")
        check("a provider's own longer delay is honoured", 295 < delay <= 300)
        conn.execute("update judge_settings set backoff_until = null, deferrals = 0")
        conn.execute("update submissions set next_attempt_at = now() - interval '1 second' where team_id = %s and status = 'error'", (limited_team,))
        judging.grade_next(db, judge(mock.rate_limited(99999)))
        delay = one("select extract(epoch from backoff_until - now()) from judge_settings")
        check("but never more than an hour", 3595 < delay <= 3600)

        for behaviour, code in ((mock.unavailable(), "provider_unavailable"), (mock.timeout(), "provider_timeout")):
            conn.execute("update judge_settings set backoff_until = null")
            conn.execute("update submissions set next_attempt_at = now() - interval '1 second' where team_id = %s and status = 'error'", (limited_team,))
            judging.grade_next(db, judge(behaviour))
            check(f"{code} defers too", one(
                "select count(*) from submissions where team_id = %s and reason = %s", limited_team, "Grading deferred: " + code,
            ) == 1 and one("select backoff_until is not null from judge_settings"))

        conn.execute("update judge_settings set backoff_until = null")
        conn.execute("update submissions set next_attempt_at = now() - interval '1 second' where team_id = %s and status = 'error'", (limited_team,))
        judging.grade_next(db, judge(mock.passing([1])))
        check("an answer from the judge ends the pause", one(
            "select deferrals = 0 and backoff_until is null from judge_settings",
        ))
        while judging.grade_next(db, judge(mock.passing([1]))):
            pass
        check("deferred submissions are graded once the judge is back",
              [field(s, "status") for s in queue] == ["passed"] * 3 and points(limited_team) == 200)

        # Switched off mid-pause, nothing is sent even after the pause ends.
        set_judging(False)
        late = submitted(limited_team, bot_b)
        conn.execute("update judge_settings set backoff_until = null")
        check("off still wins once a pause ends", judging.grade_next(db, judge()) is False and field(late, "status") == "pending")
        set_judging(True)
        judging.grade_next(db, judge(mock.passing([1])))
        check("and grading resumes when it's back on", field(late, "status") == "passed")

                # ---------- Nothing private in any response ----------

        as_team(sierra)
        responses = [
            client.get("/api/submissions"),
            client.get(f"/api/bots/{bot_a}"),
            client.get("/api/me/dashboard"),
            client.get("/api/me"),
            client.get("/api/levels"),
        ]
        for r in responses:
            check(f"{r.request.url.path} says nothing about the switch",
                  not any(word in r.text for word in ("pausedUntil", "needsOrganiser", "deferred", "judging", "pending")))
        as_admin()
        responses.append(client.get("/api/admin/submissions"))
        for r in responses:
            check(f"{r.request.url.path} answers", r.status_code == 200)
            check(f"{r.request.url.path} has no rubric or prompt", "PRIVATE" not in r.text)
        check("no reason stores a rubric", one(
            "select count(*) from judge_runs where coalesce(reason, '') || coalesce(error, '') like '%%PRIVATE%%'"
        ) == 0)
        config = db.rpc("get_judge_bot_config", {"p_bot_id": bot_a}).execute().data
        check("the judge's config has no prompt or password",
              set(config) == {"status", "id", "level", "check_type", "win_rubric", "enabled"})

        # ---------- Who can call the functions ----------

        for role in ("anon", "authenticated"):
            for name, sql in FUNCTIONS.items():
                conn.execute(f"set role {role}")
                try:
                    conn.execute(sql)
                    refused = False
                except psycopg.errors.InsufficientPrivilege:
                    refused = True
                finally:
                    conn.execute("reset role")
                check(f"{role} can't run {name}", refused)
        conn.execute("set role service_role")
        try:
            for sql in FUNCTIONS.values():
                conn.execute(sql)
        finally:
            conn.execute("reset role")
        check("the backend can run them all", True)

        # ---------- Re-running ----------

        total = one("select count(*) from submissions")
        conn.execute((BACKEND.parent / "supabase/judge.sql").read_text())
        conn.execute((BACKEND.parent / "supabase/admin.sql").read_text())
        check("re-running the files keeps the data", one("select count(*) from submissions") == total)
        check("a real row still reads back", field(stuck, "status") == "passed")

        # ---------- The worker, end to end ----------

        tango = new_team("Tango")
        submission = submitted(tango, bot_a)

        async def run_once():
            stopping = asyncio.Event()
            task = asyncio.create_task(judging.run_worker(db, judge(mock.passing([1])), stopping))
            for _ in range(100):
                if field(submission, "status") == "passed":
                    break
                await asyncio.sleep(0.05)
            stopping.set()
            await asyncio.wait_for(task, 5)

        asyncio.run(run_once())
        check("the background worker grades a submission", field(submission, "status") == "passed" and points(tango) == 200)

        # Through the real app startup, with the mock judge development runs on.
        uniform = new_team("Uniform")
        conversation = new_conversation(uniform, bot_a, ("user", "hi"), ("assistant", "[[SYNTHETIC:QUALIFYING]]"))
        env = {"ENVIRONMENT": "development", "JUDGE_PROVIDER": "mock"}
        with patch.dict(os.environ, env), patch.object(judging, "get_db", lambda: db), patch.object(judging, "POLL_SECONDS", 0.05):
            with TestClient(app, raise_server_exceptions=False) as running:
                as_team(uniform)
                r = running.post(f"/api/conversations/{conversation}/submit")
                check("the submit answers at once", r.status_code == 200 and r.json()["status"] in ("pending", "grading"))
                for _ in range(100):
                    view = running.get(f"/api/conversations/{conversation}").json()["submission"]
                    if view["status"] == "passed":
                        break
                    time.sleep(0.05)
                check("the app's own worker grades it", view["status"] == "passed" and view["pointsAwarded"] == 200)
        check("the worker stops with the app", judging._task is None)

        class BrokenDb:
            def rpc(self, name, params):
                raise RuntimeError("postgres://secret")

        async def survives():
            stopping = asyncio.Event()
            task = asyncio.create_task(judging.run_worker(BrokenDb(), judge(), stopping))
            await asyncio.sleep(0.05)
            alive = not task.done()
            stopping.set()
            await asyncio.wait_for(task, 5)
            return alive

        logs = []
        with patch.object(judging.logger, "error", lambda *args: logs.append(args)):
            check("a database outage doesn't kill the worker", asyncio.run(survives()))
        check("its error text isn't logged", logs and "postgres://secret" not in str(logs))


def test_start_rules():
    def allowed(**env):
        names = ("ENVIRONMENT", "JUDGE_PROVIDER", "JUDGE_BASE_URL", "JUDGE_API_KEY", "JUDGE_MODEL")
        clean = {name: "" for name in names}
        with patch.dict(os.environ, {**clean, **env}):
            return judging.worker_allowed()

    check("the mock runs in development", allowed(ENVIRONMENT="development") is True)
    check("a real provider runs anywhere", allowed(JUDGE_PROVIDER="openai_compatible") is True)
    check("the mock doesn't grade elsewhere, so submissions wait", allowed(ENVIRONMENT="staging") is False)
    check("nor with no environment set", allowed() is False)
    for env in ({"ENVIRONMENT": "production"}, {"ENVIRONMENT": " Production "}):
        try:
            allowed(**env)
            refused = False
        except RuntimeError as error:
            refused = "mock" in str(error)
        check(f"the mock refuses to start in {env['ENVIRONMENT']!r}", refused)

    # Bad settings stop the app at startup, not every grade.
    env = {"JUDGE_PROVIDER": "openai_compatible", "JUDGE_BASE_URL": "", "JUDGE_API_KEY": "", "JUDGE_MODEL": ""}
    with patch.dict(os.environ, env), patch.object(judging, "get_db", lambda: None):
        async def start():
            judging.start_judge_worker()
        try:
            asyncio.run(start())
            refused = False
        except ValueError:
            refused = True
    check("a real provider with no key refuses to start", refused)


def main():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        sys.exit("Set TEST_DATABASE_URL to a local Postgres. See the top of this file.")
    test_start_rules()
    name = f"judge_test_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(base)
    url = urlunsplit(parts._replace(path=f"/{name}"))
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"create database {name}")
    try:
        run_tests(url)
        print("All judge tests passed.")
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"drop database {name} with (force)")


if __name__ == "__main__":
    main()
