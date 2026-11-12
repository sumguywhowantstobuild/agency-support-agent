-- Check for migration 018: clients are added, paused, resumed, re-keyed and removed only through the admin_*
-- functions, each logged with who; locked keys are readable by no application login, and the agent gets one only for an active
-- client through sealed_key_for_location(). Runs in one transaction that is rolled back. Each "can't" line must print
-- an ERROR.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/018_client_lifecycle.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): a staff member, an existing client, two stand-in locked keys'
insert into staff (name, email, slack_user_id) values ('Check Admin', 'check.admin18@example.com', 'U0CHECK018A')
returning id as me \gset
insert into staff (name, email, slack_user_id, active) values ('Gone', 'gone18@example.com', 'U0CHECK018G', false)
returning id as gone \gset
select client_key as other, channel_id as other_channel, location_id as other_location
from client_map where status = 'active' order by client_key limit 1 \gset
select decode(repeat('ab', 60), 'hex') as key1, decode(repeat('cd', 60), 'hex') as key2 \gset

set local role admin_app;

\echo '== can: add a client (expect check-client)'
select admin_add_client(:me, 'C0CHECK018', 'Check Client', 'LOCCHECK018', 20, :'key1') as new_key \gset
select :'new_key' as client_key;
\echo '== the Clients view shows it (expect Check Client | C0CHECK018 | LOCCHECK018 | 20 | 0 | active | t)'
select display_name, channel_id, location_id, drafts_per_hour, used_this_hour, status, key_set_at is not null
from admin_clients where client_key = :'new_key';
\echo '== the view has no key column (expect 0)'
select count(*) from information_schema.columns
where table_name = 'admin_clients' and (column_name like '%key%' and column_name <> 'client_key' and column_name <> 'key_set_at'
                                        or column_name like '%seal%');
\echo '== a second client with the same name gets its own key (expect check-client-2)'
select admin_add_client(:me, 'C0CHECK018B', 'Check Client', 'LOCCHECK018B', 20, :'key1');
\echo '== a name with no plain letters still gets a key (expect client)'
select admin_add_client(:me, 'C0CHECK018C', '!!!', 'LOCCHECK018C', 20, :'key1');

\echo '== can: pause for a privacy hold (expect paused | privacy_hold | waiting for the BAA)'
select admin_pause_client(:me, :'new_key', 'privacy_hold', 'waiting for the BAA');
select status, pause_reason, pause_note from admin_clients where client_key = :'new_key';
\echo '== can: resume (expect active | null | null)'
select admin_resume_client(:me, :'new_key');
select status, pause_reason is null, pause_note is null from admin_clients where client_key = :'new_key';
\echo '== can: replace the key'
select admin_replace_client_key(:me, :'new_key', :'key2');
\echo '== can: change the limit from the app (expect Check Client | 20 | 35)'
select * from admin_set_draft_limit(:me, :'new_key', 35);
\echo '== a staff change, for the history check at the end'
select admin_add_staff(:me, 'Check Helper', 'check.helper18@example.com', 'U0CHECK018H') is not null as added;

