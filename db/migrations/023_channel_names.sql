-- 023: the admin app shows a client's Slack channel by name (#brightsmile-support), not by its ID.
-- client_map gets the name as text, filled in by the admin app from Slack: when Add client saves a client,
-- and once an hour by a timer in the admin app. Pages only read it. admin_clients adds it as its last column.
-- One checked function sets the names: admin_note_channel_names(p_names jsonb), granted to admin_app only. It touches
-- the name of an existing client's channel and nothing else. It is not a client change, so it is not in History
-- (the one admin write that is not logged: it changes only a cached display name).
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 023_channel_names.sql

insert into schema_migrations (version) values ('023');  -- a second run fails here instead of half-applying

-- Nullable: a client has no name until the app has read one from Slack, and shows its ID until then. A Slack name is
-- at most 80 characters; the function also refuses control characters.
alter table client_map
  add column channel_name text check (length(channel_name) between 1 and 80);

-- The Clients screen, as 018 made it, with the name as the last column (create or replace can only add at the end).
create or replace view admin_clients as
  select c.client_key, c.display_name, c.channel_id, c.location_id, c.drafts_per_hour,
         (select count(*)::integer from draft_claims d
          where d.client_key = c.client_key and d.allowed and d.created_at > now() - interval '60 minutes')
           as used_this_hour,
         c.status, c.pause_reason, c.pause_note, k.set_at as key_set_at, c.channel_name
  from client_map c
  left join client_keys k on k.client_key = c.client_key;

-- Sets the name for each channel ID that is a client's (any status); an ID that is no client's is ignored. The input
-- must be a JSON object of channel ID to name, or the whole call is refused. Per entry, a value that is not a string,
-- is empty, is over 80 characters or holds a control character is skipped and the rest are noted. Only a name that
-- differs is written (is distinct from), so an hourly run locks no unchanged row. Returns how many rows changed.
-- Not marked stable: in a read-only transaction the update is refused by Postgres itself.
create function admin_note_channel_names(p_names jsonb)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_changed integer;
begin
  if p_names is null or jsonb_typeof(p_names) <> 'object' then
    raise exception 'admin_note_channel_names: the names must be a JSON object of channel ID to name';
  end if;
  update client_map c set channel_name = n.name
  from (select e.key as channel_id, e.value #>> '{}' as name
        from jsonb_each(p_names) e
        where jsonb_typeof(e.value) = 'string') n
  where c.channel_id = n.channel_id
    and length(n.name) between 1 and 80
    and n.name !~ '[\u0001-\u001f\u007f-\u009f]'
    and c.channel_name is distinct from n.name;
  get diagnostics v_changed = row_count;
  return v_changed;
end;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function admin_note_channel_names(jsonb) from public;
grant execute on function admin_note_channel_names(jsonb) to admin_app;
