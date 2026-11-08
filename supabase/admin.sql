-- The organiser pages, and teams' last active times. Run after conversations.sql.
-- Safe to re-run.
--
-- Every change an organiser makes is written to audit_log in the same transaction,
-- and only when something actually changed.

begin;

-- PATCH /admin/models/:model finds the model by the name the admin pages show.
create unique index if not exists models_display_name_key on models (display_name);

-- ---------- Views ----------
-- Like the leaderboard view, these run with the caller's rights and only the
-- service role may read them. admin_bots holds every prompt and password. The
-- service role's access is granted explicitly, as fix_teams.sql does, rather than
-- relying on the project's default privileges.

-- A team's status on a bot, by the rule team_bot_cards in team_pages.sql uses, so
-- organisers see what the team sees. A conversation counts as an attempt only with
-- a complete message, since a failed turn is hidden from the team.
create or replace function admin_bot_status(p_team_id uuid, p_bot_id uuid) returns text
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select case
    when exists (select 1 from scores s where s.team_id = p_team_id and s.bot_id = p_bot_id) then 'solved'
    when exists (select 1 from answer_attempts a where a.team_id = p_team_id and a.bot_id = p_bot_id)
      or exists (
        select 1 from conversations c
        join messages m on m.conversation_id = c.id
        where c.team_id = p_team_id and c.bot_id = p_bot_id and m.status = 'complete'
      ) then 'attempted'
    else 'unattempted'
  end
$$;

create or replace view admin_teams with (security_invoker = true) as
select
  t.id,
  t.name,
  t.disabled,
  -- Charged tokens, the same total the team's limits use.
  coalesce((select sum(l.tokens) from credit_ledger l where l.team_id = t.id), 0) as tokens_used,
  -- Counted over the bots teams can see, as on the team's own home page.
  st.attempted,
  st.solved,
  coalesce((select sum(s.points) from scores s where s.team_id = t.id), 0) as score,
  -- Null for disabled teams, which the leaderboard leaves out.
  lb.rank,
  t.last_active_at,
  t.created_at
from teams t
left join leaderboard lb on lb.id = t.id
cross join lateral (
  select
    count(*) filter (where s.status <> 'unattempted') as attempted,
    count(*) filter (where s.status = 'solved') as solved
  from bots b
  join levels l on l.level_number = b.level
  cross join lateral (select admin_bot_status(t.id, b.id) as status) s
  where b.enabled and l.enabled
) st;

revoke all on admin_teams from anon, authenticated;
grant select on admin_teams to service_role;

create or replace view admin_bots with (security_invoker = true) as
select
  b.id,
  b.label,
  b.level,
  mo.display_name as model,
  b.check_type,
  -- Points are set per level.
  l.points_per_bot as points,
  b.description,
  b.goal,
  b.system_prompt,
  coalesce(b.rubric, '') as win_rubric,
  coalesce(b.secret, '') as secret,
  b.defences,
  b.enabled,
  b.display_order
from bots b
join levels l on l.level_number = b.level
join models mo on mo.id = b.model_id;

revoke all on admin_bots from anon, authenticated;
grant select on admin_bots to service_role;

-- Judge submissions and password attempts in one list, as AdminSubmission.
create or replace view admin_submissions with (security_invoker = true) as
select
  s.id::text as id,
  s.conversation_id,
  s.bot_id,
  b.level,
  b.label as bot_label,
  b.check_type,
  s.status::text as status,
  s.reason,
  s.points_awarded,
  s.created_at,
  s.graded_at,
  s.team_id,
  t.name as team_name,
  mo.display_name as model
