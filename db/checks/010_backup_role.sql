-- Check for migration 010: the backup login reads everything and changes nothing, except adding honest rows to
-- backup_runs; only n8n and the backup can connect to n8n's database. Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/010_backup_role.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== who may connect to n8n''s database (expect agent_reader f, backup t, kb_sync f, n8n t, n8n_support f, postgres t)'
select r.rolname, has_database_privilege(r.rolname, 'n8n', 'connect') as can_connect
from pg_roles r where r.rolname in ('agent_reader', 'backup', 'kb_sync', 'n8n', 'n8n_support', 'postgres')
order by 1;
\echo '== the backup may connect to this database (expect t)'
select has_database_privilege('backup', current_database(), 'connect');

set local role backup;

\echo '== can: read every table (expect four counts)'
select (select count(*) from documents) as documents, (select count(*) from tickets) as tickets,
       (select count(*) from client_map) as clients, (select count(*) from limit_changes) as limit_changes;
\echo '== can: record a good run and a failed one (expect two rows: t | 1048576 | null, then f | 0 | dump failed)'
insert into backup_runs (started_at, ok, bytes) values (now() - interval '1 minute', true, 1048576);
insert into backup_runs (started_at, ok, error) values (now() - interval '1 minute', false, 'dump failed');
select ok, bytes, error from backup_runs where started_at > now() - interval '2 minutes' order by ok desc;
\echo '== can: read the newest good run (expect one time)'
select max(finished_at) as last_good from backup_runs where ok;

\echo '== cannot (each must print ERROR):'
\echo '-- a success that carries an error'
savepoint s;
insert into backup_runs (started_at, ok, bytes, error) values (now(), true, 10, 'oops');
rollback to s;
\echo '-- a success with nothing in it'
savepoint s;
insert into backup_runs (started_at, ok, bytes) values (now(), true, 0);
rollback to s;
\echo '-- a failure that doesn''t say why'
savepoint s;
insert into backup_runs (started_at, ok) values (now(), false);
rollback to s;
\echo '-- a size over the 500 MB cap'
savepoint s;
insert into backup_runs (started_at, ok, bytes) values (now(), true, 524288001);
rollback to s;
\echo '-- an error message over 300 characters'
savepoint s;
insert into backup_runs (started_at, ok, error) values (now(), false, repeat('x', 301));
rollback to s;
\echo '-- rewrite a run'
savepoint s;
update backup_runs set ok = true;
rollback to s;
\echo '-- delete runs'
savepoint s;
delete from backup_runs;
rollback to s;
\echo '-- change a ticket'
savepoint s;
update tickets set text = text;
rollback to s;
\echo '-- delete a guide'
savepoint s;
delete from documents;
rollback to s;
\echo '-- add a client'
savepoint s;
insert into client_map (channel_id, client_key, display_name, location_id) values ('C0CHECK010', 'x', 'x', 'x');
rollback to s;
\echo '-- change a limit through the staff function'
savepoint s;
select set_draft_limit((select client_key from client_map limit 1), 5, 'U0CHECK010');
rollback to s;
\echo '-- run the clean-up'
savepoint s;
select delete_old_tickets();
rollback to s;

reset role;
set local role n8n_support;
\echo '== n8n_support can read the runs for the morning check (expect a count)'
select count(*) as runs from backup_runs;
\echo '-- n8n_support cannot add a run (must print ERROR)'
savepoint s;
insert into backup_runs (started_at, ok, bytes) values (now(), true, 10);
rollback to s;

rollback;
\echo '== done: rolled back, nothing kept'
