"""Loads and checks bots/models.yaml and the bot files. Shared by the scripts."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BOTS_DIR = ROOT / "bots"
MODELS_FILE = BOTS_DIR / "models.yaml"

LABELS = ["Bot A", "Bot B", "Bot C", "Bot D", "Bot E", "Bot F"]
PLACEHOLDER = "{{SECRET}}"
DEFAULT_CONTEXT_LIMIT = 8000
# Answers longer than this are refused by submit_answer, so it could never match.
MAX_SECRET_LENGTH = 500
# Marks an api_model that hasn't been chosen yet.
TODO = "TODO"
# How to reach each host. Open-source hosts use the OpenAI-compatible API.
HOSTS = {
    "openai": {"key": "OPENAI_API_KEY", "base_url": None},
    "anthropic": {"key": "ANTHROPIC_API_KEY", "base_url": None},
    "groq": {"key": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1"},
}

# These hosts are reached only through LiteLLM, never a direct provider client.
GATEWAY_ONLY_HOSTS = {"bedrock", "openai_compatible"}

MODEL_FIELDS = {
    "id", "provider", "display_name", "api_model", "host", "organiser_cost_per_1k_input",
    "organiser_cost_per_1k_output", "token_limit_per_team", "enabled",
}
BOT_REQUIRED = {
    "level", "label", "model", "system_prompt", "defences", "tools",
    "check_type", "description", "goal", "enabled",
}
BOT_OPTIONAL = {"secret", "rubric", "context_limit_tokens"}
DEFENCE_FIELDS = {"inputFilter", "outputFilter", "reviewerModel"}
# Optional, so files written before these existed still load.
DEFENCE_OPTIONAL = {"reviewerNote", "blockedReply"}
MAX_FILTER_WORDS = 200
MAX_FILTER_WORD_LENGTH = 100
MAX_REVIEWER_NOTE = 2000
MAX_BLOCKED_REPLY = 500
TOOL_FIELDS = {"name", "description", "parameters", "fakeResponses", "defaultResult"}
PARAMETER_TYPES = {"string", "number", "integer", "boolean"}


def is_int(value):
    # In Python, true and false also count as integers.
    return isinstance(value, int) and not isinstance(value, bool)


def is_text(value):
    return isinstance(value, str) and value.strip() != ""


def read_yaml(path):
    """Returns (data, error). Errors give the line but never quote the file."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f), None
    except yaml.MarkedYAMLError as e:
        line = e.problem_mark.line + 1 if e.problem_mark else "?"
        return None, f"not valid YAML near line {line}. Check indentation and quotes"
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        return None, f"can't read the file ({type(e).__name__})"


def load_models(path=MODELS_FILE):
    """Returns (models in Bot A to F order, errors)."""
    data, error = read_yaml(path)
    if error:
        return [], [f"{path.name}: {error}"]
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or len(models) != len(LABELS):
        return [], [f"{path.name}: `models` must list exactly {len(LABELS)} models"]

    errors = []
    seen = set()
    for i, model in enumerate(models, 1):
        where = f"{path.name}: model {i}"
        if not isinstance(model, dict):
            errors.append(f"{where}: must have named fields")
            continue
        for field in sorted((MODEL_FIELDS - {"token_limit_per_team"}) - model.keys()):
            errors.append(f"{where}: `{field}` is missing")
        for field in sorted(model.keys() - MODEL_FIELDS):
            errors.append(f"{where}: unknown field `{field}`")
        if not is_text(model.get("id")):
            errors.append(f"{where}: `id` must be text")
        elif model["id"] in seen:
            errors.append(f"{where}: `id` {model['id']} is used twice")
        else:
            seen.add(model["id"])
        if "provider" in model and model["provider"] not in ("openai", "anthropic", "open"):
            errors.append(f"{where}: `provider` must be openai, anthropic or open")
        if "display_name" in model and not is_text(model["display_name"]):
            errors.append(f"{where}: `display_name` must be text")
        if "api_model" in model and not is_text(model["api_model"]):
            errors.append(f"{where}: `api_model` must be the real model ID, or {TODO}")
        if "host" in model and model["host"] not in (HOSTS.keys() | GATEWAY_ONLY_HOSTS):
            errors.append(f"{where}: `host` must be one of {', '.join(sorted(HOSTS.keys() | GATEWAY_ONLY_HOSTS))}")
        for field in ("organiser_cost_per_1k_input", "organiser_cost_per_1k_output"):
            value = model.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{where}: `{field}` must be a number, 0 or more")
        limit = model.get("token_limit_per_team", 0)
        if not is_int(limit) or limit < 0:
            errors.append(f"{where}: `token_limit_per_team` must be a whole number, 0 or more")
        if not isinstance(model.get("enabled", True), bool):
            errors.append(f"{where}: `enabled` must be true or false")
    return models, errors