from submissions s
join bots b on b.id = s.bot_id
join teams t on t.id = s.team_id
join models mo on mo.id = b.model_id
union all
select
  -- Prefixed so it can't be mistaken for a submission ID.
  'answer-' || a.id,
  null,
  a.bot_id,
  b.level,
  b.label,
  b.check_type,
  case when a.correct then 'passed' else 'failed' end,
  case when a.correct then 'Correct password.' else 'Incorrect password.' end,
  -- Only the first correct password scores.
  case
    when a.correct and a.id = (
      select min(first.id) from answer_attempts first
      where first.team_id = a.team_id and first.bot_id = a.bot_id and first.correct
    )
    then coalesce((select sc.points from scores sc where sc.team_id = a.team_id and sc.bot_id = a.bot_id), 0)
    else 0
  end,
  a.created_at,
  a.created_at,
  a.team_id,
  t.name,
  mo.display_name
from answer_attempts a
join bots b on b.id = a.bot_id
join teams t on t.id = a.team_id
join models mo on mo.id = b.model_id;

revoke all on admin_submissions from anon, authenticated;
grant select on admin_submissions to service_role;

-- Every team's state on every level teams can see. A level_completions row decides
-- completion, and prize slots go to the first max_winners teams to complete, with
-- teams that complete at the same moment sharing a place. This is the rule
-- team_level_results in team_pages.sql uses, so both sides agree on what's owed.
create or replace view admin_level_completions with (security_invoker = true) as
with state as (
  select
    t.id as team_id,
    l.level_number as level,
    l.required_bots as required,
    l.prize_amount,
    l.max_winners,
    -- Every scored bot in the level counts, as in submit_answer.
    (
      select count(*) from scores s join bots b on b.id = s.bot_id
      where s.team_id = t.id and b.level = l.level_number
    ) as solved,
    (select count(*) from bots b where b.level = l.level_number and b.enabled) as total,
    c.source,
    c.revoked,
    c.completed_at,
    coalesce(not c.revoked, false) as complete
  from teams t
  cross join levels l
  left join level_completions c on c.team_id = t.id and c.level = l.level_number
  where l.enabled
),
ranked as (
  select
    state.*,
    rank() over (partition by level, complete order by completed_at) as position
  from state
)
select
  team_id,
  level,
  solved,
  required,
  total,
  solved >= required as earned,
  case when revoked then 'revoked' when source = 'admin_override' then 'granted' end as override,
  complete,
  case when complete then completed_at end as completed_at,
  case
    when complete and (max_winners is null or position <= max_winners) then prize_amount
    else 0
  end as prize_awarded
from ranked;

revoke all on admin_level_completions from anon, authenticated;
grant select on admin_level_completions to service_role;

-- ---------- Reads ----------

create or replace function get_admin_overview() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'total_teams', (select count(*) from teams),
    'active_teams', (select count(*) from teams where last_active_at >= now() - interval '24 hours'),
    -- Real spend, failed turns included, as the conversation log shows.
    'tokens_consumed', (
      select coalesce(sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)), 0) from messages
    ),
    'total_attempts', (select count(*) from conversations),
    'total_solves', (select count(*) from scores),
    -- The last 7 UTC days, with days that had no activity at zero.
    'series', (
      select jsonb_agg(jsonb_build_object(
        'date', to_char(d.day, 'YYYY-MM-DD'),
        'attempts', (
          select count(*) from conversations c
          where c.created_at >= d.starts and c.created_at < d.starts + interval '1 day'
        ),
        'solves', (
          select count(*) from scores s
          where s.awarded_at >= d.starts and s.awarded_at < d.starts + interval '1 day'
        ),
        'tokens_consumed', (
          select coalesce(sum(coalesce(m.input_tokens, 0) + coalesce(m.output_tokens, 0)), 0) from messages m
          where m.created_at >= d.starts and m.created_at < d.starts + interval '1 day'
        )
      ) order by d.day)
      from (
        select day, day at time zone 'utc' as starts
        from generate_series(
          (now() at time zone 'utc')::date - 6,
          (now() at time zone 'utc')::date,
          interval '1 day'
        ) as day
      ) d
    )
  )
$$;

create or replace function get_admin_teams() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'teams', coalesce((select jsonb_agg(to_jsonb(a) order by a.created_at, a.id) from admin_teams a), '[]'::jsonb)
  )
$$;

