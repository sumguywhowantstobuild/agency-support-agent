-- 024: a client can move to another Slack channel (a new workspace, or a re-shared channel), and its past tickets and
-- history stay with it. Three things change:
--  1. A ticket points at its client by client_key, no longer by channel_id. Slack threads don't move: a ticket keeps the
--     channel and thread it was written in (its true Slack address), and still belongs to the client it was written for.
--  2. client_channels records every channel a client has ever had, from the moment it gets one. A channel belongs to
--     one client, ever, so an old thread or card can never meet another client's status or conversation.
--  3. admin_change_channel moves the client: through this function only, by the admin login only, logged in
--     client_changes. admin_add_client (018) is re-created with one extra step: it records the channel the same way.
-- n8n finds the client by channel on every message, so the next message in the new channel is the client's own.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 024_change_channel.sql

insert into schema_migrations (version) values ('024');  -- a second run fails here instead of half-applying

-- Nobody changes the clients while this runs: a client added by the old admin_add_client (018) while the seed reads
-- client_map would otherwise be missed by it, and its channel could later go to another client. Reads go on. (Adding
-- the foreign keys below takes the same lock; this says it first and on purpose.)
lock table client_map in share row exclusive mode;

-- Refuse to run if any ticket names another client than the one its channel belongs to: moving the link from the
-- channel to the client would then change which client the ticket belongs to. (This also covers the seed below: with
-- every ticket matching its channel's client, no channel can appear under two clients, and client_channels' primary
-- key refuses it anyway.)
do $$
begin
  if exists (select 1 from tickets t join client_map c on c.channel_id = t.channel_id where c.client_key <> t.client_key) then
    raise exception '024: a ticket''s client_key does not match the client its channel belongs to; fix that data first';
  end if;
end
$$;

-- The link moves from the channel to the client. Dropping the old one first is what lets a client's channel change
-- while its tickets keep the channel they were written in.
alter table tickets drop constraint tickets_channel_id_fkey;
alter table tickets add constraint tickets_client_key_fkey foreign key (client_key) references client_map (client_key);

-- Every channel a client has had, kept for good (like client_changes). Written only by admin_change_channel and
-- admin_add_client, which are security definer; the admin login may read it to leave out channels another client had.
create table client_channels (
  channel_id text primary key,
  client_key text not null references client_map (client_key)
);
comment on table client_channels is
  'Every Slack channel a client has ever had (the one it has now included): a channel belongs to one client, ever. '
  'Written only by admin_change_channel and admin_add_client; the admin app reads it to leave out channels that '
  'belonged to another client.';

-- Seeded from what is there: each client's channel now, and each ticket's channel (all matching, as checked above).
insert into client_channels (channel_id, client_key)
select channel_id, client_key from client_map
union
select channel_id, client_key from tickets;

-- The admin app reads it, nothing else does: n8n's login and the agent's reader get no grant (the backup reads
-- everything through pg_read_all_data), and nobody writes it except the two functions below.
grant select on client_channels to admin_app;

-- The change log also records a channel change: note is "old label to new label".
alter table client_changes drop constraint client_changes_action_check;
alter table client_changes add constraint client_changes_action_check
  check (action in ('added', 'paused', 'resumed', 'removed', 'key_replaced', 'channel_changed'));
comment on table client_changes is
  'Every change to a client from the admin app (added, paused, resumed, removed, key replaced, channel changed): who '
  'and when. Limit changes are in limit_changes.';

-- Add a client, or bring back a removed one (018, unchanged) and now record its channel in client_channels. A channel
-- that belonged to another client is refused: the row is written if the channel is new, and the refusal is when its
-- owner is someone else (the whole call is undone then, the client row included).
create or replace function admin_add_client(p_by bigint, p_channel_id text, p_name text, p_location_id text,
                                            p_limit integer, p_sealed bytea)
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
  -- New in 024: one claim on a channel at a time with admin_change_channel (the same lock, taken before any row lock or
  -- insert), so an Add and a Move for one channel never wait on each other's rows.
  perform pg_advisory_xact_lock(hashtext('support-agent channel ' || coalesce(v_channel, '')));
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

  -- New in 024: the channel is this client's for good, or refused when another client had it.
  insert into client_channels (channel_id, client_key) values (v_channel, v_key)
  on conflict (channel_id) do nothing;
  if (select cc.client_key from client_channels cc where cc.channel_id = v_channel) <> v_key then
    raise exception 'admin_add_client: that channel belonged to another client';
  end if;

  insert into client_keys (client_key, sealed) values (v_key, p_sealed);
  insert into client_changes (changed_by, client_key, action) values (p_by, v_key, 'added');
  return v_key;
