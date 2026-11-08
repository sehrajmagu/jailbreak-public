-- Submitting a conversation, the grading queue, and judge scoring. Run after
-- admin.sql. Safe to re-run.
--
-- A submission moves from pending to grading, then to passed or failed. If grading
-- itself breaks it moves to error, which is never a verdict: it's retried until
-- next_attempt_at is null, and then waits for an organiser to regrade it. Every
-- function trusts the IDs it's given, so only the backend may call them (see the
-- grants at the end).
--
-- Organisers can turn judging off without a restart (judge_settings.enabled). Teams
-- can still submit, but nothing is sent to the judge model until it's back on. A
-- rate limit or provider outage pauses all grading for a growing delay
-- (judge_settings.backoff_until), so a busy queue can't hammer a judge that's
-- refusing calls, and the submission waits to be retried rather than failing.

begin;

alter table submissions add column if not exists attempts integer not null default 0;
alter table submissions add column if not exists next_attempt_at timestamptz;
alter table submissions add column if not exists grading_started_at timestamptz;
-- Set on each claim, so a worker that lost its claim can't save an old result.
alter table submissions add column if not exists grading_token uuid;
-- Copied to judge_runs.triggered_by.
alter table submissions add column if not exists grading_trigger text not null default 'auto';

alter table submissions drop constraint if exists submissions_grading_trigger_check;
alter table submissions add constraint submissions_grading_trigger_check
  check (grading_trigger in ('auto', 'admin_regrade'));

-- One row of settings, read by the worker before every claim. Only the backend
-- reads or writes it, through the functions below.
create table if not exists judge_settings (
  id boolean primary key default true check (id),
  enabled boolean not null default true,
  -- No grading before this time, after a rate limit or outage.
  backoff_until timestamptz,
  -- Rate limits and outages in a row, for the growing delay. A response resets it.
  deferrals integer not null default 0 check (deferrals >= 0),
  updated_at timestamptz not null default now()
);
insert into judge_settings (id) values (true) on conflict (id) do nothing;
alter table judge_settings enable row level security;
revoke all on judge_settings from anon, authenticated;

-- A submission in the shape the team API returns.
create or replace function team_submission_json(s submissions) returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'id', s.id,
    'conversation_id', s.conversation_id,
    'bot_id', s.bot_id,
    'level', b.level,
    'bot_label', b.label,
    'check_type', b.check_type,
    'status', s.status,
    'reason', s.reason,
    'points_awarded', s.points_awarded,
    'created_at', s.created_at,
    'graded_at', s.graded_at
  )
  from bots b
  where b.id = s.bot_id
$$;

-- Closes the conversation and queues it, in one transaction. Grading happens later,
-- so a slow judge can't hold up the request.
create or replace function submit_conversation(p_team_id uuid, p_conversation_id uuid) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_disabled boolean;
  v_conversation conversations%rowtype;
  v_level smallint;
  v_check_type check_type;
  v_unlock_in_order boolean;
  v_last messages%rowtype;
  v_submission submissions%rowtype;
