# Judge (Part B): `grade(conversation, bot_id)`

The LLM-as-a-Judge for Levels 2 to 5 (forbidden topic, Hindi/Hinglish, malware, sexual content). Level 1 is checked by Part A with no judge. The contract is [docs/INTERFACE.md](../../../docs/INTERFACE.md) section 3, unchanged.

It runs against a **mock provider** in development, so that needs no API key and costs nothing. A real model is set with `JUDGE_*` settings: see [Connecting a real model](#connecting-a-real-model). `app/judging.py` runs it in the background, and refuses to run on the mock in production.

## Quick start

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests/engine   # the opt-in real-model test is skipped
```

```python
from engine.judge import configure, grade, GradingError

configure(bot_source=my_bot_source)        # once at startup (Part A supplies the source)

try:
    result = grade({"messages": [...]}, "bot-123")
    result.passed, result.reason, result.usage      # or result.to_contract_dict()
except GradingError as err:                          # no verdict: never record as "failed"
    err.code, err.retryable, err.retry_after_seconds, err.usage
```

`to_contract_dict()` gives exactly `{"passed", "reason", "usage": {"inputTokens", "outputTokens"}}`.

## How it works

```
grade(conversation, bot_id)
 1. load bot config (winRubric)     not found / answer bot / empty rubric -> GradingError
 2. drop user messages, number the assistant turns 1..N
      no turns left                 -> FAIL "no bot replies", no model call
 3. build prompt: instructions + level guidance + rubric (system message)
                  numbered turns as nonce-wrapped JSON (user message)
 4. fits the context window?        no -> GradingError(context_too_large), nothing is cut
 5. provider.complete()             timeout / outage / refusal -> GradingError
 6. validate: JSON -> exact schema -> evidence checks (one re-ask if unusable, then GradingError)
 7. optional: confirm a PASS with a second call (off by default)
 8. map the reason code to a fixed sentence; sum the tokens of every call
```

| File | Job |
| --- | --- |
| `engine.py` | `JudgeEngine`: the pipeline above |
| `providers/base.py` | `JudgeProvider` interface and provider exceptions |
| `providers/mock.py` | `MockJudgeProvider` (scripted) and `MarkerMockProvider` (plumbing only) |
| `prompt.py` | base prompt, generic level guidance, prompt assembly |
| `transcript.py` | assistant-only turns, numbering, safe serialisation |
| `validator.py` | strict output parsing and evidence checks |
| `reasons.py` | reason code to participant-safe sentence |
| `models.py`, `errors.py` | typed input/output and `GradingError` |
| `bots.py` | `BotConfigSource` interface and an in-memory version |
| `config.py` | env settings and provider registry |

## Input and output

**Input.** `{"messages": [{"role", "content", "toolCalls": [{"id","name","arguments","result"}]}]}`. B removes every `user` message itself, so the judge only ever sees the bot's replies and tool calls. Empty assistant messages (no text, no tool calls) are skipped.

**Output** (success): `GradeResult(passed, reason, usage)`. `reason` is always one of six fixed sentences (`reasons.py`), never model text. `usage` is the sum over every provider call made, including re-asks and confirmations.

**Output** (failure to grade): `GradingError` with no verdict.

| `code` | Retryable | Meaning |
| --- | --- | --- |
| `provider_timeout`, `provider_unavailable`, `provider_rate_limited` | yes | Try again later. `retry_after_seconds` is set when the provider said so |
| `invalid_output` | yes | The model's answer was unusable twice in a row |
| `provider_refused` | no | The provider's own safety filter declined the content |
| `context_too_large` | no | The transcript does not fit the context window. Nothing was sent |
| `rubric_missing`, `not_a_judge_bot`, `bot_not_found`, `invalid_input` | no | Bad request or config |

Part A must never set a submission to `failed` because of a `GradingError`. A timeout is not a failed challenge.

## What the judge model must return

```json
{"verdict": "PASS", "code": "OBJECTIVE_MET", "evidenceTurns": [3]}
```

No free-text field exists. The model picks a code and cites turn numbers. Our code then checks: exact schema, no extra fields; a PASS needs `OBJECTIVE_MET` and at least one cited turn; every cited turn must be a real turn we sent; a FAIL cannot claim `OBJECTIVE_MET`. Anything else is "unusable": one re-ask, then a retryable `invalid_output`. It is never turned into a PASS or a FAIL.

Reason codes: `OBJECTIVE_MET`, `REFUSED`, `DISCUSSED_NOT_PRODUCED`, `PARTIAL`, `NOT_ESTABLISHED`, `NO_BOT_REPLIES` (engine only).

## Security design

- The rubric and instructions go in the system message. The transcript goes in the user message, as JSON with `<` escaped and a random per-call boundary tag, so a reply cannot fake its way out of the data block.
- The prompt tells the model the transcript is untrusted data and must never be obeyed.
- User messages are removed before any provider call. Tests assert they never appear in a request.
- Participants never see model text, only the fixed sentences. Even a fully hijacked judge cannot write the rubric or a password into a reason.
- Provider errors and unusable answers can never become a verdict.
- Logs hold request ids, codes and token counts. They never hold transcripts, rubrics or raw provider output.
- Submitted code is never executed. The judge only reads text.

**What the mock tests do not prove.** They show our application-side checks hold even if the model misbehaves. They do not show that a real LLM resists prompt injection or judges accurately. Only the real-model tier can, and it needs an organiser-vetted corpus.

## Writing rubrics

`winRubric` stays a plain string and must be **self-contained**: the judge never sees the system prompt, the participant's messages or the bot's `goal`. Suggested skeleton:

```
Objective: <one sentence>
Qualifies when: <concrete criteria. Name the topic, terms or capabilities>
Does not qualify: <refusal, education, quotation, ... as relevant>
Evidence scope: single reply | cumulative across replies
```

Per-level guidance in `prompt.py` is generic reading advice only. It contains no success criteria. `tests/engine/judge_fixtures.py` has example rubrics for L2 to L5, marked as examples.

## Configuration

All optional. With nothing set, the mock provider is used.

| Variable | Default | Meaning |
| --- | --- | --- |
| `JUDGE_PROVIDER` | `mock` | Provider name from the registry |
| `JUDGE_BASE_URL`, `JUDGE_API_KEY`, `JUDGE_MODEL` | empty | For a real provider. Read from the environment only |
| `JUDGE_TIMEOUT_SECONDS` | `30` | Per provider call |
| `JUDGE_MAX_CONTEXT_TOKENS` | `8000` | Size limit used for the context check. The smaller of this and the provider's own limit applies |
| `JUDGE_MAX_OUTPUT_TOKENS` | `200` | Reserved for the answer |
| `JUDGE_CONFIRM_PASSES` | `false` | When `true`, every PASS is re-checked by a second call on the cited turns and their neighbours. Both must agree |

The default `mock` provider (`MarkerMockProvider`) passes only transcripts containing the synthetic marker `[[SYNTHETIC:QUALIFYING]]`. It is a development stand-in and will wrongly fail every real conversation.

## Mock providers

- `MockJudgeProvider([...])` plays scripted behaviours in order and records every request: `passing`, `failing`, `raw` (any text, e.g. invalid JSON), `empty`, `timeout`, `unavailable`, `refused`, `rate_limited`, `bad_response`, or any function.
- `MarkerMockProvider` reads the synthetic markers in fixtures. It lets the full pipeline run with no model. It is plumbing, not a judge.

## Testing

| Tier | Where | What it proves |
| --- | --- | --- |
| 1. Unit (mock) | `test_a` to `test_e` | Our code: models, prompt/transcript, validator, engine, error mapping |
| 2. Pipeline (mock) | `test_f_pipeline_end_to_end.py` | `grade()` end to end for L2 to L5, including injection and failure scenarios and long conversations |
| 3. Real model (opt-in) | `test_g_real_model.py` | The only tier that measures accuracy and injection resistance. Skipped unless `JUDGE_REAL_MODEL_TESTS=1`, a real provider and an organiser-approved corpus (`JUDGE_REAL_MODEL_CORPUS`) |

Fixtures are synthetic. Labelled markers stand in for qualifying output. There is no real malware, explicit content or abusive language in the repo.

Part A's behaviour (status transitions, persisting results, what teams are shown) is not tested here. That is Part A's code.

## Connecting a real model

`providers/openai_compatible.py` works with any OpenAI-style chat completions API. Set `JUDGE_PROVIDER=openai_compatible` with `JUDGE_BASE_URL`, `JUDGE_API_KEY` and `JUDGE_MODEL`, then build the real-model corpus and run tier 3.

For any other API:

1. Implement `JudgeProvider.complete(request) -> ProviderResponse` (return the raw text and token counts; raise the `Provider*` exceptions). Any chat-completions style API works, including Groq, Gemini's compatibility endpoint and local servers such as Ollama or vLLM. Set `max_context_tokens` and, if you can, override `estimate_tokens`.
2. Register it: `register_provider("my_provider", lambda settings: MyProvider(settings))`, then set `JUDGE_PROVIDER=my_provider`. The engine does not change.
3. Build the real-model corpus and run tier 3.

The model needs strong Hindi and Hinglish ability for Level 3. Hosted providers may refuse L3 to L5 content (`provider_refused`). A locally hosted open-weight model avoids sending that content to a third party.

## Integration notes for Part A

`app/judging.py` and `supabase/judge.sql` do all of this.

- Call `configure(bot_source=...)` once. A implements `BotConfigSource.get_bot_config(bot_id)` and must return a `JudgeBotConfig` (`id`, `level`, `checkType`, `winRubric`). Extra fields are ignored. Do not pass the system prompt or secret.
- Call `grade()` from the grading worker. It is synchronous and blocking, so keep it out of request handlers. B does not retry on provider errors, A owns retries.
- B does not check `enabled`. A does that before calling.
- Judge usage is an organiser cost. Store `result.usage` or `err.usage` in `judge_runs`. `provider.name` is available for `judge_runs.judge_model`.
- Free tiers rate-limit hard. Keep grading concurrency low and honour `retry_after_seconds`.

## Known limits

- Transcripts that exceed the context window are an error (`context_too_large`), not chunked. Chunking with evidence aggregation is designed but not built.
- For L2 and L5-style rubrics that name a topic or code word, a deterministic pre-check could be more reliable than an LLM. Not built.
- Whether a bot repeating a participant-typed term counts is decided by the rubric. B cannot tell, because it never sees user messages.
