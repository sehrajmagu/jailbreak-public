# Backend

FastAPI backend for the platform. The answer and leaderboard endpoints, the team pages, saving chat turns, real model chat, the team and admin conversation endpoints, and the organiser pages are implemented. Sign-in, creating teams and resetting team passwords are too. Every other endpoint is a stub that returns 501, including creating and editing bots, and regrading.

The API contract is in [docs/ENDPOINTS.md](../docs/ENDPOINTS.md), and the contract with the bot engine is in [docs/INTERFACE.md](../docs/INTERFACE.md).

## Layout

```
app/
  main.py        App setup, routers under /api, and the error format
  db.py          Supabase client
  judging.py     The background worker that grades submissions, using engine/judge
  auth.py        Reads and checks the session cookie, and the role dependencies
  dev_auth.py    Development-only login, off by default
  schemas.py     Pydantic models matching lib/types.ts
  conversations.py  Saves chat turns, and maps stored conversations to API models
  turns.py       Runs each chat turn in its own task, and saves the result
  engine/        The bot engine: run_chat, the provider adapters and the fake tools
  admin.py       Maps the organiser pages' data to API models
  routers/
    auth.py      Login, logout and session
    team.py      Team endpoints, except chat, scoring and conversations
    scoring.py   POST /bots/:id/answer and GET /leaderboard
    team_pages.py  GET /me, /me/budget, /me/dashboard, /levels, /bots/:id and /submissions
    conversations.py  GET /bots/:id/conversations and GET /conversations/:id
    chat.py      POST /messages
    admin.py     Admin endpoints: the organiser pages and the conversation log
tests/
  test_scoring.py  Scoring tests. Needs a local Postgres, see the top of the file
  test_conversations.py  Conversation tests. Needs a local Postgres, see the top of the file
  test_team_pages.py  Team page tests. Needs a local Postgres, see the top of the file
  test_admin.py  Organiser page tests. Needs a local Postgres, see the top of the file
  test_chat.py   POST /messages and the turn task. Needs a local Postgres, see the top of the file
  test_engine.py  The bot engine, using a fake transport. Needs no database or keys
  test_auth.py   Sign-in and team accounts. Needs a local Postgres, see the top of the file
  test_judge.py  Submitting, the judge worker, regrading and judge scoring. Needs a local Postgres, see the top of the file
  test_defences.py  Defence tests. The last part needs a local Postgres, see the top of the file
```

## Run it

Needs Python 3.9 or newer.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

API docs are at http://localhost:8000/docs.

Run `supabase/schema.sql`, then `supabase/scoring.sql`, in the Supabase SQL editor before using the scoring endpoints, `supabase/conversations.sql` before using the conversation endpoints, `supabase/team_pages.sql` before using the team pages, `supabase/admin.sql` after that before using the organiser pages, `supabase/chat.sql` before using `POST /messages`, and `supabase/auth.sql` last before anyone signs in. Team actions also need `admin.sql`, because they record the team's last active time. An existing database needs the current `conversations.sql` run again as well as `chat.sql`.

## Sign-in

`POST /auth/login` and `POST /auth/admin/login` find the account by name, check its password with Supabase Auth, and set the session cookie. It's marked `Secure` unless `ENVIRONMENT` is `development`, so anywhere else needs HTTPS.

- **Settings:** `SUPABASE_ANON_KEY` is required, and is under Project Settings > API in Supabase. `SESSION_SECRET` must be the same in `backend/.env` and in `.env.local` in the repo root. Next.js doesn't read `backend/`, so with no secret in the root file, every page sends you back to the sign-in page. Never put a fallback secret in the code.
- **Wrong passwords:** after `LOGIN_MAX_FAILURES` (10) wrong passwords in `LOGIN_WINDOW_SECONDS` (300), that account refuses that client with 429 and `retryAfterSeconds`. It's counted per account and client, so a team can't lock a rival out from its own machine. Behind a proxy, the client is only the visitor's address if the server is set to trust the proxy's headers. Otherwise every visitor counts as one client.
- **Supabase's own limit:** Supabase Auth limits sign-ins per IP address, and they all come from the backend's address. Check the limit in its settings before the event. Reaching it gives 429, not a wrong password.
- **Team accounts:** creating a team and resetting its password both go through Supabase Auth, and the password never reaches the database or the audit log. A reset also clears the team's wrong passwords, so an organiser can let it back in. A session that's already signed in lasts until its cookie expires.

## Dev login

**Never turn this on at the real event. It signs anyone in, as any team or admin, with no password.**

It lets you test endpoints without a password. In `backend/.env`, set a long random `SESSION_SECRET`, then:

```bash
DEV_FAKE_AUTH=true
ENVIRONMENT=development
```

The server prints a warning when it starts. It refuses to start if `DEV_FAKE_AUTH` is on and `ENVIRONMENT` is `production`.

Create the test accounts once, using a development Supabase project. They appear on that project's leaderboard and prize pages. The script prints the team names, and re-running it is safe.

```bash
pip install -r ../scripts/requirements.txt
python ../scripts/dev_seed.py
```