-- One team, with its progress on every bot. Levels are always open, so no bot is locked.
create or replace function get_admin_team(p_team_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_team jsonb;
begin
  select to_jsonb(a) into v_team from admin_teams a where a.id = p_team_id;
  if v_team is null then
    return jsonb_build_object('status', 'team_not_found');
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'team', v_team || jsonb_build_object('progress', coalesce((
      select jsonb_agg(jsonb_build_object(
        'bot_id', b.id,
        'label', b.label,
        'level', b.level,
        'model', b.model,
        'status', admin_bot_status(p_team_id, b.id),
        'conversations', n.conversations
      ) order by b.level, b.display_order)
      from admin_bots b
      cross join lateral (
        select count(*) as conversations from conversations c
        where c.team_id = p_team_id and c.bot_id = b.id
      ) n
    ), '[]'::jsonb))
  );
end;
$$;

create or replace function get_admin_bots() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'bots', coalesce((select jsonb_agg(to_jsonb(b) order by b.level, b.display_order) from admin_bots b), '[]'::jsonb)
  )
$$;

create or replace function get_admin_bot(p_bot_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_bot jsonb;
begin
  select to_jsonb(b) into v_bot from admin_bots b where b.id = p_bot_id;
  if v_bot is null then
    return jsonb_build_object('status', 'bot_not_found');
  end if;
  return jsonb_build_object('status', 'ok', 'bot', v_bot);
end;
$$;

create or replace function get_bot_stats() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'stats', coalesce((
      select jsonb_agg(jsonb_build_object(
        'bot_id', b.id,
        'label', b.label,
        'level', b.level,
        'model', b.model,
        'enabled', b.enabled,
        'attempts', a.attempts,
        'teams_attempted', ta.teams_attempted,
        'solves', s.solves,
        -- Every solving team has attempted the bot, so this stays between 0 and 1.
        'solve_rate', case when ta.teams_attempted = 0 then 0 else s.solves::float8 / ta.teams_attempted end,
        'avg_attempts_to_solve', case when s.solves = 0 then null else s.solver_attempts::float8 / s.solves end,
        'tokens_used', a.tokens_used
      ) order by b.level, b.display_order)
      from admin_bots b
      cross join lateral (
        select
          count(*) as attempts,
          -- Real spend, failed turns included.
          coalesce((
            select sum(coalesce(m.input_tokens, 0) + coalesce(m.output_tokens, 0))
            from messages m join conversations mc on mc.id = m.conversation_id
            where mc.bot_id = b.id
          ), 0) as tokens_used
        from conversations c
        where c.bot_id = b.id
      ) a
      -- By the team pages' rule, so a password typed in without chatting, such as one
      -- heard from another team, counts too.
      cross join lateral (
        select count(*) as teams_attempted from teams t
        where admin_bot_status(t.id, b.id) <> 'unattempted'
      ) ta
      cross join lateral (
        select
          count(*) as solves,
          (
            select count(*) from conversations c
            where c.bot_id = b.id and c.team_id in (select sc.team_id from scores sc where sc.bot_id = b.id)
          ) as solver_attempts
        from scores s
        where s.bot_id = b.id
      ) s
    ), '[]'::jsonb)
  )
$$;

-- In the bots' model order, since display_order follows it.
create or replace function get_admin_models() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'models', coalesce((
      select jsonb_agg(
        jsonb_build_object('model', mo.display_name)
        order by (select min(b.display_order) from bots b where b.model_id = mo.id) nulls last, mo.id
      )
      from models mo
    ), '[]'::jsonb)
  )
$$;

