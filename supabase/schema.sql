-- Jailbreak platform database schema, from docs/SCHEMA.md.
-- Run once on an empty Supabase project, for example in the SQL editor.
-- It creates structure only, with no levels, models, bots or teams.

begin;

-- ---------- Enums ----------

-- Not used by a column yet. The role normally comes from the JWT claim.
create type app_role as enum ('team', 'admin');
create type check_type as enum ('answer', 'judge');
create type message_role as enum ('user', 'assistant');
create type message_status as enum ('complete', 'failed');
create type conversation_status as enum ('active', 'submitted', 'discarded');
create type submission_status as enum ('pending', 'grading', 'passed', 'failed', 'error');

-- ---------- Tables ----------

create table teams (
  id uuid primary key references auth.users (id) on delete restrict,
  name text not null check (char_length(name) between 1 and 60),
  disabled boolean not null default false,
  last_active_at timestamptz,
  created_at timestamptz not null default now()
);

create table admin_users (
  id uuid primary key references auth.users (id) on delete restrict,
  display_name text not null,
  created_at timestamptz not null default now()
);

create table levels (
  level_number smallint primary key check (level_number between 1 and 5),
  name text not null,
  description text not null,
  required_bots smallint not null default 3 check (required_bots between 1 and 6),
  points_per_bot integer not null default 100 check (points_per_bot >= 0),
  prize_amount integer not null default 1500 check (prize_amount >= 0),
  max_winners smallint,
  unlock_in_order boolean not null default false,
  enabled boolean not null default true
);

create table models (
  id text primary key,
  provider text not null check (provider in ('openai', 'anthropic', 'open')),
  display_name text not null,
  api_model text,
  host text,
  organiser_cost_per_1k_input numeric(12, 4) not null check (organiser_cost_per_1k_input >= 0),
  organiser_cost_per_1k_output numeric(12, 4) not null check (organiser_cost_per_1k_output >= 0),
  -- Deprecated compatibility column; LiteLLM enforces spending allowances.
  token_limit_per_team bigint not null default 0 check (token_limit_per_team >= 0),
  enabled boolean not null default true
);

create table bots (
  id uuid primary key default gen_random_uuid(),
  level smallint not null references levels (level_number) on delete restrict,
  label text not null,
  display_order smallint not null,
  model_id text not null references models (id) on delete restrict,
  description text not null default '',
  goal text not null default '',
  system_prompt text not null,
  defences jsonb not null default '{}',
  tools jsonb not null default '[]',
  check_type check_type not null,
  secret text,
  rubric text,
  context_limit_tokens integer not null default 8000 check (context_limit_tokens > 0),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (level, label),
  unique (level, display_order),
  check (
    (check_type = 'answer' and secret is not null)
    or (check_type = 'judge' and rubric is not null)
  )
);

create table conversations (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references teams (id) on delete restrict,
  bot_id uuid not null references bots (id) on delete restrict,
  status conversation_status not null default 'active',
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  submitted_at timestamptz,
  check ((status = 'submitted') = (submitted_at is not null))
);

create table messages (
  id bigint generated always as identity primary key,
  conversation_id uuid not null references conversations (id) on delete restrict,
  team_id uuid not null references teams (id) on delete restrict,
  seq integer not null,
  role message_role not null,
  content text not null check (char_length(content) <= 8000),
  status message_status not null default 'complete',
  input_tokens integer check (input_tokens >= 0),
  output_tokens integer check (output_tokens >= 0),
  created_at timestamptz not null default now(),
  unique (conversation_id, seq)
);

create table tool_calls (
  id bigint generated always as identity primary key,
  message_id bigint not null references messages (id) on delete restrict,
  conversation_id uuid not null references conversations (id) on delete restrict,
  team_id uuid not null references teams (id) on delete restrict,
  tool_name text not null,
  arguments jsonb not null default '{}',
  result jsonb,
  created_at timestamptz not null default now()
);

create table submissions (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references teams (id) on delete restrict,
  bot_id uuid not null references bots (id) on delete restrict,
  conversation_id uuid not null unique references conversations (id) on delete restrict,
  status submission_status not null default 'pending',
  reason text,
  points_awarded integer not null default 0 check (points_awarded >= 0),
  created_at timestamptz not null default now(),
  graded_at timestamptz,
  check ((status in ('passed', 'failed')) = (graded_at is not null))
);

create table judge_runs (
  id bigint generated always as identity primary key,
  submission_id uuid not null references submissions (id) on delete restrict,
  judge_model text not null,
  verdict boolean,
  reason text,
  error text,
  input_tokens integer,
  output_tokens integer,
  triggered_by text not null default 'auto' check (triggered_by in ('auto', 'admin_regrade')),
  created_at timestamptz not null default now()
);

create table answer_attempts (
  id bigint generated always as identity primary key,
  team_id uuid not null references teams (id) on delete restrict,
  bot_id uuid not null references bots (id) on delete restrict,
  answer text not null check (char_length(answer) <= 500),
  correct boolean not null,
  created_at timestamptz not null default now()
);

