#!/usr/bin/env python3
"""Creates test teams and a test admin for dev login. See backend/README.md.

Reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from backend/.env. Safe to re-run:
an account that exists is reused. The passwords are random and never printed,
because dev login doesn't use them. Use a development Supabase project, since these
accounts appear on its leaderboard and prize pages.

It reads every row back before reporting success, and exits with an error otherwise.
"""
import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv
from postgrest.exceptions import APIError
from supabase_auth.errors import AuthError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.db import get_db  # noqa: E402
from app.dev_auth import DEV_ADMIN_NAME  # noqa: E402

# example.com is reserved for examples, so these can never reach a real person.
TEAMS = [
    ("Dev Team 1", "dev-team-1@example.com"),
    ("Dev Team 2", "dev-team-2@example.com"),
    ("Dev Team 3", "dev-team-3@example.com"),
]
ADMIN_EMAIL = "dev-admin@example.com"
PAGE_SIZE = 1000


class SeedFailed(Exception):
    """A row wasn't saved, even though Supabase reported no error."""


def existing_users(db):
    """Returns a map of email to user ID for every user in Supabase Auth."""
    users = {}
    page = 1
    while True:
        batch = db.auth.admin.list_users(page=page, per_page=PAGE_SIZE)
        users.update({user.email: user.id for user in batch})
        if len(batch) < PAGE_SIZE:
            return users
        page += 1


def ensure_user(db, users, email, role):
    if email in users:
        return str(users[email])
    created = db.auth.admin.create_user({
        "email": email,
        "password": secrets.token_urlsafe(32),
        "email_confirm": True,
        "app_metadata": {"role": role},
    })
    return str(created.user.id)


def save_rows(db, table, name_column, rows):
    """Saves the rows, then reads them back, since no error doesn't prove they're there."""
    saved = db.table(table).upsert(rows, on_conflict="id").execute().data
    if len(saved) != len(rows):
        raise SeedFailed(f"{table}: asked to save {len(rows)} rows, but Supabase returned {len(saved)}.")

    ids = [row["id"] for row in rows]
    found = db.table(table).select(f"id,{name_column}").in_("id", ids).execute().data
    found_names = {row["id"]: row[name_column] for row in found}
    for row in rows:
        if found_names.get(row["id"]) != row[name_column]:
            raise SeedFailed(f"{table}: {row[name_column]} isn't there after saving.")


def seed(db):
    users = existing_users(db)
    # The team's disabled setting is left out, so re-running keeps it as it is.
    save_rows(
        db,
        "teams",
        "name",
        [{"id": ensure_user(db, users, email, "team"), "name": name} for name, email in TEAMS],
    )
    save_rows(
        db,
        "admin_users",
        "display_name",
        [{"id": ensure_user(db, users, ADMIN_EMAIL, "admin"), "display_name": DEV_ADMIN_NAME}],
    )


def main():
    load_dotenv(ROOT / "backend" / ".env")
    if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
        sys.exit("Refusing to run: ENVIRONMENT is production. These are test accounts.")

    seed(get_db())

    print("Test accounts are ready. Use these names with POST /api/dev/login:")
    for name, _ in TEAMS:
        print(f"  {name}")
    print("  admin (signs in as the test admin)")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"{e}. Add them to backend/.env.")
    except (APIError, AuthError) as e:
        # Only the message, so nothing else the service sends back is printed.
        sys.exit(f"Supabase refused this: {e.message}")
    except SeedFailed as e:
        sys.exit(f"The test accounts weren't saved. {e}")
