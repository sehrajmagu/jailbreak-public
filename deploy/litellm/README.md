# LiteLLM for the competition

First installation: follow sections 1–5 below.
After installation: use [Changing teams, budgets, models and keys](OPERATIONS.md).
For Bedrock and an external Qwen endpoint, see [provider settings](PROVIDERS.md).

## What this setup stores

| Information | Where it lives |
| --- | --- |
| Competition teams, accounts, problems, conversations and scores | Existing app Supabase |
| Applied team dollar caps, virtual keys and recorded spend | LiteLLM’s separate PostgreSQL |
| App team UUID → private virtual key | Generated `runtime/team-keys.json` |
| Your desired team caps, used when running provisioning | Operator-edited `runtime/teams.csv` |
| Models and which of your three provider keys each uses | Operator-edited `runtime/models.json` |
| Your three real provider keys | Private `.env` |

Your backend sends each request with the logged-in team’s virtual key. LiteLLM
checks that team’s allowance and calls the selected provider with your provider
key. One team can use several models/problems from the same dollar allowance.
Budgets aren’t duplicated in Supabase, and a second full Supabase stack isn’t
needed. The Compose setup supplies the separate PostgreSQL and Redis services.

There is no new Supabase table or numbered migration for team-to-key mapping in
this version: that association is in the private JSON file. Existing app SQL
functions do need reapplying for quota removal; section 4 has the commands.

This folder is independent of the app's Supabase deployment. It runs LiteLLM,
a dedicated PostgreSQL accounting database and Redis on an internal Docker network.
Only the proxy's port 4000 is bound to **127.0.0.1**. Keep Nginx pointing to the
website; do not publish the gateway, Redis, or accounting database to participants.
The backend must run on the same machine, or reach the gateway over a private network.

Each competition team gets one private virtual key. All allowed models share that
key's dollar budget, with no scheduled reset. Provider keys remain in this folder's
`.env`; team keys remain in `runtime/team-keys.json`. Neither goes to the browser.
The three provider keys may belong to different providers or the same provider.
The gateway supports OpenAI, Anthropic, Groq, Bedrock and custom HTTPS
OpenAI-compatible hosts. Bedrock/custom hosts are gateway-only; direct mode does
not send their credentials to a different provider.

## 1. Install and prepare on Ubuntu / Debian

First transfer/commit these project changes to your server checkout. Nothing in
this guide has been executed on your server. Docker Compose v2 and Python 3 are
required. On a fresh server, install using Docker's official Ubuntu/Debian guide:
https://docs.docker.com/engine/install/ubuntu/ or https://docs.docker.com/engine/install/debian/.
If Docker is already installed, check it rather than replacing it:

```bash
docker --version
sudo docker compose version
python3 --version
cd ~/acm-aoc-jailbreak/deploy/litellm
python3 setup.py init
nano .env
```

Run `init` only on the first installation. It creates `runtime/` and a private
`.env` with random infrastructure secrets; it refuses to overwrite an existing
`.env`. Don’t delete the file to rerun initialization.

Fill `PROVIDER_KEY_1`, `PROVIDER_KEY_2`, and `PROVIDER_KEY_3` in `.env`. Keep the
random master key, salt and Postgres password unchanged. The salt must survive
restarts/restores. No provider key is required in the app backend when proxy mode
is on. A pinned LiteLLM version is generated; upgrade deliberately after testing.
Do not enable verbose/debug prompt logging. The editable provider-key lines are:

```dotenv
PROVIDER_KEY_1=your-first-provider-api-key
PROVIDER_KEY_2=your-second-provider-api-key
PROVIDER_KEY_3=your-third-provider-api-key
```

For example, if slot 1 holds your OpenAI key, models using that credential get
`"key_slot": 1`. Slot 2 could hold Anthropic and slot 3 Groq, but these assignments
are your choice. The keys must match the providers configured for the models.

## 2. Select your actual models

```bash
cp models.example.json runtime/models.json
nano runtime/models.json
python3 setup.py configure runtime/models.json
sudo docker compose config --quiet
sudo docker compose up -d
sudo docker compose ps
curl --fail http://127.0.0.1:4000/health/liveliness
```

For every model, set:

- `host` and `api_model`: exactly the values stored in your app's `models` table
  (and `bots/models.yaml`). Replace existing `TODO` IDs before importing bots.
