import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from postgrest.exceptions import APIError
from supabase import Client

from app.auth import get_session
from app.conversations import RateLimited, database_error, run_function
from app.db import get_db
from app.dev_auth import COOKIE_NAME, MAX_AGE_SECONDS, sign_session
from app.schemas import AdminCredentials, AdminSession, Session, TeamCredentials, TeamSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Longer than any real name or password, so a huge guess never reaches the database.
MAX_NAME_LENGTH = 100
MAX_PASSWORD_LENGTH = 200
# Stands in for an unknown name, so a guess at one still calls Supabase Auth. That
# narrows the timing difference between known and unknown names, but may not remove
# it. example.com is reserved for examples, so this can never be a real account.
UNKNOWN_ACCOUNT_EMAIL = "no-such-account@example.com"
SUPABASE_RETRY_SECONDS = 60


def wrong_login() -> HTTPException:
    # One message for an unknown name and a wrong password, so names can't be guessed.
    return HTTPException(status_code=401, detail="Wrong name or password.")


def password_ok(db: Client, user_id: Optional[str], password: str) -> bool:
    """Checks the password with Supabase Auth. A null user_id is an unknown name.

    This calls the API directly, because signing in through the shared service
    client would replace its service role with this user's session.
    """
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    if not anon_key:
        logger.error("SUPABASE_ANON_KEY isn't set, so nobody can sign in.")
        raise database_error()
    try:
        email = db.auth.admin.get_user_by_id(user_id).user.email if user_id else UNKNOWN_ACCOUNT_EMAIL
        response = httpx.post(
            f"{os.environ['SUPABASE_URL']}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key},
            json={"email": email, "password": password},
            timeout=10,
        )
    except Exception as error:
        # Only the class, because the details can quote the request.
        logger.error("Couldn't check a password: %s", type(error).__name__)
        raise database_error()
    if response.status_code == 200:
        return True
    if response.status_code == 400:  # Supabase's answer for a wrong password
        return False
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after", "")
        raise RateLimited(
            int(retry_after) if retry_after.isdigit() else SUPABASE_RETRY_SECONDS,
            "Too many sign-ins right now. Please try again shortly.",
        )
    logger.error("Couldn't check a password: Supabase returned %s", response.status_code)
    raise database_error()


def check_login(db: Client, request: Request, account: str, user_id: Optional[str], password: str) -> None:
    """Raises unless the password is right, and refuses too many wrong guesses."""
    attempt = run_function(db, "begin_login_attempt", {
        "p_account": account,
        "p_client": request.client.host if request.client else "unknown",
        "p_max_failures": int(os.environ.get("LOGIN_MAX_FAILURES", "10")),
        "p_window_seconds": int(os.environ.get("LOGIN_WINDOW_SECONDS", "300")),
    })["attempt_id"]
    try:
        correct = password_ok(db, user_id, password)
    except HTTPException:
        # The check failed, which says nothing about the password.
        run_function(db, "release_login_attempt", {"p_attempt_id": attempt, "p_signed_in": False})
        raise
    if not correct:
        raise wrong_login()
    run_function(db, "release_login_attempt", {"p_attempt_id": attempt, "p_signed_in": True})


def cookie_options() -> Dict[str, Any]:
    # Secure unless this is development, which often runs over plain HTTP.
    secure = os.environ.get("ENVIRONMENT", "").strip().lower() != "development"
    return {"path": "/", "httponly": True, "samesite": "lax", "secure": secure}


def start_session(response: Response, claims: Dict[str, Any]) -> None:
    if not os.environ.get("SESSION_SECRET"):
        logger.error("SESSION_SECRET isn't set, so nobody can sign in.")
        raise database_error()
    response.set_cookie(COOKIE_NAME, sign_session(claims), max_age=MAX_AGE_SECONDS, **cookie_options())


@router.post("/login")
def login(
    credentials: TeamCredentials,
    request: Request,
    response: Response,
    db: Client = Depends(get_db),
) -> Session:
    wanted = credentials.team.strip().lower()
    if len(wanted) > MAX_NAME_LENGTH or len(credentials.password) > MAX_PASSWORD_LENGTH:
        raise wrong_login()
    try:
        rows = db.table("teams").select("id,name,disabled").execute().data
    except APIError:
        raise database_error()
    team = next((r for r in rows if r["name"].lower() == wanted or str(r["id"]).lower() == wanted), None)
    account = f"team:{team['id'] if team else wanted}"
    check_login(db, request, account, team["id"] if team else None, credentials.password)
    if team is None:
        raise wrong_login()
    if team["disabled"]:
        raise HTTPException(status_code=401, detail="This team is not active.")
    start_session(response, {"role": "team", "teamId": team["id"], "teamName": team["name"]})
    return TeamSession(team_id=team["id"], team_name=team["name"])


@router.post("/admin/login")
def admin_login(
    credentials: AdminCredentials,
    request: Request,
    response: Response,
    db: Client = Depends(get_db),
) -> Session:
    wanted = credentials.username.strip().lower()
    if len(wanted) > MAX_NAME_LENGTH or len(credentials.password) > MAX_PASSWORD_LENGTH:
        raise wrong_login()
    try:
        rows = db.table("admin_users").select("id,display_name").execute().data
    except APIError:
        raise database_error()
    admin = next((r for r in rows if r["display_name"].lower() == wanted), None)
    account = f"admin:{admin['id'] if admin else wanted}"
    check_login(db, request, account, admin["id"] if admin else None, credentials.password)
    if admin is None:
        raise wrong_login()
    start_session(response, {"role": "admin", "adminId": admin["id"], "username": admin["display_name"]})
    return AdminSession(admin_id=admin["id"], username=admin["display_name"])


@router.post("/logout", status_code=204)
def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, **cookie_options())
    return response


@router.get("/session")
def current_session(
    session: Optional[str] = Cookie(default=None),
    db: Client = Depends(get_db),
) -> Optional[Session]:
    """The signed-in user, or null when nobody is signed in."""
    try:
        return get_session(session, db)
    except HTTPException as err:
        if err.status_code == 401:
            return None
        raise
