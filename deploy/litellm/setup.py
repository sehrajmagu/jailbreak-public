#!/usr/bin/env python3
"""Local operator CLI; stdlib only. Run from this directory, never from the browser."""
import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

HOME = Path(__file__).resolve().parent
RUNTIME = HOME / "runtime"
# Slots above 3 are spare, for backups such as a second Cloudflare account.
KEY_SLOTS = range(1, 9)


def save(path, value):
    """Atomic, private replacement: the app never observes half a key registry."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(value if isinstance(value, str) else json.dumps(value, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def init():
    if (HOME / ".env").exists():
        raise ValueError(".env already exists; keeping existing secrets.")
    save(HOME / ".env", "\n".join([
        "LITELLM_IMAGE=ghcr.io/berriai/litellm:v1.106.6",
        "LITELLM_MASTER_KEY=sk-" + secrets.token_hex(32),
        "LITELLM_SALT_KEY=" + secrets.token_hex(32),
        "POSTGRES_PASSWORD=" + secrets.token_hex(32),
        # The slots can be any mix of supported providers, not necessarily one provider each.
        "PROVIDER_KEY_1=", "PROVIDER_KEY_2=", "PROVIDER_KEY_3=", "PROVIDER_KEY_4=",
        "AWS_REGION_NAME=", "AWS_ACCESS_KEY_ID=", "AWS_SECRET_ACCESS_KEY=", "AWS_SESSION_TOKEN=", "",
        "CLOUDFLARE_ACCOUNT_ID=", "CLOUDFLARE_PLAN=", "",
    ]))
    print("Created private .env. Add your three provider keys there.")


def warn_about_empty_keys(models):
    """A backup with no key just fails quietly, so say so now. Prints names, never values."""
    if not (HOME / ".env").exists():
        return
    env = dict(line.split("=", 1) for line in (HOME / ".env").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    names = {model["litellm_params"].get("api_key", "").removeprefix("os.environ/") for model in models}
    for name in sorted(names - {""}):
        if not env.get(name, "").strip():
            print(f"Warning: {name} is empty in .env, so routes using it will fail.")


def configure(path):
    inventory = json.loads(Path(path).read_text())["models"]
    if not inventory:
        raise ValueError("At least one model is required.")
    models, routes, aliases = [], {}, set()
    # A backup is the same model on another provider, so it shares its primary's alias.
    primaries = {entry["alias"] for entry in inventory if entry.get("backup_order") is None}
    backed_up = {entry["alias"] for entry in inventory if entry.get("backup_order") is not None}
    for entry in inventory:
        alias, host, model = entry["alias"], entry["host"], entry["api_model"]
        slot, provider = entry.get("key_slot"), entry["provider"]
        iam = provider == "bedrock" and entry.get("bedrock_auth") == "iam"
        backup = entry.get("backup_order")
        if not alias or (alias, backup) in aliases or not model or "REPLACE" in model or model == "TODO":
            raise ValueError("Use unique aliases and real provider model IDs.")
        if backup is not None:
            if isinstance(backup, bool) or not isinstance(backup, int) or backup < 2 or alias not in primaries:
                raise ValueError("backup_order must be 2 or more, on an alias that also has a primary entry.")
            if entry.get("free_model") is not True and "input_cost_per_token" not in entry:
                raise ValueError("A backup must set free_model: true or its real prices.")
        if (not iam and slot not in KEY_SLOTS) or host not in ("openai", "anthropic", "groq", "bedrock", "openai_compatible"):
            raise ValueError("Use key_slot 1..8 (except Bedrock IAM) and a supported app host.")
        if provider not in ("openai", "anthropic", "groq", "bedrock"):
            raise ValueError("Supported providers: openai, anthropic, groq, bedrock.")
        if (host == "bedrock") != (provider == "bedrock"):
            raise ValueError("Bedrock routes must use both host and provider bedrock.")
        if host == "openai_compatible" and provider != "openai":
            raise ValueError("Custom compatible hosts use the openai protocol provider.")
        route = f"{host}:{model}"
        if backup is None and route in routes:
            raise ValueError("Each host:model must have exactly one explicit route.")
        parallel = entry.get("max_parallel_requests", 5)
        if not isinstance(parallel, int) or parallel < 1:
            raise ValueError("max_parallel_requests must be positive.")
        aliases.add((alias, backup))
        # The app only ever asks for the alias, so a backup needs no route of its own.
        if backup is None:
            routes[route] = alias
        params = {"model": f"{provider}/{model}", "max_parallel_requests": parallel}
        if alias in backed_up:
            params["order"] = backup or 1
        if not iam:
            params["api_key"] = f"os.environ/PROVIDER_KEY_{slot}"
        if provider == "bedrock":
            # No region or credential type is guessed. IAM uses AWS_* environment
            # credentials; bearer auth uses the selected private key slot.
            if entry.get("bedrock_auth") not in ("bearer", "iam"):
                raise ValueError("Bedrock requires explicit bedrock_auth: bearer or iam.")
            region = entry.get("aws_region_name")
            if not isinstance(region, str) or not region or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in region):
                raise ValueError("Set the actual Bedrock aws_region_name.")
            params["aws_region_name"] = region
        if "api_base" in entry:
            address = urlsplit(entry["api_base"])
            if provider != "openai" or address.scheme != "https" or not address.hostname or address.username or address.password or address.query or address.fragment:
                raise ValueError("Custom OpenAI-compatible API bases must be HTTPS URLs without credentials, query or fragment.")
            params["api_base"] = entry["api_base"].rstrip("/")
        if host == "openai_compatible" and "api_base" not in params:
            raise ValueError("A custom OpenAI-compatible host requires api_base.")
        # Optional custom pricing is per token, not per million. Omit for built-in pricing.
        if ("input_cost_per_token" in entry) != ("output_cost_per_token" in entry):
            raise ValueError("Set both custom input/output prices, or neither.")
        for name in ("input_cost_per_token", "output_cost_per_token"):
            if name in entry:
                rate = entry[name]
                if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0 or (rate == 0 and entry.get("free_model") is not True):
                    raise ValueError("Custom rates must be finite and positive; zero requires explicit free_model: true.")
                params[name] = rate
        if entry.get("free_model") is True:
            if any(entry.get(name, 0) != 0 for name in ("input_cost_per_token", "output_cost_per_token")):
                raise ValueError("A declared free model cannot have nonzero custom prices.")
            params.update(input_cost_per_token=0, output_cost_per_token=0)
        models.append({"model_name": alias, "litellm_params": params})
    config = {
        "model_list": models,
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY",
                             "fail_closed_budget_enforcement": True,
                             "disable_budget_reservation": False},
        "router_settings": {"redis_host": "os.environ/REDIS_HOST", "redis_port": "os.environ/REDIS_PORT",
                            "num_retries": 0, "fallbacks": [], "context_window_fallbacks": [],
                            "allowed_fails": 0},
        "litellm_settings": {
            "turn_off_message_logging": True,
            "cache": True,
            # Redis backs accounting; [] disables response caching for challenge calls.
            "cache_params": {"type": "redis", "host": "os.environ/REDIS_HOST",
                             "port": "os.environ/REDIS_PORT", "supported_call_types": []},
        },
    }
    # JSON is valid YAML, so no extra Python/YAML dependency is needed on the server.
    save(RUNTIME / "config.json", config)
    save(RUNTIME / "routes.json", routes)
    warn_about_empty_keys(models)
    print(f"Configured {len(models)} explicit routes. Recreate the proxy after edits.")


def configure_direct_claude(path, model, key_slot):
    """Replace only the Claude route; retain its alias, concurrency and other providers."""
    if not isinstance(model, str) or not model.startswith("claude-") or key_slot not in (1, 2, 3):
        raise ValueError("Use a direct Claude model ID (claude-...) and key_slot 1..3.")
    path = Path(path)
    inventory = json.loads(path.read_text())
    previous = [row for row in inventory["models"] if row["alias"] == "claude-fable"]
    if len(previous) > 1:
        raise ValueError("The Claude alias must occur only once.")
    entry = {
        "alias": "claude-fable", "host": "anthropic", "provider": "anthropic",
        "api_model": model, "key_slot": key_slot,
        "max_parallel_requests": previous[0].get("max_parallel_requests", 1) if previous else 1,
    }
    # AWS auth/region and old provider prices must not follow a route to Anthropic.
    inventory["models"] = [row for row in inventory["models"] if row["alias"] != entry["alias"]] + [entry]
    # Validate the full candidate before replacing the operator's inventory.
    with tempfile.TemporaryDirectory(dir=RUNTIME) as folder:
        candidate = Path(folder) / "models.json"
        save(candidate, inventory)
        configure(candidate)
    save(path, inventory)
    print("Configured direct Claude. Recreate the proxy, then update the app's shared model definition.")


def api(method, path, body=None, key=None):
    # Operator management traffic stays on localhost. The browser never gets this key.
    env = dict(line.split("=", 1) for line in (HOME / ".env").read_text().splitlines()
               if line and not line.startswith("#"))
    request = Request("http://127.0.0.1:4000" + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + (key or env["LITELLM_MASTER_KEY"]),
                               "Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        # Never print a response body: errors may echo credentials or hidden prompts.
        raise RuntimeError(f"Gateway management request failed: HTTP {error.code}") from None


def provision(path, update=False):
    with Path(path).open(newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError("CSV must contain team_id,budget_usd rows.")
    checked, seen = [], set()
    for row in rows:
        team = str(UUID(row["team_id"]))
        budget = float(row["budget_usd"])
        if team in seen or not math.isfinite(budget) or budget <= 0:
            raise ValueError("Use unique team UUIDs and positive, finite budgets.")
        seen.add(team)
        checked.append((team, budget))
    aliases = sorted(set(json.loads((RUNTIME / "routes.json").read_text()).values()))
    registry_path = RUNTIME / "team-keys.json"
    keys = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    pending_path = RUNTIME / "pending-keys.json"
    pending = json.loads(pending_path.read_text()) if pending_path.exists() else {}
    for team, budget in checked:
        if team in keys:
            if update:
                # Change allowance/access only. Never reset spend or renew the budget period.
                api("POST", "/key/update", {"key": keys[team], "max_budget": budget, "models": aliases})
            print(f"{team}: {'updated' if update else 'kept existing key/budget'}")
            continue
        key = pending.setdefault(team, "sk-" + secrets.token_hex(32))
        # Persist candidate BEFORE remote creation, so a lost response can't issue a fresh allowance.
        save(pending_path, pending)
        try:
            api("GET", "/key/info?" + urlencode({"key": hashlib.sha256(key.encode()).hexdigest()}))
        except RuntimeError as error:
            if "HTTP 404" not in str(error):
                raise
            api("POST", "/key/generate", {"key": key, "key_alias": "acm-" + team,
                "models": aliases, "max_budget": budget, "budget_duration": None,
                "max_parallel_requests": 1, "rpm_limit": 30,
                "key_type": "llm_api", "metadata": {"competition_team_id": team}})
        keys[team] = key
        save(registry_path, keys)
        pending.pop(team)
        save(pending_path, pending)
        print(f"{team}: provisioned (key stored privately)")


def spend():
    keys = json.loads((RUNTIME / "team-keys.json").read_text())
    print("team_id,spent_usd,budget_usd")
    for team, key in keys.items():
        info = api("GET", "/key/info?" + urlencode({"key": hashlib.sha256(key.encode()).hexdigest()}))["info"]
        print(f"{team},{info.get('spend', 0)},{info.get('max_budget')}")


def budget_reader():
    """Creates the read-only key the app uses to show teams their budget. Safe to re-run."""
    path = RUNTIME / "budget-reader-key"
    key = path.read_text().strip() if path.exists() else "sk-" + secrets.token_hex(32)
    # Persist candidate BEFORE remote creation, as provision does.
    save(path, key)
    try:
        api("GET", "/key/info?" + urlencode({"key": hashlib.sha256(key.encode()).hexdigest()}))
        print("Budget reader already exists.")
    except RuntimeError as error:
        if "HTTP 404" not in str(error):
            raise
        api("POST", "/key/generate", {"key": key, "key_alias": "acm-budget-reader", "key_type": "read_only"})
        print("Budget reader created (key stored privately).")


def smoke(team, alias):
    """One deliberately small paid request, using the selected team's allowance."""
    team = str(UUID(team))
    keys = json.loads((RUNTIME / "team-keys.json").read_text())
    aliases = set(json.loads((RUNTIME / "routes.json").read_text()).values())
    if alias not in aliases or team not in keys:
        raise ValueError("Use a provisioned team UUID and a configured model alias.")
    reply = api("POST", "/v1/chat/completions", {
        "model": alias, "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_completion_tokens": 32, "stream": False,
    }, key=keys[team])
    print("Gateway call completed. Reported usage:", json.dumps(reply.get("usage", {})))
    print("Run setup.py spend after spend logging settles to confirm the charge.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    config = commands.add_parser("configure")
    config.add_argument("inventory")
    claude = commands.add_parser("claude-direct", help="Switch the existing Claude alias to Anthropic's direct API")
    claude.add_argument("inventory")
    claude.add_argument("--model", default="claude-fable-5-1")
    claude.add_argument("--key-slot", type=int, choices=(1, 2, 3), default=3)
    teams = commands.add_parser("teams")
    teams.add_argument("csv")
    teams.add_argument("--update", action="store_true", help="Update budgets/access without resetting spend")
    commands.add_parser("spend")
    commands.add_parser("budget-reader", help="Let the app show teams their budget, using a read-only key")
    smoke_parser = commands.add_parser("smoke", help="Make one small PAID request against a team budget")
    smoke_parser.add_argument("team_id")
    smoke_parser.add_argument("alias")
    args = parser.parse_args()
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    RUNTIME.chmod(0o700)
    # Serialize operators, including a retry after a lost API response.
    with (RUNTIME / ".operator.lock").open("w") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "init":
            init()
        elif args.command == "configure":
            configure(args.inventory)
        elif args.command == "claude-direct":
            configure_direct_claude(args.inventory, args.model, args.key_slot)
        elif args.command == "teams":
            provision(args.csv, args.update)
        elif args.command == "budget-reader":
            budget_reader()
        elif args.command == "smoke":
            smoke(args.team_id, args.alias)
        else:
            spend()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        # Don't dump tracebacks with local variables containing provider keys.
        raise SystemExit(str(error)) from None
