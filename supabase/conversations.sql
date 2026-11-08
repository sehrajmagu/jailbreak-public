-- Saving chat turns, reading conversations back, and the admin conversation
-- log. Run after schema.sql. Safe to re-run.
--
-- A turn is saved in two steps around the model call: begin_turn saves the
-- team's message, and finish_turn saves the reply with its tool calls and
-- token charge. Every function trusts the team ID it's given, so only the
-- backend may call them (see the grants at the end).

begin;

-- ---------- Message length ----------
-- Team messages keep the 8,000-character limit. Replies get more room, so a
-- long reply is saved exactly as the team saw it instead of failing to save.

alter table messages drop constraint if exists messages_content_check;
alter table messages drop constraint if exists messages_content_length;
alter table messages add constraint messages_content_length
  check (char_length(content) <= case role when 'user' then 8000 else 32000 end);

-- ---------- Shared ----------

-- One message in the shape the API returns. Tokens are the turn's input plus
-- output on a reply, and 0 on a team message, which stores none.
create or replace function message_json(m messages) returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'id', m.id,
    'role', m.role,
    'content', m.content,
    'tokens', coalesce(m.input_tokens, 0) + coalesce(m.output_tokens, 0),
    'created_at', m.created_at,
    'tool_calls', coalesce((
      select jsonb_agg(
        jsonb_build_object('id', t.id, 'name', t.tool_name, 'arguments', t.arguments, 'result', t.result)
        order by t.id
      )
      from tool_calls t
      where t.message_id = m.id
    ), '[]'::jsonb)
  )
$$;

-- Whether a team sees a message. Failed turns are hidden, and so is a team message
-- left with no reply for 10 minutes, because its server died mid-turn. begin_turn
-- marks that message failed if the team continues the conversation.
create or replace function team_sees_message(m messages) returns boolean
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select m.status = 'complete' and not (
    m.role = 'user'
    and m.created_at <= now() - interval '10 minutes'
    and not exists (
      select 1 from messages later
      where later.conversation_id = m.conversation_id and later.seq > m.seq
    )
  )
$$;

-- ---------- Saving a turn ----------