create table scores (
  team_id uuid not null references teams (id) on delete restrict,
  bot_id uuid not null references bots (id) on delete restrict,
  points integer not null check (points >= 0),
  source text not null check (source in ('answer', 'judge', 'admin')),
  submission_id uuid references submissions (id) on delete restrict,
  awarded_at timestamptz not null default now(),
  primary key (team_id, bot_id)
);

create table level_completions (
  team_id uuid not null references teams (id) on delete restrict,
  level smallint not null references levels (level_number) on delete restrict,
  completed_at timestamptz not null default now(),
  source text not null default 'earned' check (source in ('earned', 'admin_override')),
  granted_by uuid references admin_users (id) on delete restrict,
  note text,
  revoked boolean not null default false,
  revoked_by uuid references admin_users (id) on delete restrict,
  revoked_at timestamptz,
  primary key (team_id, level),
  check (revoked = (revoked_at is not null)),
  check (revoked = (revoked_by is not null))
);

create table credit_ledger (
  id bigint generated always as identity primary key,
  team_id uuid not null references teams (id) on delete restrict,
  model_id text not null references models (id) on delete restrict,
  tokens bigint not null check (tokens > 0),
  message_id bigint not null references messages (id) on delete restrict,
  created_at timestamptz not null default now()
);

create table audit_log (
  id bigint generated always as identity primary key,
  admin_id uuid not null references admin_users (id) on delete restrict,
  action text not null,
  target_type text not null,
  target_id text,
  details jsonb not null default '{}',
  created_at timestamptz not null default now()
);

-- ---------- Indexes ----------
-- The unique constraints above already index bots (level, display_order) and
-- messages (conversation_id, seq), so those aren't repeated here.

create unique index teams_lower_name_key on teams (lower(name));
create index teams_disabled_idx on teams (disabled);

create index bots_model_id_idx on bots (model_id);
create index bots_enabled_level_idx on bots (level) where enabled;

create index conversations_team_bot_created_idx on conversations (team_id, bot_id, created_at desc);
create index conversations_bot_created_idx on conversations (bot_id, created_at desc);
create index conversations_active_team_idx on conversations (team_id) where status = 'active';

create index messages_team_created_idx on messages (team_id, created_at desc);

create index tool_calls_conversation_idx on tool_calls (conversation_id);
create index tool_calls_tool_created_idx on tool_calls (tool_name, created_at);
create index tool_calls_message_idx on tool_calls (message_id);

create index submissions_team_created_idx on submissions (team_id, created_at desc);
create index submissions_bot_status_idx on submissions (bot_id, status);
create index submissions_in_flight_idx on submissions (created_at) where status in ('pending', 'grading');

create index judge_runs_submission_created_idx on judge_runs (submission_id, created_at desc);

create index answer_attempts_cap_idx on answer_attempts (team_id, bot_id, correct);
create index answer_attempts_bot_created_idx on answer_attempts (bot_id, created_at);

create index scores_team_idx on scores (team_id);
create index scores_awarded_idx on scores (awarded_at);

create index level_completions_level_completed_idx on level_completions (level, completed_at);

create index credit_ledger_team_model_idx on credit_ledger (team_id, model_id);
create index credit_ledger_team_created_idx on credit_ledger (team_id, created_at desc);
create index credit_ledger_message_idx on credit_ledger (message_id);

create index audit_log_created_idx on audit_log (created_at desc);
create index audit_log_target_idx on audit_log (target_type, target_id);

-- ---------- Leaderboard view ----------
-- A view normally runs with its owner's rights and ignores row-level security,
-- and Supabase exposes new views by default. security_invoker and the revoke
-- below keep this one to the service role, since only the API reads it.
-- The coalesce in the ranking keeps teams with no scores last, not first.

create view leaderboard with (security_invoker = true) as
select
  t.id,
  t.name,
  coalesce(sum(s.points), 0) as total_points,
  max(s.awarded_at) as last_scored_at,
  rank() over (order by coalesce(sum(s.points), 0) desc, max(s.awarded_at) asc) as rank
from teams t
left join scores s on s.team_id = t.id
where not t.disabled
group by t.id;

revoke all on leaderboard from anon, authenticated;

-- ---------- Row-level security ----------
-- Enabled on every table with no policies, so team tokens can read and write
-- nothing. The FastAPI service role bypasses RLS.

alter table teams enable row level security;
alter table admin_users enable row level security;
alter table levels enable row level security;
alter table models enable row level security;
alter table bots enable row level security;
alter table conversations enable row level security;
alter table messages enable row level security;
alter table tool_calls enable row level security;
alter table submissions enable row level security;
alter table judge_runs enable row level security;
alter table answer_attempts enable row level security;
alter table scores enable row level security;
alter table level_completions enable row level security;
alter table credit_ledger enable row level security;
alter table audit_log enable row level security;

commit;
