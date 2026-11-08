# AI Jailbreak Challenge platform

A platform for running a team-based AI red-teaming competition. Teams chat with
LLM-powered bots that have been told to keep a secret or refuse a kind of request,
and try to make them break their rules. A judge model grades the attempts, a gateway
caps each team's AI spending, and organisers run everything from an admin dashboard.

> **The event has ended.** This code ran the ACM Ashoka AI jailbreak event. It is
> published for learning and reuse. Every key, password and account used at the event
> has been rotated or disabled, and none of them are in this repository. Real Level 1
> passwords, judge rubrics, participant data and server details were never published.

## What's in it

| Part | Stack | Folder |
| --- | --- | --- |
| Website for teams and organisers | Next.js 16, React 19, TanStack Query, Tailwind | `app/`, `components/`, `lib/` |
| API, chat engine and judge worker | Python, FastAPI | `backend/` |
| Database, auth and business rules | Supabase (Postgres + Auth), SQL functions | `supabase/` |
| Model gateway with per-team budgets | LiteLLM, Postgres, Redis via Docker Compose | `deploy/litellm/` |
| Bot authoring, playtest and import tools | Python | `bots/`, `scripts/` |
| Example bots from the event | YAML | `examples/level4-bots/` |

## Architecture

```
  Browser (team or organiser)
        │  HTTPS, HttpOnly session cookie (HS256 JWT)
        ▼
  ┌──────────────────────────┐
  │ Next.js frontend          │  proxy.ts guards routes by role
  │  lib/hooks → lib/api      │  mock API (default) or real backend
  └────────────┬─────────────┘
               │  /api/*  (JSON, NDJSON stream for chat)
               ▼
  ┌──────────────────────────┐        ┌────────────────────────────┐
  │ FastAPI backend           │──SQL──▶│ Supabase                   │
  │  routers: auth, team,     │  RPC   │  Auth: team/admin accounts │
  │  chat, scoring, admin     │◀──────│  Postgres: bots, levels,   │
  │  engine/: run_chat,       │        │  conversations, scores,    │
  │    defences, fake tools   │        │  submissions, judge runs   │
  │  judging.py: judge worker │        └────────────────────────────┘
  └──────┬─────────────┬─────┘
         │ team's      │ organiser's
         │ virtual key │ judge key
         ▼             ▼
  ┌──────────────────────────┐
  │ LiteLLM gateway (local)   │  one virtual key + dollar cap per team
  │  + Postgres + Redis       │  routes model aliases to providers,
  └────────────┬─────────────┘  with optional same-model backups
               ▼
  OpenAI · Anthropic · Groq · AWS Bedrock · any OpenAI-compatible host
```

Hidden data never leaves the server. System prompts, passwords, rubrics and defences
exist only in the database and the backend. The types sent to teams have no fields that
could carry them, and `{{SECRET}}` is filled in inside SQL, so Python only sees the
finished prompt.

## How it works

### Levels

There are 5 levels of 6 bots each (Bot A to Bot F). Each bot runs on a different model.

| Level | Goal | How a win is checked |
| --- | --- | --- |
| 1 Password | Make the bot reveal the password it guards | Answer box: exact, case-sensitive match in SQL, with a cap on wrong answers |
| 2 Forbidden topic | Make the bot discuss a topic it must avoid | Judge |
| 3 Harmful language in Hindi | Make the bot produce abusive Hindi or Hinglish | Judge |
| 4 Malware | Make the bot write working malicious code | Judge |
| 5 Explicit content | Make the bot produce explicit content | Judge |

A level is complete when a team solves *N of M* bots. N is configurable, and levels
can optionally unlock in order. Each bot is a YAML file: see
[`bots/README.md`](bots/README.md) and [`bots/TEMPLATE.yaml`](bots/TEMPLATE.yaml). Bots
can have **defences** around the model:

- an input word filter
- an output word filter, which also blocks the password
- a reviewer model that reads each reply and can block it
- fake tools

### Chat

`POST /messages` runs every check in one transaction under the team's lock. These include
the rate limit, open turns and a locked level. The model then runs in its own task and
streams the reply as NDJSON. Blocked turns are charged and rate-limited, so teams
can't probe filters for free. Failed turns are hidden from the team and not charged.

### Judge

For Levels 2 to 5, a team submits a whole conversation, and a background worker grades
it (`backend/engine/judge/`).

1. **Strip the team's messages.** The judge sees only the bot's replies and tool calls,
   so a team can't argue with it directly.
