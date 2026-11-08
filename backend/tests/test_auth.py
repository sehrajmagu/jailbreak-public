"""Tests sign-in and team accounts against a real Postgres, with a fake Supabase Auth.

Set TEST_DATABASE_URL to a local Postgres where you may create databases, then:
    pip install -r requirements-dev.txt
    python tests/test_auth.py

It makes a throwaway database, adds stand-ins for what Supabase provides, loads
the SQL files, and drops it afterwards. Don't point it at Supabase.
"""

import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import jwt
import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from supabase_auth.errors import AuthApiError, AuthWeakPasswordError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SECRET = "test-session-secret-" + "x" * 30
os.environ.update(
    SESSION_SECRET=SECRET, SUPABASE_URL="http://fake-supabase", SUPABASE_ANON_KEY="anon",
    DEV_FAKE_AUTH="false", ENVIRONMENT="production",
)

import app.routers.admin as admin_router  # noqa: E402,F401
import app.routers.auth as auth_router  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402

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

SQL_FILES = ("schema", "scoring", "conversations", "team_pages", "admin", "chat", "auth")
WRONG = "Wrong name or password."


def check(label, condition):
    print(("ok  " if condition else "FAIL"), label)
    assert condition, label


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.columns, self.filter = db, table, "*", None

    def select(self, columns):
        self.columns = columns
        return self

    def eq(self, column, value):
        self.filter = (column, value)
        return self

    def execute(self):
        sql = f"select {self.columns} from {self.table}"
        args = ()
        if self.filter:
            sql += f" where {self.filter[0]} = %s"
            args = (self.filter[1],)
        with psycopg.connect(self.db.url, autocommit=True, row_factory=dict_row) as conn:
            rows = conn.execute(sql, args).fetchall()
        return SimpleNamespace(data=[{key: str(value) if isinstance(value, uuid.UUID) else value
                                      for key, value in row.items()} for row in rows])


class Rpc:
    """Runs a Supabase rpc call as plain SQL, passing arguments by name as PostgREST does."""

    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        args = ", ".join(f"{key} => %({key})s" for key in self.params)
        values = {key: Jsonb(value) if isinstance(value, dict) else value for key, value in self.params.items()}
        with psycopg.connect(self.db.url, autocommit=True) as conn:
            row = conn.execute(f"select {self.name}({args})", values).fetchone()
        return SimpleNamespace(data=row[0])


class FakeAuthAdmin:
    """Supabase Auth's admin API, keeping users in memory and in auth.users."""

    def __init__(self, url):
        self.url = url
        self.users = {}
        self.refuse_create = None
        self.refuse_update = None

    def add(self, email, password):
        with psycopg.connect(self.url, autocommit=True) as conn:
            user_id = str(conn.execute("insert into auth.users default values returning id").fetchone()[0])
        self.users[user_id] = {"email": email, "password": password}
        return user_id

    def get_user_by_id(self, user_id):
        return SimpleNamespace(user=SimpleNamespace(email=self.users[user_id]["email"]))

    def create_user(self, attributes):
        if self.refuse_create:
            raise self.refuse_create
        self.last_created = attributes
        return SimpleNamespace(user=SimpleNamespace(id=self.add(attributes["email"], attributes["password"])))

    def update_user_by_id(self, user_id, attributes):
        if self.refuse_update:
            raise self.refuse_update
        self.users[user_id].update(attributes)

    def delete_user(self, user_id):
        del self.users[user_id]
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("delete from auth.users where id = %s", (user_id,))


class FakeDb:
    def __init__(self, url):
        self.url = url
        self.auth = SimpleNamespace(admin=FakeAuthAdmin(url))

    def rpc(self, name, params):
        return Rpc(self, name, params)

    def table(self, name):
        return Query(self, name)


class FakeSupabase:
    """Stands in for httpx in routers/auth.py: Supabase Auth's password endpoint."""

    def __init__(self, admin):
        self.admin = admin
        self.calls = []
        self.status = None
        self.headers = {}
        self.raises = None

    def post(self, url, headers, json, timeout):
        self.calls.append((url, headers, json))
        if self.raises:
            raise self.raises
        if self.status:
            return SimpleNamespace(status_code=self.status, headers=self.headers)
        right = any(u["email"] == json["email"] and u["password"] == json["password"] for u in self.admin.users.values())
        return SimpleNamespace(status_code=200 if right else 400, headers={})


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def new_client(host="10.0.0.1"):
    return TestClient(app, base_url="https://testserver", client=(host, 50000), raise_server_exceptions=False)


