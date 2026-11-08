"""Tests the session cookie check and the development-only login.

Needs no database or keys, because it uses a small in-memory stand-in:
    python tests/test_dev_auth.py
"""

import importlib
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SECRET = "test-only-secret-that-is-long-enough-for-hs256"
TEAM_ID = str(uuid.uuid4())
DISABLED_ID = str(uuid.uuid4())
ADMIN_ID = str(uuid.uuid4())
# Set before importing the app, so that a local backend/.env can't change them.
os.environ.update(SESSION_SECRET=SECRET, DEV_FAKE_AUTH="false", ENVIRONMENT="development")

import app.main as main  # noqa: E402
from app.db import get_db  # noqa: E402


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _):
        return self

    def eq(self, column, value):
        self.rows = [row for row in self.rows if row[column] == value]
        return self

    def execute(self):
        self.data = self.rows
        return self


class FakeDb:
    def __init__(self):
        self.tables = {
            "teams": [
                {"id": TEAM_ID, "name": "Dev Team 1", "disabled": False},
                {"id": DISABLED_ID, "name": "Dev Team 2", "disabled": True},
            ],
            "admin_users": [{"id": ADMIN_ID, "display_name": "Dev Admin"}],
        }

    def table(self, name):
        return Query(self.tables[name])

    def rpc(self, name, params):
        # What GET /me and GET /admin/teams need, so signed-in users get past the role check.
        if name == "get_admin_teams":
            return Query({"status": "ok", "teams": []})
        return Query({"status": "ok", "id": params["p_team_id"], "name": "Dev Team 1", "tokens_used": 0})


db = FakeDb()


def check(name, passed):
    print(f"{'ok  ' if passed else 'FAIL'} {name}")
    if not passed:
        sys.exit(1)


def client_with(flag, environment="development"):
    os.environ.update(DEV_FAKE_AUTH=flag, ENVIRONMENT=environment)
    application = importlib.reload(main).app
    application.dependency_overrides[get_db] = lambda: db
    return TestClient(application)


def start_server(**env):
    """Imports the app in a new process, as starting the server does."""
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


def cookie(secret=SECRET, algorithm="HS256", expires_in=3600, **claims):
    expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return jwt.encode({**claims, "exp": expires}, secret, algorithm=algorithm)


def status(client, path, value):
    client.cookies.clear()
    if value is not None:
        client.cookies.set("session", value)
    return client.get(path).status_code


def test_flag():
    off = client_with("false").post("/api/dev/login", json={"team": "Dev Team 1"})
    check("dev login doesn't exist when the flag is off", off.status_code == 404)
    unset = client_with("").post("/api/dev/login", json={"team": "Dev Team 1"})
    check("dev login doesn't exist when the flag is empty", unset.status_code == 404)
    production = client_with("false", "production").post("/api/dev/login", json={"team": "Dev Team 1"})
    check("production with the flag off starts, with no dev login", production.status_code == 404)

    client = client_with("true")
    response = client.post("/api/dev/login", json={"team": "dev team 1"})
    check("dev login works when the flag is on", response.status_code == 200)
    check(
        "it returns the team's session",
        response.json() == {"role": "team", "teamId": TEAM_ID, "teamName": "Dev Team 1"},
    )
    attributes = response.headers["set-cookie"].lower()
    check("the cookie is httpOnly, lax and 12 hours long", all(
        part in attributes for part in ("session=", "httponly", "samesite=lax", "path=/", "max-age=43200")
    ))
    check("the signed-in team passes the team check", client.get("/api/me").status_code == 200)

    claims = jwt.decode(client.cookies["session"], SECRET, algorithms=["HS256"])
    check("the claims are in proxy.ts's format", claims["role"] == "team" and claims["teamId"] == TEAM_ID)

    client.cookies.clear()
    admin = client.post("/api/dev/login", json={"team": "admin"})
    check("admin signs in", admin.json()["role"] == "admin" and admin.json()["username"] == "Dev Admin")
    check("the admin's ID is in the session", admin.json()["adminId"] == ADMIN_ID)
    client.cookies.clear()
    check("an unknown team is refused", client.post("/api/dev/login", json={"team": "Nobody"}).status_code == 404)
    check("no cookie is set for an unknown team", "session" not in client.cookies)
    disabled = client.post("/api/dev/login", json={"team": "Dev Team 2"})
    check("a disabled team can't use dev login", disabled.status_code == 401 and "session" not in client.cookies)