-- One page of submissions and password attempts, newest first. A null filter
-- matches everything. Teams have no "error" status, so grading includes it.
create or replace function get_admin_submissions(
  p_status text,
  p_team_id uuid,
  p_page integer,
  p_page_size integer
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
begin
  if p_page is null or p_page < 1 or p_page_size is null or p_page_size not between 1 and 100 then
    return jsonb_build_object('status', 'invalid_page');
  end if;

  return (
    with matching as (
      select * from admin_submissions a
      where (p_status is null or a.status = p_status or (p_status = 'grading' and a.status = 'error'))
        and (p_team_id is null or a.team_id = p_team_id)
    ),
    page as (
      select * from matching
      order by created_at desc, id
      limit p_page_size
      offset (p_page - 1)::bigint * p_page_size
    )
    select jsonb_build_object(
      'status', 'ok',
      'items', coalesce((select jsonb_agg(to_jsonb(p) order by p.created_at desc, p.id) from page p), '[]'::jsonb),
      'total', (select count(*) from matching),
      'page', p_page,
      'page_size', p_page_size
    )
  );
end;
$$;

create or replace function get_admin_prizes() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  -- Worked out once, since every team's prize slot depends on the others.
  with completion as materialized (
    select * from admin_level_completions
  ),
  team_prizes as (
    select
      t.id,
      t.created_at,
      jsonb_build_object(
        'team_id', t.id,
        'team_name', t.name,
        'levels', coalesce(jsonb_agg(jsonb_build_object(
          'level', c.level,
          'solved', c.solved,
          'required', c.required,
          'total', c.total,
          'earned', c.earned,
          'override', c.override,
          'complete', c.complete,
          'prize_awarded', c.prize_awarded,
          'completed_at', c.completed_at
        ) order by c.level) filter (where c.level is not null), '[]'::jsonb),
        'prize_owed', coalesce(sum(c.prize_awarded), 0)
      ) as prizes
    from teams t
    left join completion c on c.team_id = t.id
    group by t.id
  )
  select jsonb_build_object(
    'status', 'ok',
    'require_levels_in_order', coalesce((select bool_or(unlock_in_order) from levels), false),
    'levels', coalesce((
      select jsonb_agg(jsonb_build_object(
        'level', l.level_number,
        'name', l.name,
        'prize_amount', l.prize_amount,
        'required_bots', l.required_bots,
        'max_winners', l.max_winners
      ) order by l.level_number)
      from levels l
      where l.enabled
    ), '[]'::jsonb),
    'teams', coalesce((select jsonb_agg(tp.prizes order by tp.created_at, tp.id) from team_prizes tp), '[]'::jsonb),
    'total_prize_owed', (select coalesce(sum(prize_awarded), 0) from completion)
  )
$$;

-- ---------- Organiser changes ----------

create or replace function write_audit(
  p_admin_id uuid,
  p_action text,
  p_target_type text,
  p_target_id text,
  p_before jsonb,
  p_after jsonb
) returns void
language sql
security invoker
set search_path = public, pg_temp
as $$
  insert into audit_log (admin_id, action, target_type, target_id, details)
  select p_admin_id, p_action, p_target_type, p_target_id, jsonb_build_object('before', p_before, 'after', p_after)
  where p_before is distinct from p_after
$$;

-- Renames, disables or enables a team. A null field is left as it is.
create or replace function update_team(
  p_admin_id uuid,
  p_team_id uuid,
  p_name text,
  p_disabled boolean
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  -- Plain btrim removes only spaces, so name the other whitespace too.
  v_name text := btrim(p_name, E' \t\n\r\f\v');
  v_before jsonb;
  v_after jsonb;
begin
  select jsonb_build_object('name', name, 'disabled', disabled) into v_before
  from teams where id = p_team_id for update;
  if v_before is null then
    return jsonb_build_object('status', 'team_not_found');
  end if;

  if p_name is not null and char_length(v_name) not between 1 and 60 then
    return jsonb_build_object('status', 'invalid_name');
  end if;

  begin
    update teams
    set name = coalesce(v_name, name), disabled = coalesce(p_disabled, disabled)
    where id = p_team_id
    returning jsonb_build_object('name', name, 'disabled', disabled) into v_after;
  exception when unique_violation then
    -- Names are unique ignoring case, including against a rename running at the same time.
    return jsonb_build_object('status', 'name_taken');
  end;

  perform write_audit(p_admin_id, 'update_team', 'team', p_team_id::text, v_before, v_after);
  return jsonb_build_object('status', 'ok', 'team', (select to_jsonb(a) from admin_teams a where a.id = p_team_id));
end;
$$;

-- Turns a bot on or off. A level needs required_bots solves, so it keeps at least
-- that many enabled bots.
create or replace function set_bot_enabled(
  p_admin_id uuid,
  p_bot_id uuid,
  p_enabled boolean
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_level smallint;
  v_before boolean;
begin
  select level into v_level from bots where id = p_bot_id;
  if not found then
    return jsonb_build_object('status', 'bot_not_found');
  end if;

  -- Locks the whole level, so two bots disabled at once can't both pass the check.
  perform 1 from bots where level = v_level order by id for update;
  select enabled into v_before from bots where id = p_bot_id;

  if v_before and not p_enabled and (
    select count(*) from bots where level = v_level and enabled
  ) - 1 < (select required_bots from levels where level_number = v_level) then
    return jsonb_build_object('status', 'too_few_bots');
  end if;

  if v_before is distinct from p_enabled then
    update bots set enabled = p_enabled, updated_at = now() where id = p_bot_id;
    perform write_audit(
      p_admin_id, 'set_bot_enabled', 'bot', p_bot_id::text,
      jsonb_build_object('enabled', v_before), jsonb_build_object('enabled', p_enabled)
    );
  end if;

  return jsonb_build_object('status', 'ok', 'bot', (select to_jsonb(b) from admin_bots b where b.id = p_bot_id));
end;
$$;

create or replace function set_model_token_limit(
  p_admin_id uuid,
  p_model text,
  p_limit bigint
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_id text;
  v_before bigint;
begin
  if p_limit is null or p_limit < 0 then
    return jsonb_build_object('status', 'invalid_limit');
  end if;

  select id, token_limit_per_team into v_id, v_before from models where display_name = p_model for update;
  if not found then
    return jsonb_build_object('status', 'model_not_found');
  end if;

  if v_before <> p_limit then
    update models set token_limit_per_team = p_limit where id = v_id;
    perform write_audit(
      p_admin_id, 'set_model_token_limit', 'model', v_id,
      jsonb_build_object('token_limit_per_team', v_before), jsonb_build_object('token_limit_per_team', p_limit)
    );
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'model', jsonb_build_object('model', p_model, 'token_limit_per_team', p_limit)
  );
end;
$$;

-- Grants or revokes a level for a team, or clears the override to go back to what
-- the team's solves earn. Returns the prizes page.
create or replace function set_level_override(
  p_admin_id uuid,
  p_team_id uuid,
  p_level integer,
  p_override text
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_required smallint;
  v_earned_at timestamptz;
  v_before jsonb;
  v_after jsonb;
begin
  if p_override is not null and p_override not in ('granted', 'revoked') then
    return jsonb_build_object('status', 'invalid_override');
  end if;

  -- The lock submit_answer takes, so this can't cross a completion being recorded.
  perform 1 from teams where id = p_team_id for no key update;
  if not found then
    return jsonb_build_object('status', 'team_not_found');
  end if;

  select required_bots into v_required from levels where level_number = p_level;
  if not found then
    return jsonb_build_object('status', 'level_not_found');
  end if;

  select to_jsonb(c) into v_before from level_completions c where c.team_id = p_team_id and c.level = p_level;

  if p_override = 'granted' then
    insert into level_completions (team_id, level, source, granted_by)
    values (p_team_id, p_level, 'admin_override', p_admin_id)
    on conflict (team_id, level) do update set
      source = 'admin_override',
      granted_by = case
        when level_completions.source = 'admin_override' and not level_completions.revoked
        then level_completions.granted_by else p_admin_id
      end,
      -- A level that was already complete keeps its place in the prize order.
      completed_at = case when level_completions.revoked then now() else level_completions.completed_at end,
      revoked = false,
      revoked_by = null,
      revoked_at = null;
  elsif p_override = 'revoked' then
    insert into level_completions (team_id, level, source, revoked, revoked_by, revoked_at)
    values (p_team_id, p_level, 'admin_override', true, p_admin_id, now())
    on conflict (team_id, level) do update set
      revoked = true,
      revoked_by = case when level_completions.revoked then level_completions.revoked_by else p_admin_id end,
      revoked_at = coalesce(level_completions.revoked_at, now());
  else
    -- Back to what the solves earn, completed when the required-th score came in.
    delete from level_completions where team_id = p_team_id and level = p_level;
    select s.awarded_at into v_earned_at
    from scores s join bots b on b.id = s.bot_id
    where s.team_id = p_team_id and b.level = p_level
    order by s.awarded_at
    offset v_required - 1
    limit 1;
    if v_earned_at is not null then
      insert into level_completions (team_id, level, completed_at) values (p_team_id, p_level, v_earned_at);
    end if;
  end if;

  select to_jsonb(c) into v_after from level_completions c where c.team_id = p_team_id and c.level = p_level;
  perform write_audit(
    p_admin_id, 'set_level_override', 'level_completion', p_team_id::text || ':' || p_level, v_before, v_after
  );
  return get_admin_prizes();
end;
$$;

-- ---------- Last active ----------

-- Called on every team action. Writes at most once a minute per team.
create or replace function touch_team(p_team_id uuid) returns void
language sql
security invoker
set search_path = public, pg_temp
as $$
  update teams set last_active_at = now()
  where id = p_team_id and (last_active_at is null or last_active_at < now() - interval '1 minute')
$$;

-- ---------- Access ----------
-- Supabase exposes public functions through its API, and these trust the IDs
-- they're given, so only the backend may call them.

revoke all on function admin_bot_status(uuid, uuid) from public, anon, authenticated;
revoke all on function get_admin_overview() from public, anon, authenticated;
revoke all on function get_admin_teams() from public, anon, authenticated;
revoke all on function get_admin_team(uuid) from public, anon, authenticated;
revoke all on function get_admin_bots() from public, anon, authenticated;
revoke all on function get_admin_bot(uuid) from public, anon, authenticated;
revoke all on function get_bot_stats() from public, anon, authenticated;
revoke all on function get_admin_models() from public, anon, authenticated;
revoke all on function get_admin_submissions(text, uuid, integer, integer) from public, anon, authenticated;
revoke all on function get_admin_prizes() from public, anon, authenticated;
revoke all on function write_audit(uuid, text, text, text, jsonb, jsonb) from public, anon, authenticated;
revoke all on function update_team(uuid, uuid, text, boolean) from public, anon, authenticated;
revoke all on function set_bot_enabled(uuid, uuid, boolean) from public, anon, authenticated;
revoke all on function set_model_token_limit(uuid, text, bigint) from public, anon, authenticated;
revoke all on function set_level_override(uuid, uuid, integer, text) from public, anon, authenticated;
revoke all on function touch_team(uuid) from public, anon, authenticated;

grant execute on function admin_bot_status(uuid, uuid) to service_role;
grant execute on function get_admin_overview() to service_role;
grant execute on function get_admin_teams() to service_role;
grant execute on function get_admin_team(uuid) to service_role;
grant execute on function get_admin_bots() to service_role;
grant execute on function get_admin_bot(uuid) to service_role;
grant execute on function get_bot_stats() to service_role;
grant execute on function get_admin_models() to service_role;
grant execute on function get_admin_submissions(text, uuid, integer, integer) to service_role;
grant execute on function get_admin_prizes() to service_role;
grant execute on function write_audit(uuid, text, text, text, jsonb, jsonb) to service_role;
grant execute on function update_team(uuid, uuid, text, boolean) to service_role;
grant execute on function set_bot_enabled(uuid, uuid, boolean) to service_role;
grant execute on function set_model_token_limit(uuid, text, bigint) to service_role;
grant execute on function set_level_override(uuid, uuid, integer, text) to service_role;
grant execute on function touch_team(uuid) to service_role;

commit;

-- Makes Supabase's API notice the new functions and views straight away.
notify pgrst, 'reload schema';