def check_tools(tools):
    errors = []
    if not isinstance(tools, list):
        return ["`tools` must be a list, or [] for none"]
    names = set()
    for i, tool in enumerate(tools, 1):
        where = f"tool {i}"
        if not isinstance(tool, dict):
            errors.append(f"{where}: must have named fields")
            continue
        for field in sorted(TOOL_FIELDS - tool.keys()):
            errors.append(f"{where}: `{field}` is missing")
        # Tools are fake, so a field such as `url` or `command` is always a mistake.
        for field in sorted(tool.keys() - TOOL_FIELDS):
            errors.append(f"{where}: unknown field `{field}`. Tools are fake and only return fixed text")
        name = tool.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            errors.append(f"{where}: `name` may only use letters, numbers, _ and -")
        elif name in names:
            errors.append(f"{where}: the name {name} is used twice")
        else:
            names.add(name)
        if "description" in tool and not is_text(tool["description"]):
            errors.append(f"{where}: `description` must be text")
        if "defaultResult" in tool and not isinstance(tool["defaultResult"], str):
            errors.append(f"{where}: `defaultResult` must be text. Quote it")

        parameters = tool.get("parameters", {})
        if not isinstance(parameters, dict):
            errors.append(f"{where}: `parameters` must map each input to its type")
            parameters = {}
        for parameter, kind in parameters.items():
            if not isinstance(kind, str) or kind not in PARAMETER_TYPES:
                errors.append(f"{where}: parameter {parameter} must be string, number, integer or boolean")

        responses = tool.get("fakeResponses", [])
        if not isinstance(responses, list):
            errors.append(f"{where}: `fakeResponses` must be a list, or [] for none")
            continue
        for j, response in enumerate(responses, 1):
            if not isinstance(response, dict) or set(response) != {"when", "result"}:
                errors.append(f"{where}, fake response {j}: needs exactly `when` and `result`")
                continue
            if not isinstance(response["result"], str):
                errors.append(f"{where}, fake response {j}: `result` must be text. Quote it")
            when = response["when"]
            if not isinstance(when, dict):
                errors.append(f"{where}, fake response {j}: `when` must map inputs to values")
                continue
            for parameter in sorted(when.keys() - parameters.keys(), key=str):
                errors.append(f"{where}, fake response {j}: `when` uses {parameter}, which isn't in `parameters`")
    return errors


def check_word_list(name, value):
    if not isinstance(value, list) or not value:
        return [f"`defences.{name}` must be false or a list of words to block"]
    errors = []
    if len(value) > MAX_FILTER_WORDS:
        errors.append(f"`defences.{name}` can list at most {MAX_FILTER_WORDS} words")
    for i, word in enumerate(value, 1):
        if not is_text(word):
            errors.append(f"`defences.{name}` word {i} must be text. Quote it")
        elif len(word) > MAX_FILTER_WORD_LENGTH:
            errors.append(f"`defences.{name}` word {i} can't be longer than {MAX_FILTER_WORD_LENGTH} characters")
    return errors