- `provider`: the real upstream API (`openai`, `anthropic`, or `groq`).
- `key_slot`: which of your three provider keys to use (1, 2, or 3). Slots 4 to 8 are spare, for a
  [backup provider](OPERATIONS.md#use-a-second-provider-for-the-same-model).
- `alias`: your own unique name for this route; not a model chosen by participants.
- `max_parallel_requests`: simultaneous upstream calls for that deployment. Tune
  against your account's limits; this isn't a requests-per-minute or token limit.

Add as many model entries as needed. A key slot can serve multiple models.
This version assigns each host/model to one explicit slot; it does not balance
several provider keys for the identical model. No fallback switches challenges
or models when a model or wallet is exhausted. To try the same model on a second
provider, see [Use a second provider](OPERATIONS.md#use-a-second-provider-for-the-same-model).

Built-in pricing comes from LiteLLM's model cost map. If a chosen model is absent,
add **both** `input_cost_per_token` and `output_cost_per_token` with real positive
prices, expressed in dollars per token. Models with cache/tier-specific pricing
need the corresponding LiteLLM pricing configuration too. Verify priced requests
against the provider dashboard before the event. Unknown prices must not be
accepted as zero; the app's organiser_cost fields are not used by LiteLLM.

After inventory or `.env` edits, regenerate config and recreate the proxy:

```bash
python3 setup.py configure runtime/models.json
sudo docker compose up -d --force-recreate proxy
```

## 3. Provision any number of teams

The teams must already exist in the **app's Supabase**. In Supabase Studio's SQL
editor, this read-only query shows the IDs to use (CSV export can then be edited
with your chosen budget amounts):

```sql
select id as team_id, name from public.teams order by name;
```

The provisioning CSV must use the exact header `team_id,budget_usd`, without the
`name` column. This script provisions
spending keys; it does not create participant accounts/passwords. Copy the actual
`public.teams.id` UUIDs into a CSV (not names or registration numbers):

```bash
nano runtime/teams.csv
```

```csv
team_id,budget_usd
11111111-1111-4111-8111-111111111111,5.00
22222222-2222-4222-8222-222222222222,5.00
```

Replace those example UUIDs. The $5.00 cap is only an example: keep part of your
funding in reserve for testing, and choose budgets so **all existing plus new allocations** fit your funding. Provider
wallets remain separate even when a team's allowance is shared across providers.

```bash
python3 setup.py teams runtime/teams.csv
python3 setup.py spend
```

Append future team UUIDs to the CSV and run the same command. Existing keys and
budgets are preserved. Lost API responses reuse a saved candidate key rather than
creating another allowance. Keep `pending-keys.json` too if an operation fails.
To intentionally adjust existing total allowances or allowed models:

```bash
python3 setup.py teams runtime/teams.csv --update
```

`--update` changes total caps; it **does not reset money already spent**.
For detailed top-up examples, one-team changes and bulk updates, see
[budget changes](OPERATIONS.md#increase-or-decrease-budgets). Keys
aren't printed. LiteLLM represents each competition team here as one budgeted key,
labelled with its app team UUID, rather than a second set of team/member accounts.
All sessions for that app team use the same key. Don't generate extra keys for it
in the UI: those would have independent allowances in this design.

You can test a provisioned key directly, independently of app login. This
makes **one paid request** from that team's budget (replace UUID/alias):

```bash
python3 setup.py smoke 11111111-1111-4111-8111-111111111111 challenge-openai
python3 setup.py spend
```

Allow a few seconds for background spend logging. Repeat with your Claude alias.

## 4. Connect the backend

From the project root, find the absolute folder path:

```bash
cd ~/acm-aoc-jailbreak
realpath deploy/litellm/runtime
nano backend/.env
```

LiteLLM is enabled by default, even when `LITELLM_ENABLED` is omitted. Missing
gateway configuration or team keys must be fixed; requests do not fall back to
direct provider credentials. If an existing deployment has `LITELLM_ENABLED=false`,
change it to `true`. Add/update these values, replacing the paths with the output above:

```dotenv
LITELLM_ENABLED=true
LITELLM_BASE_URL=http://127.0.0.1:4000/v1
LITELLM_ROUTES_FILE=/path/to/app/deploy/litellm/runtime/routes.json
LITELLM_TEAM_KEYS_FILE=/path/to/app/deploy/litellm/runtime/team-keys.json
CHAT_MAX_OPEN_TURNS=1
CHAT_MAX_OUTPUT_TOKENS=2048
```

Keep your existing Supabase/session settings. The account running FastAPI must be
able to read the private runtime files. Run provisioning as that account (not
sudo); don't loosen secrets to world-readable permissions. Restart your existing
backend process/service after changing its environment. For a manual launch:

```bash
cd ~/acm-aoc-jailbreak/backend
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Use your actual virtualenv path. Frontend `.env.local` must have:

```dotenv
NEXT_PUBLIC_USE_MOCK=false
BACKEND_URL=http://127.0.0.1:8001
```

Rebuild/restart Next.js after changing those values. Port 8001 avoids Supabase's
usual port 8000. See the project's regular deployment instructions for its process
manager; don't start a duplicate backend if one is already running.

### Apply the quota removal to an existing Supabase database

The old per-model token quota is removed from chat admission and the mock. The
team dashboard shows tokens **used** only; there are no quota or remaining-token
columns, chat lockouts, or admin token-limit editor. LiteLLM enforces dollar
allowances. Per-call output caps, context limits, rate limits, and open-turn limits
remain in place.

For an existing server DB, pause traffic while deploying the changed app and
reapply these SQL function files (do not rerun `schema.sql`):

```bash
cd ~/supabase-local
sudo docker compose exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 < ~/acm-aoc-jailbreak/supabase/chat.sql
sudo docker compose exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 < ~/acm-aoc-jailbreak/supabase/team_pages.sql
sudo docker compose exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 < ~/acm-aoc-jailbreak/supabase/admin.sql
```

Use your actual Supabase Compose directory if it differs. Each file is safe to
rerun. Fresh databases get the new behavior from the usual SQL setup order.
`models.token_limit_per_team` is retained, unused, for import/rollback compatibility;
no model rows, credit ledger, conversations, or scores are deleted. You do not need
to set that column to a huge number. Old app versions expect quota fields in API
responses, so deploy/restart the frontend and backend together before reopening
traffic. To roll back, restore the previous app version and its three SQL files;
the legacy column and historical values remain available.

Use `setup.py spend` for dollar accounting. Teams see their own remaining dollars
once you run `setup.py budget-reader`: see [OPERATIONS.md](OPERATIONS.md#show-teams-their-budget).
Failed turns may spend real money at the provider even if the app marks them
failed. Reviewer calls also consume that team's budget. Submission judging is now
implemented by the backend worker; it does not inherit a participant gateway key.
Configure its `JUDGE_BASE_URL`, `JUDGE_MODEL` and `JUDGE_API_KEY` with a separate
capped organiser virtual key, as described in the
[backend setup](../../backend/README.md). Without that gateway configuration,
direct judge calls are outside participant dollar accounting.
When LiteLLM is disabled there is **no team spending
allowance enforcement**; keep it enabled for the funded competition.

## 5. Check before opening registration

Production sign-in is implemented on the PR's current base. Apply
`supabase/auth.sql` after the app's other setup SQL and configure Supabase Auth
and the matching frontend/backend session secret as described in
[backend authentication setup](../../backend/README.md).
Use HTTPS for the public site: production session cookies are marked Secure.
Never enable passwordless development auth for the competition.

Log in as a provisioned team, send a short message, then run `setup.py spend`.
Check both provider families, tools, and any reviewer models (each needs a route).
All upstream calls must go through the gateway for its accounting to cover them.
Standalone scripts/playtests still use direct provider keys and aren't team-budgeted.

Using a disposable test team with a tiny allowance, verify an over-budget request
is rejected before an upstream call. Verify a missing team key fails closed.
Try two team sessions concurrently, restart the proxy, and confirm recorded spend
survives and no allowance resets. Check pricing against provider usage, including
cached tokens. Configure provider-account spending limits separately where available.
The chat shows a dedicated insufficient-budget message when LiteLLM returns its
structured `budget_exceeded` error. It covers an exhausted allowance or a remaining
amount too small to reserve the request. Other provider/rate-limit failures stay
separate; raw provider messages are never shown.

Reservations and fail-closed enforcement are enabled; Redis stores shared counters,
Postgres stores durable spend. This doesn't make the app's in-process chat tasks
a durable worker queue. Infrastructure failures and provider billing discrepancies
still require reconciliation; leave an organiser reserve.

## Maintenance, private UI, backup, rollback

On your laptop, access the gateway UI through SSH (use the generated master key
privately as the admin password; do not paste it into participant accounts):

```bash
ssh -N -L 4000:127.0.0.1:4000 your-server
```

Open http://localhost:4000/ui. Don't expose this port through Nginx.

On the server, back up accounting plus private configuration before upgrades:

```bash
cd ~/acm-aoc-jailbreak/deploy/litellm
umask 077
sudo docker compose exec -T db pg_dump -U litellm -d litellm -Fc > accounting.dump
```

Securely back up `.env` and `runtime/` alongside this dump. Pause participant
traffic for a consistent accounting backup/restore. Never run `docker compose
down -v` on the event deployment: it deletes accounting and counters. `down`
without `-v` retains named volumes. On restore, reconcile Redis with restored DB
spend before reopening traffic; don't reuse counters from a different snapshot.

To disable the integration, set `LITELLM_ENABLED=false`, restore backend provider
keys, then restart FastAPI. Disabling the proxy
also disables its dollar-budget enforcement, so do this only with traffic paused.

## Local checks and sources

```bash
python3 deploy/litellm/test_setup.py
cd backend
python tests/test_gateway.py
python tests/test_engine.py
python tests/test_defences.py
```

Tests use fake provider transports. Docker startup and paid provider billing must
be checked on the target machine after real keys/model IDs are selected.

- https://docs.litellm.ai/docs/proxy/virtual_keys
- https://docs.litellm.ai/docs/proxy/users
- https://docs.litellm.ai/docs/proxy/cost_tracking
- https://docs.litellm.ai/docs/proxy/redis_requirements/
- https://github.com/BerriAI/litellm/releases/tag/v1.106.6