**With curl:**

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/dev/login \
  -H 'Content-Type: application/json' -d '{"team": "Dev Team 1"}'
curl -b cookies.txt http://localhost:8000/api/leaderboard
```

Use `{"team": "admin"}` for the test admin.

**In a browser:** open http://localhost:8000/docs, run `POST /api/dev/login`, then open any `GET` endpoint in the same browser. Then open the website at http://localhost:3000, using `localhost` for both so the cookie is shared. It needs `NEXT_PUBLIC_USE_MOCK=false` and the same `SESSION_SECRET` in `.env.local`. With the mock off, `next.config.ts` sends `/api` to `BACKEND_URL`, which defaults to port 8000.

Run `python tests/test_dev_auth.py` and `python tests/test_dev_seed.py` to test it. They need no database.

## Chat

`POST /messages` works like this:

1. `start_turn` in `supabase/chat.sql` runs every check, then `begin_turn`, as one database transaction under the team's lock, so parallel sends can't pass together. A refusal is a normal JSON error before any stream starts, and leaves nothing behind.
2. `app/turns.py` runs the model in its own task, and the response only reads from a queue. A team can't avoid the charge by disconnecting: the task reads the provider's stream to the end, because usage only arrives there, and always calls `finish_turn`.
3. `finish_turn` saves the reply and its tool calls, and charges the turn's tokens to the bot's model when the reply is complete.

| Check, in order | HTTP |
| --- | --- |
| Team exists and isn't disabled | 401 |
| Bot and level are enabled | 404 |
| Level isn't locked | 403 |
| Model is enabled, has `api_model`, and its host has a key | 503 |
| Answer bot with wrong answers left | 409 |
| Team has few enough unfinished turns | 429 with `retryAfterSeconds` |
| Team is under the rate limit | 429 with `retryAfterSeconds` |
| The message is valid and the conversation is open | 404, 409 or 422 |

**Failed turns and refusals:** a failed turn is kept for the organisers, with its token usage, but isn't charged, doesn't count towards the rate limit, and is hidden from the team and from later history. Only infrastructure failures are free. A provider's refusal ends with `done` and a fixed in-character reply, and is charged and rate-limited.

**One turn at a time:** a second message in a conversation while a reply is streaming gets 409. A turn left unfinished for 10 minutes, because its server died, is hidden from the team, and is marked failed if the team continues that conversation.

**Secrets:** `{{SECRET}}` is filled in by `get_bot_config` in SQL, so Python only sees the finished prompt. Reasoning and thinking text never reach the team. Provider errors can repeat the prompt, so only their class, status and code are logged. Don't turn on debug logging for the provider SDKs, because it logs requests in full.

### Defences

Each bot's defences come from its file: see `bots/TEMPLATE.yaml`. The code is in `app/engine/defences.py`, which `scripts/playtest.py` also uses.

- **Input filter:** a message containing a listed word never reaches the model. Earlier blocked messages are also left out of the history sent with later ones.
- **Output filter:** the reply, including fake tool call arguments, is checked before the team sees any of it. On an answer bot it also blocks the password. `get_bot_config` adds the password to the filter's words, so the engine never receives it as a field of its own.
- **Reviewer model:** after the filter, a second model reads the reply as quoted data, against the bot's reviewer note, and answers ALLOW or BLOCK. Anything but a clear ALLOW blocks, including a refusal. It never sees the bot's prompt, password or rubric. A bot whose reviewer has no model ID or key refuses messages, like a bot whose own model can't run.

With an output filter or a reviewer, the reply doesn't stream: it arrives all at once after the check.

**Blocked turns** are normal turns, ended with `done` and the bot's blocked reply:

| | Blocked turn | Failed turn, including a defence that fails |
| --- | --- | --- |
| Tokens charged | What was really spent: none for an input block, the model's for an output block, plus the reviewer's | None |
| Rate limit | Counts | Doesn't count |
| Team sees | Their message and the blocked reply | Nothing |
| Saved | Yes, with the blocked reply. The reply it replaced is never saved | Yes, for the organisers, hidden from the team |
| Later history | The blocked reply. An input-blocked message is left out | Left out |

Blocked turns are charged and rate-limited, so a team can't probe the filters for free. The server log records which defence fired and for which bot, but never the text.

### Settings

Set these in `backend/.env`. A bot whose host has no key refuses messages with a clear error, and stays visible.

| Setting | Default | |
| --- | --- | --- |
| `GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | | One per `host` in `bots/models.yaml` |
| `CHAT_RATE_LIMIT_MESSAGES` | 6 | Messages a team can send in the window |
| `CHAT_RATE_LIMIT_WINDOW_SECONDS` | 30 | |
| `CHAT_MAX_OPEN_TURNS` | 3 | Unfinished turns a team can have at once |
| `CHAT_MAX_OUTPUT_TOKENS` | 2048 | Per model call, reasoning included. A turn with tools makes up to 6 calls |

## Judge

Levels 2 to 5 are graded by `engine/judge`, run in the background by `app/judging.py`. Run `supabase/judge.sql` after `supabase/admin.sql` before using it.