def check_defences(defences, model_ids, check_type, secret):
    if not isinstance(defences, dict) or not DEFENCE_FIELDS <= set(defences) <= DEFENCE_FIELDS | DEFENCE_OPTIONAL:
        return ["`defences` needs inputFilter, outputFilter and reviewerModel, and can also have reviewerNote and blockedReply"]
    errors = []

    if defences["inputFilter"] is not False:
        errors += check_word_list("inputFilter", defences["inputFilter"])

    output = defences["outputFilter"]
    if output is True:
        if check_type == "judge":
            errors.append("`defences.outputFilter` can only be true on an answer bot. List the words to block instead")
    elif output is not False:
        errors += check_word_list("outputFilter", output)

    reviewer = defences["reviewerModel"]
    note = defences.get("reviewerNote", "")
    if reviewer is not None and reviewer not in model_ids:
        errors.append("`defences.reviewerModel` must be null or an `id` from bots/models.yaml")
    if not isinstance(note, str):
        errors.append("`defences.reviewerNote` must be text")
    elif reviewer is not None and not note.strip():
        errors.append("a reviewer model needs `defences.reviewerNote`, saying what to block")
    elif reviewer is None and note.strip():
        errors.append("`defences.reviewerNote` only works with a reviewerModel")
    elif len(note) > MAX_REVIEWER_NOTE:
        errors.append(f"`defences.reviewerNote` can't be longer than {MAX_REVIEWER_NOTE} characters")

    blocked = defences.get("blockedReply", "")
    if not isinstance(blocked, str):
        errors.append("`defences.blockedReply` must be text")
    elif len(blocked) > MAX_BLOCKED_REPLY:
        errors.append(f"`defences.blockedReply` can't be longer than {MAX_BLOCKED_REPLY} characters")

    # The reviewer note goes to a second model, and the blocked reply to teams.
    for field, text in (("reviewerNote", note), ("blockedReply", blocked)):
        if isinstance(text, str) and PLACEHOLDER in text:
            errors.append(f"`defences.{field}` can't contain {PLACEHOLDER}")
        if isinstance(text, str) and is_text(secret) and secret in text:
            errors.append(f"`defences.{field}` can't contain the password")
    return errors


def check_bot(bot, models):
    """Returns a list of mistakes. Messages never contain a secret, prompt or rubric."""
    if not isinstance(bot, dict):
        return ["the file must have named fields, as in TEMPLATE.yaml"]

    errors = [f"`{field}` is missing" for field in sorted(BOT_REQUIRED - bot.keys())]
    errors += [f"unknown field `{field}`" for field in sorted(bot.keys() - BOT_REQUIRED - BOT_OPTIONAL)]
    model_ids = [model.get("id") for model in models]

    level = bot.get("level")
    if "level" in bot and (not is_int(level) or not 1 <= level <= 5):
        errors.append("`level` must be a whole number from 1 to 5")
    label = bot.get("label")
    if "label" in bot and label not in LABELS:
        errors.append('`label` must be "Bot A" to "Bot F"')

    model = bot.get("model")
    if "model" in bot and model not in model_ids:
        errors.append("`model` must be an `id` from bots/models.yaml")
    elif model in model_ids and label in LABELS and model_ids.index(model) != LABELS.index(label):
        errors.append(f"{label} must use the model {model_ids[LABELS.index(label)]}, because bots follow the model order")

    check_type = bot.get("check_type")
    if "check_type" in bot and check_type not in ("answer", "judge"):
        errors.append("`check_type` must be answer or judge")
    elif is_int(level) and check_type == "answer" and level != 1:
        errors.append("levels 2 to 5 must use `check_type: judge`")
    elif is_int(level) and check_type == "judge" and level == 1:
        errors.append("Level 1 must use `check_type: answer`")

    prompt = bot.get("system_prompt")
    if "system_prompt" in bot and not is_text(prompt):
        errors.append("`system_prompt` must be text")
        prompt = None
    if prompt:
        for found in sorted(set(re.findall(r"\{\{.*?\}\}", prompt)) - {PLACEHOLDER}):
            errors.append(f"`system_prompt` has an unknown placeholder {found}. Only {PLACEHOLDER} is allowed")

    secret = bot.get("secret")
    if check_type == "answer":
        if not is_text(secret):
            errors.append('answer bots need `secret`, as quoted text, such as "example-password-1"')
        elif secret != secret.strip():
            errors.append("`secret` can't start or end with a space, because answers are trimmed and could never match")
        elif len(secret) > MAX_SECRET_LENGTH:
            errors.append(f"`secret` can't be longer than {MAX_SECRET_LENGTH} characters")
        elif prompt and secret in prompt:
            errors.append(f"`system_prompt` contains the password. Write {PLACEHOLDER} instead")
        if prompt and PLACEHOLDER not in prompt:
            errors.append(f"answer bots need {PLACEHOLDER} in `system_prompt`")
        if "rubric" in bot:
            errors.append("answer bots can't have `rubric`")
    elif check_type == "judge":
        if not is_text(bot.get("rubric")):
            errors.append("judge bots need `rubric`, as text")
        if "secret" in bot:
            errors.append("judge bots can't have `secret`")
        if prompt and PLACEHOLDER in prompt:
            errors.append(f"judge bots can't use {PLACEHOLDER}, because they have no password")

    for field in ("description", "goal"):
        text = bot.get(field)
        if field in bot and not is_text(text):
            errors.append(f"`{field}` must be text")
        elif is_text(text):
            if PLACEHOLDER in text:
                errors.append(f"`{field}` is shown to teams and can't contain {PLACEHOLDER}")
            if is_text(secret) and secret in text:
                errors.append(f"`{field}` is shown to teams and can't contain the password")

    if "defences" in bot:
        errors += check_defences(bot["defences"], model_ids, check_type, secret)

    if "tools" in bot:
        errors += check_tools(bot["tools"])
    if "enabled" in bot and not isinstance(bot["enabled"], bool):
        errors.append("`enabled` must be true or false")
    limit = bot.get("context_limit_tokens", DEFAULT_CONTEXT_LIMIT)
    if not is_int(limit) or limit <= 0:
        errors.append("`context_limit_tokens` must be a whole number above 0")
    return errors


