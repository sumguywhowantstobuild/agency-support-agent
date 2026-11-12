-- 012: health monitoring. n8n's "Health check" workflow checks each part every 5 minutes (the agent,
-- the tunnel, OpenAI) and reports the result to monitor_report(), which answers "down" once a part has failed 2 checks
-- in a row (a single blip, like one odd answer from OpenAI, stays silent) and "back" once when it recovers, so
-- #support-alerts gets one message per change, not one every 5 minutes. The state is kept here, not inside n8n, so it
-- survives restarts and can be tried on the test copy.
-- The outside watcher's ping address (Healthchecks.io) is a setting here; a test copy refresh deletes it, so trying
-- the workflow in the copy can never tell the watcher that all is well.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 012_monitoring.sql

insert into schema_migrations (version) values ('012');  -- a second run fails here instead of half-applying

create table monitor_state (
  part text primary key check (part ~ '^[a-z_]{1,40}$'),
  down_since timestamptz,                         -- null: the part is fine
  failing_since timestamptz,                      -- the first of the failed checks in a row (one is not "down" yet)
  detail text check (length(detail) <= 300),      -- why it fails, one short line
  checked_at timestamptz not null default now()
);
comment on table monitor_state is
  'Each monitored part (agent, tunnel, openai) and since when it has been down (empty: fine). Written only by '
  'monitor_report(), which the Health check workflow calls every 5 minutes.';

create table system_settings (
  name text primary key check (name ~ '^[a-z_]{1,60}$'),
  value text not null check (length(value) <= 500),
  note text
);
comment on table system_settings is
  'Settings the system reads that are not secrets in .env, set by the admin only. healthchecks_ping_url: where the '
  'Health check says "all well" (deleted in the test copy at every refresh).';

-- One call per part and check. No row back means no change; one row means "down" (the second failed check in a row,
-- dated from the first) or "back" (just recovered, with how long it was down).
create function monitor_report(p_part text, p_ok boolean, p_detail text)
returns table (change text, down_since timestamptz, down_for interval)
language plpgsql
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
declare
  v_since timestamptz;
  v_failing timestamptz;
begin
  if p_part is null or p_part !~ '^[a-z_]{1,40}$' then
    raise exception 'monitor_report: part must be lowercase words joined by _';
  end if;
  if p_ok is null then
    raise exception 'monitor_report: ok must be true or false';
  end if;

  insert into monitor_state (part) values (p_part) on conflict (part) do nothing;  -- first time: counts as fine
  select m.down_since, m.failing_since into v_since, v_failing
  from monitor_state m where m.part = p_part for update;  -- two runs can't cross

  if p_ok then
    update monitor_state m set down_since = null, failing_since = null, detail = null, checked_at = now()
    where m.part = p_part;
    if v_since is not null then
      change := 'back'; down_since := v_since; down_for := now() - v_since;
      return next;
    end if;
  elsif v_failing is null then  -- the first failed check: remembered, not reported (a blip stays silent)
    update monitor_state m set failing_since = now(), detail = left(p_detail, 300), checked_at = now()
    where m.part = p_part;
  else
    update monitor_state m set down_since = coalesce(m.down_since, v_failing), detail = left(p_detail, 300),
                               checked_at = now()
    where m.part = p_part;
    if v_since is null then  -- the second in a row: now it is down, since the first
      change := 'down'; down_since := v_failing; down_for := now() - v_failing;
      return next;
    end if;
  end if;
end;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function monitor_report(text, boolean, text) from public;
grant execute on function monitor_report(text, boolean, text) to n8n_support;
grant select on monitor_state to n8n_support;
grant select on system_settings to n8n_support;  -- reads the ping address; only the admin sets it
