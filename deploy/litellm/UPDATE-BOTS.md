# Updating live bot YAMLs

This guide assumes the app is deployed on a server at `/path/to/app`.
The app reads bot configuration from Supabase. Editing a file alone does not update
the site. Validate the private files, then import them into Supabase.

## Private files used by this deployment

| File or directory under the repository root | Purpose |
| --- | --- |
| `deploy/litellm/runtime/selected/challenges/` | Live challenge source YAMLs; keep files directly in this directory |
| `deploy/litellm/runtime/selected/deployment-models.yaml` | Model definitions used by this selection |
| `deploy/litellm/runtime/selected/import-selected.py` | Private validation/import helper already installed on this server |
| `deploy/litellm/runtime/models.json` | LiteLLM upstream routing and pricing inventory |
| `deploy/litellm/.env` | Private gateway/provider credentials |
| `backend/.env` | Private app/database credentials |

The helper and challenge files are deliberately absent from GitHub. This guide
assumes they already exist on this server; a fresh clone needs the private
selection restored securely from a backup. Never commit private runtime files,
provider keys, real challenges, passwords, hidden prompts or judging rubrics.
`bots/TEMPLATE.yaml` is only a generic example, and `bots/models.yaml` contains
repository defaults rather than the live deployment's configured providers.

## 1. Connect and back up the YAML selection

Run on your laptop:

```bash
ssh your-server
```

Then run on the server:

```bash
cd /path/to/app
umask 077
mkdir -p "$HOME/bot-backups"
yaml_backup="$(mktemp -d "$HOME/bot-backups/bot-yamls.XXXXXXXX")"
cp -a deploy/litellm/runtime/selected "$yaml_backup/"
printf 'Private YAML backup: %s/selected\n' "$yaml_backup"
```

This makes a private copy of the current selection and helper before editing.
It is a file backup, not a database backup. Keep its printed path privately.

## 2. Add or edit a challenge

For example, to edit Level 1 Bot A:

```bash
nano deploy/litellm/runtime/selected/challenges/level1-bot-a.yaml
```

For another bot, replace the filename with its actual private YAML filename. To
add a bot, create its file in that same directory using the generic template's
structure. The filename is for organisation: `level` and `label` inside the YAML
identify the database slot.

- Keep an existing bot's `level` and `label` unchanged to preserve its ID and team
  progress. Changing either creates a different slot and leaves the old row behind.
- Use a `model` ID from the private `deployment-models.yaml`; the corresponding
  LiteLLM route must exist before participants can use it.
- Level 1 requires `check_type: answer`, a `secret`, and `{{SECRET}}` in the system
  prompt. Levels 2–5 require `check_type: judge` and a nonempty winning `rubric`.
- Each slot may occur only once. Level 1 secrets must be unique across files.
- Coordinate prompt, password or rubric changes during play: old conversations
  remain, while future chat turns read the new configuration. Pending judging
  can also be affected by rubric changes.

`context_limit_tokens` is active. The chat backend estimates conversation-history
size and drops older complete turns to fit it, always retaining the newest turn.
The estimate is one token per three characters, including saved tool arguments
and results. It excludes the system prompt, tool definitions and generated reply,
so it is a soft history limit rather than a hard cap on the full provider request.
It does not control the team's dollar budget. Omitting it defaults to 8000.

## 3. Validate without writing to the database

```bash
backend/.venv/bin/python deploy/litellm/runtime/selected/import-selected.py
```

This checks all files in the private selection against its model definitions.
Continue only if it reports successful validation and exits successfully. Fix
validation errors and rerun this command before importing. Do not use
`scripts/import_bots.py` with its default paths on this deployment: that would
read the repository's default model inventory instead of the private selection.

## 4. Apply the validated selection

```bash
backend/.venv/bin/python deploy/litellm/runtime/selected/import-selected.py --apply
```

This revalidates, checks that the required levels exist, then upserts models and
bots into Supabase. It imports the whole selection, not just the file you edited.
It also reapplies model settings from `deployment-models.yaml`, so review that
file if model settings were changed separately through the admin UI.

An existing bot keeps its ID, progress and admin-controlled `enabled` setting.
A newly created bot uses the YAML's `enabled` setting. No schema SQL, database
reset, app rebuild or service restart is needed for YAML content changes.
Models and bots are separate database writes, so the overall import is not one
transaction. If applying fails, keep the backup, inspect the reported error and
repair the cause before retrying; do not reset or delete the database.

## 5. Verify the result

To list only nonsecret bot metadata from Supabase:

```bash
backend/.venv/bin/python - <<'PY'
import sys
from pathlib import Path
from dotenv import load_dotenv

root = Path.cwd()
sys.path.insert(0, str(root / "backend"))
load_dotenv(root / "backend/.env")
from app.db import get_db

rows = get_db().table("bots").select(
    "level,label,model_id,enabled,context_limit_tokens"
).order("level").order("label").execute().data
for row in rows:
    print(row)
print(f"Total bots: {len(rows)}")
PY
```

Open the admin Bots page and confirm the expected slots, models and availability.
If an existing bot should be enabled or disabled, change it there: reimporting its
YAML intentionally preserves the current database toggle. A real chat test spends
provider money; validation and the metadata query do not.

## Removing a bot or undoing an edit

Deleting a YAML does not delete its database row. Disable the bot through the
admin page if it should stop accepting messages. Do not delete rows to hide bots:
existing conversations and progress reference their IDs.

To undo a content edit, copy the earlier version of that file from the private
backup into `runtime/selected/challenges/`, validate, and apply again. If you also
changed model definitions, restore their file deliberately before applying. A
reimport does not undo conversations, spend, scores, or newly added slots; disable
any newly added bot separately if it should no longer be available.

A new upstream model or provider is more than a bot edit: configure its LiteLLM
route, pricing and team access using [the gateway operations guide](OPERATIONS.md),
then keep the private deployment model ID and bot references consistent. Schema
changes require their own reviewed SQL migration; never rerun a destructive reset.
