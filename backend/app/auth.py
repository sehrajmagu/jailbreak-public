import os
from typing import Any, Dict, Optional
from uuid import UUID

import jwt
from fastapi import Cookie, Depends, HTTPException, Request
from postgrest.exceptions import APIError
from supabase import Client

from app.db import get_db
from app.schemas import AdminSession, Session, TeamSession


def not_signed_in() -> HTTPException:
    return HTTPException(status_code=401, detail="Please sign in.")


def read_cookie(token: Optional[str]) -> Dict[str, Any]:
    """Returns the claims of a signed session cookie, or raises 401."""
    # With no secret, every cookie is refused, as in lib/session.ts.
    secret = os.environ.get("SESSION_SECRET")
    if not token or not secret:
        raise not_signed_in()
    try:
        # Naming the algorithm stops a forged cookie choosing its own, such as "none".
        return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp"]})
    except jwt.InvalidTokenError:
        raise not_signed_in()


def find_row(db: Client, table: str, columns: str, row_id: Any) -> Dict[str, Any]:
    """Returns the row a cookie's ID points to, or raises 401 if it isn't there."""
    try:
        UUID(row_id)
    except (AttributeError, TypeError, ValueError):
        raise not_signed_in()
    try:
        rows = db.table(table).select(columns).eq("id", row_id).execute().data
    except APIError:
        # A database fault isn't a bad login, and its details stay private.
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")
    if not rows:
        raise not_signed_in()
    return rows[0]


def get_session(session: Optional[str] = Cookie(default=None), db: Client = Depends(get_db)) -> Session:
    """Returns the signed-in user from the session cookie, or raises 401.

    See "Session cookie" in the root README for the format. The database is checked
    on every request, so disabling a team or removing an admin takes effect at once.
    """
    claims = read_cookie(session)
    role = claims.get("role")
    if role == "team" and isinstance(claims.get("teamName"), str):
        team = find_row(db, "teams", "id,name,disabled", claims.get("teamId"))
        if team["disabled"]:
            raise HTTPException(status_code=401, detail="This team is not active.")
        return TeamSession(team_id=team["id"], team_name=team["name"])
    if role == "admin" and isinstance(claims.get("username"), str):
        admin = find_row(db, "admin_users", "id,display_name", claims.get("adminId"))
        return AdminSession(admin_id=admin["id"], username=admin["display_name"])
    raise not_signed_in()


def require_team(
    request: Request,
    session: Session = Depends(get_session),
    db: Client = Depends(get_db),
) -> TeamSession:
    if not isinstance(session, TeamSession):
        raise HTTPException(status_code=403, detail="Team access only.")
    # Only actions count as activity. The leaderboard refreshes itself, so counting
    # page views would keep an idle open tab active.
    if request.method != "GET":
        try:
            db.rpc("touch_team", {"p_team_id": session.team_id}).execute()
        except APIError:
            # A missed activity time isn't worth refusing the request over.
            pass
    return session


def require_admin(session: Session = Depends(get_session)) -> AdminSession:
    if not isinstance(session, AdminSession):
        raise HTTPException(status_code=403, detail="Admin access only.")
    return session
