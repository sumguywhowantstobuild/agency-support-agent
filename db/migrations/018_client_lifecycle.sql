-- 018: clients are run from the admin app. A client can be paused (the AI is off for them, with a reason
-- staff pick) and removed (their key deleted, their row kept: tickets point at it and its history stays). HighLevel
-- keys move from .env into client_keys, locked by the admin app with the public half of a key pair: no application
-- login can read the table (the database admin and the backup login read everything, but see only locked keys), and
-- only the agent, which holds the private half, can open what sealed_key_for_location()
-- hands it. Every change goes through an admin_* function with an active staff member as "who" and is logged in
-- client_changes; limit changes keep their own log (limit_changes, 009) and admin_history shows both.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 018_client_lifecycle.sql

insert into schema_migrations (version) values ('018');  -- a second run fails here instead of half-applying

alter table client_map
  add column status text not null default 'active' check (status in ('active', 'paused', 'removed')),
  add column pause_reason text check (pause_reason in ('key_broken', 'drafts_wrong', 'account_changing',
                                                       'privacy_hold', 'client_asked', 'billing_hold')),
  add column pause_note text check (length(pause_note) <= 200),
  add constraint client_map_pause_reason_when_paused check ((status = 'paused') = (pause_reason is not null));

-- One locked key per client. Nobody is granted anything here: only the functions below (admin rights) touch it.
create table client_keys (
  client_key text primary key references client_map (client_key) on delete cascade,
  sealed bytea not null check (length(sealed) between 48 and 4096),  -- a sealed box is the key plus 48 bytes
  set_at timestamptz not null default now()
);

-- Every change to a client except limits: who, when, what, and the pause reason and note. Kept for good; no link to
-- client_map, like limit_changes, so it outlives anything.
create table client_changes (
  id bigint generated always as identity primary key,
  changed_by bigint not null references staff,
  changed_at timestamptz not null default now(),
  client_key text not null,
  action text not null check (action in ('added', 'paused', 'resumed', 'removed', 'key_replaced')),
  reason text,
  note text
);

comment on table client_keys is
  'Each client''s HighLevel key, locked: only the agent can open it. No application login can read this table.';
comment on table client_changes is
  'Every change to a client from the admin app (added, paused, resumed, removed, key replaced): who and when. '
  'Limit changes are in limit_changes.';

-- The Clients screen: no key, only when it was set.
create view admin_clients as
  select c.client_key, c.display_name, c.channel_id, c.location_id, c.drafts_per_hour,
         (select count(*)::integer from draft_claims d
          where d.client_key = c.client_key and d.allowed and d.created_at > now() - interval '60 minutes')
           as used_this_hour,
         c.status, c.pause_reason, c.pause_note, k.set_at as key_set_at
  from client_map c
  left join client_keys k on k.client_key = c.client_key;
comment on view admin_clients is 'The clients as the admin app shows them, with this hour''s use. No keys.';

-- Everything the admin app shows as history, newest first. Limit changes made from Slack name the Slack user; one
-- not on the staff list shows as its ID.
create view admin_history as
  select c.changed_at, s.name as staff_name, 'client'::text as kind,
         coalesce(m.display_name, c.client_key) as subject, c.action, concat_ws(': ', c.reason, c.note) as detail
  from client_changes c
  join staff s on s.id = c.changed_by
  left join client_map m on m.client_key = c.client_key
  union all
  select l.changed_at, coalesce(s.name, l.changed_by), 'limit', coalesce(m.display_name, l.client_key),
         'limit_changed', l.old_limit || ' to ' || l.new_limit
  from limit_changes l
  left join staff s on s.slack_user_id = l.changed_by
  left join client_map m on m.client_key = l.client_key
  union all
  select sc.changed_at, s.name, 'staff', t.name, sc.action, null
  from staff_changes sc
  join staff s on s.id = sc.changed_by
  join staff t on t.id = sc.staff_id
  order by 1 desc;
comment on view admin_history is 'Every change made in the admin app or with the Slack limit button, newest first.';