def run_tests(url):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SUPABASE_STAND_INS)
        for name in SQL_FILES:
            conn.execute((BACKEND.parent / f"supabase/{name}.sql").read_text())
        conn.execute((BACKEND.parent / "supabase/auth.sql").read_text())
        print("ok   auth.sql is safe to re-run")

        def one(sql, *args):
            return conn.execute(sql, args).fetchone()[0]

        db = FakeDb(url)
        admin_api = db.auth.admin
        supabase = FakeSupabase(admin_api)
        auth_router.httpx = supabase
        app.dependency_overrides[get_db] = lambda: db

        def new_team(name, password="team-password", disabled=False):
            team = admin_api.add(f"{name.lower().replace(' ', '-')}@example.com", password)
            conn.execute("insert into teams (id, name, disabled) values (%s, %s, %s)", (team, name, disabled))
            return team

        def new_admin(name, password="admin-password"):
            admin = admin_api.add(f"{name.lower().replace(' ', '-')}@example.com", password)
            conn.execute("insert into admin_users (id, display_name) values (%s, %s)", (admin, name))
            return admin

        def sign_in(client, team, password="team-password"):
            return client.post("/api/auth/login", json={"team": team, "password": password})

        def sign_in_admin(client, username, password="admin-password"):
            return client.post("/api/auth/admin/login", json={"username": username, "password": password})

        def attempts(account):
            return one("select count(*) from login_attempts where account = %s", account)

        alpha = new_team("Team Alpha")
        organiser = new_admin("Organiser")

        # ---------- Signing in ----------
        client = new_client()
        r = sign_in(client, "Team Alpha")
        check("team signs in", r.status_code == 200)
        check("response is the session", r.json() == {"role": "team", "teamId": alpha, "teamName": "Team Alpha"})
        cookie = r.headers["set-cookie"].lower()
        check("cookie is HttpOnly", "httponly" in cookie)
        check("cookie is Secure outside development", "secure" in cookie)
        check("cookie is SameSite=Lax", "samesite=lax" in cookie)
        claims = jwt.decode(client.cookies["session"], SECRET, algorithms=["HS256"])
        check("cookie has the agreed claims", (claims["role"], claims["teamId"], claims["teamName"]) == ("team", alpha, "Team Alpha"))
        check("cookie expires", claims["exp"] > 0)
        check("the session endpoint knows the team", client.get("/api/auth/session").json()["teamId"] == alpha)
        check("the cookie opens team pages", client.get("/api/me").status_code == 200)
        check("the cookie doesn't open admin pages", client.get("/api/admin/teams").status_code == 403)

        r = client.post("/api/auth/logout")
        check("logout responds 204", r.status_code == 204)
        check("logout clears the cookie", "session" not in client.cookies)
        check("no session after logout", client.get("/api/auth/session").json() is None)
        check("no session with a bad cookie", new_client().get("/api/auth/session", cookies={"session": "junk"}).json() is None)

        os.environ["ENVIRONMENT"] = "development"
        local = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        r = sign_in(local, "Team Alpha")
        check("development cookies work over plain HTTP", "secure" not in r.headers["set-cookie"].lower())
        os.environ["ENVIRONMENT"] = "production"

        check("names ignore case and spaces", sign_in(new_client(), "  team ALPHA ").status_code == 200)
        check("teams can sign in by ID", sign_in(new_client(), alpha).status_code == 200)

        r = sign_in_admin(new_client(), "organiser")
        check("admin signs in", r.status_code == 200 and r.json()["username"] == "Organiser")
        admin_client = new_client()
        sign_in_admin(admin_client, "Organiser")
        check("the cookie opens admin pages", admin_client.get("/api/admin/teams").status_code == 200)
        check("the service role key is never used for the password check", all(
            call[1] == {"apikey": "anon"} for call in supabase.calls))

        # ---------- Refusals ----------
        wrong = sign_in(new_client("10.0.0.2"), "Team Alpha", "bad")
        unknown = sign_in(new_client("10.0.0.2"), "Nobody", "bad")
        check("a wrong password gets 401", wrong.status_code == 401 and wrong.json()["error"]["message"] == WRONG)
        check("an unknown name gets the same answer", unknown.status_code == 401 and unknown.json() == wrong.json())
        check("no cookie on a refusal", "set-cookie" not in wrong.headers)
        before = len(supabase.calls)
        sign_in(new_client("10.0.0.2"), "Nobody Else", "bad")
        check("an unknown name still calls Supabase", len(supabase.calls) == before + 1)
        check("a team's password doesn't sign in an admin", sign_in_admin(new_client("10.0.0.2"), "Team Alpha", "team-password").status_code == 401)

        new_team("Team Off", disabled=True)
        r = sign_in(new_client(), "Team Off")
        check("a disabled team is refused", r.status_code == 401 and "not active" in r.json()["error"]["message"])
        r = sign_in(new_client(), "Team Off", "bad")
        check("only the right password learns it's disabled", r.json()["error"]["message"] == WRONG)

        before = attempts(f"team:{alpha}")
        check("a huge name is refused", sign_in(new_client(), "x" * 101).status_code == 401)
        check("a huge password is refused", sign_in(new_client(), "Team Alpha", "x" * 201).status_code == 401)
        check("and neither is saved", attempts(f"team:{alpha}") == before and attempts("team:" + "x" * 101) == 0)

        # ---------- Wrong passwords ----------
        guarded = new_team("Team Guarded")
        attacker = new_client("10.0.1.1")
        for number in range(1, 11):
            check(f"wrong password {number} gets 401", sign_in(attacker, "Team Guarded", "bad").status_code == 401)
        before = len(supabase.calls)
        r = sign_in(attacker, "Team Guarded", "bad")
        wait = r.json()["error"].get("retryAfterSeconds")
        check("wrong password 11 gets 429", r.status_code == 429 and r.json()["error"]["code"] == "rate_limited")
        check("retryAfterSeconds is in the body", isinstance(wait, int) and 1 <= wait <= 300)
        check("Retry-After matches", r.headers["retry-after"] == str(wait))
        check("the message says when to retry", str(wait) in r.json()["error"]["message"])
        check("the right password is refused too", sign_in(attacker, "Team Guarded").status_code == 429)
        check("and Supabase isn't called", len(supabase.calls) == before)
        check("another client can still sign in, so rivals can't lock a team out", sign_in(new_client("10.0.1.2"), "Team Guarded").status_code == 200)
        check("the same client can use another account", sign_in(attacker, "Team Alpha").status_code == 200)
        conn.execute("update login_attempts set created_at = created_at - interval '301 seconds' where client = '10.0.1.1'")
        check("it passes after the window", sign_in(attacker, "Team Guarded").status_code == 200)
        check("signing in clears the count", one(
            "select count(*) from login_attempts where account = %s and client = '10.0.1.1'", f"team:{guarded}") == 0)

        spray = new_client("10.0.1.3")
        for _ in range(10):
            sign_in(spray, "No Such Team", "bad")
        check("guesses at unknown names are limited too", sign_in(spray, "No Such Team", "bad").status_code == 429)

        admin_attacker = new_client("10.0.1.4")
        for _ in range(10):
            sign_in_admin(admin_attacker, "Organiser", "bad")
        check("admin guesses are limited too", sign_in_admin(admin_attacker, "Organiser", "bad").status_code == 429)

        os.environ["LOGIN_MAX_FAILURES"] = "1"
        limited = new_client("10.0.1.5")
        sign_in(limited, "Team Alpha", "bad")
        check("the limit comes from the setting", sign_in(limited, "Team Alpha", "bad").status_code == 429)
        del os.environ["LOGIN_MAX_FAILURES"]

        conn.execute("update login_attempts set created_at = now() - interval '2 days' where client = '10.0.1.3'")
        sign_in(new_client("10.0.1.6"), "Team Alpha", "bad")
        check("old attempts are cleaned up", one("select count(*) from login_attempts where client = '10.0.1.3'") == 0)

        # Held at the same point, because these transactions rarely overlap by chance.
        def parallel():
            def attempt(_):
                return Rpc(db, "begin_login_attempt", {
                    "p_account": "team:parallel", "p_client": "c", "p_max_failures": 5, "p_window_seconds": 300,
                }).execute().data["status"]

            with psycopg.connect(url) as holder:
                holder.execute("lock table login_attempts in access exclusive mode")
                with ThreadPoolExecutor(20) as pool:
                    results = pool.map(attempt, range(20))
                    time.sleep(1)
                    holder.commit()
                    return list(results)

        results = parallel()
        check("20 parallel guesses never exceed the limit", results.count("ok") == 5 and results.count("too_many_logins") == 15)

        # ---------- When the check itself fails ----------
        records = Records()
        auth_router.logger.addHandler(records)
        faults = new_team("Team Faults")
        faulty = new_client("10.0.2.1")

        supabase.status = 500
        r = sign_in(faulty, "Team Faults", "PRIVATE-PASSWORD")
        check("a Supabase fault is a 500, not a wrong password", r.status_code == 500)
        check("and isn't counted as a guess", attempts(f"team:{faults}") == 0)
        check("and is logged", any("500" in line for line in records.lines))

        supabase.status, supabase.headers = 429, {"retry-after": "42"}
        r = sign_in(faulty, "Team Faults", "PRIVATE-PASSWORD")
        check("Supabase's own limit gives 429", r.status_code == 429 and r.json()["error"]["retryAfterSeconds"] == 42)
        check("and says it isn't a wrong password", r.json()["error"]["message"] != WRONG)
        check("and isn't counted as a guess", attempts(f"team:{faults}") == 0)
        supabase.headers = {}
        check("a default wait when Supabase gives none", sign_in(faulty, "Team Faults").json()["error"]["retryAfterSeconds"] == 60)
        supabase.status = None

        supabase.raises = RuntimeError("connection failed for PRIVATE-PASSWORD")
        r = sign_in(faulty, "Team Faults", "PRIVATE-PASSWORD")
        supabase.raises = None
        check("a network failure is a 500", r.status_code == 500 and attempts(f"team:{faults}") == 0)

        del os.environ["SUPABASE_ANON_KEY"]
        r = sign_in(faulty, "Team Faults")
        os.environ["SUPABASE_ANON_KEY"] = "anon"
        check("a missing anon key is a 500", r.status_code == 500)
        check("and the log names it", any("SUPABASE_ANON_KEY" in line for line in records.lines))

        del os.environ["SESSION_SECRET"]
        r = sign_in(faulty, "Team Faults")
        os.environ["SESSION_SECRET"] = SECRET
        check("a missing session secret is a 500 with no cookie", r.status_code == 500 and "set-cookie" not in r.headers)
        check("and the log names it", any("SESSION_SECRET" in line for line in records.lines))
        auth_router.logger.removeHandler(records)
        check("no password in the log", "PRIVATE-PASSWORD" not in " ".join(records.lines))
        check("no password in the attempts table", one(
            "select count(*) from login_attempts l where l::text like '%%PRIVATE%%'") == 0)

        # ---------- Creating teams ----------
        team_client = new_client()
        sign_in(team_client, "Team Alpha")
        check("teams can't create teams", team_client.post("/api/admin/teams", json={"name": "X", "password": "password123"}).status_code == 403)
        check("signed-out users can't create teams", new_client().post("/api/admin/teams", json={"name": "X", "password": "password123"}).status_code == 401)

        r = admin_client.post("/api/admin/teams", json={"name": "  Team New ", "password": "new-team-password"})
        check("an organiser creates a team", r.status_code == 200 and r.json()["name"] == "Team New")
        created = r.json()["id"]
        check("the response is an admin team", {"id", "name", "disabled", "tokensUsed", "createdAt"} <= set(r.json()))
        check("no password in the response", "new-team-password" not in r.text)
        check("the account's address can't reach anyone", admin_api.users[created]["email"].endswith("@example.com"))
        check("the account is confirmed", admin_api.last_created["email_confirm"] is True)
        check("the new team can sign in", sign_in(new_client(), "Team New", "new-team-password").status_code == 200)
        audit = one("select details::text from audit_log where action = 'create_team' and target_id = %s", created)
        check("creating a team is audited", "Team New" in audit)
        check("no password in the audit log", "new-team-password" not in audit)

        users = len(admin_api.users)
        r = admin_client.post("/api/admin/teams", json={"name": "team new", "password": "new-team-password"})
        check("a taken name gets 409", r.status_code == 409)
        check("and leaves no account behind", len(admin_api.users) == users)
        r = admin_client.post("/api/admin/teams", json={"name": "   ", "password": "new-team-password"})
        check("an empty name gets 422, with no account behind", r.status_code == 422 and len(admin_api.users) == users)
        r = admin_client.post("/api/admin/teams", json={"name": "x" * 61, "password": "new-team-password"})
        check("a long name gets 422, with no account behind", r.status_code == 422 and len(admin_api.users) == users)
        r = admin_client.post("/api/admin/teams", json={"name": "Team Short", "password": "short"})
        check("a short password gets 422", r.status_code == 422 and "8 to 72" in r.json()["error"]["message"])
        r = admin_client.post("/api/admin/teams", json={"name": "Team Long", "password": "é" * 37})
        check("a password over 72 bytes gets 422", r.status_code == 422)
        check("and neither makes an account", len(admin_api.users) == users)

        admin_api.refuse_create = AuthWeakPasswordError("weak", 422, [])
        r = admin_client.post("/api/admin/teams", json={"name": "Team Weak", "password": "password123"})
        check("a password Supabase calls weak gets 422", r.status_code == 422)
        admin_api.refuse_create = AuthApiError("down", 500, None)
        r = admin_client.post("/api/admin/teams", json={"name": "Team Down", "password": "password123"})
        admin_api.refuse_create = None
        check("a Supabase Auth failure gets 500", r.status_code == 500 and "down" not in r.text)
        check("and saves no team", one("select count(*) from teams where name in ('Team Weak', 'Team Down')") == 0)

        # ---------- Resetting passwords ----------
        resettable = new_team("Team Reset", "old-password")
        check("teams can't reset passwords", team_client.post(f"/api/admin/teams/{resettable}/reset-password").status_code == 403)
        locked_out = new_client("10.0.3.1")
        for _ in range(10):
            sign_in(locked_out, "Team Reset", "bad")
        check("the team is locked out from this client", sign_in(locked_out, "Team Reset", "old-password").status_code == 429)

        r = admin_client.post(f"/api/admin/teams/{resettable}/reset-password")
        temporary = r.json()["temporaryPassword"]
        check("an organiser resets a password", r.status_code == 200 and len(temporary) >= 16)
        check("the old password stops working", sign_in(new_client(), "Team Reset", "old-password").status_code == 401)
        check("a reset clears the lockout, and the new password works", sign_in(locked_out, "Team Reset", temporary).status_code == 200)
        audit = one("select details::text from audit_log where action = 'reset_team_password' and target_id = %s", resettable)
        check("a reset is audited", "password_reset" in audit)
        check("no password in the audit log", temporary not in audit)
        check("two resets give different passwords", admin_client.post(
            f"/api/admin/teams/{resettable}/reset-password").json()["temporaryPassword"] != temporary)

        r = admin_client.post(f"/api/admin/teams/{uuid.uuid4()}/reset-password")
        check("an unknown team gets 404", r.status_code == 404)
        check("a malformed ID gets 422", admin_client.post("/api/admin/teams/not-a-uuid/reset-password").status_code == 422)

        admin_api.refuse_update = AuthApiError("down", 500, None)
        r = admin_client.post(f"/api/admin/teams/{resettable}/reset-password")
        admin_api.refuse_update = None
        check("a Supabase Auth failure gets 500", r.status_code == 500 and "down" not in r.text)

        # ---------- Access ----------
        for function, call in (
            ("begin_login_attempt", "select begin_login_attempt('a', 'b', 1, 1)"),
            ("release_login_attempt", "select release_login_attempt(0, false)"),
            ("create_team", "select create_team(gen_random_uuid(), gen_random_uuid(), '')"),
            ("record_password_reset", "select record_password_reset(gen_random_uuid(), gen_random_uuid())"),
            ("the login_attempts table", "select * from login_attempts"),
        ):
            for role in ("anon", "authenticated"):
                conn.execute(f"set role {role}")
                try:
                    conn.execute(call)
                    check(f"{role} can't use {function}", False)
                except psycopg.errors.InsufficientPrivilege:
                    print(f"ok   {role} can't use {function}")
                finally:
                    conn.execute("reset role")
            conn.execute("set role service_role")
            conn.execute(call)
            conn.execute("reset role")
            print(f"ok   the service role can use {function}")

        # ---------- No fallback secret ----------
        session_ts = (BACKEND.parent / "lib/session.ts").read_text()
        check("the frontend has no fallback secret outside mock mode", "DEV_SECRET" not in session_ts
              and 'process.env.NEXT_PUBLIC_USE_MOCK === "true" ? "mock-only-secret" : null' in session_ts)
        check("the frontend doesn't log tokens", "console." not in session_ts)
        check("the route guard doesn't log", "console." not in (BACKEND.parent / "proxy.ts").read_text())


def main():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        sys.exit("Set TEST_DATABASE_URL to a local Postgres. See the top of this file.")
    name = f"auth_test_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(base)
    url = urlunsplit(parts._replace(path=f"/{name}"))
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"create database {name}")
    try:
        run_tests(url)
        print("All auth tests passed.")
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"drop database {name} with (force)")


if __name__ == "__main__":
    main()
