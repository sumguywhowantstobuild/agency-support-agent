-- Check for migration 009: n8n_support can change a client's limit only through set_draft_limit(), only to 1..200,
-- only for a known client and a Slack user ID; every real change is logged, and the new limit is what
-- claim_draft_slot() uses. Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/009_limit_changes.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

select client_key as a, drafts_per_hour as start from client_map order by client_key limit 1 \gset
delete from draft_claims where client_key = :'a';

set local role n8n_support;

\echo '== can: change the first client''s limit to 2 (expect its name | its old limit | 2)'
select * from set_draft_limit(:'a', 2, 'U0CHECK009');
\echo '== the new limit is used at once: three claims (expect (t,f,1,2) (t,f,2,2) (f,t,2,2))'
select n as message, claim_draft_slot(:'a') from generate_series(1, 3) n;
\echo '== same number again: no change, nothing logged (expect 2 | 2)'
select limit_before, limit_after from set_draft_limit(:'a', 2, 'U0CHECK009');

\echo '== cannot (each must print ERROR):'
\echo '-- set 0'
savepoint s;
select set_draft_limit(:'a', 0, 'U0CHECK009');
rollback to s;
\echo '-- set 201'
savepoint s;
select set_draft_limit(:'a', 201, 'U0CHECK009');
rollback to s;
\echo '-- change an unknown client'
savepoint s;
select set_draft_limit('no-such-client', 5, 'U0CHECK009');
rollback to s;
\echo '-- leave out who made the change'
savepoint s;
select set_draft_limit(:'a', 5, '');
rollback to s;
\echo '-- change the limit directly'
savepoint s;
update client_map set drafts_per_hour = 100 where client_key = :'a';
rollback to s;
\echo '-- rewrite the change log'
savepoint s;
delete from limit_changes;
rollback to s;

reset role;
\echo '== as admin: one change logged (expect the first client | its old limit | 2 | U0CHECK009)'
select client_key, old_limit, new_limit, changed_by from limit_changes where changed_by = 'U0CHECK009';

rollback;
\echo '== done: rolled back, nothing kept'
