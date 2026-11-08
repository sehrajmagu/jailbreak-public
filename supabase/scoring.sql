-- Level 1 answer checking and scoring. Run after schema.sql. Safe to re-run.

begin;

-- Does the whole answer step in one transaction, and never returns the secret.
-- Returns { status } and, when status is 'ok', the result fields too. Other
-- statuses mean nothing was recorded.
create or replace function submit_answer(
  p_team_id uuid,
  p_bot_id uuid,
  p_answer text,
  p_max_wrong_answers integer default 10
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_disabled boolean;
  v_level smallint;
  v_check_type check_type;
  v_secret text;
  v_points integer;
  v_required smallint;
  v_unlock_in_order boolean;
  -- Plain btrim removes only spaces, so name the other whitespace too.
  v_answer text := btrim(p_answer, E' \t\n\r\f\v');
  v_correct boolean;
  v_wrong integer;
  v_inserted integer;
begin
  -- Runs one team's answers one at a time, so simultaneous requests can't get
  -- past the cap or miss a level completion. This lock mode doesn't block the
  -- team's other inserts, such as chat messages.
  select disabled into v_disabled from teams where id = p_team_id for no key update;
  if not found or v_disabled then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  select b.level, b.check_type, b.secret, l.points_per_bot, l.required_bots, l.unlock_in_order
    into v_level, v_check_type, v_secret, v_points, v_required, v_unlock_in_order
  from bots b
  join levels l on l.level_number = b.level
  where b.id = p_bot_id and b.enabled and l.enabled;
  if not found then
    return jsonb_build_object('status', 'bot_not_found');
  end if;

  if v_check_type <> 'answer' then
    return jsonb_build_object('status', 'not_answer_bot');
  end if;

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

  if v_answer is null or v_answer = '' or char_length(v_answer) > 500 then
    return jsonb_build_object('status', 'invalid_answer');
  end if;

  select count(*) into v_wrong from answer_attempts
  where team_id = p_team_id and bot_id = p_bot_id and not correct;
  if v_wrong >= p_max_wrong_answers then
    return jsonb_build_object('status', 'cap_reached');
  end if;

  v_correct := v_answer = v_secret;
  insert into answer_attempts (team_id, bot_id, answer, correct)
  values (p_team_id, p_bot_id, v_answer, v_correct);

  if not v_correct then
    return jsonb_build_object(
      'status', 'ok', 'correct', false, 'points_awarded', 0,
      'wrong_answers_used', v_wrong + 1, 'max_wrong_answers', p_max_wrong_answers
    );
  end if;

  insert into scores (team_id, bot_id, points, source)
  values (p_team_id, p_bot_id, v_points, 'answer')
  on conflict (team_id, bot_id) do nothing;
  get diagnostics v_inserted = row_count;

  if v_inserted > 0 and (
    select count(*) from scores s join bots b on b.id = s.bot_id
    where s.team_id = p_team_id and b.level = v_level
  ) >= v_required then
    -- Leaves an existing row alone, so a revoked completion stays revoked.
    insert into level_completions (team_id, level)
    values (p_team_id, v_level)
    on conflict (team_id, level) do nothing;
  end if;

  return jsonb_build_object(
    'status', 'ok', 'correct', true,
    'points_awarded', case when v_inserted > 0 then v_points else 0 end,
    'wrong_answers_used', v_wrong, 'max_wrong_answers', p_max_wrong_answers
  );
end;
$$;

-- Supabase exposes public functions through its API, and this one trusts the
-- team ID it's given, so only the backend may call it.
revoke all on function submit_answer(uuid, uuid, text, integer) from public, anon, authenticated;
grant execute on function submit_answer(uuid, uuid, text, integer) to service_role;

commit;
