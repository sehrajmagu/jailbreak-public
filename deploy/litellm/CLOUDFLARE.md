# Free Qwen for the challenge and judge

The selected model is `@cf/qwen/qwen3-30b-a3b-fp8`. The app can reach it using
the existing custom OpenAI-compatible gateway route; no new Supabase instance or
public Cloudflare Worker is needed. The provider remains replaceable later.

## Private inputs

Use a Cloudflare account on **Workers Free**, as chosen for this deployment.
Cloudflare currently provides 10,000 Neurons per day; requests beyond the free
plan's allowance fail. A Workers Paid account can charge for overage, so a paid
account is not interchangeable with this free-only configuration.

In the existing server `deploy/litellm/.env`, enter:

```dotenv
# Account ID is not an API token. Use the account that owns Workers AI.
CLOUDFLARE_ACCOUNT_ID=
# Keep this free for this deployment; do not assume a paid account cannot charge.
CLOUDFLARE_PLAN=free
# Workers AI API token, scoped to that account. Never send it to the browser.
PROVIDER_KEY_2=
```

Preserve the existing generated infrastructure secrets and other provider keys.
The account ID and token are required before making any Qwen request. Merely
editing these fields does not activate a route or the judge.

## Add the route after filling the inputs

Run from the server's `deploy/litellm/` directory. This prepares configuration;
it does not make a model call. It preserves other inventory entries.

```bash
python3 - <<'PY'
import json
import re
from pathlib import Path
import setup

env = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines()
           if line and not line.startswith('#') and '=' in line)
account = env.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
if not re.fullmatch(r'[a-fA-F0-9]{32}', account):
    raise SystemExit('Fill the actual Cloudflare account ID first.')
if env.get('CLOUDFLARE_PLAN') != 'free' or not env.get('PROVIDER_KEY_2', '').strip():
    raise SystemExit('Confirm Workers Free and fill PROVIDER_KEY_2 first.')
path = Path('runtime/models.json')
inventory = json.loads(path.read_text())
entry = {
    'alias': 'open-source-llm', 'host': 'openai_compatible', 'provider': 'openai',
    'api_model': '@cf/qwen/qwen3-30b-a3b-fp8', 'key_slot': 2,
    'api_base': f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1',
    'max_parallel_requests': 1,
    # Zero cash cost is valid here only because Workers Free hard-stops at its quota.
    'free_model': True,
}
inventory['models'] = [row for row in inventory['models']
                       if row['alias'] != entry['alias']] + [entry]
setup.save(path, inventory)
setup.configure(path)
PY

# Load the new route and updated private provider token into the proxy.
sudo docker compose up -d --force-recreate proxy

# Add the Qwen alias to existing teams, preserving their caps and recorded spend.
python3 setup.py teams runtime/teams.csv --update
```

The `CLOUDFLARE_PLAN` field records the operator's confirmation; it does not change
or verify the billing plan in Cloudflare. Check the actual account before enabling
traffic. The sample intentionally does not select a paid backup. To add one, see
[Use a second provider](OPERATIONS.md#use-a-second-provider-for-the-same-model).

## Replies arrive all at once

The app doesn't stream from `openai_compatible` hosts. Cloudflare's stream sends a token
that looks like a number as a JSON number, such as `{"content": 4}`. LiteLLM 1.106.6 then
drops it and doesn't free the team's parallel slot, so numbers vanish from replies and the
team's next message gets a 429. Check this again before streaming from this host.

## If the free quota runs out

The quickest answer is to upgrade this account to Workers Paid, with no code change. It
costs at least $5 a month and keeps the free daily Neurons. Above those, usage is $0.011
per 1,000 Neurons, which is about $0.00065 for a message of 9,500 tokens in and 500 out.

1. Upgrade the account in the Cloudflare dashboard.
2. In `runtime/models.json`, raise this route's `max_parallel_requests` from 1 to about 5.
   With no backup, a second team chatting at the same moment gets an error otherwise.
3. Run `python3 setup.py configure runtime/models.json`, then
   `sudo docker compose up -d --force-recreate proxy`.
4. Set `CLOUDFLARE_PLAN=paid` in `.env`. The script above then refuses to run, because it
   only confirms Workers Free.

**Don't stretch the free quota with extra free accounts.** Cloudflare's
[terms](https://www.cloudflare.com/terms/) (section 2.2.1(c)) prohibit use "intended to
circumvent Service-specific usage limits, quotas", and allow it to suspend or terminate
accounts. That would stop this model, and any judge using it, during the event.

The route stays `free_model`, so team budgets don't limit this spending, and Cloudflare's
pricing pages don't describe a cap. Watch usage in the dashboard during the event. To keep
a provider outage from taking this model down too, see
[Use a second provider](OPERATIONS.md#use-a-second-provider-for-the-same-model).

## Judge activation

The judge can call Cloudflare directly, independently of LiteLLM team budgets.
Set these fields in the existing private `backend/.env`, preserving other values:

```dotenv
JUDGE_PROVIDER=openai_compatible
JUDGE_BASE_URL=https://api.cloudflare.com/client/v4/accounts/REPLACE_WITH_ACCOUNT_ID/ai/v1
JUDGE_MODEL=@cf/qwen/qwen3-30b-a3b-fp8
JUDGE_API_KEY=REPLACE_WITH_PRIVATE_WORKERS_AI_TOKEN
JUDGE_MAX_OUTPUT_TOKENS=2048
JUDGE_TIMEOUT_SECONDS=60
```

Replace every placeholder before starting the backend. An endpoint containing
angle-bracket placeholders is rejected at provider initialization. The 2048-token
allowance gives this reasoning model room to finish its JSON verdict; a 200-token
allowance can end before any usable verdict. The token belongs only in the private
server file, not the browser or a committed example.

Alternatively, use `JUDGE_BASE_URL=http://127.0.0.1:4000/v1`,
`JUDGE_MODEL=open-source-llm`, and a separate private LiteLLM virtual key restricted
to that alias. This key must not be a participant key or the gateway master key.
Match the credential to the endpoint: the Cloudflare token is for Cloudflare,
while a gateway virtual key is for localhost LiteLLM.

Cloudflare's account quota is shared if Bot E also uses that account. To make it
judge-only, separately move or disable Bot E and remove its participant route
access; configuring the judge alone does not change Bot E.

Check valid pass/fail examples and malformed/refused responses before enabling
grading. A provider failure must remain a pending/error submission, never an
invented verdict. The existing validator and retry worker enforce that distinction.
Once verified, restart the backend to load the real judge settings. The admin
Submissions switch controls queue claims without a restart; it does not configure
the provider or start a worker that was paused on the mock provider. Direct judge
calls do not require enabling a Qwen challenge bot.
Do not switch to a mock judge for real submissions when the free quota runs out.

## Sources

- [Cloudflare model and context window](https://developers.cloudflare.com/workers-ai/models/qwen3-30b-a3b-fp8/)
- [Cloudflare free allocation and paid overage](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [Cloudflare OpenAI-compatible endpoint](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)
