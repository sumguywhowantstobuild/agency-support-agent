-- 008: per-client limits. Before every AI call, intake claims a slot for that client. Over the client's
-- hourly limit there is no AI call: the ticket gets a "limit reached" card instead, and the team gets one alert per
-- client per hour, not one per message.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 008_draft_limits.sql

insert into schema_migrations (version) values ('008');  -- a second run fails here instead of half-applying

-- AI calls allowed per client in any 60 minutes. 20 suits a small agency client; a busy one can get more:
--   update client_map set drafts_per_hour = 40 where client_key = '...';
alter table client_map add column drafts_per_hour integer not null default 20 check (drafts_per_hour >= 1);

-- A ticket over the limit gets a draft row with this status and no AI text, so the card and the stats show it.
alter table drafts drop constraint drafts_status_check;
alter table drafts add constraint drafts_status_check
  check (status in ('pending', 'approved', 'edited', 'taken_over', 'agent_unavailable', 'replaced', 'limit_reached'));

-- One row per claim: which client, when, allowed or not, alert raised or not. No text. Rows older than a day are
-- removed by the function itself, so the table stays small.
create table draft_claims (
  id bigint generated always as identity primary key,
  client_key text not null references client_map (client_key) on delete cascade,
  allowed boolean not null,
  alerted boolean not null,
  created_at timestamptz not null default now()
);
create index draft_claims_client_time_idx on draft_claims (client_key, created_at);

-- Intake asks this before every AI call. It runs with the admin's rights, so n8n_support can't touch the claims or
-- the limit. Answers:
--   draft_allowed   true: ask the agent. false: limit reached, no AI call
--   alert_needed    true: first refusal for this client in 60 minutes, so post one alert
--   used_this_hour  AI calls for this client in the last 60 minutes (this one included if allowed)
--   hourly_limit    the client's limit, for the card and the alert
create function claim_draft_slot(p_client_key text)
returns table (draft_allowed boolean, alert_needed boolean, used_this_hour integer, hourly_limit integer)
language plpgsql
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
declare
  v_limit integer;
  v_used integer;
  v_alerted boolean;
begin
  -- One claim per client at a time: messages that arrive together for the same client wait here for each other, so
  -- the count can't overshoot. Other clients aren't held up, and saving tickets isn't blocked ("no key" lock).
  select c.drafts_per_hour into v_limit
  from client_map c where c.client_key = p_client_key
  for no key update;
  if not found then
    raise exception 'claim_draft_slot: unknown client';
  end if;

  delete from draft_claims d where d.client_key = p_client_key and d.created_at < now() - interval '1 day';

  -- Rolling 60 minutes, not the clock hour: 20 at 10:59 and 20 more at 11:00 isn't possible.
  select count(*) filter (where d.allowed), coalesce(bool_or(d.alerted), false)
  into v_used, v_alerted
  from draft_claims d
  where d.client_key = p_client_key and d.created_at > now() - interval '60 minutes';

  draft_allowed := v_used < v_limit;
  alert_needed := not draft_allowed and not v_alerted;
  insert into draft_claims (client_key, allowed, alerted) values (p_client_key, draft_allowed, alert_needed);

  used_this_hour := v_used + draft_allowed::integer;
  hourly_limit := v_limit;
  return next;
end;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function claim_draft_slot(text) from public;
grant execute on function claim_draft_slot(text) to n8n_support;
