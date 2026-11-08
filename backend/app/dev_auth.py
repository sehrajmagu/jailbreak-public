"""Development-only login, so endpoints can be built before the real login exists.

It signs anyone in with no password, so it must never run at the real event. Its
route is only added when DEV_FAKE_AUTH is on outside production, and the app refuses
to start if DEV_FAKE_AUTH is on in production.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from postgrest.exceptions import APIError
from pydantic import BaseModel
from supabase import Client

from app.db import get_db
from app.schemas import AdminSession, Session, TeamSession

logger = logging.getLogger(__name__)

# The same as lib/session.ts.
COOKIE_NAME = "session"
MAX_AGE_SECONDS = 60 * 60 * 12
# The admin row that scripts/dev_seed.py creates.
DEV_ADMIN_NAME = "Dev Admin"

WARNING = """
################################################################################
#  DEV LOGIN IS ON: anyone who can reach this server can sign in as any team   #
#  or as an admin, with no password. Never use this at the real event.         #
#  Set DEV_FAKE_AUTH=false to turn it off.                                     #
################################################################################"""


class DevLoginRequest(BaseModel):
    team: str


def sign_session(claims: Dict[str, Any]) -> str:
    """Signs the session cookie's value. Its claims are in the root README's format."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=MAX_AGE_SECONDS)
    return jwt.encode({**claims, "exp": expires}, os.environ["SESSION_SECRET"], algorithm="HS256")


def set_session_cookie(response: Response, claims: Dict[str, Any]) -> None:
    # Not `secure`, because this only runs in development, often over plain HTTP.
    response.set_cookie(
        COOKIE_NAME,
        sign_session(claims),
        max_age=MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
    )


def seed_needed(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No test account named {name}. Run scripts/dev_seed.py.")


def sign_in_admin(db: Client, response: Response) -> Session:
    rows = db.table("admin_users").select("id,display_name").eq("display_name", DEV_ADMIN_NAME).execute().data
    if not rows:
        raise seed_needed(DEV_ADMIN_NAME)
    admin = rows[0]
    set_session_cookie(response, {"role": "admin", "adminId": admin["id"], "username": admin["display_name"]})
    return AdminSession(admin_id=admin["id"], username=admin["display_name"])


def sign_in_team(db: Client, response: Response, name: str) -> Session:
    # Team names are unique whatever their case, as in the teams table.
    rows = db.table("teams").select("id,name,disabled").execute().data
    team = next((row for row in rows if row["name"].lower() == name.lower()), None)
    if team is None:
        raise seed_needed(name)
    if team["disabled"]:
        raise HTTPException(status_code=401, detail="This team is not active.")
    set_session_cookie(response, {"role": "team", "teamId": team["id"], "teamName": team["name"]})
    return TeamSession(team_id=team["id"], team_name=team["name"])


router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/login")
def dev_login(body: DevLoginRequest, response: Response, db: Client = Depends(get_db)) -> Session:
    """Signs in as a team by name, or as the test admin with "admin". No password."""
    name = body.team.strip()
    try:
        if name.lower() == "admin":
            return sign_in_admin(db, response)
        return sign_in_team(db, response, name)
    except APIError:
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")


def is_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("true", "1", "yes", "on")


def setup_dev_auth(app: FastAPI) -> None:
    """Adds the dev login if it's on, and stops the app if that isn't safe."""
    if not is_on("DEV_FAKE_AUTH"):
        return
    if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
        raise RuntimeError(
            "Refusing to start: DEV_FAKE_AUTH is on while ENVIRONMENT is production. "
            "Dev login signs anyone in with no password. Set DEV_FAKE_AUTH=false."
        )
    if not os.environ.get("SESSION_SECRET"):
        raise RuntimeError("Refusing to start: DEV_FAKE_AUTH is on, but SESSION_SECRET isn't set.")
    logger.warning(WARNING)
    app.include_router(router, prefix="/api")
