-- Check for migration 022: admin_drafts_today gives the admin app the time of every real draft per client for the last
-- 48 hours, and nothing else. A draft counts by the approval report's rule (013): it has a confidence and is not test
-- traffic. Cards for a limit, a paused client or an agent outage do not count (even taken over later), a removed
-- client's drafts do not, and the admin login can read the view but none of the ticket or draft tables. Runs in one
-- transaction that is rolled back; it adds made-up clients and counts only theirs, so real rows don't matter. Each
-- "can't" line must print an ERROR, and no line may print a NOTICE saying LEAK.
-- It writes test rows as the database admin, so run it ONLY on a throwaway database: a new container from the pinned
-- pgvector image (compose.dev.yaml) with migrations 001 to 022 applied, named here asa-throwaway-022, removed after.
-- Never on the running database.
-- Run:  docker exec -i asa-throwaway-022 psql -U postgres -d knowledge < db/checks/022_drafts_today.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): two active clients and one removed client, all made up'
insert into client_map (channel_id, client_key, display_name, location_id)
values ('check022-channel-a', 'check022-a', 'Check 022 A', 'check022-location-a'),
       ('check022-channel-b', 'check022-b', 'Check 022 B', 'check022-location-b');
insert into client_map (channel_id, client_key, display_name, location_id, status)
values ('check022-channel-r', 'check022-r', 'Check 022 R', 'check022-location-r', 'removed');

-- One made-up ticket with one draft, made p_ago before now. A card with no draft carries no confidence, as the real
-- ones do ({"limit": 1}); a decided status needs who and when (the table's own check).
create function pg_temp.fake(p_client text, p_status text, p_has_draft boolean, p_ago interval)
returns void
language sql
as $$
  with t as (
    insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text, created_at)
    select 'check022-' || gen_random_uuid(), c.channel_id, 'check022-' || gen_random_uuid(), c.client_key,
           c.location_id, 'check 022 made-up ticket TEXT-MUST-NOT-LEAK', now() - p_ago - interval '20 seconds'
    from client_map c
    where c.client_key = p_client
    returning id
  )
  insert into drafts (ticket_id, draft, status, decided_by, decided_at, created_at)
  select t.id,
         case when p_has_draft then '{"confidence": "high", "needs_person": false, "sources": []}'::jsonb
              else '{"limit": 1}'::jsonb end,
         p_status,
         case when p_status in ('approved', 'edited', 'taken_over', 'test_closed') then 'U0CHECK022' end,
         case when p_status in ('approved', 'edited', 'taken_over', 'test_closed') then now() - p_ago end,
         now() - p_ago
  from t;
$$;

-- Client A: four real drafts that count, then everything that must not.
\o /dev/null
select pg_temp.fake('check022-a', 'approved', true, '1 hour');
select pg_temp.fake('check022-a', 'pending', true, '2 hours');
select pg_temp.fake('check022-a', 'taken_over', true, '3 hours');            -- a real draft someone took over: counts
select pg_temp.fake('check022-a', 'replaced', true, '47 hours');             -- inside the 48 hours: counts
select pg_temp.fake('check022-a', 'test_closed', true, '13 minutes');        -- test traffic, even with a confidence
select pg_temp.fake('check022-a', 'limit_reached', false, '1 hour');
select pg_temp.fake('check022-a', 'client_paused', false, '1 hour');
select pg_temp.fake('check022-a', 'agent_unavailable', false, '1 hour');
select pg_temp.fake('check022-a', 'taken_over', false, '1 hour');            -- a limit card someone took over: no draft
select pg_temp.fake('check022-a', 'approved', true, '49 hours');             -- past the 48 hours
-- Client B: one real draft. Client R (removed): a real draft that must stay out.
select pg_temp.fake('check022-b', 'edited', true, '30 minutes');
select pg_temp.fake('check022-r', 'approved', true, '11 minutes');
\o

\echo '== as admin: only real drafts of active clients, inside 48 hours (expect check022-a | 4, check022-b | 1)'
select client_key, count(*) from admin_drafts_today where client_key like 'check022-%' group by client_key order by 1;
\echo '== the removed client has none (expect 0)'
select count(*) from admin_drafts_today where client_key = 'check022-r';
\echo '== a draft shows the time it was made (expect t)'
select count(*) = 1 and bool_and(now() - created_at = interval '30 minutes') from admin_drafts_today
where client_key = 'check022-b';
\echo '== the view holds the key and the time only, no ticket or draft text (expect client_key,created_at)'
select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns
where table_name = 'admin_drafts_today';

-- A probe the admin login could write: a very cheap condition that says out loud what it sees. Its times are those of
-- the draft rows the view must hide (test traffic 13 minutes ago, the removed client's 11 minutes ago, one 49 hours
-- ago), each different from every row that counts. Without a security barrier the planner runs it before the view's
-- filters and it prints LEAK for them.
create function public.check022_probe(p_at timestamptz) returns boolean
language plpgsql cost 0.0001
as $$
begin
  if p_at in (now() - interval '13 minutes', now() - interval '11 minutes', now() - interval '49 hours') then
    raise notice 'LEAK: a hidden draft time reached the probe: %', p_at;
  end if;
  return true;
end;
$$;

set local role admin_app;
\echo '== a probing condition sees only the rows the view lets through (expect no NOTICE line; 5 rows)'
select count(*) from admin_drafts_today where public.check022_probe(created_at);
\echo '== can: the admin login reads the view (expect 4, from the same rows as above)'
select count(*) from admin_drafts_today where client_key = 'check022-a';
\echo '== the ticket text is nowhere in it (expect 0)'
select count(*) from admin_drafts_today v where v::text like '%MUST-NOT-LEAK%';
\echo '== the admin login may only select from the view (expect SELECT)'
select string_agg(privilege_type, ',' order by privilege_type) from information_schema.role_table_grants
where grantee = 'admin_app' and table_name = 'admin_drafts_today';
\echo '== cannot (each must print ERROR):'
\echo '-- the admin login reads the drafts'
savepoint s;
select * from drafts limit 1;
rollback to s;
\echo '-- the admin login reads the tickets'
savepoint s;
select * from tickets limit 1;
rollback to s;
\echo '-- the admin login reads the numbers kept after the clean-up'
savepoint s;
select * from ticket_stats limit 1;
rollback to s;
\echo '-- the admin login reads the drafts'' numbers view'
savepoint s;
select * from draft_stats limit 1;
rollback to s;
\echo '-- the admin login changes the view'
savepoint s;
delete from admin_drafts_today;
rollback to s;

reset role;
set local role n8n_support;
\echo '-- n8n reads the view (the grant is the admin login''s alone)'
savepoint s;
select * from admin_drafts_today limit 1;
rollback to s;

reset role;
set local role agent_reader;
\echo '-- the agent reads the view'
savepoint s;
select * from admin_drafts_today limit 1;
rollback to s;

reset role;
rollback;
\echo '== done: rolled back, nothing kept'
