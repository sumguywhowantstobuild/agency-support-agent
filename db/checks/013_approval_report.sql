-- Check for migration 013: approval_report() counts what staff did with the agent's drafts in a period, per client
-- and for all clients: approved as written, edited, taken over, still waiting. Cards with no draft (agent busy,
-- limit reached) and drafts closed as test traffic are counted on their own, never as a staff decision, and the
-- "closed as test" mark survives the 90-day clean-up. Only the admin login can run it.
-- Runs in one transaction that is rolled back. On live it holds every ticket row for the few milliseconds it runs:
-- run it (and live migrations) when no tickets are expected (SETUP section 12).
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/013_approval_report.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== the flood test''s closed drafts now say "closed as test" (expect 0)'
select count(*) from drafts where decided_by = 'flood-test-cleanup' and status <> 'test_closed';

-- From here on only made-up rows: everything real is removed inside this transaction (rolled back at the end), and
-- two made-up clients are added, so the check works however many clients the install has.
delete from tickets;
delete from ticket_stats;
insert into client_map (channel_id, client_key, display_name, location_id)
values ('check013-channel-a', 'check013-a', 'Check 013 A', 'check013-location-a'),
       ('check013-channel-b', 'check013-b', 'Check 013 B', 'check013-location-b');
\set a check013-a
\set b check013-b

-- One made-up ticket with one draft: drafted p_ago before now, 20 s after the client's message; decided
-- p_decision_secs after the draft (null: nobody decided). A card with no draft carries no confidence.
create function pg_temp.fake(p_client text, p_status text, p_has_draft boolean, p_ago interval,
                             p_decision_secs integer default null)
returns void
language sql
as $$
  with t as (
    insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text, created_at)
    select 'check013-' || gen_random_uuid(), c.channel_id, 'check013-' || gen_random_uuid(), c.client_key,
           c.location_id, 'check 013 made-up ticket', now() - p_ago - interval '20 seconds'
    from client_map c
    where c.client_key = p_client
    returning id
  )
  insert into drafts (ticket_id, draft, status, decided_by, decided_at, created_at)
  select t.id,
         case when p_has_draft then '{"confidence": "high", "needs_person": false, "sources": []}'::jsonb
              else '{"limit": 1}'::jsonb end,
         p_status,
         case when p_decision_secs is not null then 'U0CHECK013' end,
         now() - p_ago + make_interval(secs => p_decision_secs),
         now() - p_ago
  from t;
$$;

-- First client, inside the 30-day period.
select pg_temp.fake(:'a', 'approved', true, '1 day', 60);
select pg_temp.fake(:'a', 'approved', true, '1 day', 120);
select pg_temp.fake(:'a', 'approved', true, '1 day', 180);
select pg_temp.fake(:'a', 'edited', true, '1 day', 600);
select pg_temp.fake(:'a', 'taken_over', true, '1 day', 300);
select pg_temp.fake(:'a', 'taken_over', false, '1 day', 30);       -- a busy card someone took over: no draft
select pg_temp.fake(:'a', 'limit_reached', false, '1 day');
select pg_temp.fake(:'a', 'agent_unavailable', false, '1 day');
select pg_temp.fake(:'a', 'replaced', false, '1 day');             -- a busy card crossed out by a newer one: no draft
select pg_temp.fake(:'a', 'pending', true, '1 day');
select pg_temp.fake(:'a', 'replaced', true, '1 day');
select pg_temp.fake(:'a', 'test_closed', true, '1 day', 10);
select pg_temp.fake(:'a', 'test_closed', true, '1 day', 10);
select pg_temp.fake(:'a', 'test_closed', false, '1 day', 10);      -- test traffic with no draft: counted as test
select pg_temp.fake(:'a', 'approved', true, '40 days', 60);        -- before the period: not counted
-- Second client: one live draft, and two rows as the archive holds them.
select pg_temp.fake(:'b', 'taken_over', true, '1 day', 240);
insert into ticket_stats (draft_id, ticket_id, client_key, main_source, confidence, needs_person, status, drafted_at,
                          seconds_to_draft, seconds_to_decision, follow_ups)
values (gen_random_uuid(), gen_random_uuid(), :'b', null, 'high', false, 'approved', now() - interval '2 days',
        20, 60, 0),
       (gen_random_uuid(), gen_random_uuid(), :'b', null, 'high', false, 'test_closed', now() - interval '2 days',
        20, 10, 0);

\echo '== the last 30 days (expect 3 rows:'
\echo '   check013-a  | 5 | 3 | 1 | 1 | 60 | 1 | 1 | 4 | 3 | 20 | 3.0'
\echo '   check013-b  | 2 | 1 | 0 | 1 | 50 | 0 | 0 | 0 | 1 | 20 | 2.5'
\echo '   all clients | 7 | 4 | 1 | 2 | 57 | 1 | 1 | 4 | 4 | 20 | 3.0)'
select * from approval_report(now() - interval '30 days');

-- Through the real 90-day clean-up: three drafts from 100 days ago, one closed as test, one approved, one never
-- decided. The clean-up moves their numbers to the archive and deletes the tickets.
select pg_temp.fake(:'b', 'test_closed', true, '100 days', 10);
select pg_temp.fake(:'b', 'approved', true, '100 days', 60);
select pg_temp.fake(:'b', 'pending', true, '100 days');
\echo '== the clean-up removes those 3 tickets (expect 3)'
select delete_old_tickets();
\echo '== each is now in the archive once, and no longer among the live drafts (expect 3 | 0)'
select (select count(*) from ticket_stats where drafted_at < now() - interval '80 days') as archived,
       (select count(*) from draft_stats where drafted_at < now() - interval '80 days') as live;
\echo '== the closed-as-test mark survived; the undecided one shows as waiting (never decided) (expect 2 rows:'
\echo '   check013-b  | 1 | 1 | 0 | 0 | 100 | 1 | 0 | 0 | 1 | 20 | 1.0'
\echo '   all clients | 1 | 1 | 0 | 0 | 100 | 1 | 0 | 0 | 1 | 20 | 1.0)'
select * from approval_report(now() - interval '120 days', now() - interval '80 days');

\echo '== a period with nothing in it (expect 1 row: all clients | 0 | 0 | 0 | 0 | (blank) | 0 | 0 | 0 | 0 | (blank) | (blank))'
select * from approval_report(now() - interval '400 days', now() - interval '300 days');

\echo '== cannot (each must print ERROR):'
\echo '-- close a draft as test without saying who and when'
savepoint s;
select pg_temp.fake(:'a', 'test_closed', true, '1 day');
rollback to s;
\echo '-- run the report as the agent''s login'
savepoint s;
set local role agent_reader;
select * from approval_report(now() - interval '30 days');
rollback to s;
\echo '-- run the report as the workflows'' login'
savepoint s;
set local role n8n_support;
select * from approval_report(now() - interval '30 days');
rollback to s;

rollback;
