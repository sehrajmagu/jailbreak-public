-- Brings a database whose teams table was replaced back in line with schema.sql.
-- It keeps every row, drops nothing, and is safe to run twice. Run it once in the
-- Supabase SQL editor. A new project doesn't need it.
--
-- Replacing the table (drop table teams cascade, then create table teams) removes
-- the views and foreign keys that depended on it, so this restores those as well.
-- If a step fails, for example on two teams with the same name, the whole file is
-- undone and nothing changes. The extra columns credits and score stay.

begin;

-- ---------- Columns ----------

alter table teams add column if not exists disabled boolean not null default false;
alter table teams add column if not exists last_active_at timestamptz;
alter table teams alter column created_at set not null;

-- ---------- Rules and indexes ----------

create unique index if not exists teams_lower_name_key on teams (lower(name));
create index if not exists teams_disabled_idx on teams (disabled);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'teams'::regclass and conname = 'teams_name_check'
  ) then
    alter table teams add constraint teams_name_check check (char_length(name) between 1 and 60);
  end if;
end $$;

-- ---------- Foreign keys ----------
-- Each is added only if that column has no link to that table yet, so a link with
-- a different name is left as it is.

do $$
declare
  link record;
begin
  for link in
    select * from (values
      ('teams', 'id', 'auth.users'),
      ('conversations', 'team_id', 'public.teams'),
      ('messages', 'team_id', 'public.teams'),
      ('tool_calls', 'team_id', 'public.teams'),
      ('submissions', 'team_id', 'public.teams'),
      ('answer_attempts', 'team_id', 'public.teams'),
      ('scores', 'team_id', 'public.teams'),
      ('level_completions', 'team_id', 'public.teams'),
      ('credit_ledger', 'team_id', 'public.teams')
    ) as wanted (child, col, parent)
  loop
    if not exists (
      select 1
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
      where c.contype = 'f'
        and c.conrelid = format('public.%I', link.child)::regclass
        and c.confrelid = link.parent::regclass
        and array_length(c.conkey, 1) = 1
        and a.attname = link.col
    ) then
      execute format(
        'alter table public.%I add constraint %I foreign key (%I) references %s (id) on delete restrict',
        link.child, link.child || '_' || link.col || '_fkey', link.col, link.parent
      );
    end if;
  end loop;
end $$;

-- ---------- Leaderboard view ----------
-- The same as schema.sql. The coalesce in the ranking keeps teams with no scores
-- last, not first.

create or replace view leaderboard with (security_invoker = true) as
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
grant select on leaderboard to service_role;

-- ---------- Admin conversation view ----------
-- The same as conversations.sql.

create or replace view admin_conversations with (security_invoker = true) as
select
  c.id,
  c.team_id,
  t.name as team_name,
  c.bot_id,
  b.label as bot_label,
  b.level,
  -- Attribute recorded token usage to the bot's current model.
  mo.display_name as model,
  -- A conversation with only failed turns has no title, so use its first message.
  coalesce(c.title, (
    select case when char_length(m.content) > 60 then left(m.content, 57) || '...' else m.content end
    from messages m
    where m.conversation_id = c.id and m.role = 'user'
    order by m.seq
    limit 1
  ), '') as title,
  n.message_count,
  n.tokens,
  c.created_at,
  c.updated_at,
  s.status as submission_status,
  -- Teams have no "error" status. Grading is retried, so it shows as grading.
  case
    when b.check_type = 'answer' then 'answer_bot'
    when s.status is null then 'not_submitted'
    when s.status = 'error' then 'grading'
    else s.status::text
  end as result
from conversations c
join teams t on t.id = c.team_id
join bots b on b.id = c.bot_id
join models mo on mo.id = b.model_id
cross join lateral (
  select
    count(*) as message_count,
    coalesce(sum(coalesce(m.input_tokens, 0) + coalesce(m.output_tokens, 0)), 0) as tokens
  from messages m
  where m.conversation_id = c.id
) n
left join submissions s on s.conversation_id = c.id;

revoke all on admin_conversations from anon, authenticated;
grant select on admin_conversations to service_role;

-- ---------- Access ----------

alter table teams enable row level security;

commit;

-- Makes Supabase's API notice the new columns and views straight away.
notify pgrst, 'reload schema';
