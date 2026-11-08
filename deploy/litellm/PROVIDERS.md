# Bedrock and an external Qwen endpoint

The app's `host` identifies how a model is reached. Its `provider` in
`bots/models.yaml` identifies the model family: use `anthropic` for Claude on
Bedrock and `open` for a Qwen model. In `runtime/models.json`, `provider` instead
selects LiteLLM's protocol/provider adapter, as shown below.

Real provider keys stay in `deploy/litellm/.env`. A team virtual key identifies
whose dollar allowance is charged; it is not one of these upstream credentials.

## Provider slots for the requested deployment

| Bands | Provider | Credential input |
| --- | --- | --- |
| A–D | OpenAI | `PROVIDER_KEY_1` |
| E | The chosen Qwen endpoint | `PROVIDER_KEY_2` |
| F | Direct Claude API, or AWS Bedrock | `PROVIDER_KEY_3` for the selected provider's key; Bedrock IAM uses the standard AWS fields |

An organiser judge uses its own capped virtual key and an explicitly selected
model alias. It can use one of the same upstream credentials without charging a
participant key.

## Direct Claude API

The existing team gateway can call Anthropic directly. This retains the same
`claude-fable` alias, so team keys, budgets and challenge model references remain
valid. A Bedrock key cannot be used on Anthropic's direct API.
Enter an Anthropic API key privately in `PROVIDER_KEY_3` in the existing `.env`.
Preserve all other values; never commit the key or the generated runtime files.

From `deploy/litellm/`:

```bash
python3 setup.py claude-direct runtime/models.json --model claude-fable-5-1 --key-slot 3
sudo docker compose up -d --no-deps --force-recreate proxy
```

The command replaces only the Claude inventory entry, retaining its alias and
parallel request cap. It removes AWS-specific settings and old price overrides,
validates the full candidate inventory, and generates
`anthropic/claude-fable-5-1` with the private credential slot reference. Other
provider entries are retained. It makes no model request and changes no budgets.
Take a private inventory backup before switching so the old route can be restored.

Update the existing `claude-fable` entry in the private deployment model YAML to
`host: anthropic`, `provider: anthropic`, and `api_model: claude-fable-5-1`.
After verifying the gateway, enable only that model row in Supabase; do not reset
or reimport unrelated challenge data:

```sql
update public.models
set host = 'anthropic', provider = 'anthropic',
    api_model = 'claude-fable-5-1', enabled = true
where id = 'claude-fable';
```

No schema migration is needed. Existing team keys which already permit
`claude-fable` retain access. Bot YAMLs continue to use `model: claude-fable`.
The API model ID is shared metadata, not something to repeat in each challenge.

Direct Claude Fable 5.1 also carries a 30-day retention requirement unless
Anthropic has expressly authorized an exception; changing API providers does not
remove that model's data-handling requirements.
[Anthropic model ID, pricing and availability](https://platform.claude.com/docs/en/models/fable-5-1/overview)
and [retention notes](https://platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1).

## Bedrock bearer API key

This is a Bedrock API key, not an OpenAI/Anthropic direct API key. Put the token in
`PROVIDER_KEY_3`. The region and inference/model ID must come from the actual AWS
account. Do not infer a region or substitute a similarly named Claude model.

Example inventory entry; replace all placeholders:

```json
{
  "alias": "claude-fable",
  "host": "bedrock",
  "provider": "bedrock",
  "api_model": "REPLACE_WITH_AWS_MODEL_OR_INFERENCE_PROFILE_ID",
  "bedrock_auth": "bearer",
  "aws_region_name": "replace-with-actual-region",
  "key_slot": 3,
  "max_parallel_requests": 1
}
```

The generator adds `bedrock/` to the actual model/profile ID and uses the selected
key slot. In the app model YAML use `host: bedrock`, `provider: anthropic`, and the
same actual model/profile ID without adding the LiteLLM prefix.

## Bedrock IAM credentials

Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in the private `.env`. Set
`AWS_SESSION_TOKEN` as well when the credentials are temporary. Don't put an IAM
access-key ID alone into a provider-key slot: IAM needs its accompanying secret.

Use the same inventory shape with `bedrock_auth: iam`; `key_slot` can be omitted.
The generator deliberately omits `api_key` in IAM mode, allowing AWS's credential
chain to read the private AWS fields. The region is still required explicitly.
The Docker proxy must be recreated after editing environment values.

[LiteLLM Bedrock authentication](https://docs.litellm.ai/docs/providers/bedrock)

## Qwen through an OpenAI-compatible HTTPS service

This deployment selects Cloudflare Workers Free; follow [the Qwen setup](CLOUDFLARE.md).
The generic route below remains available if the organiser changes providers later.

Confirm the service, its exact API base URL, model ID and actual pricing first.
The name Qwen alone does not identify an endpoint or guarantee free usage.

```json
{
  "alias": "open-source-llm",
  "host": "openai_compatible",
  "provider": "openai",
  "api_model": "REPLACE_WITH_ACTUAL_QWEN_ID",
  "api_base": "https://replace-with-provider.example/v1",
  "key_slot": 2,
  "max_parallel_requests": 1
}
```

For the app model YAML use `host: openai_compatible`, `provider: open`, and the same
API model ID. The custom API base must be HTTPS without embedded credentials,
query parameters or a fragment. In the inventory, `provider: openai` specifies the
wire protocol; it does not claim that OpenAI hosts the model.

If the selected endpoint/model is verified to cost zero, add `free_model: true`.
This explicitly configures both token prices as zero. Do not use it for a paid
model, even if your current usage happens to be free. Otherwise rely on an
accurate built-in price or specify both real per-token cost fields. A paid model
cannot be silently marked free with zero rates. The provider's own request/free
usage limits still apply.

## Apply changes

Run on the server from `deploy/litellm/` after supplying real values:

```bash
python3 setup.py configure runtime/models.json
sudo docker compose up -d --force-recreate proxy
```

If aliases were added or renamed, refresh existing team model access. This also
applies the total caps currently in the CSV, while preserving recorded spend:

```bash
python3 setup.py teams runtime/teams.csv --update
```

Gateway-only models are intentionally unavailable in direct-provider mode and in
the direct playtest CLI. Use the site or gateway smoke test. This prevents a
Bedrock/Qwen key from accidentally being sent to another provider's SDK endpoint.

Selected OpenAI models support plain Chat Completions. Tool calling on Astra,
Terra and Sol needs the Responses API; the challenge files recovered for this
deployment currently have no tools. Don't add tools to those bands without adding
and validating that endpoint integration first.
[OpenAI model compatibility](https://developers.openai.com/api/docs/guides/latest-model)
