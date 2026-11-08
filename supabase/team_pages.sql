-- What teams see on their home, levels, bot and submissions pages. Run after
-- schema.sql. Safe to re-run.
--
-- Every function trusts the team ID it's given, so only the backend may call them
-- (see the grants at the end). Each builds its result field by field, so a system
-- prompt, password, rubric, defence, api_model or host can't reach a team.

begin;

-- ---------- Shared ----------

-- This team's view of every bot it can see. Levels are always open, so no bot is
-- locked. A conversation counts as an attempt only with a complete message, since
-- a failed turn is hidden from the team.
create or replace function team_bot_cards(p_team_id uuid)
returns table (
  id uuid,
  level smallint,
  label text,
  display_order smallint,
  model_id text,
  model text,
  check_type check_type,
  points integer,
  status text,
  wrong_answers_used integer
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select
    b.id,
    b.level,
    b.label,
    b.display_order,
    b.model_id,
    m.display_name,
    b.check_type,
    l.points_per_bot,
    case
      when exists (select 1 from scores s where s.team_id = p_team_id and s.bot_id = b.id) then 'solved'
      when exists (select 1 from answer_attempts a where a.team_id = p_team_id and a.bot_id = b.id)
        or exists (
          select 1 from conversations c
          join messages ms on ms.conversation_id = c.id
          where c.team_id = p_team_id and c.bot_id = b.id and ms.status = 'complete'
        ) then 'attempted'
      else 'unattempted'
    end,
    (
      select count(*)::integer from answer_attempts a
      where a.team_id = p_team_id and a.bot_id = b.id and not a.correct
    )
  from bots b
  join levels l on l.level_number = b.level
  join models m on m.id = b.model_id
  where b.enabled and l.enabled
$$;

-- This team's standing on each visible level. A revoked completion doesn't count,
-- and with a winner limit, only the first teams to complete win the prize.
create or replace function team_level_results(p_team_id uuid)
returns table (level smallint, complete boolean, revoked boolean, prize_awarded integer)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select
    l.level_number,
    coalesce(not c.revoked, false),
    coalesce(c.revoked, false),
    case
      when c.team_id is null or c.revoked then 0
      when l.max_winners is null or (
        select count(*) from level_completions o
        where o.level = l.level_number
          and not o.revoked
          and o.team_id <> p_team_id
          and o.completed_at < c.completed_at
      ) < l.max_winners then l.prize_amount
      else 0
    end
  from levels l
  left join level_completions c on c.level = l.level_number and c.team_id = p_team_id
  where l.enabled
$$;

-- ---------- Pages ----------

create or replace function get_team_me(p_team_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_team teams%rowtype;
begin
  select * into v_team from teams where id = p_team_id;
  if not found or v_team.disabled then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'id', v_team.id,
    'name', v_team.name,
    'tokens_used', (select coalesce(sum(tokens), 0)::bigint from credit_ledger where team_id = p_team_id)
  );
end;
$$;

create or replace function get_team_dashboard(p_team_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_standing leaderboard%rowtype;
begin
  -- The leaderboard leaves out disabled teams.
  select * into v_standing from leaderboard where id = p_team_id;
  if not found then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  return (
    with cards as (select * from team_bot_cards(p_team_id)),
    results as (select * from team_level_results(p_team_id)),
    usage as (
      select
        m.id,
        m.display_name,
        m.enabled,
        coalesce((
          select sum(l.tokens) from credit_ledger l
          where l.team_id = p_team_id and l.model_id = m.id
        ), 0)::bigint as tokens_used,
        (select min(b.display_order) from bots b where b.model_id = m.id) as bot_order
      from models m
    )
    select jsonb_build_object(
      'status', 'ok',
      'points', v_standing.total_points,
      'rank', v_standing.rank,
      'total_teams', (select count(*) from leaderboard),
      'attempted', (select count(*) from cards where status in ('attempted', 'solved')),
      'solved', (select count(*) from cards where status = 'solved'),
      'remaining', (select count(*) from cards where status <> 'solved'),
      'levels_completed', (select count(*) from results where complete),
      'total_levels', (select count(*) from results),
      'prize_earned', (select coalesce(sum(prize_awarded), 0)::bigint from results),
      'tokens_used', (select coalesce(sum(tokens_used), 0)::bigint from usage),
      -- A model that's since been turned off stays listed if the team used it.
      'usage_by_model', coalesce((
        select jsonb_agg(
          jsonb_build_object(
            'model', u.display_name,
            'tokens_used', u.tokens_used
          )
          order by u.bot_order nulls last, u.id
        )
        from usage u
        where u.enabled or u.tokens_used > 0
      ), '[]'::jsonb)
    )
  );
end;
$$;

create or replace function get_team_levels(p_team_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from teams where id = p_team_id and not disabled) then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  return (
    with cards as (select * from team_bot_cards(p_team_id))
    select jsonb_build_object(
      'status', 'ok',
      'levels', coalesce(jsonb_agg(
        jsonb_build_object(
          'level', l.level_number,
          'name', l.name,
          'locked', false,
          'solved', (select count(*) from cards c where c.level = l.level_number and c.status = 'solved'),
          'required', l.required_bots,
          'total', (select count(*) from cards c where c.level = l.level_number),
          'complete', r.complete,
          'revoked', r.revoked,
          'prize_amount', l.prize_amount,
          'prize_awarded', r.prize_awarded,
          'bots', coalesce((
            select jsonb_agg(
              jsonb_build_object(
                'id', c.id,
                'level', c.level,
                'label', c.label,
                'model', c.model,
                'check_type', c.check_type,
                'points', c.points,
                'status', c.status,
                'wrong_answers_used', c.wrong_answers_used
              )
              order by c.display_order
            )
            from cards c
            where c.level = l.level_number
          ), '[]'::jsonb)
        )
        order by l.level_number
      ), '[]'::jsonb)
    )
    from levels l
    join team_level_results(p_team_id) r on r.level = l.level_number
  );
end;
$$;

create or replace function get_team_bot(p_team_id uuid, p_bot_id uuid, p_max_wrong_answers integer) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_card record;
begin
  if not exists (select 1 from teams where id = p_team_id and not disabled) then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  select * into v_card from team_bot_cards(p_team_id) c where c.id = p_bot_id;
  if not found then
    return jsonb_build_object('status', 'bot_not_found');
  end if;

  return (
    select jsonb_build_object(
      'status', 'ok',
      'id', v_card.id,
      'level', v_card.level,
      'label', v_card.label,
      'model', v_card.model,
      'check_type', v_card.check_type,
      'points', v_card.points,
      'bot_status', v_card.status,
      'level_name', l.name,
      'description', b.description,
      'goal', b.goal,
      'wrong_answers_used', v_card.wrong_answers_used,
      'max_wrong_answers', p_max_wrong_answers
    )
    from bots b
    join levels l on l.level_number = b.level
    join models m on m.id = b.model_id
    where b.id = p_bot_id
  );
end;
$$;

-- Judge submissions and password attempts, newest first. The text of a guess is
-- never returned. An attempt earns points only if it's the team's first correct
-- one on that bot and the score came from the answer box.
create or replace function get_team_submissions(p_team_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from teams where id = p_team_id and not disabled) then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  return (
    with merged as (
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
        s.graded_at
      from submissions s
      join bots b on b.id = s.bot_id
      where s.team_id = p_team_id
      union all
      select
        'attempt-' || a.id,
        null,
        a.bot_id,
        b.level,
        b.label,
        b.check_type,
        case when a.correct then 'passed' else 'failed' end,
        case when a.correct then 'Correct password.' else 'Incorrect password.' end,
        case
          when a.correct and a.id = (
            select min(first.id) from answer_attempts first
            where first.team_id = a.team_id and first.bot_id = a.bot_id and first.correct
          ) then coalesce((
            select sc.points from scores sc
            where sc.team_id = a.team_id and sc.bot_id = a.bot_id and sc.source = 'answer'
          ), 0)
          else 0
        end,
        a.created_at,
        a.created_at
      from answer_attempts a
      join bots b on b.id = a.bot_id
      where a.team_id = p_team_id
    )
    select jsonb_build_object(
      'status', 'ok',
      'submissions', coalesce(jsonb_agg(
        jsonb_build_object(
          'id', m.id,
          'conversation_id', m.conversation_id,
          'bot_id', m.bot_id,
          'level', m.level,
          'bot_label', m.bot_label,
          'check_type', m.check_type,
          'status', m.status,
          'reason', m.reason,
          'points_awarded', m.points_awarded,
          'created_at', m.created_at,
          'graded_at', m.graded_at
        )
        order by m.created_at desc, m.id
      ), '[]'::jsonb)
    )
    from merged m
  );
end;
$$;

-- ---------- Access ----------
-- Supabase exposes public functions through its API, and these trust the team ID
-- they're given, so only the backend may call them.

revoke all on function team_bot_cards(uuid) from public, anon, authenticated;
revoke all on function team_level_results(uuid) from public, anon, authenticated;
revoke all on function get_team_me(uuid) from public, anon, authenticated;
revoke all on function get_team_dashboard(uuid) from public, anon, authenticated;
revoke all on function get_team_levels(uuid) from public, anon, authenticated;
revoke all on function get_team_bot(uuid, uuid, integer) from public, anon, authenticated;
revoke all on function get_team_submissions(uuid) from public, anon, authenticated;

grant execute on function team_bot_cards(uuid) to service_role;
grant execute on function team_level_results(uuid) to service_role;
grant execute on function get_team_me(uuid) to service_role;
grant execute on function get_team_dashboard(uuid) to service_role;
grant execute on function get_team_levels(uuid) to service_role;
grant execute on function get_team_bot(uuid, uuid, integer) to service_role;
grant execute on function get_team_submissions(uuid) to service_role;

commit;
