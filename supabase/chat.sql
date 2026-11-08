-- The checks before a chat turn, and the bot details the engine reads. Run after
-- admin.sql, because POST /messages records team activity with touch_team. Safe
-- to re-run.
--
-- Every function trusts the IDs it's given, so only the backend may call them
-- (see the grants at the end).

begin;

-- Runs every check before a turn and then begin_turn, as one transaction, so
-- parallel sends can't pass the checks together. A refusal leaves nothing behind.
-- p_available_hosts holds the hosts the backend has a key for.
create or replace function start_turn(
  p_team_id uuid,
  p_bot_id uuid,
  p_conversation_id uuid,
  p_content text,
  p_available_hosts text[],
  p_max_wrong_answers integer,
  p_rate_limit_messages integer,
  p_rate_limit_window_seconds integer,
  p_max_open_turns integer
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_disabled boolean;
  v_level smallint;
  v_unlock_in_order boolean;
  v_check_type check_type;
  v_available boolean;
  v_reviewer text;
  v_window interval := make_interval(secs => p_rate_limit_window_seconds);
  v_recent integer;
  v_oldest timestamptz;
begin
  -- The lock submit_answer takes, so a team's sends run one at a time. It's held
  -- until this function commits, which is before the model call.
  select disabled into v_disabled from teams where id = p_team_id for no key update;
  if not found or v_disabled then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  select
    b.level, l.unlock_in_order, b.check_type,
    coalesce(mo.enabled and nullif(mo.api_model, '') is not null and mo.host = any(p_available_hosts), false),
    b.defences ->> 'reviewerModel'
    into v_level, v_unlock_in_order, v_check_type, v_available, v_reviewer
  from bots b
  join levels l on l.level_number = b.level
  join models mo on mo.id = b.model_id
  where b.id = p_bot_id and b.enabled and l.enabled;
  if not found then
    return jsonb_build_object('status', 'bot_not_found');
  end if;

  -- The same rule as submit_answer. It does nothing while unlock_in_order is off.
  if v_unlock_in_order and exists (
    select 1 from levels earlier
    where earlier.level_number < v_level
      and earlier.enabled
      and exists (select 1 from bots b where b.level = earlier.level_number and b.enabled)
      and not exists (
        select 1 from level_completions c
        where c.team_id = p_team_id and c.level = earlier.level_number and not c.revoked
      )
  ) then
    return jsonb_build_object('status', 'level_locked');
  end if;

  -- A bot whose reviewer can't run would fail every turn, so it refuses messages
  -- the same way, with nothing saved.
  if v_reviewer is not null and not coalesce((
    select r.enabled and nullif(r.api_model, '') is not null and r.host = any(p_available_hosts)
    from models r where r.display_name = v_reviewer
  ), false) then
    v_available := false;
  end if;

  if not v_available then
    return jsonb_build_object('status', 'model_unavailable');
  end if;

  -- This also closes the chat on a bot the team has already solved.
  if v_check_type = 'answer' and (
    select count(*) from answer_attempts
    where team_id = p_team_id and bot_id = p_bot_id and not correct
  ) >= p_max_wrong_answers then
    return jsonb_build_object('status', 'wrong_answers_used_up');
  end if;

  -- Tokens remain usage statistics. LiteLLM enforces the shared dollar budget.

  -- A team message with no reply after it, from the last 10 minutes, is still
  -- streaming. This bounds simultaneous turns independently of spending budgets.
  if (
    select count(*) from messages m
    where m.team_id = p_team_id and m.role = 'user' and m.status = 'complete'
      and m.created_at > now() - interval '10 minutes'
      and not exists (
        select 1 from messages later
        where later.conversation_id = m.conversation_id and later.seq > m.seq
      )
  ) >= p_max_open_turns then
    return jsonb_build_object('status', 'rate_limited', 'retry_after_seconds', 5);
  end if;

  -- A failed turn's message is marked failed, so it doesn't count here.
  select count(*), min(created_at) into v_recent, v_oldest
  from messages
  where team_id = p_team_id and role = 'user' and status = 'complete'
    and created_at > now() - v_window;
  if v_recent >= p_rate_limit_messages then
    return jsonb_build_object(
      'status', 'rate_limited',
      'retry_after_seconds', greatest(ceil(extract(epoch from v_oldest + v_window - now())), 1)
    );
  end if;

  return begin_turn(p_team_id, p_bot_id, p_conversation_id, p_content);
end;
$$;

-- What the engine needs to run a bot. The password is never its own field: it
-- reaches the engine inside the filled-in prompt, and, when the output filter is
-- on, as one of the output filter's phrases, unlabelled. The rubric stays out too.
-- The same rule is in output_phrases in backend/app/engine/defences.py.
create or replace function get_bot_config(p_bot_id uuid) returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select coalesce((
    select jsonb_build_object(
      'status', 'ok',
      'id', b.id,
      'level', b.level,
      'label', b.label,
      'model', mo.display_name,
      'provider', mo.provider,
      'host', mo.host,
      'api_model', mo.api_model,
      'tools', b.tools,
      'defences', b.defences,
      'check_type', b.check_type,
      'context_limit_tokens', b.context_limit_tokens,
      'system_prompt', case
        when b.check_type = 'answer' then replace(b.system_prompt, '{{SECRET}}', b.secret)
        else b.system_prompt
      end,
      'output_filter_phrases', case
        when coalesce(b.defences -> 'outputFilter', 'false'::jsonb) in ('false'::jsonb, 'null'::jsonb) then '[]'::jsonb
        else
          case when jsonb_typeof(b.defences -> 'outputFilter') = 'array' then b.defences -> 'outputFilter' else '[]'::jsonb end
          || case when b.check_type = 'answer' and b.secret is not null then jsonb_build_array(b.secret) else '[]'::jsonb end
      end,
      'reviewer', case when r.id is null then null else jsonb_build_object(
        'model', r.display_name, 'provider', r.provider, 'host', r.host, 'api_model', r.api_model
      ) end
    )
    from bots b
    join models mo on mo.id = b.model_id
    left join models r on r.display_name = b.defences ->> 'reviewerModel'
    where b.id = p_bot_id
  ), jsonb_build_object('status', 'bot_not_found'))
$$;

-- ---------- Access ----------
-- Supabase exposes public functions through its API, and these trust the IDs
-- they're given, so only the backend may call them.

revoke all on function start_turn(uuid, uuid, uuid, text, text[], integer, integer, integer, integer)
  from public, anon, authenticated;
revoke all on function get_bot_config(uuid) from public, anon, authenticated;

grant execute on function start_turn(uuid, uuid, uuid, text, text[], integer, integer, integer, integer)
  to service_role;
grant execute on function get_bot_config(uuid) to service_role;

commit;

-- Makes Supabase's API notice the new functions straight away.
notify pgrst, 'reload schema';
