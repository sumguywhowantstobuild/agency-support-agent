-- Check for migration 023: admin_note_channel_names sets a client's Slack channel name and nothing else. The admin
-- login can call it (and read the name from admin_clients) but cannot update client_map itself; n8n's login and the
-- agent's reader cannot call it. Per entry, a bad value (empty, 81 characters, a control character, a number, a null) is
-- skipped while the rest are noted; input that is not a JSON object, and the call inside a read-only transaction, are
-- refused. Runs in one transaction that is rolled back. Each "can't" line must print an ERROR.
-- It writes made-up clients as the database admin, so it is for the dev database (rolled back) or a throwaway one.
-- Until the migration is applied for real, run it with the migration in the SAME transaction, so nothing persists:
--   { echo 'begin;'; cat db/migrations/023_channel_names.sql db/checks/023_channel_names.sql; } | docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge
-- (the "there is already a transaction in progress" warning from this file's own begin is expected there). After the
-- release, run it alone:
--   docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/023_channel_names.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): nine made-up clients, no channel name yet'
insert into client_map (channel_id, client_key, display_name, location_id)
select 'C0CHECK023' || n, 'check-023-' || lower(n), 'Check 023 ' || n, 'LOCCHECK023' || n
from unnest(array['A', 'B', 'E', 'L', 'K', 'N', 'Z', 'M', 'R']) as t(n);

set local role admin_app;

\echo '== can: note a name for a client''s channel (expect 1)'
select admin_note_channel_names('{"C0CHECK023A": "check-a-support"}');
\echo '== the Clients view shows it (expect check-023-a | check-a-support)'
select client_key, channel_name from admin_clients where client_key = 'check-023-a';
\echo '== an unknown channel ID changes nothing (expect 0)'
select admin_note_channel_names('{"C0NOCLIENT": "not-a-client"}');
\echo '== the same name again updates no row (expect 0)'
select admin_note_channel_names('{"C0CHECK023A": "check-a-support"}');
\echo '== a renamed channel is noted (expect 1, then check-a-renamed)'
select admin_note_channel_names('{"C0CHECK023A": "check-a-renamed"}');
select channel_name from admin_clients where client_key = 'check-023-a';
\echo '== an 80-character name is noted (expect 1, then 80)'
select admin_note_channel_names(jsonb_build_object('C0CHECK023M', repeat('m', 80)));
select length(channel_name) from admin_clients where client_key = 'check-023-m';

\echo '== per entry: one good name and five bad ones in one call (expect 1; the bad five stay empty)'
select admin_note_channel_names(jsonb_build_object(
  'C0CHECK023B', 'check-b-support',
  'C0CHECK023E', '',
  'C0CHECK023L', repeat('x', 81),
  'C0CHECK023K', 'bell' || chr(7) || 'ring',
  'C0CHECK023N', to_jsonb(5),
  'C0CHECK023Z', 'null'::jsonb));
\echo '== (expect check-023-b | check-b-support and check-023-e, k, l, n, z with no name)'
select client_key, channel_name from admin_clients
where client_key in ('check-023-b', 'check-023-e', 'check-023-k', 'check-023-l', 'check-023-n', 'check-023-z')
order by client_key;
\echo '== a tab and a newline are control characters too (expect 0)'
select admin_note_channel_names(jsonb_build_object('C0CHECK023R', 'a' || chr(9) || 'b', 'C0CHECK023E', 'a' || chr(10)));

\echo '== cannot (each must print ERROR):'
\echo '-- input that is a JSON array'
savepoint s;
select admin_note_channel_names('["C0CHECK023A"]');
rollback to s;
\echo '-- input that is a JSON string'
savepoint s;
select admin_note_channel_names('"C0CHECK023A"');
rollback to s;
\echo '-- input that is a JSON number'
savepoint s;
select admin_note_channel_names('5');
rollback to s;
\echo '-- input that is a JSON null'
savepoint s;
select admin_note_channel_names('null');
rollback to s;
\echo '-- input that is SQL null'
savepoint s;
select admin_note_channel_names(null);
rollback to s;
\echo '-- update client_map directly'
savepoint s;
update client_map set channel_name = 'direct' where client_key = 'check-023-a';
rollback to s;
\echo '-- update any other column of client_map directly'
savepoint s;
update client_map set display_name = 'Renamed' where client_key = 'check-023-a';
rollback to s;
\echo '-- a name over 80 characters straight into the column (the table''s own check; as admin)'
reset role;
savepoint s;
update client_map set channel_name = repeat('x', 81) where client_key = 'check-023-a';
rollback to s;
set local role admin_app;

\echo '== n8n''s login cannot call it (must print ERROR)'
set local role n8n_support;
savepoint s;
select admin_note_channel_names('{"C0CHECK023A": "n8n"}');
rollback to s;
\echo '== the agent''s reader cannot call it (must print ERROR)'
set local role agent_reader;
savepoint s;
select admin_note_channel_names('{"C0CHECK023A": "agent"}');
rollback to s;

\echo '== the name did not change (expect check-a-renamed)'
reset role;
select channel_name from client_map where client_key = 'check-023-a';

\echo '== in a read-only transaction (a page''s GET) the call is refused (must print ERROR)'
set local role admin_app;
set transaction read only;
select admin_note_channel_names('{"C0CHECK023A": "from-a-get"}');

rollback;
\echo '== done: rolled back, nothing kept'
