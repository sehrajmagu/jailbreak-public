-- Limits on password guesses, and the organiser actions for team accounts. Run
-- after admin.sql, because these write to its audit log. Safe to re-run.
--
-- Every function trusts the IDs it's given, so only the backend may call them
-- (see the grants at the end).

begin;

-- ---------- Password guesses ----------
-- One row per sign-in attempt that hasn't succeeded. Counted per account and per
-- client, so a team can't lock a rival out by guessing its password on purpose.

create table if not exists login_attempts (
  id bigint generated always as identity primary key,
  account text not null,
  client text not null,
  created_at timestamptz not null default now()
);

create index if not exists login_attempts_account_client_idx on login_attempts (account, client, created_at);
create index if not exists login_attempts_created_idx on login_attempts (created_at);

alter table login_attempts enable row level security;
revoke all on login_attempts from anon, authenticated;

-- Counts this attempt before the password is checked, so parallel guesses can't
-- pass together. Call release_login_attempt unless the password was wrong.
create or replace function begin_login_attempt(
  p_account text,
  p_client text,
  p_max_failures integer,
  p_window_seconds integer
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_window interval := make_interval(secs => p_window_seconds);
  v_recent integer;
  v_oldest timestamptz;
  v_attempt_id bigint;
begin
  -- Runs one account and client's attempts one at a time, until this commits.
  perform pg_advisory_xact_lock(hashtextextended(p_account || '|' || p_client, 0));

  -- Guesses at names that don't exist would otherwise stay here for good.
  delete from login_attempts where created_at < now() - interval '1 day';

  select count(*), min(created_at) into v_recent, v_oldest
  from login_attempts
  where account = p_account and client = p_client and created_at > now() - v_window;
  if v_recent >= p_max_failures then
    return jsonb_build_object(
      'status', 'too_many_logins',
      'retry_after_seconds', greatest(ceil(extract(epoch from v_oldest + v_window - now())), 1)
    );
  end if;

  insert into login_attempts (account, client) values (p_account, p_client)
  returning id into v_attempt_id;
  return jsonb_build_object('status', 'ok', 'attempt_id', v_attempt_id);
end;
$$;

-- A successful sign-in clears that client's count for the account. Otherwise only
-- this attempt is removed, because the check itself failed and it wasn't a guess.
create or replace function release_login_attempt(p_attempt_id bigint, p_signed_in boolean) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_attempt login_attempts%rowtype;
begin
  delete from login_attempts where id = p_attempt_id returning * into v_attempt;
  if found and p_signed_in then
    delete from login_attempts where account = v_attempt.account and client = v_attempt.client;
  end if;
  return jsonb_build_object('status', 'ok');
end;
$$;

-- ---------- Team accounts ----------

-- Saves the team for an account the backend has just created in Supabase Auth.
-- The password never reaches the database or the audit log.
create or replace function create_team(p_admin_id uuid, p_team_id uuid, p_name text) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  -- Plain btrim removes only spaces, so name the other whitespace too.
  v_name text := btrim(p_name, E' \t\n\r\f\v');
begin
  if v_name is null or char_length(v_name) not between 1 and 60 then
    return jsonb_build_object('status', 'invalid_name');
  end if;

  begin
    insert into teams (id, name) values (p_team_id, v_name);
  exception when unique_violation then
    -- Names are unique ignoring case, including against a create running at the same time.
    return jsonb_build_object('status', 'name_taken');
  end;

  perform write_audit(
    p_admin_id, 'create_team', 'team', p_team_id::text, null, jsonb_build_object('name', v_name, 'disabled', false)
  );
  return jsonb_build_object('status', 'ok', 'team', (select to_jsonb(a) from admin_teams a where a.id = p_team_id));
end;
$$;

-- Records a reset, and clears the team's guesses so an organiser can let it back in.
create or replace function record_password_reset(p_admin_id uuid, p_team_id uuid) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from teams where id = p_team_id) then
    return jsonb_build_object('status', 'team_not_found');
  end if;

  delete from login_attempts where account = 'team:' || p_team_id::text;
  perform write_audit(
    p_admin_id, 'reset_team_password', 'team', p_team_id::text, null, jsonb_build_object('password_reset', true)
  );
  return jsonb_build_object('status', 'ok');
end;
$$;

-- ---------- Access ----------
-- Supabase exposes public functions through its API, and these trust the IDs
-- they're given, so only the backend may call them.

revoke all on function begin_login_attempt(text, text, integer, integer) from public, anon, authenticated;
revoke all on function release_login_attempt(bigint, boolean) from public, anon, authenticated;
revoke all on function create_team(uuid, uuid, text) from public, anon, authenticated;
revoke all on function record_password_reset(uuid, uuid) from public, anon, authenticated;

grant select, insert, delete on login_attempts to service_role;
grant execute on function begin_login_attempt(text, text, integer, integer) to service_role;
grant execute on function release_login_attempt(bigint, boolean) to service_role;
grant execute on function create_team(uuid, uuid, text) to service_role;
grant execute on function record_password_reset(uuid, uuid) to service_role;

commit;

-- Makes Supabase's API notice the new functions straight away.
notify pgrst, 'reload schema';
