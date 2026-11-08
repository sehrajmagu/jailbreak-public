# Database schema (Supabase Postgres + FastAPI)

## 0. Assumptions (change if wrong)
- "Band" in the user story = **level** (5 levels, 6 bots each).
- Supabase Auth handles login. `teams.id` and `admin_users.id` equal `auth.users.id`. Passwords live in Supabase Auth only; the role (`team` / `admin`) is stored in `app_metadata`.
- After Supabase login, the backend sets its own signed `session` cookie containing `role`, `teamId` and `teamName` (or `role` and `username` for admins). This is what `proxy.ts` expects, in the format given under "Session cookie" in the README. The Supabase token itself is not what the frontend's route guards read.
- All data access goes through FastAPI using the **service role**. Browsers never query Supabase tables directly. RLS is still enabled everywhere as a second layer.
- Team dollar budgets are enforced by LiteLLM. `models.token_limit_per_team` is a deprecated compatibility column and never blocks chat. Token usage is still recorded.
- Teams can see each bot's model display name and per-message token counts.
- Level 1 passwords are the same for every team (one per bot), stored server-side only.
- Failed streams are not charged to teams (see `messages.status`).

## 1. ENUM vs JSONB vs relational

**ENUM (stable, small, closed sets, enforced by the type):**
- `app_role` ('team', 'admin') - only used if mirrored in a table; otherwise the JWT claim
- `check_type` ('answer', 'judge')
- `message_role` ('user', 'assistant')
- `message_status` ('complete', 'failed')
- `conversation_status` ('active', 'submitted', 'discarded')
- `submission_status` ('pending', 'grading', 'passed', 'failed', 'error')
- Why: they drive logic in code, never change often, and invalid values must be impossible. Adding a value later is easy (`ALTER TYPE ... ADD VALUE`), removing one is painful, so only use ENUM for sets you're sure about.

**text + CHECK constraint (sets that may grow):**
- `models.provider`, `level_completions.source`, `scores.source`
- Why: a CHECK constraint can be dropped and recreated in one migration, unlike an ENUM.

**JSONB (config blobs that vary and are never queried relationally):**
- `bots.defences` (shape differs per level: input filter, output filter, reviewer model)
- `bots.tools` (fake tool definitions as JSON Schema)
- `audit_log.details`
- `tool_calls.arguments` and `tool_calls.result` (arbitrary per tool)
- Why: the structure is flexible, it's read as a whole by the engine, and it doesn't need joins.

**Separate relational tables (things you query, count, or join):**
- `tool_calls` (not a JSONB column on messages): admins and the grader need "was tool X called in this conversation?", counts per tool, and failed vs successful calls.
- `models` (not a column on bots): organiser cost rates are shared by many bots and change in one place.
- `levels` (not constants in code): required bots, points and unlock rules become editable by admins.
- `judge_runs` (not columns on submissions): regrading keeps the full history of verdicts.
- `credit_ledger` (not only a usage column): every charge is traceable and auditable.

## 2. Tables