def load_bot(path, models):
    """Returns (bot, errors) for one file."""
    bot, error = read_yaml(path)
    if error:
        return None, [error]
    return bot, check_bot(bot, models)


def bot_paths():
    return sorted(
        path for path in BOTS_DIR.iterdir()
        if path.suffix in (".yaml", ".yml")
        and path.is_file()
        and path.stem.upper() != "TEMPLATE"
        and path != MODELS_FILE
    )


def load_bots(models):
    """Returns (bots, errors) for every bot file, including clashes between files."""
    bots = []
    errors = []
    slots = {}
    passwords = {}
    for path in bot_paths():
        bot, bot_errors = load_bot(path, models)
        errors += [f"{path.name}: {error}" for error in bot_errors]
        if bot_errors:
            continue
        slot = (bot["level"], bot["label"])
        if slot in slots:
            errors.append(f"{path.name}: level {slot[0]} {slot[1]} is already in {slots[slot]}")
        slots[slot] = path.name
        if bot["check_type"] == "answer":
            if bot["secret"] in passwords:
                errors.append(f"{path.name}: has the same password as {passwords[bot['secret']]}")
            passwords[bot["secret"]] = path.name
        bots.append(bot)
    return bots, errors


def model_row(model):
    """The `models` table row. An api_model still marked TODO is saved as empty."""
    # The unused legacy column remains for compatibility with existing databases.
    row = {field: model[field] for field in MODEL_FIELDS - {"token_limit_per_team"}}
    row["token_limit_per_team"] = model.get("token_limit_per_team", 0)
    if row["api_model"] == TODO:
        row["api_model"] = None
    return row


def bot_row(bot, models):
    """The `bots` table row."""
    names = {model["id"]: model["display_name"] for model in models}
    defences = dict(bot["defences"])
    # docs/INTERFACE.md stores the reviewer as a display name.
    if defences["reviewerModel"] is not None:
        defences["reviewerModel"] = names[defences["reviewerModel"]]
    return {
        "level": bot["level"],
        "label": bot["label"],
        "display_order": LABELS.index(bot["label"]) + 1,
        "model_id": bot["model"],
        "description": bot["description"],
        "goal": bot["goal"],
        "system_prompt": bot["system_prompt"],
        "defences": defences,
        "tools": bot["tools"],
        "check_type": bot["check_type"],
        "secret": bot.get("secret"),
        "rubric": bot.get("rubric"),
        "context_limit_tokens": bot.get("context_limit_tokens", DEFAULT_CONTEXT_LIMIT),
        "enabled": bot["enabled"],
    }