\echo '== cannot (each must print ERROR):'
\echo '-- pause without a reason'
savepoint s;
select admin_pause_client(:me, :'new_key', null, null);
rollback to s;
\echo '-- pause for a reason not on the list'
savepoint s;
select admin_pause_client(:me, :'new_key', 'bored', null);
rollback to s;
\echo '-- a note over 200 characters'
savepoint s;
select admin_pause_client(:me, :'new_key', 'client_asked', repeat('x', 201));
rollback to s;
\echo '-- add on a channel an active client has'
savepoint s;
select admin_add_client(:me, :'other_channel', 'Thief', 'LOCCHECK018X', 20, :'key1');
rollback to s;
\echo '-- add on a HighLevel location an active client has'
savepoint s;
select admin_add_client(:me, 'C0CHECK018X', 'Thief', :'other_location', 20, :'key1');
rollback to s;
\echo '-- add the same new client twice'
savepoint s;
select admin_add_client(:me, 'C0CHECK018', 'Check Client', 'LOCCHECK018', 20, :'key1');
rollback to s;
\echo '-- a channel ID that is not one'
savepoint s;
select admin_add_client(:me, 'general', 'Bad', 'LOCCHECK018Y', 20, :'key1');
rollback to s;
\echo '-- a limit of 0'
savepoint s;
select admin_add_client(:me, 'C0CHECK018Z', 'Bad', 'LOCCHECK018Z', 0, :'key1');
rollback to s;
\echo '-- no key'
savepoint s;
select admin_add_client(:me, 'C0CHECK018Z', 'Bad', 'LOCCHECK018Z', 20, null);
rollback to s;
\echo '-- a deactivated member makes a change'
savepoint s;
select admin_set_draft_limit(:gone, :'new_key', 40);
rollback to s;
\echo '-- read the locked keys'
savepoint s;
select * from client_keys;
rollback to s;
\echo '-- change a client directly'
savepoint s;
update client_map set status = 'paused' where client_key = :'new_key';
rollback to s;
\echo '-- read the change log directly'
savepoint s;
select * from client_changes;
rollback to s;

reset role;
set local role agent_reader;
\echo '== agent: the key of an active client (expect t), of an unknown location (expect t: null)'
select sealed_key_for_location('LOCCHECK018') = :'key2', sealed_key_for_location('LOCNOWHERE') is null;
\echo '-- agent reads the locked keys (must print ERROR)'
savepoint s;
select * from client_keys;
rollback to s;

reset role;
set local role admin_app;
\echo '== can: pause again; the agent gets no key while paused (expect t)'
select admin_pause_client(:me, :'new_key', 'key_broken', null);
reset role;
set local role agent_reader;
select sealed_key_for_location('LOCCHECK018') is null;

reset role;
set local role admin_app;
\echo '== can: remove (expect removed)'
select admin_remove_client(:me, :'new_key');
select status from admin_clients where client_key = :'new_key';
\echo '-- pause a removed client (must print ERROR)'
savepoint s;
select admin_pause_client(:me, :'new_key', 'client_asked', null);
rollback to s;
\echo '-- remove it twice (must print ERROR)'
savepoint s;
select admin_remove_client(:me, :'new_key');
rollback to s;
\echo '-- re-add its channel with another location (must print ERROR)'
savepoint s;
select admin_add_client(:me, 'C0CHECK018', 'Check Client', 'LOCCHECK018Q', 20, :'key1');
rollback to s;
\echo '== can: re-add it (expect the same client key, then active | 20 | t)'
select admin_add_client(:me, 'C0CHECK018', 'Check Client Again', 'LOCCHECK018', 20, :'key1');
select status, drafts_per_hour, display_name = 'Check Client Again' from admin_clients where client_key = :'new_key';

reset role;
set local role n8n_support;
\echo '== n8n reads the status (expect active)'
select status from client_map where channel_id = 'C0CHECK018';
\echo '-- n8n reads the locked keys (must print ERROR)'
savepoint s;
select * from client_keys;
rollback to s;

reset role;
\echo '== as admin: the removed key was deleted, the re-added one is key1 (expect 1 | t)'
select count(*), bool_and(sealed = :'key1') from client_keys where client_key = :'new_key';
\echo '== the log (expect added, paused, resumed, key_replaced, paused, removed, added; all by Check Admin)'
select c.action, s.name from client_changes c join staff s on s.id = c.changed_by
where c.client_key = :'new_key' order by c.id;
\echo '== the app''s limit change is logged with the member''s Slack ID (expect 20 | 35 | U0CHECK018A)'
select old_limit, new_limit, changed_by from limit_changes where client_key = :'new_key';
\echo '== history shows all three kinds by Check Admin (expect client | 9, limit | 1, staff | 1)'
select kind, count(*) from admin_history where staff_name = 'Check Admin' group by kind order by kind;

rollback;
\echo '== done: rolled back, nothing kept'