def test_startup():
    refused = start_server(DEV_FAKE_AUTH="true", ENVIRONMENT="production")
    check("the app refuses to start in production", refused.returncode != 0)
    check("the refusal is clear", "Refusing to start: DEV_FAKE_AUTH is on" in refused.stderr)
    check("the capital-letter spelling is refused", start_server(DEV_FAKE_AUTH="TRUE", ENVIRONMENT="Production").returncode != 0)
    check("other ways to say on are refused", start_server(DEV_FAKE_AUTH="1", ENVIRONMENT="production").returncode != 0)
    check("dev login needs a session secret", start_server(DEV_FAKE_AUTH="true", SESSION_SECRET="").returncode != 0)

    allowed = start_server(DEV_FAKE_AUTH="true", ENVIRONMENT="development")
    check("the app starts in development", allowed.returncode == 0)
    check("it warns loudly at startup", "DEV LOGIN IS ON" in allowed.stderr)
    quiet = start_server(DEV_FAKE_AUTH="false", ENVIRONMENT="development")
    check("no warning when it's off", quiet.returncode == 0 and "DEV LOGIN IS ON" not in quiet.stderr)


def test_cookies():
    client = client_with("false")
    team = {"role": "team", "teamId": TEAM_ID, "teamName": "Dev Team 1"}
    admin = {"role": "admin", "adminId": ADMIN_ID, "username": "Dev Admin"}

    check("a team cookie reaches team routes", status(client, "/api/me", cookie(**team)) == 200)
    check("an admin cookie reaches admin routes", status(client, "/api/admin/teams", cookie(**admin)) == 200)
    check("a team cookie gets 403 on admin routes", status(client, "/api/admin/teams", cookie(**team)) == 403)
    check("an admin cookie gets 403 on team routes", status(client, "/api/me", cookie(**admin)) == 403)

    check("no cookie gets 401", status(client, "/api/me", None) == 401)
    check("a bad signature gets 401", status(client, "/api/me", cookie(secret="wrong-secret-that-is-also-long-enough", **team)) == 401)
    check("an expired cookie gets 401", status(client, "/api/me", cookie(expires_in=-10, **team)) == 401)
    check("a cookie that never expires gets 401", status(client, "/api/me", jwt.encode(team, SECRET, algorithm="HS256")) == 401)
    check("an unsigned cookie gets 401", status(client, "/api/me", jwt.encode(team, "", algorithm="none")) == 401)
    check("another algorithm gets 401", status(client, "/api/me", cookie(algorithm="HS512", **team)) == 401)
    check("rubbish gets 401", status(client, "/api/me", "not-a-cookie") == 401)
    check("an unknown role gets 401", status(client, "/api/me", cookie(**{**team, "role": "owner"})) == 401)
    check("an ID that isn't a UUID gets 401", status(client, "/api/me", cookie(**{**team, "teamId": "t-01"})) == 401)
    check("an unknown team gets 401", status(client, "/api/me", cookie(**{**team, "teamId": str(uuid.uuid4())})) == 401)
    check("an admin who isn't in the table gets 401", status(client, "/api/admin/teams", cookie(**{**admin, "adminId": str(uuid.uuid4())})) == 401)
    disabled = {"role": "team", "teamId": DISABLED_ID, "teamName": "Dev Team 2"}
    check("a disabled team gets 401", status(client, "/api/me", cookie(**disabled)) == 401)

    os.environ["SESSION_SECRET"] = ""
    check("with no secret, every cookie gets 401", status(client, "/api/me", cookie(**team)) == 401)
    os.environ["SESSION_SECRET"] = SECRET


test_flag()
test_startup()
test_cookies()
print("All dev login tests passed.")
