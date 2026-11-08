# Changing teams, budgets, models and keys

For the first installation, follow [the setup guide](README.md) in order. This
runbook is for operating that installation afterwards. Commands below run **on
the server**, unless labelled as laptop commands. Nothing here runs automatically.

Start each maintenance session in this directory, as the account that runs the backend:

```bash
cd ~/acm-aoc-jailbreak/deploy/litellm
```

Replace that path if your checkout is elsewhere. Use `sudo` for Docker commands,
not for `setup.py`, so the backend can still read the generated private files.

## What to edit

| Change | Edit | Apply it with |
| --- | --- | --- |
| Add a team allowance | `runtime/teams.csv` | `python3 setup.py teams runtime/teams.csv` |
| Change existing team allowances | `runtime/teams.csv` | Same command with `--update` |
| Change models, routes, provider-key slots or deployment concurrency | `runtime/models.json` | `configure`, recreate proxy, update team access as below |
| Replace one of your three provider keys | `.env` | Recreate proxy |
| Change which model a problem uses | Private bot YAML and deployment model settings | Follow [Updating live bot YAMLs](UPDATE-BOTS.md), with a matching gateway route |
| See actual recorded spend | No edit | `python3 setup.py spend` |

`runtime/teams.csv` is the operator's input, not a live database. Editing it alone
does **not** change a running team's allowance. The command applies the changes.
LiteLLM's PostgreSQL is the authority for the applied budget and recorded spend.

The app's Supabase holds team accounts, problems, conversations and scores.
`runtime/team-keys.json` privately connects each Supabase team UUID to its LiteLLM
virtual key. The CLI maintains that mapping; don't invent or replace its keys by
hand. `runtime/config.json` and `runtime/routes.json` are generated files: edit
`runtime/models.json` and regenerate them instead.

## Add teams whenever you need to

1. Create the team in the competition app's Supabase/account system first.
2. Copy its real `public.teams.id` UUID. A name or registration number won't work.
3. Append a row to `runtime/teams.csv`:

```csv
team_id,budget_usd
11111111-1111-4111-8111-111111111111,5.00
22222222-2222-4222-8222-222222222222,5.00
```

The UUIDs above are examples; replace them. Each UUID must appear only once.
There is no fixed team count in the provisioning script.

```bash
nano runtime/teams.csv
python3 setup.py teams runtime/teams.csv
python3 setup.py spend
```

Without `--update`, existing teams keep their keys, budgets and allowed models.
Only new teams get provisioned. The backend reads the mapping for each new chat
turn, so adding a team does not require restarting it or the proxy. The script
creates a private key on behalf of the team; don't give that key to participants.

If provisioning stops partway, fix the reported problem and rerun the command.
Completed teams are retained. Preserve `runtime/pending-keys.json`: it lets a retry
recover a key whose creation response was lost. Do not delete the registry or
pending file to start again, since that can create separate allowances.

## Increase or decrease budgets

**`budget_usd` is the total spending cap, not the amount left and not a top-up.**
There is no daily/monthly reset in this setup.

For example, a team has a $5.00 cap and has spent $2.00:

| Desired result | New `budget_usd` | Approximate amount left |
| --- | --- | --- |
| Leave its allowance unchanged | `5.00` | $3.00 |
| Give it $1.00 extra | `6.00` | $4.00 |
| Leave it with $1.00 to spend | `3.00` | $1.00 |
| Lower cap below the $2.00 already spent | `1.50` | $0; further calls should be refused |

Recorded spend is never undone by lowering the cap. Calls already in progress may
finish, so pause that team's traffic before a strict cutoff. Reservations can also
reject a call when its estimated maximum cost is more than the remaining amount.

To change all teams listed in your main CSV:

```bash
python3 setup.py spend
nano runtime/teams.csv
python3 setup.py teams runtime/teams.csv --update
python3 setup.py spend
```

`--update` changes budgets **and refreshes allowed models** for every listed row.
It doesn't reset spend, recreate existing keys or renew the allowance period.
No service restart is required. Check the final spend report for the applied caps.

To change just one team, make a small CSV with just that team's real UUID and its
new total cap. For example:

```bash
nano runtime/budget-change.csv
```

```csv
team_id,budget_usd
11111111-1111-4111-8111-111111111111,6.60
```

```bash
python3 setup.py teams runtime/budget-change.csv --update
python3 setup.py spend
```

Also update that team's row in your main `runtime/teams.csv`, so a later bulk update
doesn't accidentally restore the old cap. Teams omitted from an update CSV are
untouched. Update operations across several teams aren't one transaction: if a
command fails, verify the report and rerun after fixing the problem.