-- A short, unique client key made from the name: lower case, other characters become dashes, a number added if
-- taken. A name with no plain letters or digits gives "client".
create function admin_new_client_key(p_name text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_base text := left(btrim(regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g'), '-'), 56);
  v_key text;
  v_n integer := 1;
begin
  v_base := btrim(v_base, '-');
  if v_base = '' then
    v_base := 'client';
  end if;
  v_key := v_base;
  while exists (select 1 from client_map c where c.client_key = v_key) loop
    v_n := v_n + 1;
    v_key := v_base || '-' || v_n;
  end loop;
  return v_key;
end;
$$;

-- Add a client, or bring back a removed one: a removed client whose channel and location both match comes back
-- active with the new name, limit and key, under its old client key (its history and past numbers stay together).
create function admin_add_client(p_by bigint, p_channel_id text, p_name text, p_location_id text, p_limit integer,
                                 p_sealed bytea)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_channel text := btrim(p_channel_id);
  v_location text := btrim(p_location_id);
  v_name text := btrim(p_name);
  v_key text;
  v_status text;
begin
  perform admin_actor(p_by);
  if coalesce(v_channel, '') !~ '^[CG][A-Z0-9]{2,}$' then
    raise exception 'admin_add_client: the channel must be a Slack channel ID';
  end if;
  if coalesce(v_location, '') !~ '^[A-Za-z0-9_-]{1,64}$' then
    raise exception 'admin_add_client: the HighLevel location id is letters and digits only';
  end if;
  if coalesce(v_name, '') = '' or length(v_name) > 80 then
    raise exception 'admin_add_client: the name must be 1 to 80 characters';
  end if;
  if p_limit is null or p_limit not between 1 and 200 then
    raise exception 'admin_add_client: the limit must be a whole number from 1 to 200';
  end if;
  if p_sealed is null then
    raise exception 'admin_add_client: the locked key is missing';
  end if;

  select c.client_key, c.status into v_key, v_status
  from client_map c where c.channel_id = v_channel or c.location_id = v_location
  for update;
  if found then
    if v_status <> 'removed' or (select count(*) from client_map c
                                 where c.channel_id = v_channel or c.location_id = v_location) > 1
       or not exists (select 1 from client_map c
                      where c.client_key = v_key and c.channel_id = v_channel and c.location_id = v_location) then
      raise exception 'admin_add_client: that channel or HighLevel location already belongs to a client';
    end if;
    update client_map c set display_name = v_name, drafts_per_hour = p_limit, status = 'active',
                            pause_reason = null, pause_note = null
    where c.client_key = v_key;
  else
    v_key := admin_new_client_key(v_name);
    insert into client_map (channel_id, client_key, display_name, location_id, drafts_per_hour)
    values (v_channel, v_key, v_name, v_location, p_limit);
  end if;

  insert into client_keys (client_key, sealed) values (v_key, p_sealed);
  insert into client_changes (changed_by, client_key, action) values (p_by, v_key, 'added');
  return v_key;
end;
$$;

-- The client a function changes, locked for the rest of the transaction; refused when unknown or removed.
create function admin_client_status(p_client_key text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_status text;
begin
  select c.status into v_status from client_map c where c.client_key = p_client_key for no key update;
  if not found or v_status = 'removed' then
    raise exception 'admin: unknown or removed client';
  end if;
  return v_status;
end;
$$;

create function admin_pause_client(p_by bigint, p_client_key text, p_reason text, p_note text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_note text := nullif(btrim(p_note), '');
begin
  perform admin_actor(p_by);
  if admin_client_status(p_client_key) = 'paused' then
    raise exception 'admin_pause_client: the client is already paused';
  end if;
  if p_reason is null then
    raise exception 'admin_pause_client: pick a reason';
  end if;
  update client_map c set status = 'paused', pause_reason = p_reason, pause_note = v_note
  where c.client_key = p_client_key;
  insert into client_changes (changed_by, client_key, action, reason, note)
  values (p_by, p_client_key, 'paused', p_reason, v_note);
end;
$$;

create function admin_resume_client(p_by bigint, p_client_key text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform admin_actor(p_by);
  if admin_client_status(p_client_key) <> 'paused' then
    raise exception 'admin_resume_client: the client is not paused';
  end if;
  update client_map c set status = 'active', pause_reason = null, pause_note = null
  where c.client_key = p_client_key;
  insert into client_changes (changed_by, client_key, action) values (p_by, p_client_key, 'resumed');
end;
$$;

create function admin_replace_client_key(p_by bigint, p_client_key text, p_sealed bytea)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform admin_actor(p_by);
  perform admin_client_status(p_client_key);
  if p_sealed is null then
    raise exception 'admin_replace_client_key: the locked key is missing';
  end if;
  insert into client_keys (client_key, sealed) values (p_client_key, p_sealed)
  on conflict (client_key) do update set sealed = excluded.sealed, set_at = now();
  insert into client_changes (changed_by, client_key, action) values (p_by, p_client_key, 'key_replaced');
end;
$$;

create function admin_remove_client(p_by bigint, p_client_key text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform admin_actor(p_by);
  perform admin_client_status(p_client_key);
  update client_map c set status = 'removed', pause_reason = null, pause_note = null
  where c.client_key = p_client_key;
  delete from client_keys k where k.client_key = p_client_key;
  insert into client_changes (changed_by, client_key, action) values (p_by, p_client_key, 'removed');
end;
$$;

-- The app changes a limit through the same function as the Slack button, logged with the member's Slack user ID.
create function admin_set_draft_limit(p_by bigint, p_client_key text, p_new_limit integer)
returns table (client_name text, limit_before integer, limit_after integer)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform admin_actor(p_by);
  perform admin_client_status(p_client_key);
  return query
    select * from set_draft_limit(p_client_key, p_new_limit, (select s.slack_user_id from staff s where s.id = p_by));
end;
$$;

-- For the agent: the locked key of an active client, or nothing (unknown, paused or removed).
create function sealed_key_for_location(p_location_id text)
returns bytea
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select k.sealed from client_map c join client_keys k on k.client_key = c.client_key
  where c.location_id = p_location_id and c.status = 'active';
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function admin_new_client_key(text) from public;
revoke execute on function admin_add_client(bigint, text, text, text, integer, bytea) from public;
revoke execute on function admin_client_status(text) from public;
revoke execute on function admin_pause_client(bigint, text, text, text) from public;
revoke execute on function admin_resume_client(bigint, text) from public;
revoke execute on function admin_replace_client_key(bigint, text, bytea) from public;
revoke execute on function admin_remove_client(bigint, text) from public;
revoke execute on function admin_set_draft_limit(bigint, text, integer) from public;
revoke execute on function sealed_key_for_location(text) from public;

grant select on admin_clients, admin_history to admin_app;
grant execute on function admin_add_client(bigint, text, text, text, integer, bytea) to admin_app;
grant execute on function admin_pause_client(bigint, text, text, text) to admin_app;
grant execute on function admin_resume_client(bigint, text) to admin_app;
grant execute on function admin_replace_client_key(bigint, text, bytea) to admin_app;
grant execute on function admin_remove_client(bigint, text) to admin_app;
grant execute on function admin_set_draft_limit(bigint, text, integer) to admin_app;

grant execute on function sealed_key_for_location(text) to agent_reader;