Conventions: `uuid` PKs use `gen_random_uuid()`; log-style tables use `bigint generated always as identity`; all timestamps are `timestamptz`; FKs are `ON DELETE RESTRICT` (disable teams, don't delete them).

### 2.1 `teams`
Purpose: one shared account per team.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | uuid | no | | PK, FK -> auth.users(id) |
| name | text | no | | unique, case-insensitive |
| disabled | boolean | no | false | blocks login and all requests |
| last_active_at | timestamptz | yes | | |
| created_at | timestamptz | no | now() | |

- Unique: `lower(name)`. Check: `char_length(name) between 1 and 60`.
- Indexes: unique index on `lower(name)`; `(disabled)`.
- Relationships: 1-to-many with conversations, messages, submissions, answer_attempts, scores, level_completions, credit_ledger.
- Read: auth, teams, credits, leaderboard, admin. Write: auth (last_active_at), admin (create, disable, rename).
- RLS: required. No team access (see section 5). Service role does everything.

### 2.2 `admin_users`
Purpose: organiser accounts.

| Column | Type | Null | Default |
|---|---|---|---|
| id | uuid | no | PK, FK -> auth.users(id) |
| display_name | text | no | |
| created_at | timestamptz | no | now() |

- Read/Write: auth, admin. RLS: required, no access for team users. Role itself comes from the JWT `app_metadata`, and the backend must check both the claim and the row.

### 2.3 `levels`
Purpose: per-level configuration, editable without a deploy.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| level_number | smallint | no | | PK, check 1-5 |
| name | text | no | | participant-visible |
| description | text | no | | participant-visible, must not contain secrets |
| required_bots | smallint | no | 3 | check 1-6 |
| points_per_bot | integer | no | 100 | check >= 0 |
| prize_amount | integer | no | 1500 | check >= 0 |
| max_winners | smallint | yes | | null = unlimited |
| unlock_in_order | boolean | no | false | |
| enabled | boolean | no | true | |

- Relationships: 1-to-many with bots and level_completions.
- Read: catalog, chat, scoring, admin. Write: admin.
- RLS: required. No team access (see section 5).

### 2.4 `models`
Purpose: the real models and their organiser cost rates. Teams see `display_name` and usage statistics only, through the API, and never `api_model` or `host`.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | text | no | | PK, e.g. `gpt-6-luna` |
| provider | text | no | | check in ('openai','anthropic','open') |
| display_name | text | no | | shown to teams |
| api_model | text | yes | | the provider's real model ID, such as `openai/gpt-oss-120b`. Null until chosen |
| host | text | yes | | the service that runs the model, such as `openai`, `anthropic` or `groq` |
| organiser_cost_per_1k_input | numeric(12,4) | no | | check >= 0, organiser reporting only, never applied to team balances |
| organiser_cost_per_1k_output | numeric(12,4) | no | | check >= 0, organiser reporting only, never applied to team balances |
| token_limit_per_team | bigint | no | | Deprecated, unused compatibility column; default 0, check >= 0 |
| enabled | boolean | no | true | |

- Read: catalog, chat, engine client, admin. Write: admin.
- RLS: required, no direct access for teams.

### 2.5 `bots`
Purpose: the 30 challenge bots (5 levels x 6). Holds all hidden prompts, secrets and rubrics.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | uuid | no | gen_random_uuid() | PK |
| level | smallint | no | | FK -> levels |
| label | text | no | | "Bot A" to "Bot F" |
| display_order | smallint | no | | follows model order within a level |
| model_id | text | no | | FK -> models |
| description | text | no | '' | shown to teams, must not contain secrets |
| goal | text | no | '' | shown to teams, must not contain secrets |
| system_prompt | text | no | | contains `{{SECRET}}` placeholder where relevant |
| defences | jsonb | no | '{}' | |
| tools | jsonb | no | '[]' | fake tools only |
| check_type | check_type | no | | |
| secret | text | yes | | level 1 password |
| rubric | text | yes | | judge levels |
| context_limit_tokens | integer | no | 8000 | check > 0 |
| enabled | boolean | no | true | |
| created_at / updated_at | timestamptz | no | now() | |

- Unique: `(level, label)`; `(level, display_order)`.
- Check: `(check_type = 'answer' and secret is not null) or (check_type = 'judge' and rubric is not null)`.
- Indexes: `(level, display_order)`; `(model_id)`; partial `(level) where enabled`.
- Relationships: belongs to level and model; 1-to-many with conversations, submissions, answer_attempts, scores.
- Read: catalog, chat (builds the engine request), judge client, admin. Write: admin only.
- RLS: **critical.** No access for team users at all. The API must never `select *` from this table for team endpoints; use a mapper or a view exposing only `id, level, label, display_order, description, goal` and the model's display name.

### 2.6 `conversations`
Purpose: one chat session between a team and a bot.

| Column | Type | Null | Default |
|---|---|---|---|
| id | uuid | no | gen_random_uuid(), PK |
| team_id | uuid | no | FK -> teams |
| bot_id | uuid | no | FK -> bots |
| status | conversation_status | no | 'active' |
| title | text | yes | |
| created_at | timestamptz | no | now() |
| updated_at | timestamptz | no | now() |
| submitted_at | timestamptz | yes | |

- Indexes: `(team_id, bot_id, created_at desc)`; `(bot_id, created_at desc)` for admin logs; partial `(team_id) where status = 'active'`.
- Check: `(status = 'submitted') = (submitted_at is not null)`.
- Read: chat, submissions, admin. Write: chat (create, update status), submissions (mark submitted).
- RLS: required. No team access (see section 5).

### 2.7 `messages`
Purpose: every turn in a conversation, including failed ones, with token usage.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | bigint | no | identity | PK |
| conversation_id | uuid | no | | FK -> conversations |
| team_id | uuid | no | | FK -> teams, denormalised for RLS and queries |
| seq | integer | no | | order within the conversation |
| role | message_role | no | | |
| content | text | no | | |
| status | message_status | no | 'complete' | failed streams are kept but not charged |
| input_tokens | integer | yes | | assistant messages: usage for the whole turn |
| output_tokens | integer | yes | | |
| created_at | timestamptz | no | now() | |

- Unique: `(conversation_id, seq)`. Check: `char_length(content)` is at most 8,000 for user messages and 32,000 for assistant messages (`supabase/conversations.sql`), tokens >= 0.
- Indexes: `(conversation_id, seq)`; `(team_id, created_at desc)`.
- Relationships: belongs to conversation; 1-to-many with tool_calls and credit_ledger.
- Read: chat, submissions (for the judge), admin. Write: chat/logging (insert only, never update content).
- RLS: required. No team access (see section 5). The API returns per-message token counts to teams, and failed rows may be hidden from teams by the API.

### 2.8 `tool_calls`
Purpose: every call to a (fake) tool by a bot. Used for admin logs, and only for bots that have optional fake tools.

| Column | Type | Null | Default |
|---|---|---|---|
| id | bigint | no | identity, PK |
| message_id | bigint | no | FK -> messages |
| conversation_id | uuid | no | FK -> conversations |
| team_id | uuid | no | FK -> teams |
| tool_name | text | no | |
| arguments | jsonb | no | '{}' |
| result | jsonb | yes | |
| created_at | timestamptz | no | now() |

- Indexes: `(conversation_id)`; `(tool_name, created_at)`; `(message_id)`.
- Read: judge client, grader, admin. Write: logging.
- RLS: required. No team access (see section 5).

### 2.9 `submissions`
Purpose: a conversation a team submitted for judging.

| Column | Type | Null | Default |
|---|---|---|---|
| id | uuid | no | gen_random_uuid(), PK |
| team_id | uuid | no | FK -> teams |
| bot_id | uuid | no | FK -> bots |
| conversation_id | uuid | no | FK -> conversations, **unique** |
| status | submission_status | no | 'pending' |
| reason | text | yes | |
| points_awarded | integer | no | 0 |
| created_at | timestamptz | no | now() |
| graded_at | timestamptz | yes | |

- Unique: `conversation_id` (a conversation is submitted once). Check: `points_awarded >= 0`; `(status in ('passed','failed')) = (graded_at is not null)`.
- Indexes: `(team_id, created_at desc)`; `(bot_id, status)`; partial `(created_at) where status in ('pending','grading')` for the grading worker.
- Read: submissions, scoring, admin, teams (status page). Write: submissions (insert), judge worker (status, reason), admin (regrade).
- RLS: required. No team access (see section 5). `reason` must be safe to show (no rubric text).

### 2.10 `judge_runs`
Purpose: history of every judge attempt, including regrades.

| Column | Type | Null | Default |
|---|---|---|---|
| id | bigint | no | identity, PK |
| submission_id | uuid | no | FK -> submissions |
| judge_model | text | no | |
| verdict | boolean | yes | |
| reason | text | yes | |
| error | text | yes | |
| input_tokens / output_tokens | integer | yes | |
| triggered_by | text | no | 'auto', check in ('auto','admin_regrade') |
| created_at | timestamptz | no | now() |

- Indexes: `(submission_id, created_at desc)`.
- Read: admin, judge worker. Write: judge worker. Judge cost is not charged to teams.
- RLS: required, no access for teams (it can contain rubric details).

### 2.11 `answer_attempts`
Purpose: level 1 guesses. Used for the 10-wrong-answer cap and for admin logs.

| Column | Type | Null | Default |
|---|---|---|---|
| id | bigint | no | identity, PK |
| team_id | uuid | no | FK -> teams |
| bot_id | uuid | no | FK -> bots |
| answer | text | no | |
| correct | boolean | no | |
| created_at | timestamptz | no | now() |

- Check: `char_length(answer) <= 500`.
- Indexes: `(team_id, bot_id, correct)` (cap count); `(bot_id, created_at)`.
- Read: scoring (cap check), admin. Write: scoring/answer endpoint (insert only).
- RLS: required. No team access (see section 5). The API returns the count for "x of 10 used". Never expose other teams' answers.

### 2.12 `scores`
Purpose: points per team per bot, first pass only.

| Column | Type | Null | Default |
|---|---|---|---|
| team_id | uuid | no | FK -> teams |
| bot_id | uuid | no | FK -> bots |
| points | integer | no | check >= 0 |
| source | text | no | check in ('answer','judge','admin') |
| submission_id | uuid | yes | FK -> submissions |
| awarded_at | timestamptz | no | now() |

- PK: `(team_id, bot_id)`. This enforces "first pass only" (insert with `ON CONFLICT DO NOTHING`).
- Indexes: `(team_id)`; `(awarded_at)`.
- Read: leaderboard, dashboard, admin. Write: scoring only.
- RLS: required. No team access (see section 5). Teams see totals only through the API's leaderboard endpoint.

### 2.13 `level_completions`
Purpose: records when a team completes a level (X of 6 bots solved), once per level.

| Column | Type | Null | Default |
|---|---|---|---|
| team_id | uuid | no | FK -> teams |
| level | smallint | no | FK -> levels |
| completed_at | timestamptz | no | now() |
| source | text | no | 'earned', check in ('earned','admin_override') |
| granted_by | uuid | yes | FK -> admin_users |
| note | text | yes | |
| revoked | boolean | no | false |
| revoked_by | uuid | yes | FK -> admin_users |
| revoked_at | timestamptz | yes | |

- PK: `(team_id, level)`. Index: `(level, completed_at)` (for ordering and max winners).
- Check: `revoked = (revoked_at is not null)`; `revoked = (revoked_by is not null)`.
- Recounts must ignore revoked rows: a recount never clears `revoked`, and revoked rows don't count towards levels completed, prizes owed, `max_winners` or level unlocking. Only an admin can clear a revocation.
- Clearing an override (`set_level_override` in `supabase/admin.sql`): deletes the row, then recounts. If the team's solves meet the requirement, a row with source `earned` is added back, completed when the required-th score was awarded, so the team keeps its place in the prize order.
- Read: dashboard, admin. Write: scoring (after each pass), admin (override, revoke).
- RLS: required. No team access (see section 5).

### 2.14 `credit_ledger`
Purpose: append-only record of token usage per model. It records usage only, so there are no grants or top-ups.

| Column | Type | Null | Default |
|---|---|---|---|
| id | bigint | no | identity, PK |
| team_id | uuid | no | FK -> teams |
| model_id | text | no | FK -> models, the model that served the turn |
| tokens | bigint | no | check > 0, the turn's input + output tokens |
| message_id | bigint | no | FK -> messages |
| created_at | timestamptz | no | now() |

- Indexes: `(team_id, model_id)` (usage and limit checks); `(team_id, created_at desc)`; `(message_id)`.
- Read: credits, admin. Write: credits only. Never update or delete rows.
- RLS: required. No team access (see section 5). The API returns per-model totals and per-message token counts, not ledger rows.

### 2.15 `audit_log`
Purpose: who changed what in the admin dashboard (regrades, overrides, prize changes, model changes).

| Column | Type | Null | Default |
|---|---|---|---|
| id | bigint | no | identity, PK |
| admin_id | uuid | no | FK -> admin_users |
| action | text | no | |
| target_type | text | no | |
| target_id | text | yes | |
| details | jsonb | no | '{}' |
| created_at | timestamptz | no | now() |

- Indexes: `(created_at desc)`; `(target_type, target_id)`.
- Read/Write: admin only. RLS: required, no access for teams.
- Written by the functions in `supabase/admin.sql`, in the same transaction as the change and only when something changed. `action` is the function's name, and `details` is `{ before, after }`.

### 2.16 View: `leaderboard`
`select t.id, t.name, coalesce(sum(s.points),0) as total_points, max(s.awarded_at) as last_scored_at, rank() over (order by sum(points) desc, max(awarded_at) asc) from teams t left join scores s ... where not t.disabled group by t.id`.
- Disabled teams are excluded. Expose `name`, `total_points`, `rank` only. Only the API reads it, so it does not need to be a security-definer view.

## 3. Relationships
```
auth.users 1-1 teams            auth.users 1-1 admin_users
levels 1-n bots                 models 1-n bots
teams 1-n conversations         bots 1-n conversations
conversations 1-n messages      messages 1-n tool_calls
conversations 1-1 submissions   submissions 1-n judge_runs
teams+bots 1-n answer_attempts  teams+bots 1-1 scores
teams+levels 1-1 level_completions
teams 1-n credit_ledger         messages 1-n credit_ledger
admin_users 1-n audit_log
```

## 4. Backend modules (FastAPI) and table access
| Module | Reads | Writes |
|---|---|---|
| auth | teams, admin_users | teams.last_active_at |
| catalog | levels, bots (safe fields), models | - |
| chat | conversations, messages, bots, teams | conversations, messages, tool_calls |
| engine client | bots, models | - |
| credits | models, credit_ledger | credit_ledger |
| logging | - | messages, tool_calls |
| submissions | conversations, messages, tool_calls | submissions, conversations |
| judge worker | submissions, messages, tool_calls, bots | judge_runs, submissions |
| scoring | answer_attempts, scores, submissions, levels | answer_attempts, scores, level_completions |
| leaderboard | leaderboard view | - |
| admin | everything | teams, bots, levels, models, level_completions, audit_log |

## 5. Row-level security summary
- Enable RLS on **every** table. Default is deny. The FastAPI service role bypasses RLS, so the real security is the role and team checks in the API, and RLS is a backstop.
- There are no team read policies on any table or view. Teams access data only through the API.
- Why: tables hold hidden data such as prompts, passwords, rubrics and judge details, so a team must not be able to read any row directly with a Supabase token. The API returns only safe fields.
- Teams never get insert/update/delete on anything. All writes go through FastAPI.
- Admin access is through the service role plus the `app_metadata.role = 'admin'` check in the API.

## 6. Key transactions
- **Saving a chat turn** (`begin_turn` and `finish_turn` in `supabase/conversations.sql`): `begin_turn` locks the conversation, refuses if a turn is still open, inserts the user message with the next `seq`, and reserves the assistant message's ID. `finish_turn` inserts the assistant message at `seq + 1` with its tool calls, then either charges the turn (below) or marks both of its messages failed.
- **Charging a chat turn** (one transaction, done by `finish_turn`): insert the assistant message, then insert the ledger row with the bot's current `model_id` and the turn's input + output tokens. LiteLLM prices and enforces the team dollar allowance independently. Don't charge failed streams.
- **Answer check:** count wrong attempts for `(team, bot)`; if >= 10, refuse; otherwise insert an attempt; if correct, insert into `scores ... on conflict do nothing`, then recompute `level_completions`.
- **Level completion:** after any new score, count scored bots in that level; if >= `levels.required_bots`, insert into `level_completions ... on conflict do nothing`. The `on conflict do nothing` leaves a revoked row revoked.
- **Submission:** insert the submission (the unique `conversation_id` stops double submissions), mark the conversation submitted, queue the judge. Judge result updates the submission and inserts `scores`.

- **Token usage by model** (`GET /me/dashboard`, `usageByModel`, usage statistics only): no new table is needed. Sum `credit_ledger.tokens` per `model_id` for the session's team only. Start from `models` so that models with zero usage still appear, and return `display_name` and tokens used. No remaining-token allowance is returned.

  ```sql
  select m.display_name, coalesce(sum(l.tokens), 0) as tokens_used
  from models m
  left join credit_ledger l on l.model_id = m.id and l.team_id = :team
  group by m.id;
  ```

  The ledger stores the model for each charge, so changing a bot's `model_id` mid-event doesn't move its past usage to another model. The `(team_id, model_id)` index keeps usage queries fast.

## 7. Known mismatches with lib/types.ts

`lib/types.ts` is what the frontend uses today. Until one side changes, the API must translate between them. Part A means the platform and Part B means the bot engine, as in `docs/INTERFACE.md`.

| Topic | This schema | `lib/types.ts` and the API | What to do | Owner |
| --- | --- | --- | --- | --- |
| Failed turns | Kept in `messages` with `status = 'failed'` | The README says a failed turn isn't saved, because the UI restores the text for a retry | Always leave failed rows out of team responses and out of history sent to the model | A |
| Regrades | A pass that becomes a fail leaves rows in `scores` and `level_completions` | Points and completion follow the latest verdict | On regrade, recalculate the score and the level's completion in one transaction | A |
| Wrong-answer cap | Hardcoded as 10 in section 6 | `BotDetail.maxWrongAnswers`, configurable with a default of 10 | Add a config value and return it from `GET /bots/:id` | A |
| Level unlocking | `levels.unlock_in_order`, per level | `AdminPrizes.requireLevelsInOrder`, one global flag | Choose one. The default is off either way | A |
| Points | `levels.points_per_bot` | `points` on each bot, in `BotCard` and `AdminBot` | Return the level's value on each bot. Bots are edited in their files, so `BotInput.points` isn't used | A |
| Answer attempts | In `answer_attempts`, and `submissions.conversation_id` can't be null | Listed as `Submission` with `conversationId: null` | Merge both tables in `getSubmissions` and `getAdminSubmissions` | A |
| Error status | `submission_status` includes `error` | `SubmissionStatus` doesn't | Show `error` as `grading` while retrying, or add it to the type and UI | A |
| Ungraded points | `submissions.points_awarded` defaults to 0 | `pointsAwarded` is null until graded | Return null until `graded_at` is set | A |
| Leaderboard | The view exposes `name`, `total_points` and `rank` | `LeaderboardEntry` also needs `teamId`, `solved` and `isOwnTeam` | Extend the view, and work out `isOwnTeam` from the session | A |
| Naming | `rubric`, `model_id`, `level_number`, and models such as `gpt-6-luna` | `winRubric`, `model`, `level`, and the fixed `ModelName` list | Map names in the API, and update `ModelName` when the models are final. Until then, the backend's admin models take any `models.display_name` as a plain string | A |
| Secret field | `bots.secret` and `bots.rubric` can be null | `AdminBot.secret` and `winRubric` are strings | Return `""` for null | A |
| Per-message tokens | `input_tokens` and `output_tokens` on the assistant message, for the whole turn | `ChatMessage.tokens`, one number per message | Choose how to map them. See `docs/INTERFACE.md` | A |
| `tool_calls` shape | `arguments` and `result` are JSONB, and `result` can be null | `ToolCall.arguments` and `result` are JSON strings | Serialise in the API, and return `""` for a null result | A |
| `defences` shape | Structured JSONB | `AdminBot.defences` is a `string[]` shown as free text | Change the type and admin form, or convert. See `docs/INTERFACE.md` | A and B |
