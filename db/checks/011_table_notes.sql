-- Check for migration 011: every table and view in the knowledge database says in plain words what it holds.
-- Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/011_table_notes.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== every table and view with its description'
select c.relname as name, coalesce(obj_description(c.oid, 'pg_class'), '(missing)') as description
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('r', 'v')
order by 1;

\echo '== tables and views without a description (expect 0)'
select count(*) as missing
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('r', 'v') and obj_description(c.oid, 'pg_class') is null;

\echo '== 011 is recorded (expect t)'
select exists (select from schema_migrations where version = '011') as recorded;

rollback;
\echo '== done: rolled back, nothing kept'