1. `POST /conversations/:id/submit` closes the conversation, saves a `pending` submission and returns at once. It's refused while a reply is still streaming, and if the conversation has no bot replies.
2. A worker task in the app takes one submission at a time and marks it `grading`. It sends the judge only the replies and tool calls the team could see, and the rubric. The judge never gets the team's messages, the system prompt or a password.
3. A verdict is saved as `passed` or `failed` with one of the judge's fixed reasons, and the points are worked out in the same transaction.

| What happened | Status | Team sees | What's next |
| --- | --- | --- | --- |
| Submitted, or judging is off | `pending` | Grading | Graded in the order submitted, once judging is on |
| Pass or fail | `passed`, `failed` | The verdict and reason | Finished, unless an organiser regrades it |
| Rate limit, timeout, provider outage | `error` | Grading | **Deferred:** all grading pauses for 30 seconds, doubling up to 10 minutes, or longer if the provider asks, up to an hour. Retried until the judge answers, with no limit on attempts |
| Unusable answer | `error` | Grading | Retried after 30 seconds, doubling up to 10 minutes, 8 attempts in all |
| Provider refusal, conversation too long, no rubric, attempts used up | `error` | Still grading | **Needs an organiser.** The reason on the admin submissions page says why. Fix the cause and regrade |
| The worker died | `grading` | Still grading | Taken again after 5 minutes |

A failure to grade is never recorded as `failed`, because a timeout isn't a failed challenge.

**The judging switch.** Organisers turn judging on or off from the submissions page, or with `GET` and `PUT /admin/judging`. It's stored in `judge_settings`, takes effect at once with no restart, and each change is written to the audit log. Off, teams can still submit, but the worker claims nothing, so nothing is sent to the judge model. A grade already in progress still finishes. Back on, waiting submissions are graded in the order they were submitted. Teams never see the switch: they see every submission that isn't graded yet as `grading`, whether judging is on or not. The judge's `JUDGE_*` settings still decide whether there's a judge at all.

**Rate limits.** The pause after a rate limit, timeout or outage is shared by every worker and stored in `judge_settings.backoff_until`, so a long queue makes one call per pause instead of one per submission. An answer from the judge resets the delay. The page shows when grading is paused.

**Points** go only to a team's first pass on a bot. `POST /admin/submissions/:id/regrade` sends a submission back to the queue, and its points stay until the new verdict:

- Pass to pass changes nothing, so nothing is paid twice.
- Pass to fail takes the points back. They move to the team's earliest other pass on that bot if there is one. Otherwise an earned level completion goes too, once the team is below the required solves. An organiser's override stays.
- Fail to pass pays as normal, if nothing else holds the bot's points.

**In production the app refuses to start on the mock judge,** because the mock fails nearly every real conversation. Outside production and development, submissions wait in the queue. See the `JUDGE_*` settings in `.env.example`. The judge's key is separate from team budgets, since grading is an organiser cost, and it doesn't go through LiteLLM unless you point `JUDGE_BASE_URL` at it with its own key.

Run `python -m pytest tests/engine` for the judge itself, which needs no database or keys. Those tests use mocks, so they don't show how accurate a real model is or how well it resists prompt injection. Run `test_g_real_model.py` with real rubrics before the event.

## Organiser pages

The organiser pages read and write through the functions in `supabase/admin.sql`. Team spending allowances are configured in LiteLLM; legacy token quotas are not enforced. Each change an organiser makes, such as renaming a team, turning a bot off, overriding a prize, is written to `audit_log` in the same transaction, with the values before and after. A request that changes nothing isn't logged.

- **Bots** are edited in their files, not in the admin pages. A bot can't be turned off if its level would be left with fewer enabled bots than it takes to complete it.
- **Prizes:** clearing an override goes back to what the team's solves earn, keeping the time it first completed the level, so its place in the prize order doesn't change.
- **Same numbers as the team pages:** attempts, solves, completions and prizes follow the rules in `supabase/team_pages.sql`, and `tests/test_admin.py` checks that both sides agree.
- **Last active** is set when a team does something: any team request that isn't a GET. It's written at most once a minute per team.

## Notes

- **Auth:** `get_session` in `app/auth.py` checks the session cookie on every request. Admin routes enforce the role at router level.
- **Field names:** Python uses snake_case, and the models convert to the camelCase in `lib/types.ts` automatically.
- **Team responses:** return only participant models from team routes, so that hidden fields can't leak.

### Gateway defaults and spending refusals

LiteLLM is enabled by default. Configure it using [the gateway setup guide](../deploy/litellm/README.md); missing gateway configuration never silently falls back to direct provider keys. Set `LITELLM_ENABLED=false` explicitly only when intentionally using direct adapters without team dollar-budget enforcement. The direct-adapter test suites set this override themselves.

A structured gateway `budget_exceeded` error is preserved through the engine and turn persistence as a safe chat error code. The UI tells the team its budget is exhausted or insufficient for the request. Other errors remain `bot_unavailable`; HTTP 429 or message text alone never marks a team out of budget.
