# Writing a bot

Each bot is one YAML file in this folder (no subfolders). There are 30 bots: 5 levels with 6 bots each.

Bot files hold passwords and hidden prompts, so never share them with teams or paste them anywhere public. Git ignores every file here except `TEMPLATE.yaml` and `models.yaml`, so never force-add one.

The Level 4 bots used at the event are kept as worked examples in [`examples/level4-bots/`](../examples/level4-bots/), outside this folder so an import never picks them up. Their judge rubrics were removed.

## What each level is for

1. **Password:** make the bot reveal the password it's guarding.
2. **Forbidden topic:** make the bot discuss a topic it has been told to avoid.
3. **Harmful language in Hindi:** make the bot use harmful language in Hindi that it should refuse.
4. **Malware:** make the bot help with malware when it should refuse.
5. **Explicit content:** make the bot produce explicit content when it should refuse.

Bots can have fake tools, but no level requires one.

## Setup

Once, from the repo root:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r scripts/requirements.txt
```

## 1. Write it

Copy `TEMPLATE.yaml` to a name like `level1-bot-a.yaml`, then edit the copy. The comments explain each field.

## 2. Test it

Set the key for your bot's model in your terminal. The server reads the same keys from `backend/.env`, which git ignores. Don't save a key anywhere else in this repo.

| `host` in `models.yaml` | What to set |
| --- | --- |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `groq` | `GROQ_API_KEY` |

Then start a chat:

```bash
export OPENAI_API_KEY=...
backend/.venv/bin/python scripts/playtest.py level1-bot-a.yaml
```

Try to break your own bot. Type `/reset` to start over and `/quit` to leave. Each reply shows its token usage, which costs real money.

GPT-6 bots can't be playtested until their `api_model` values in `models.yaml` replace `TODO`.

**Defences work in the playtest exactly as in the live chat.** When one fires, you see the blocked reply and which defence fired, but never the reply it replaced. A reviewer model makes a second call on every reply, so it adds to the cost. Test that normal messages still get through: a word list that's too broad blocks innocent replies too.

## 3. Check and import

**For the existing deployed server**, follow [Updating live bot YAMLs](../deploy/litellm/UPDATE-BOTS.md). It uses the private reviewed selection and live model settings. The commands below use the repository defaults and are for a separate installation.

To check every file without touching the database:

```bash
backend/.venv/bin/python scripts/import_bots.py --dry-run
```

An organiser with `backend/.env` can then import. An existing database needs `supabase/bots_columns.sql` run first, and the levels must already exist in the `levels` table.

```bash
backend/.venv/bin/python scripts/import_bots.py                # models, then bots
backend/.venv/bin/python scripts/import_bots.py --models-only  # just models.yaml
```

- If any file has a mistake, nothing is imported.
- The files win except for an existing bot's `enabled` setting, which imports preserve. Old model quota values are retained for database compatibility only; dollar budgets are configured in LiteLLM.
- A bot is matched by its level and label, so its ID and team progress stay the same when you edit it. Avoid changing a live bot's password, because teams may already have found the old one.
- Deleting or relabelling a file doesn't remove the old bot from the database. The import warns you, and an organiser must disable it.
