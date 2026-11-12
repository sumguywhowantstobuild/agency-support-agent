-- Check for migration 012: health monitoring. monitor_report() answers "down" once a part has failed 2 checks in a row
-- (a single blip stays silent), "back" once when it recovers, and nothing in between; n8n_support can report and read
-- the ping address, nothing more.
-- Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/012_monitoring.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;
set local role n8n_support;  -- everything below runs with the workflow's own login

\echo '== a part seen for the first time and fine: no message (expect 0 rows)'
select * from monitor_report('check_part', true, null);

\echo '== one failed check: no message yet, a blip stays silent (expect 0 rows)'
select * from monitor_report('check_part', false, 'agent: connection refused');

\echo '== fine at the next check: still no message, the blip is forgotten (expect 0 rows, then f)'
select * from monitor_report('check_part', true, null);
select down_since is not null or failing_since is not null as remembered from monitor_state where part = 'check_part';

\echo '== failed twice in a row: one "down", dated from the first failure (expect 1 row: down, t)'
select * from monitor_report('check_part', false, 'agent: connection refused');
select change, down_since < now() + interval '1 second' as dated
from monitor_report('check_part', false, 'agent: connection refused');

\echo '== still failing 5 minutes later: no message again (expect 0 rows)'
select * from monitor_report('check_part', false, 'agent: connection refused');

\echo '== its state while down (expect t, "agent: connection refused")'
select down_since is not null as down, detail from monitor_state where part = 'check_part';

\echo '== it recovers: one "back" with how long it was down (expect 1 row: back, t)'
select change, down_for >= interval '0' as has_duration from monitor_report('check_part', true, null);

\echo '== fine again: no message (expect 0 rows)'
select * from monitor_report('check_part', true, null);

\echo '== a long detail is cut to 300 characters (expect 1 row: down, then 300)'
select * from monitor_report('long_part', false, repeat('x', 1000));
select change from monitor_report('long_part', false, repeat('x', 1000));
select length(detail) from monitor_state where part = 'long_part';

\echo '== the parts that are down right now, for the outside watcher (expect long_part only)'
select part from monitor_state where down_since is not null and part in ('check_part', 'long_part');

\echo '== n8n_support can read the ping address (expect a count, no error: 0 on a new database, 1 on live once set)'
select count(*) from system_settings where name = 'healthchecks_ping_url';

\echo '== n8n_support cannot change the ping address (expect ERROR: permission denied)'
savepoint s1;
insert into system_settings (name, value) values ('healthchecks_ping_url', 'https://example.com/steal');
rollback to savepoint s1;

\echo '== n8n_support cannot write monitor_state directly (expect ERROR: permission denied)'
savepoint s2;
update monitor_state set down_since = null;
rollback to savepoint s2;

\echo '== a part name that is not lowercase words is refused (expect ERROR: monitor_report: part)'
savepoint s3;
select * from monitor_report('Agent; drop table x', true, null);
rollback to savepoint s3;

reset role;
\echo '== 012 is recorded (expect t)'
select exists (select from schema_migrations where version = '012') as recorded;

\echo '== both new tables have a description (expect 0 missing)'
select count(*) as missing from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('monitor_state', 'system_settings')
  and obj_description(c.oid, 'pg_class') is null;

rollback;
\echo '== done: rolled back, nothing kept'
