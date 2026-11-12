-- Check for migration 005: follow-up messages. Runs in one transaction that is rolled back, so nothing is left
-- behind. Every "can't" line must print an ERROR.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/005_ticket_messages.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;
set local role n8n_support;

\echo '== can: open a ticket and add a follow-up (expect 1)'
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select channel_id || ':100.1', channel_id, '100.1', client_key, location_id, 'opening message'
from client_map order by client_key limit 1
returning id as tid, channel_id as ch \gset
insert into ticket_messages (ticket_id, slack_key, slack_user, text)
values (:'tid', :'ch' || ':100.2', 'U_CLIENT', 'follow-up');
select count(*) from ticket_messages where ticket_id = :'tid';

\echo '== can: find the ticket from a reply in its thread (expect opening message)'
select text from tickets where channel_id = :'ch' and thread_ts = '100.1';

\echo '== refused (each must print ERROR):'
\echo '-- the same follow-up delivered twice'
savepoint s;
insert into ticket_messages (ticket_id, slack_key, slack_user, text) values (:'tid', :'ch' || ':100.2', 'U_CLIENT', 'again');
rollback to s;
\echo '-- a second ticket for the same thread'
savepoint s;
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select channel_id || ':100.3', channel_id, '100.1', client_key, location_id, 'x' from client_map where channel_id = :'ch';
rollback to s;
\echo '-- edit a message'
savepoint s;
update ticket_messages set text = 'changed' where ticket_id = :'tid';
rollback to s;
\echo '-- delete a message on its own'
savepoint s;
delete from ticket_messages where ticket_id = :'tid';
rollback to s;

\echo '== can: the clean-up deletes the ticket, and its messages go with it (expect 0)'
delete from tickets where id = :'tid';
select count(*) from ticket_messages where ticket_id = :'tid';

rollback;
\echo '== done: rolled back, nothing kept'