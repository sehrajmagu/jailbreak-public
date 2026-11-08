#!/usr/bin/env python3
"""Copies bots/models.yaml and the bot files into Supabase. See bots/README.md.

Reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from backend/.env. Nothing is
written unless every file passes its checks. Never prints a prompt or password.

A bot's enabled setting is preserved on re-import. Existing legacy quota data is
also preserved for rollback compatibility, but no longer controls spending.
"""
import argparse
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from postgrest.exceptions import APIError

from bot_files import LABELS, ROOT, TODO, bot_paths, bot_row, load_bots, load_models, model_row

sys.path.insert(0, str(ROOT / "backend"))
from app.db import get_db  # noqa: E402


def stop(lines):
    print("\nNothing was imported. Fix these first:", file=sys.stderr)
    for line in lines:
        print(f"  - {line}", file=sys.stderr)
    sys.exit(1)


def check_levels(db, bots):
    """The table starts empty, and each bot needs its level to exist."""
    rows = db.table("levels").select("level_number,required_bots").execute().data
    required = {row["level_number"]: row["required_bots"] for row in rows}
    missing = sorted({bot["level"] for bot in bots} - required.keys())
    if missing:
        stop([f"level {level} isn't in the `levels` table" for level in missing])
    for level in sorted({bot["level"] for bot in bots}):
        enabled = sum(1 for bot in bots if bot["level"] == level and bot["enabled"])
        if enabled < required[level]:
            print(f"Warning: level {level} has {enabled} enabled bots in these files, but completing it takes {required[level]}.")


def import_models(db, models):
    rows = db.table("models").select("id,token_limit_per_team").execute().data
    # Preserve legacy column data for rollback; it no longer controls admission.
    limits = {row["id"]: row["token_limit_per_team"] for row in rows}
    existing = set(limits)
    new_rows = []
    for model in models:
        row = model_row(model)
        row["token_limit_per_team"] = limits.get(row["id"], row["token_limit_per_team"])
        new_rows.append(row)
    db.table("models").upsert(new_rows, on_conflict="id").execute()
    ids = {model["id"] for model in models}
    print(f"Models: {len(ids - existing)} added, {len(ids & existing)} updated.")
    for model_id in sorted(existing - ids):
        print(f"Warning: the model {model_id} is in the database but not in models.yaml. It was left as it is.")


def import_bots(db, bots, models):
    rows = db.table("bots").select("level,label,enabled").execute().data
    enabled = {(row["level"], row["label"]): row["enabled"] for row in rows}
    existing = set(enabled)
    now = datetime.now(timezone.utc).isoformat()
    # Leaving out `id` means an existing bot keeps its ID, and so its team progress.
    # One request is one transaction, so either every bot is saved or none is.
    new_rows = []
    for bot in bots:
        row = dict(bot_row(bot, models), updated_at=now)
        row["enabled"] = enabled.get((row["level"], row["label"]), row["enabled"])
        new_rows.append(row)
    db.table("bots").upsert(new_rows, on_conflict="level,label").execute()
    slots = {(bot["level"], bot["label"]) for bot in bots}
    print(f"Bots: {len(slots - existing)} added, {len(slots & existing)} updated.")
    for level, label in sorted(existing - slots):
        print(f"Warning: level {level} {label} has no file but is still in the database. Disable it if it shouldn't be live.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="check the files without connecting to the database")
    parser.add_argument("--models-only", action="store_true", help="only import bots/models.yaml")
    args = parser.parse_args()

    models, errors = load_models()
    if errors:
        stop(errors)
    bots = []
    if not args.models_only:
        bots, errors = load_bots(models)
        if errors:
            stop(errors)
        print(f"Checked {len(bot_paths())} bot files and {len(models)} models: no mistakes. Bots written: {len(bots)} of {5 * len(LABELS)}.")
    else:
        print(f"Checked {len(models)} models: no mistakes.")
    for model in models:
        if model["api_model"] == TODO:
            print(f"Warning: {model['display_name']} has api_model {TODO}, so it can't be playtested or run yet.")
    if args.dry_run:
        print("Dry run, so nothing was imported.")
        return

    load_dotenv(ROOT / "backend" / ".env")
    try:
        db = get_db()
        if bots:
            check_levels(db, bots)
        import_models(db, models)
        if bots:
            import_bots(db, bots, models)
    except APIError as e:
        # Only the message: a database error's details can quote the whole row,
        # including its password.
        print(f"The database refused the import ({e.code}): {e.message}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"{e}. Add them to backend/.env.", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Couldn't reach the database ({type(e).__name__}).", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"The import failed ({type(e).__name__}). Check SUPABASE_URL in backend/.env.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
