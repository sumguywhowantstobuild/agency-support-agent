-- Check for migration 019: the agent's login can list the active clients' HighLevel locations and nothing more.
-- Runs in one transaction that is rolled back. Each "can't" line must print an ERROR.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/019_active_client_locations.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): one active, one paused and one removed client'
insert into client_map (channel_id, client_key, display_name, location_id, status, pause_reason)
values ('C0CHECK019A', 'check-019-a', 'Check A', 'LOCCHECK019A', 'active', null),
       ('C0CHECK019P', 'check-019-p', 'Check P', 'LOCCHECK019P', 'paused', 'client_asked'),
       ('C0CHECK019R', 'check-019-r', 'Check R', 'LOCCHECK019R', 'removed', null);

set local role agent_reader;
\echo '== can: list the active locations (expect LOCCHECK019A only, among the check clients)'
select l from active_client_locations() l where l like 'LOCCHECK019%';
\echo '-- read the client list directly (must print ERROR)'
savepoint s;
select * from client_map;
rollback to s;

rollback;
\echo '== done: rolled back, nothing kept'
