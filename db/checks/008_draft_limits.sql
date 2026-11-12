-- Check for migration 008: a client gets its hourly number of AI calls, then refusals with exactly one alert; another
-- client isn't affected; after 60 minutes there is room again (and a new alert if it fills up); n8n_support can claim
-- and save a "limit reached" draft, but can't change the limit or the claims.
-- Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/008_draft_limits.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

-- Two clients from client_map. A gets a limit of 3 for this check; earlier claims are cleared so counts start at 0.
select client_key as a from client_map order by client_key limit 1 \gset
select client_key as b from client_map order by client_key offset 1 limit 1 \gset
update client_map set drafts_per_hour = 3 where client_key = :'a';
delete from draft_claims;

set local role n8n_support;

\echo '== client A, limit 3, five messages (expect (t,f,1,3) (t,f,2,3) (t,f,3,3) (f,t,3,3) (f,f,3,3))'
\echo '   columns: allowed, alert, used this hour, limit. Only the first refusal raises the alert.'
select n as message, claim_draft_slot(:'a') from generate_series(1, 5) n;

\echo '== client B is not affected (expect (t,f,1,20))'
select claim_draft_slot(:'b');

\echo '== can: save a "limit reached" draft (expect limit_reached)'
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select 'check-008', channel_id, 'check-008', client_key, location_id, 'check ticket' from client_map where client_key = :'a';
insert into drafts (ticket_id, draft, status)
select id, '{"limit": 3}', 'limit_reached' from tickets where slack_event_id = 'check-008'
returning status;

\echo '== cannot (each must print ERROR):'
\echo '-- raise its own limit'
savepoint s;
update client_map set drafts_per_hour = 1000 where client_key = :'a';
rollback to s;
\echo '-- clear the claims'
savepoint s;
delete from draft_claims;
rollback to s;
\echo '-- add a claim directly'
savepoint s;
insert into draft_claims (client_key, allowed, alerted) values (:'a', true, false);
rollback to s;
\echo '-- claim for a client that does not exist'
savepoint s;
select claim_draft_slot('no-such-client');
rollback to s;

reset role;
\echo '-- as admin: move client A''s claims 61 minutes into the past'
update draft_claims set created_at = created_at - interval '61 minutes' where client_key = :'a';
set local role n8n_support;

\echo '== an hour later: room again, and a new alert when it fills up (expect (t,f,1,3) (t,f,2,3) (t,f,3,3) (f,t,3,3))'
select n as message, claim_draft_slot(:'a') from generate_series(1, 4) n;

reset role;
\echo '== as admin: the claims hold no text (expect id, client_key, allowed, alerted, created_at)'
select string_agg(column_name, ', ' order by ordinal_position) from information_schema.columns
where table_name = 'draft_claims';

rollback;
\echo '== done: rolled back, nothing kept'
