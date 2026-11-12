-- 009: change a client's hourly limit from Slack. Staff click "Change limit" on the limit alert and type
-- the new number; n8n checks the click is genuine and from the team, then calls set_draft_limit(). That function is
-- the only way n8n_support can change a limit: it accepts only a known client and 1 to 200, and logs every change.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 009_limit_changes.sql

insert into schema_migrations (version) values ('009');  -- a second run fails here instead of half-applying

-- Every change: which client, old and new limit, who (Slack user ID) and when. Kept for good (a few rows a year).
-- No link to client_map, so the history stays if a client leaves.
create table limit_changes (
  id bigint generated always as identity primary key,
  client_key text not null,
  old_limit integer not null,
  new_limit integer not null,
  changed_by text not null,
  changed_at timestamptz not null default now()
);

-- 200 an hour is the most one client can be given from Slack: with the 8-at-once limit the agent can't do much more
-- than that anyway, and it keeps one client's worst hour bounded. A higher number needs the admin.
create function set_draft_limit(p_client_key text, p_new_limit integer, p_changed_by text)
returns table (client_name text, limit_before integer, limit_after integer)
language plpgsql
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
declare
  v_name text;
  v_old integer;
begin
  if p_new_limit is null or p_new_limit not between 1 and 200 then
    raise exception 'set_draft_limit: the limit must be a whole number from 1 to 200';
  end if;
  if coalesce(p_changed_by, '') !~ '^[UW][A-Z0-9]{2,}$' then
    raise exception 'set_draft_limit: changed_by must be a Slack user ID';
  end if;

  -- Same lock as claim_draft_slot(), so a change and a claim for this client don't cross.
  select c.display_name, c.drafts_per_hour into v_name, v_old
  from client_map c where c.client_key = p_client_key
  for no key update;
  if not found then
    raise exception 'set_draft_limit: unknown client';
  end if;

  if p_new_limit <> v_old then
    update client_map c set drafts_per_hour = p_new_limit where c.client_key = p_client_key;
    insert into limit_changes (client_key, old_limit, new_limit, changed_by)
    values (p_client_key, v_old, p_new_limit, p_changed_by);
  end if;

  client_name := v_name;
  limit_before := v_old;
  limit_after := p_new_limit;
  return next;
end;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function set_draft_limit(text, integer, text) from public;
grant execute on function set_draft_limit(text, integer, text) to n8n_support;