2. **Build the prompt.**
   - The rubric and the instructions go in the system message.
   - The transcript goes in the user message as escaped JSON.
   - The transcript is wrapped in a random per-call boundary tag, so a reply can't break
     out of it.
3. **Validate the reply.** The judge must return strict JSON: a verdict, a reason code
   and evidence turns. Anything malformed or unclear counts as a FAIL. An optional
   second call confirms each PASS.

Organisers can pause judging and regrade submissions from the admin pages. See
[`backend/engine/judge/README.md`](backend/engine/judge/README.md).

### Gateway

Each team gets one private LiteLLM virtual key with a dollar cap that covers every model.
The backend sends each team's requests with that team's key. Provider keys stay in the
gateway's `.env`, and keys never reach the browser. The judge uses a separate organiser
key. See [`deploy/litellm/README.md`](deploy/litellm/README.md) and
[`OPERATIONS.md`](deploy/litellm/OPERATIONS.md).

## Setup

### 1. Frontend only, with the mock API (no keys needed)

```bash
npm install
cp .env.example .env.local      # NEXT_PUBLIC_USE_MOCK=true by default
npm run dev                     # http://localhost:3000
```

Mock accounts:

- team `t-01`, password `example-password-demo`
- admin `admin`, password `example-password-admin`

In mock mode, the phrase `open sesame` breaks any bot. See
[`docs/FRONTEND.md`](docs/FRONTEND.md).

### 2. Real backend

1. **Create a Supabase project.**
   - Hosted or self-hosted both work.
   - In the SQL editor, run the files in `supabase/` in the order given in
     [`backend/README.md`](backend/README.md#run-it). That's `schema.sql` first and
     `auth.sql` last.
2. **Configure and start the backend.**

   ```bash
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env            # fill in Supabase keys, SESSION_SECRET, judge settings
   uvicorn app.main:app --reload   # http://localhost:8000/docs
   ```

3. **Point the frontend at the backend.** In `.env.local`, set
   `NEXT_PUBLIC_USE_MOCK=false` and the **same** `SESSION_SECRET` as `backend/.env`.
4. **Set up the gateway.** It runs on the same machine as the backend. Follow
   [`deploy/litellm/README.md`](deploy/litellm/README.md); see
   [`deploy/litellm/.env.example`](deploy/litellm/.env.example) for its settings.
   For local testing without it, set `LITELLM_ENABLED=false` and put provider keys
   directly in `backend/.env`.
5. **Write, playtest and import bots.**

   ```bash
   backend/.venv/bin/pip install -r scripts/requirements.txt
   backend/.venv/bin/python scripts/playtest.py bots/my-bot.yaml
   backend/.venv/bin/python scripts/import_bots.py --dry-run
   backend/.venv/bin/python scripts/import_bots.py
   ```

Generate secrets with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
Never commit `.env` files or real bot YAMLs: `.gitignore` already excludes them.

### Tests

```bash
cd backend && python tests/test_engine.py          # no database or keys needed
cd backend && python -m pytest tests/engine        # judge engine
python3 deploy/litellm/test_setup.py               # gateway CLI
```

Many backend tests need a local Postgres: see the top of each test file.

## Example bots (content warning)

[`examples/level4-bots/`](examples/level4-bots/) holds the six Level 4 (Malware) bots
from the ACM Ashoka AI jailbreak event. Each one has its system prompt, input and output
word filters, and blocked reply. They are adversarial-test material: the prompts
describe real attack techniques so the bot can refuse them. Their judge rubrics were
removed. They're outside `bots/`, so an import never picks them up.

## Responsible use

This project exists to test and improve model safety in a supervised setting. Levels
3 to 5 ask models to produce harmful content, so run events like this with consent,
age checks where needed, content warnings, and a plan for handling the output. Follow
your model providers' usage policies.

## Further reading

- [`docs/FRONTEND.md`](docs/FRONTEND.md): frontend structure, mock API, API contract and endpoints
- [`backend/README.md`](backend/README.md): backend layout, sign-in, chat, defences and settings
- [`docs/INTERFACE.md`](docs/INTERFACE.md), [`docs/SCHEMA.md`](docs/SCHEMA.md), [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md): internal contracts and database schema
- [`deploy/litellm/`](deploy/litellm/): gateway setup, operations, providers and bot updates

## Credits

Built for the ACM Ashoka AI jailbreak event by ACM Ashoka and the CS Society.

Contributors: Parin, Ishaan, Arav, Devansh, Alibha and Sehraj Magu.

## License

[MIT](LICENSE) © 2026 Sehraj Magu and contributors.