Use positive dollar amounts with a decimal point, without `$` symbols or thousands
separators. Zero, negative, infinite and non-numeric budgets are rejected. To pause
a team, disable its account in the app rather than entering a zero budget.
Removing a row from the CSV does **not** revoke its key or remove its allowance.
Disabling an account blocks app access; it doesn't delete or revoke the private
LiteLLM key, nor does it undo work already in progress.

### Keep allocations within your funding

Decide your total funding first, keep part of it in reserve for testing and
top-ups, and divide the rest between teams. The CLI doesn't enforce a global pool or
redistribute caps automatically when more teams join. Count existing allocations
as well as new ones. Lowering a cap cannot recover money already spent.

OpenAI, Anthropic and other provider wallets remain independent. A shared team
allowance does not transfer funds between those provider accounts. Leave room for
test requests, billable failed calls, pricing differences and organiser activity.

## Change models or which key pays for them

Each model entry in `runtime/models.json` has a `key_slot`:

| `key_slot` | Secret read from `.env` |
| --- | --- |
| `1` | `PROVIDER_KEY_1` |
| `2` | `PROVIDER_KEY_2` |
| `3` | `PROVIDER_KEY_3` |

The number is only a reference to one of your provider credentials. It is not a
team number or a budget. Several models may use the same slot. The selected key
must work with that entry's provider. Slots can belong to the same provider, but
this version doesn't pool several keys for an identical host/model route.

Edit the inventory, then apply it:

```bash
nano runtime/models.json
python3 setup.py configure runtime/models.json
sudo docker compose up -d --force-recreate proxy
python3 setup.py teams runtime/teams.csv --update
sudo docker compose ps
```

Keep every active team and its intended current cap in `runtime/teams.csv` before
that bulk update. Adding or renaming an alias requires refreshing existing keys'
allowed models; `configure` alone doesn't do it. The final `--update` also applies
CSV budget values, preserving spend. For a slot-only change with unchanged aliases,
refreshing team access is unnecessary; configure and recreate the proxy is enough.

The backend reads routes on each call; a proxy recreation briefly interrupts
requests. Pause traffic for model changes, particularly if tools/reviewers may be
running. This setup gives every provisioned team access to all configured aliases.
Per-team model allowlists aren't exposed as a CLI option.

### Different models for different problems

Bot YAML files select a model by its `id` from `bots/models.yaml`. The imported
model's `host` and real `api_model` must exactly match a gateway inventory entry.
The inventory's `alias` is a private gateway name; do not substitute it for the
provider's real `api_model` in `bots/models.yaml`.

To change a problem's model, edit its bot YAML, add/update the selected model in
`bots/models.yaml` if needed, and import those changes using the
[bot importer guide](../../bots/README.md). Add/configure a matching gateway route
and refresh team access as above before reopening traffic. Reviewer models need
routes too. All calls for a team share its one dollar allowance, regardless of
which problem or model is used. Changing models doesn't reset its recorded spend.

## Replace a provider API key

Change only the affected `PROVIDER_KEY_1`, `PROVIDER_KEY_2` or `PROVIDER_KEY_3` value:

```bash
nano .env
sudo docker compose up -d --force-recreate proxy
sudo docker compose ps
```

A plain `docker compose restart` doesn't apply changed environment values. Keep
the existing `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` and `POSTGRES_PASSWORD` as they
are. This procedure replaces a provider credential; it isn't a database-password
or master-key rotation procedure. If the replacement is for another provider,
update the corresponding model entries too.

Replacing your provider key doesn't change team virtual keys, caps or spend.
Test the replacement before revoking the old key at its provider.

## Show teams their budget

The team sidebar and dashboard show dollars left. Team keys aren't allowed to read
their own budget, so the app needs one read-only key. Run this once, with the proxy up:

```bash
python3 setup.py budget-reader
```

It's safe to re-run. It writes `runtime/budget-reader-key` next to `team-keys.json`,
where the backend finds it, so there's no backend setting to change and no restart
needed. That key can read budgets, but can't call models or change anything. Until it
exists, teams see "Unavailable" and can still play.

Teams see their applied cap and recorded spend, which can lag a call by up to a minute.
A free model costs nothing, so a team with no dollars left can still use it.

## Use a second provider for the same model

When a provider fails or runs out of quota, LiteLLM can send the request to the **same
model** on another provider. This is off unless you add it. It never switches models:
only list a provider serving the same model, because LiteLLM can't check that for you.

1. Make a provider key with a hard spending cap, and add it to `.env`. Slots 4 to 8 are spare:

   ```dotenv
   PROVIDER_KEY_4=
   ```

2. In `runtime/models.json`, add a second entry with the **same alias** and `backup_order`:

   ```json
   {
     "alias": "open-source-llm",
     "backup_order": 2,
     "host": "openai_compatible",
     "provider": "openai",
     "api_model": "qwen/qwen3-30b-a3b",
     "api_base": "https://openrouter.ai/api/v1",
     "key_slot": 4,
     "max_parallel_requests": 5,
     "free_model": true
   }
   ```

