-- Check for migration 004: what n8n_support can and can't do. Everything runs in one transaction that is rolled
-- back, so nothing is left behind. Every "can't" line must print an ERROR.
-- The save_past_ticket lines are valid before 015 only: from 015 it takes the approved draft's id (see its check).
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/004_n8n_support_role.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;
set local role n8n_support;

\echo '== can: read the client list (expect 2)'
select count(*) from client_map;

\echo '== can: add a ticket and a draft, then approve it once (expect approved, then nothing)'
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select 'check-004', channel_id, '1.0', client_key, location_id, 'check ticket'
from client_map order by client_key limit 1
returning id as tid \gset
insert into drafts (ticket_id, draft) values (:'tid', '{}') returning id as did \gset
update drafts set status = 'approved', decided_by = 'check', decided_at = now()
where id = :'did' and status = 'pending' returning status;
update drafts set status = 'approved', decided_by = 'check', decided_at = now()
where id = :'did' and status = 'pending' returning status;

\echo '== can: refuse a repeated Slack event (expect ERROR duplicate key)'
savepoint s;
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select 'check-004', channel_id, '1.0', client_key, location_id, 'again' from client_map limit 1;
rollback to s;

\echo '== can: save a past ticket (expect a blank line, no ERROR)'
select save_past_ticket(:'tid', 'Check title', 'Problem: x. Cause: y. Fix: z.', '', array_fill(0.01::real, array[1536])::vector);

\echo '== cannot (each must print ERROR):'
\echo '-- save a past ticket holding an email'
savepoint s;
select save_past_ticket(:'tid', 'Check', 'Write to jane@example.com', '', array_fill(0.01::real, array[1536])::vector);
rollback to s;
\echo '-- save a past ticket holding a phone number'
savepoint s;
select save_past_ticket(:'tid', 'Check', 'Call (555) 123-4567 ext', '', array_fill(0.01::real, array[1536])::vector);
rollback to s;
\echo '-- read documents'
savepoint s;
select count(*) from documents;
rollback to s;
\echo '-- change a guide'
savepoint s;
update documents set content = 'x' where metadata->>'source' = 'guide';
rollback to s;
\echo '-- delete guides'
savepoint s;
delete from documents where metadata->>'source' = 'guide';
rollback to s;
\echo '-- add a document directly'
savepoint s;
insert into documents (content, metadata) values ('x', '{"source": "guide"}');
rollback to s;
\echo '-- change the client list'
savepoint s;
update client_map set location_id = 'other' where false;
rollback to s;
\echo '-- run the agent search'
savepoint s;
select count(*) from hybrid_search('x', array_fill(0::real, array[1536])::vector, 5, 'guide');
rollback to s;

\echo '== as admin: the saved past ticket is labelled past_ticket (expect past_ticket | ticket:...)'
reset role;
select metadata->>'source' as source, left(metadata->>'doc_id', 7) as doc_id from documents where metadata->>'doc_id' = 'ticket:' || :'tid';

rollback;
\echo '== done: rolled back, nothing kept'