end;
$$;

-- Move an active or paused client to another channel. In order: the actor, the client (unknown or removed is
-- refused), the channel ID's shape, the client's own channel (refused, with a message of its own: someone got there
-- first), a channel any client row holds (removed clients keep their row, and channel_id is its primary key), and a
-- channel that belonged to another client (client_channels). Claims on one channel, by this function and by
-- admin_add_client, take a transaction lock on it first, so they run one after the other: the second finds the
-- channel held (the client_map check) and is refused as such, or, if the first client has moved off it again, as a
-- channel that belonged to another client. client_map's primary key is only a backstop, which the app also reads as
-- taken. The name is Slack's, from the app's own read of Slack, and is kept only if
-- it follows 023's rules (1 to 80 characters, no control character); otherwise the client shows its ID until the
-- hourly refresh. Tickets keep their own channel. The log's note reads "old label to new label": a label is #name, or
-- the ID when there is no name.
create function admin_change_channel(p_by bigint, p_client_key text, p_channel_id text, p_channel_name text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_old_channel text;
  v_old_name text;
  v_name text := case when length(p_channel_name) between 1 and 80 and p_channel_name !~ '[\u0001-\u001f\u007f-\u009f]'
                      then p_channel_name end;
begin
  perform admin_actor(p_by);
  -- One claim on a channel at a time, with admin_add_client (the same lock, before any row lock or insert).
  perform pg_advisory_xact_lock(hashtext('support-agent channel ' || coalesce(p_channel_id, '')));
  perform admin_client_status(p_client_key);
  if coalesce(p_channel_id, '') !~ '^[CG][A-Z0-9]{2,}$' then
    raise exception 'admin_change_channel: the channel must be a Slack channel ID';
  end if;
  select c.channel_id, c.channel_name into v_old_channel, v_old_name from client_map c where c.client_key = p_client_key;
  -- The channel it leaves is recorded as its own too (a no-op when the seed or an Add already did). A client added by
  -- the old admin_add_client while this migration ran has no row yet; without this line its old channel could later go
  -- to another client.
  insert into client_channels (channel_id, client_key) values (v_old_channel, p_client_key)
  on conflict (channel_id) do nothing;
  if p_channel_id = v_old_channel then
    raise exception 'admin_change_channel: the client is already on that channel';
  end if;
  if exists (select 1 from client_map c where c.channel_id = p_channel_id) then
    raise exception 'admin_change_channel: that channel already belongs to a client';
  end if;
  insert into client_channels (channel_id, client_key) values (p_channel_id, p_client_key)
  on conflict (channel_id) do nothing;
  if (select cc.client_key from client_channels cc where cc.channel_id = p_channel_id) <> p_client_key then
    raise exception 'admin_change_channel: that channel belonged to another client';
  end if;
  update client_map c set channel_id = p_channel_id, channel_name = v_name where c.client_key = p_client_key;
  insert into client_changes (changed_by, client_key, action, note)
  values (p_by, p_client_key, 'channel_changed',
          coalesce('#' || v_old_name, v_old_channel) || ' to ' || coalesce('#' || v_name, p_channel_id));
end;
$$;

-- Postgres lets every login run new functions by default; close that. admin_add_client keeps its grant (same
-- signature), repeated here so the file says what it ends with.
revoke execute on function admin_add_client(bigint, text, text, text, integer, bytea) from public;
revoke execute on function admin_change_channel(bigint, text, text, text) from public;
grant execute on function admin_add_client(bigint, text, text, text, integer, bytea) to admin_app;
grant execute on function admin_change_channel(bigint, text, text, text) to admin_app;