3. Apply it. Teams keep the alias they already have, so they need no update:

   ```bash
   python3 setup.py configure runtime/models.json
   sudo docker compose up -d --force-recreate proxy
   ```

To undo it, remove that entry and repeat step 3. For more backups, add more entries with
`backup_order` 3, 4 and so on, each with its own `key_slot`. They're tried in order. `configure`
warns if a slot's key is empty, because that backup would otherwise just fail quietly. If every
provider fails, the team sees the bot as unavailable.

A backup must say who pays. With `free_model: true`, teams aren't charged and you pay the
provider, so your cap there is the limit. With `input_cost_per_token` and
`output_cost_per_token` instead, teams pay from their budget, but then a team with too
little budget left is refused on this bot, even while the free provider is working.

Check these before relying on it:

- **It isn't only for running out of quota.** Any failure moves to the backup, including a
  temporary error or a revoked key, so a broken primary can go unnoticed. Watch both
  provider dashboards.
- **Busy periods use it too.** A request over the primary's `max_parallel_requests` goes
  straight to the backup, even with quota left. With the 1 in `CLOUDFLARE.md`, a second
  team chatting at the same moment uses the backup. Raise it as far as the provider allows.
- **One failure pauses the primary for all teams** for about 5 seconds, then it's tried again.
- **The same model can still differ a little.** Check that both providers serve the same
  weights and quantization. Replies are never identical, and hosts can differ on defaults.
- **The judge only uses the backup if it goes through LiteLLM.** Calling Cloudflare
  directly, it shares the free quota and grading waits once that runs out.

## Check spend, test, restart and back up

```bash
python3 setup.py spend
```

The report contains `team_id,spent_usd,budget_usd`, without exposing virtual keys.
Allow up to a minute for background spend logging. LiteLLM, rather than the
dashboard's token table, controls the team's dollar allowance.

For a test, replace the example UUID and use an alias from your inventory. This
makes **one paid request** against that team's allowance:

```bash
python3 setup.py smoke 11111111-1111-4111-8111-111111111111 challenge-openai
python3 setup.py spend
```

An ordinary proxy restart preserves PostgreSQL data, Redis volumes and team keys:

```bash
sudo docker compose restart proxy
sudo docker compose ps
```

Before an upgrade, pause traffic, back up the accounting DB and securely back up
`.env` plus all of `runtime/`. See [backup commands and rollout notes](README.md#maintenance-private-ui-backup-rollback).
Deleting Docker volumes, resetting spend or creating replacement keys is not a
normal budget-change procedure. The CLI doesn't implement spend resets or team-key
rotation; those need a separate coordinated procedure. Do not expose the master
key or private mapping to participants, source control or public support logs.

LiteLLM is on by default; an existing explicit `LITELLM_ENABLED=false` still
disables it until changed and the backend restarted. Leave it enabled for the
competition. A budget refusal shows a dedicated message in chat. After a top-up,
the team can retry its message; it does not need to log out or obtain a new key.

## If something doesn't work

| Symptom | Check |
| --- | --- |
| `kept existing key/budget` after editing a cap | Use `teams ... --update`; editing CSV alone doesn't apply budgets |
| Team missing from `spend` | Provision its actual Supabase UUID; confirm provisioning completed |
| Team works on one model but not a newly added alias | Refresh team model access with `--update`; ensure CSV caps are current |
| Insufficient AI budget message | Check the applied cap and spend with `setup.py spend`; remaining budget may be too low to reserve this request |
| Missing-key or bot-unavailable error | Team registry entry, model route, backend file permissions, provider key, and remaining allowance |
| Team sidebar shows budget "Unavailable" | Run `setup.py budget-reader`; check the proxy is up, the team is provisioned, and the backend can read `runtime/budget-reader-key` |
| Spend appears on the backup provider's dashboard | The backup is being used: check the primary's quota, key and `max_parallel_requests` |
| Budget update rejected | Positive numeric cap, unique valid UUIDs and reachable running proxy |
| Key replacement didn't take effect | Recreate `proxy`; restarting it doesn't reload `.env` |
| Gateway works but website still uses mock | Set `NEXT_PUBLIC_USE_MOCK=false`, configure backend URL, rebuild/restart frontend |
| Real login fails or loops back to login | Apply `supabase/auth.sql`, set `SUPABASE_ANON_KEY`, match session secrets in backend/frontend, and use HTTPS for production cookies |

Keep the configuration files after errors. Retry with the same records rather
than issuing fresh keys. Provider billing and container startup still require
verification on the deployment machine; the local automated tests use fake APIs.