begin
  -- The same lock order as start_turn, so a turn and a submit can't overlap.
  select disabled into v_disabled from teams where id = p_team_id for no key update;
  if not found or v_disabled then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  select * into v_conversation from conversations
  where id = p_conversation_id and team_id = p_team_id
  for update;
  if not found then
    return jsonb_build_object('status', 'conversation_not_found');
  end if;

  select b.level, b.check_type, l.unlock_in_order
    into v_level, v_check_type, v_unlock_in_order
  from bots b
  join levels l on l.level_number = b.level
  where b.id = v_conversation.bot_id and b.enabled and l.enabled;
  if not found then
    return jsonb_build_object('status', 'bot_not_found');
  end if;

  if v_check_type <> 'judge' then
    return jsonb_build_object('status', 'not_judge_bot');
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

  if v_conversation.status = 'submitted'
    or exists (select 1 from submissions where conversation_id = v_conversation.id) then
    return jsonb_build_object('status', 'already_submitted');
  end if;
  if v_conversation.status <> 'active' then
    return jsonb_build_object('status', 'conversation_closed');
  end if;

  select * into v_last from messages
  where conversation_id = v_conversation.id
  order by seq desc
  limit 1;
  -- The reply is still streaming, and it's what the judge needs to read. After 10
  -- minutes it can only be a turn whose server died, as in begin_turn.
  if found and v_last.role = 'user' and v_last.status = 'complete'
    and v_last.created_at > now() - interval '10 minutes' then
    return jsonb_build_object('status', 'turn_in_progress');
  end if;

  if not exists (
    select 1 from messages m
    where m.conversation_id = v_conversation.id and m.role = 'assistant' and m.status = 'complete'
  ) then
    return jsonb_build_object('status', 'no_bot_replies');
  end if;

  update conversations set status = 'submitted', submitted_at = now(), updated_at = now()
  where id = v_conversation.id;

  insert into submissions (team_id, bot_id, conversation_id)
  values (p_team_id, v_conversation.bot_id, v_conversation.id)
  returning * into v_submission;

  return jsonb_build_object('status', 'ok', 'submission', team_submission_json(v_submission));
end;
$$;

-- The only bot fields the judge reads: never the system prompt or the password.
create or replace function get_judge_bot_config(p_bot_id uuid) returns jsonb
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
      'check_type', b.check_type,
      'win_rubric', coalesce(b.rubric, ''),
      'enabled', b.enabled
    )
    from bots b
    where b.id = p_bot_id
  ), jsonb_build_object('status', 'bot_not_found'))
$$;