-- Saves the team's message, creating the conversation when p_conversation_id
-- is null. Returns the new message, the ID reserved for the reply, and the
-- history to send to the model: complete messages only, ending with this one.
-- start_turn in chat.sql checks rate limits and level locks before
-- calling this, since a refusal there should leave nothing behind.
create or replace function begin_turn(
  p_team_id uuid,
  p_bot_id uuid,
  p_conversation_id uuid,
  p_content text
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  -- Plain btrim removes only spaces, so name the other whitespace too.
  v_content text := btrim(p_content, E' \t\n\r\f\v');
  v_disabled boolean;
  v_conversation conversations%rowtype;
  v_last messages%rowtype;
  v_message messages%rowtype;
begin
  select disabled into v_disabled from teams where id = p_team_id;
  if not found or v_disabled then
    return jsonb_build_object('status', 'team_inactive');
  end if;

  if not exists (
    select 1 from bots b
    join levels l on l.level_number = b.level
    where b.id = p_bot_id and b.enabled and l.enabled
  ) then
    return jsonb_build_object('status', 'bot_not_found');
  end if;

  if v_content is null or v_content = '' or char_length(v_content) > 8000 then
    return jsonb_build_object('status', 'invalid_message');
  end if;

  if p_conversation_id is null then
    insert into conversations (team_id, bot_id)
    values (p_team_id, p_bot_id)
    returning * into v_conversation;
  else
    -- Holds the conversation until commit, so the team's turns in it run one
    -- at a time and get consecutive seq numbers.
    select * into v_conversation from conversations
    where id = p_conversation_id and team_id = p_team_id and bot_id = p_bot_id
    for update;
    if not found then
      return jsonb_build_object('status', 'conversation_not_found');
    end if;

    if v_conversation.status <> 'active'
      or exists (select 1 from submissions where conversation_id = v_conversation.id) then
      return jsonb_build_object('status', 'conversation_closed');
    end if;

    select * into v_last from messages
    where conversation_id = v_conversation.id
    order by seq desc
    limit 1;

    -- A team message with no reply after it is a turn still streaming. After
    -- 10 minutes it can only be a turn whose server died, so it's marked
    -- failed rather than blocking the conversation for good.
    if found and v_last.role = 'user' and v_last.status = 'complete' then
      if v_last.created_at > now() - interval '10 minutes' then
        return jsonb_build_object('status', 'turn_in_progress');
      end if;
      update messages set status = 'failed' where id = v_last.id;
    end if;
  end if;

  insert into messages (conversation_id, team_id, seq, role, content)
  values (v_conversation.id, p_team_id, coalesce(v_last.seq, 0) + 1, 'user', v_content)
  returning * into v_message;

  return jsonb_build_object(
    'status', 'ok',
    'conversation_id', v_conversation.id,
    'user_message', message_json(v_message),
    -- Reserved now so the stream can name the reply before it's saved.
    'assistant_message_id', nextval(pg_get_serial_sequence('messages', 'id')),
    'history', (
      select jsonb_agg(message_json(m) order by m.seq)
      from messages m
      where m.conversation_id = v_conversation.id and m.status = 'complete'
    )
  );
end;
$$;

-- Saves the reply to the turn begin_turn started, under the ID it reserved.
-- A complete reply charges the turn's tokens to the bot's model. A failed one
-- is kept for the organisers with its token usage, isn't charged, and takes the
-- team's message with it, so the whole turn is hidden from the team and from
-- the model's history.
-- p_tool_calls is an array of { name, arguments, result }, with JSON values.
create or replace function finish_turn(
  p_team_id uuid,
  p_conversation_id uuid,
  p_user_message_id bigint,
  p_assistant_message_id bigint,
  p_content text,
  p_status message_status,
  p_input_tokens integer,
  p_output_tokens integer,
  p_tool_calls jsonb default '[]'
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_conversation conversations%rowtype;
  v_last messages%rowtype;
  v_reply messages%rowtype;
  v_tokens bigint := p_input_tokens::bigint + p_output_tokens;
begin
  if p_content is null or char_length(p_content) > 32000
    or p_status is null
    or p_input_tokens is null or p_input_tokens < 0
    or p_output_tokens is null or p_output_tokens < 0
    or p_assistant_message_id is null
    or jsonb_typeof(p_tool_calls) is distinct from 'array'
    or exists (
      select 1 from jsonb_array_elements(p_tool_calls) c
      where jsonb_typeof(c) <> 'object'
        or jsonb_typeof(c -> 'name') is distinct from 'string'
        or c ->> 'name' = ''
    ) then
    return jsonb_build_object('status', 'invalid_reply');
  end if;

  select * into v_conversation from conversations
  where id = p_conversation_id and team_id = p_team_id
  for update;
  if not found then
    return jsonb_build_object('status', 'conversation_not_found');
  end if;

  -- Only the open turn can be finished, and only once, so a reply is never
  -- saved or charged twice.
  select * into v_last from messages
  where conversation_id = v_conversation.id
  order by seq desc
  limit 1;
  if not found or v_last.id <> p_user_message_id or v_last.role <> 'user' or v_last.status <> 'complete' then
    return jsonb_build_object('status', 'turn_not_open');
  end if;

  insert into messages (
    id, conversation_id, team_id, seq, role, content, status, input_tokens, output_tokens
  )
  overriding system value
  values (
    p_assistant_message_id, v_conversation.id, p_team_id, v_last.seq + 1, 'assistant',
    p_content, p_status, p_input_tokens, p_output_tokens
  )
  returning * into v_reply;

  insert into tool_calls (message_id, conversation_id, team_id, tool_name, arguments, result)
  select
    v_reply.id, v_conversation.id, p_team_id, c ->> 'name',
    coalesce(nullif(c -> 'arguments', 'null'::jsonb), '{}'::jsonb),
    nullif(c -> 'result', 'null'::jsonb)
  from jsonb_array_elements(p_tool_calls) with ordinality as t (c, n)
  order by n;

  if p_status = 'failed' then
    update messages set status = 'failed' where id = v_last.id;
  else
    -- The title is set by the first complete turn, so a failed first turn
    -- leaves nothing for the team to see.
    update conversations set
      updated_at = now(),
      title = coalesce(
        title,
        case when char_length(v_last.content) > 60 then left(v_last.content, 57) || '...' else v_last.content end
      )
    where id = v_conversation.id;

    -- Attribute recorded token usage to the bot's current model.
    if v_tokens > 0 then
      insert into credit_ledger (team_id, model_id, tokens, message_id)
      select p_team_id, b.model_id, v_tokens, v_reply.id
      from bots b
      where b.id = v_conversation.bot_id;
    end if;
  end if;

  return jsonb_build_object('status', 'ok', 'message', message_json(v_reply));
end;
$$;

-- ---------- Reading ----------

-- The team's conversations with one bot, newest first. Conversations with no
-- message the team can see are left out, since to the team nothing was saved.
create or replace function get_team_conversations(p_team_id uuid, p_bot_id uuid) returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'status', 'ok',
    'conversations', coalesce(jsonb_agg(
      jsonb_build_object(
        'id', c.id,
        'bot_id', c.bot_id,
        'title', coalesce(c.title, ''),
        'message_count', n.count,
        'created_at', c.created_at,
        'updated_at', c.updated_at,
        'submission_status', s.status
      )
      order by c.created_at desc, c.id
    ), '[]'::jsonb)
  )
  from conversations c
  cross join lateral (
    select count(*) as count from messages m
    where m.conversation_id = c.id and team_sees_message(m)
  ) n
  left join submissions s on s.conversation_id = c.id
  where c.team_id = p_team_id and c.bot_id = p_bot_id and n.count > 0
$$;

-- One of the team's conversations with the messages it can see and its submission.
-- Another team's conversation, or one with nothing to see, is not found.
create or replace function get_team_conversation(p_team_id uuid, p_conversation_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_conversation conversations%rowtype;
  v_messages jsonb;
  v_submission jsonb;
begin
  select * into v_conversation from conversations
  where id = p_conversation_id and team_id = p_team_id;
  if not found then
    return jsonb_build_object('status', 'conversation_not_found');
  end if;

  select jsonb_agg(message_json(m) order by m.seq) into v_messages
  from messages m
  where m.conversation_id = v_conversation.id and team_sees_message(m);
  if v_messages is null then
    return jsonb_build_object('status', 'conversation_not_found');
  end if;

  select jsonb_build_object(
    'id', s.id,
    'conversation_id', s.conversation_id,
    'bot_id', s.bot_id,
    'level', b.level,
    'bot_label', b.label,
    'check_type', b.check_type,
    'status', s.status,
    -- An ungraded submission's reason may hold error text, so teams don't see it.
    'reason', case when s.graded_at is not null then s.reason end,
    'points_awarded', s.points_awarded,
    'created_at', s.created_at,
    'graded_at', s.graded_at
  ) into v_submission
  from submissions s
  join bots b on b.id = s.bot_id
  where s.conversation_id = v_conversation.id;

  return jsonb_build_object(
    'status', 'ok',
    'conversation', jsonb_build_object(
      'id', v_conversation.id,
      'bot_id', v_conversation.bot_id,
      'title', coalesce(v_conversation.title, ''),
      'message_count', jsonb_array_length(v_messages),
      'created_at', v_conversation.created_at,
      'updated_at', v_conversation.updated_at,
      'submission_status', v_submission -> 'status',
      'messages', v_messages,
      'submission', v_submission
    )
  );
end;
$$;

-- ---------- Admin ----------

-- One row per conversation, as the admin log lists it. Unlike the team view,
-- failed turns count: message_count includes them, and tokens is everything
-- spent on the conversation, charged or not.
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

-- A message as the admin log shows it, with its status.
create or replace function admin_message_json(m messages) returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select message_json(m) || jsonb_build_object('status', m.status)
$$;

-- One page of the admin log, newest first. A null filter matches everything.
create or replace function get_admin_conversations(
  p_team_id uuid,
  p_bot_id uuid,
  p_model text,
  p_result text,
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
      select * from admin_conversations a
      where (p_team_id is null or a.team_id = p_team_id)
        and (p_bot_id is null or a.bot_id = p_bot_id)
        and (p_model is null or a.model = p_model)
        and (p_result is null or a.result = p_result)
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

-- One conversation with every message, failed ones included, and its verdict.
create or replace function get_admin_conversation(p_conversation_id uuid) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_conversation jsonb;
  v_messages jsonb;
  v_verdict jsonb;
begin
  select to_jsonb(a) into v_conversation from admin_conversations a where a.id = p_conversation_id;
  if v_conversation is null then
    return jsonb_build_object('status', 'conversation_not_found');
  end if;

  select coalesce(jsonb_agg(admin_message_json(m) order by m.seq), '[]'::jsonb) into v_messages
  from messages m
  where m.conversation_id = p_conversation_id;

  -- Organisers see the reason even before grading ends, since it may explain a
  -- grading error.
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
    'graded_at', s.graded_at,
    'team_id', s.team_id,
    'team_name', t.name,
    'model', mo.display_name
  ) into v_verdict
  from submissions s
  join bots b on b.id = s.bot_id
  join teams t on t.id = s.team_id
  join models mo on mo.id = b.model_id
  where s.conversation_id = p_conversation_id;

  return jsonb_build_object(
    'status', 'ok',
    'conversation', v_conversation || jsonb_build_object('messages', v_messages, 'verdict', v_verdict)
  );
end;
$$;

-- ---------- Access ----------
-- Supabase exposes public functions through its API, and these trust the team
-- ID they're given, so only the backend may call them.

revoke all on function message_json(messages) from public, anon, authenticated;
revoke all on function team_sees_message(messages) from public, anon, authenticated;
revoke all on function begin_turn(uuid, uuid, uuid, text) from public, anon, authenticated;
revoke all on function finish_turn(uuid, uuid, bigint, bigint, text, message_status, integer, integer, jsonb)
  from public, anon, authenticated;
revoke all on function get_team_conversations(uuid, uuid) from public, anon, authenticated;
revoke all on function get_team_conversation(uuid, uuid) from public, anon, authenticated;
revoke all on function admin_message_json(messages) from public, anon, authenticated;
revoke all on function get_admin_conversations(uuid, uuid, text, text, integer, integer)
  from public, anon, authenticated;
revoke all on function get_admin_conversation(uuid) from public, anon, authenticated;

grant execute on function message_json(messages) to service_role;
grant execute on function team_sees_message(messages) to service_role;
grant execute on function begin_turn(uuid, uuid, uuid, text) to service_role;
grant execute on function finish_turn(uuid, uuid, bigint, bigint, text, message_status, integer, integer, jsonb)
  to service_role;
grant execute on function get_team_conversations(uuid, uuid) to service_role;
grant execute on function get_team_conversation(uuid, uuid) to service_role;
grant execute on function admin_message_json(messages) to service_role;
grant execute on function get_admin_conversations(uuid, uuid, text, text, integer, integer) to service_role;
grant execute on function get_admin_conversation(uuid) to service_role;

commit;