-- Takes the next submission for one worker, and returns what the team could see of
-- its conversation, so failed turns are never graded. Returns a null submission when
-- there's nothing to do. A claim older than the lease belongs to a worker that died,
-- so it's taken again, until it has used up its attempts.
create or replace function claim_submission(
  p_judge_model text,
  p_lease_seconds integer,
  p_max_attempts integer
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_lease interval := make_interval(secs => p_lease_seconds);
  v_submission submissions%rowtype;
  v_settings judge_settings%rowtype;
begin
  -- Off, or waiting out a rate limit: nothing is claimed, so nothing reaches the judge.
  select * into v_settings from judge_settings where id;
  if found and (not v_settings.enabled or v_settings.backoff_until > now()) then
    return jsonb_build_object('status', 'ok', 'submission', null);
  end if;

  with lost as (
    update submissions
    set status = 'error', reason = 'Grading error: worker_lost', next_attempt_at = null,
      grading_token = null, grading_started_at = null
    where status = 'grading' and grading_started_at < now() - v_lease and attempts >= p_max_attempts
    returning id, grading_trigger
  )
  insert into judge_runs (submission_id, judge_model, error, triggered_by)
  select id, p_judge_model, 'worker_lost', grading_trigger from lost;

  select * into v_submission from submissions
  where status = 'pending'
    or (status = 'error' and next_attempt_at <= now())
    or (status = 'grading' and grading_started_at < now() - v_lease)
  order by created_at, id
  limit 1
  -- Workers skip each other's rows, so no submission is graded twice at once.
  for update skip locked;
  if not found then
    return jsonb_build_object('status', 'ok', 'submission', null);
  end if;

  update submissions
  set status = 'grading', attempts = attempts + 1, next_attempt_at = null, reason = null,
    grading_started_at = now(), grading_token = gen_random_uuid()
  where id = v_submission.id
  returning * into v_submission;

  return jsonb_build_object(
    'status', 'ok',
    'submission', jsonb_build_object(
      'id', v_submission.id,
      'bot_id', v_submission.bot_id,
      'token', v_submission.grading_token,
      'attempts', v_submission.attempts,
      'messages', coalesce((
        select jsonb_agg(message_json(m) order by m.seq)
        from messages m
        where m.conversation_id = v_submission.conversation_id and team_sees_message(m)
      ), '[]'::jsonb)
    )
  );
end;
$$;

-- Saves a verdict and its points, in one transaction under the team's lock.
-- Returns saved = false, changing nothing, if this worker's claim has been replaced.
--
-- Points go only to a team's first pass on a bot. A regrade changes them like this:
-- - A pass that already holds the bot's points keeps them, so nothing is paid twice.
-- - A fail that held them takes them back. They move to the team's earliest other
--   passing submission on this bot if there is one. Otherwise, an earned level
--   completion goes too once the team is below the level's required solves.
-- Answer and admin scores hold no submission, so they're never affected.
create or replace function finish_submission(
  p_submission_id uuid,
  p_token uuid,
  p_judge_model text,
  p_passed boolean,
  p_reason text,
  p_input_tokens integer,
  p_output_tokens integer
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_team_id uuid;
  v_submission submissions%rowtype;
  v_level smallint;
  v_points integer;
  v_required smallint;
  v_awarded integer := 0;
  v_held integer;
  v_inserted integer;
  v_next uuid;
begin
  select team_id into v_team_id from submissions where id = p_submission_id;
  if not found then
    return jsonb_build_object('status', 'ok', 'saved', false);
  end if;

  -- Runs one team's scoring one at a time, as submit_answer does.
  perform 1 from teams where id = v_team_id for no key update;

  select * into v_submission from submissions where id = p_submission_id for update;
  if v_submission.status <> 'grading' or v_submission.grading_token is distinct from p_token then
    return jsonb_build_object('status', 'ok', 'saved', false);
  end if;

  select b.level, l.points_per_bot, l.required_bots into v_level, v_points, v_required
  from bots b
  join levels l on l.level_number = b.level
  where b.id = v_submission.bot_id;

  if p_passed then
    insert into scores (team_id, bot_id, points, source, submission_id)
    values (v_team_id, v_submission.bot_id, v_points, 'judge', p_submission_id)
    on conflict (team_id, bot_id) do nothing;
    get diagnostics v_inserted = row_count;

    if v_inserted > 0 then
      v_awarded := v_points;
      if (
        select count(*) from scores s join bots b on b.id = s.bot_id
        where s.team_id = v_team_id and b.level = v_level
      ) >= v_required then
        -- Leaves an existing row alone, so a revoked completion stays revoked.
        insert into level_completions (team_id, level)
        values (v_team_id, v_level)
        on conflict (team_id, level) do nothing;
      end if;
    else
      select points into v_held from scores
      where team_id = v_team_id and bot_id = v_submission.bot_id and submission_id = p_submission_id;
      v_awarded := coalesce(v_held, 0);
    end if;
  else
    delete from scores
    where team_id = v_team_id and bot_id = v_submission.bot_id and submission_id = p_submission_id
    returning points into v_held;

    if v_held is not null then
      select id into v_next from submissions
      where team_id = v_team_id and bot_id = v_submission.bot_id
        and status = 'passed' and id <> p_submission_id
      order by created_at, id
      limit 1;

      if v_next is not null then
        insert into scores (team_id, bot_id, points, source, submission_id)
        values (v_team_id, v_submission.bot_id, v_held, 'judge', v_next);
        update submissions set points_awarded = v_held where id = v_next;
      elsif (
        select count(*) from scores s join bots b on b.id = s.bot_id
        where s.team_id = v_team_id and b.level = v_level
      ) < v_required then
        -- Only an earned completion: an organiser's override stays as they set it.
        delete from level_completions
        where team_id = v_team_id and level = v_level and source = 'earned' and not revoked;
      end if;
    end if;
  end if;

  insert into judge_runs (submission_id, judge_model, verdict, reason, input_tokens, output_tokens, triggered_by)
  values (p_submission_id, p_judge_model, p_passed, p_reason, p_input_tokens, p_output_tokens, v_submission.grading_trigger);

  update submissions
  set status = case when p_passed then 'passed'::submission_status else 'failed'::submission_status end,
    reason = p_reason, points_awarded = v_awarded, graded_at = now(),
    next_attempt_at = null, grading_started_at = null, grading_token = null
  where id = p_submission_id;

  return jsonb_build_object('status', 'ok', 'saved', true);
end;
$$;

-- Records that grading broke, which is never a verdict. A null p_retry_seconds means
-- it waits for an organiser. p_code is one of the judge's fixed error codes.
create or replace function record_submission_error(
  p_submission_id uuid,
  p_token uuid,
  p_judge_model text,
  p_code text,
  p_input_tokens integer,
  p_output_tokens integer,
  p_retry_seconds integer
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_submission submissions%rowtype;
begin
  select * into v_submission from submissions where id = p_submission_id for update;
  if not found or v_submission.status <> 'grading' or v_submission.grading_token is distinct from p_token then
    return jsonb_build_object('status', 'ok', 'saved', false);
  end if;

  insert into judge_runs (submission_id, judge_model, error, input_tokens, output_tokens, triggered_by)
  values (p_submission_id, p_judge_model, p_code, p_input_tokens, p_output_tokens, v_submission.grading_trigger);

  update submissions
  set status = 'error', reason = 'Grading error: ' || p_code,
    next_attempt_at = now() + make_interval(secs => p_retry_seconds),
    grading_started_at = null, grading_token = null
  where id = p_submission_id;

  return jsonb_build_object('status', 'ok', 'saved', true);
end;
$$;

-- Records a rate limit, timeout or provider outage. The submission waits, never
-- counted as failed and never out of attempts, and all grading pauses for a delay
-- that doubles with each one in a row: 30 seconds up to 10 minutes, or longer if
-- the provider asked. Returns the delay in seconds.
create or replace function defer_submission(
  p_submission_id uuid,
  p_token uuid,
  p_judge_model text,
  p_code text,
  p_input_tokens integer,
  p_output_tokens integer,
  p_retry_after_seconds integer
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_submission submissions%rowtype;
  v_deferrals integer;
  v_delay integer;
begin
  select * into v_submission from submissions where id = p_submission_id for update;
  if not found or v_submission.status <> 'grading' or v_submission.grading_token is distinct from p_token then
    return jsonb_build_object('status', 'ok', 'saved', false);
  end if;

  -- One worker at a time, so two deferrals in a row both count.
  update judge_settings set deferrals = deferrals + 1 where id returning deferrals into v_deferrals;
  v_delay := greatest(
    least(30 * power(2, least(coalesce(v_deferrals, 1), 6) - 1)::integer, 600),
    -- A provider's own delay is honoured, up to an hour.
    least(coalesce(p_retry_after_seconds, 0), 3600)
  );
  update judge_settings set backoff_until = now() + make_interval(secs => v_delay) where id;

  insert into judge_runs (submission_id, judge_model, error, input_tokens, output_tokens, triggered_by)
  values (p_submission_id, p_judge_model, p_code, p_input_tokens, p_output_tokens, v_submission.grading_trigger);

  -- Not counted as an attempt, so a long outage never uses them up.
  update submissions
  set status = 'error', reason = 'Grading deferred: ' || p_code,
    attempts = greatest(attempts - 1, 0),
    next_attempt_at = now() + make_interval(secs => v_delay),
    grading_started_at = null, grading_token = null
  where id = p_submission_id;

  return jsonb_build_object('status', 'ok', 'saved', true, 'delay_seconds', v_delay);
end;
$$;

-- The judge answered, so the next rate limit starts again from the shortest delay.
create or replace function clear_judge_backoff() returns jsonb
language sql
security invoker
set search_path = public, pg_temp
as $$
  update judge_settings set deferrals = 0, backoff_until = null
  where id and (deferrals > 0 or backoff_until is not null);
  select jsonb_build_object('status', 'ok');
$$;

-- The switch and the queue, for organisers only.
create or replace function get_judging() returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'enabled', coalesce(s.enabled, true),
    'paused_until', case when s.backoff_until > now() then s.backoff_until end,
    'updated_at', s.updated_at,
    'waiting', (
      select count(*) from submissions
      where status in ('pending', 'grading') or (status = 'error' and next_attempt_at is not null)
    ),
    'needs_organiser', (
      select count(*) from submissions where status = 'error' and next_attempt_at is null
    )
  )
  from (select true) one
  left join judge_settings s on s.id
$$;

create or replace function set_judging(p_admin_id uuid, p_enabled boolean) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_before boolean;
begin
  if p_enabled is null then
    return jsonb_build_object('status', 'invalid_page');
  end if;

  insert into judge_settings (id) values (true) on conflict (id) do nothing;
  select enabled into v_before from judge_settings where id for update;
  if v_before is distinct from p_enabled then
    update judge_settings set enabled = p_enabled, updated_at = now() where id;
    perform write_audit(
      p_admin_id, 'set_judging', 'setting', 'judging',
      jsonb_build_object('enabled', v_before), jsonb_build_object('enabled', p_enabled)
    );
  end if;
  return get_judging();
end;
$$;

-- Sends a submission back to the queue. Its points stay as they are until the new
-- verdict, so the leaderboard doesn't jump while it's being graded.
create or replace function regrade_submission(p_admin_id uuid, p_submission_id uuid) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_submission submissions%rowtype;
begin
  select * into v_submission from submissions where id = p_submission_id for update;
  if not found then
    return jsonb_build_object('status', 'submission_not_found');
  end if;
  if v_submission.status in ('pending', 'grading') then
    return jsonb_build_object('status', 'submission_in_flight');
  end if;

  update submissions
  set status = 'pending', reason = null, graded_at = null, attempts = 0, next_attempt_at = null,
    grading_started_at = null, grading_token = null, grading_trigger = 'admin_regrade'
  where id = p_submission_id;

  perform write_audit(
    p_admin_id, 'regrade_submission', 'submission', p_submission_id::text,
    jsonb_build_object('status', v_submission.status), jsonb_build_object('status', 'pending')
  );

  return jsonb_build_object(
    'status', 'ok',
    'submission', (select to_jsonb(a) from admin_submissions a where a.id = p_submission_id::text)
  );
end;
$$;

revoke all on function team_submission_json(submissions) from public, anon, authenticated;
revoke all on function submit_conversation(uuid, uuid) from public, anon, authenticated;
revoke all on function get_judge_bot_config(uuid) from public, anon, authenticated;
revoke all on function claim_submission(text, integer, integer) from public, anon, authenticated;
revoke all on function finish_submission(uuid, uuid, text, boolean, text, integer, integer) from public, anon, authenticated;
revoke all on function record_submission_error(uuid, uuid, text, text, integer, integer, integer) from public, anon, authenticated;
revoke all on function regrade_submission(uuid, uuid) from public, anon, authenticated;
revoke all on function defer_submission(uuid, uuid, text, text, integer, integer, integer) from public, anon, authenticated;
revoke all on function clear_judge_backoff() from public, anon, authenticated;
revoke all on function get_judging() from public, anon, authenticated;
revoke all on function set_judging(uuid, boolean) from public, anon, authenticated;

grant execute on function team_submission_json(submissions) to service_role;
grant execute on function submit_conversation(uuid, uuid) to service_role;
grant execute on function get_judge_bot_config(uuid) to service_role;
grant execute on function claim_submission(text, integer, integer) to service_role;
grant execute on function finish_submission(uuid, uuid, text, boolean, text, integer, integer) to service_role;
grant execute on function record_submission_error(uuid, uuid, text, text, integer, integer, integer) to service_role;
grant execute on function regrade_submission(uuid, uuid) to service_role;
grant execute on function defer_submission(uuid, uuid, text, text, integer, integer, integer) to service_role;
grant execute on function clear_judge_backoff() to service_role;
grant execute on function get_judging() to service_role;
grant execute on function set_judging(uuid, boolean) to service_role;

commit;
