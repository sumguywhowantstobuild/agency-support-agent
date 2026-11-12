-- Check for migration 015: save_past_ticket() takes the approved draft's id and checks that draft's mark, even when
-- a newer draft exists for the same ticket; it saves only approved or edited drafts, and only n8n_support can run it.
-- Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/015_save_approved_draft.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

-- Two made-up tickets, each with an older decided draft and a newer waiting one (a follow-up's draft):
--   race:  older approved draft names a customer, newer waiting draft is clean
--   clean: older edited draft is clean, newer waiting draft names a customer
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select 'check-015-' || k, channel_id, 'check-015-' || k, client_key, location_id, 'check ticket'
from (select * from client_map order by client_key limit 1) c, (values ('race'), ('clean')) v(k);
select id as race_ticket from tickets where slack_event_id = 'check-015-race' \gset
select id as clean_ticket from tickets where slack_event_id = 'check-015-clean' \gset
insert into drafts (ticket_id, draft, status, decided_by, decided_at, created_at) values
  (:'race_ticket', '{"keep_out_of_past_tickets": true}', 'approved', 'check', now(), now() - interval '1 hour'),
  (:'race_ticket', '{"keep_out_of_past_tickets": false}', 'pending', null, null, now()),
  (:'clean_ticket', '{"keep_out_of_past_tickets": false}', 'edited', 'check', now(), now() - interval '1 hour'),
  (:'clean_ticket', '{"keep_out_of_past_tickets": true}', 'pending', null, null, now());
select id as race_approved from drafts where ticket_id = :'race_ticket' and status = 'approved' \gset
select id as clean_edited from drafts where ticket_id = :'clean_ticket' and status = 'edited' \gset
select id as clean_waiting from drafts where ticket_id = :'clean_ticket' and status = 'pending' \gset

set local role n8n_support;

\echo '== can: save an edited clean draft although a newer draft names a customer (expect a blank line, no ERROR)'
select save_past_ticket(:'clean_edited', 'Check title', 'Cause: x. Fix: y.', '', array_fill(0.01::real, array[1536])::vector);

\echo '== cannot (each must print ERROR):'
\echo '-- the approved draft names a customer, the newer draft is clean (expect ... names one of the client''s customers)'
savepoint s1;
select save_past_ticket(:'race_approved', 'Check title', 'Cause: x. Fix: y.', '', array_fill(0.01::real, array[1536])::vector);
rollback to s1;
\echo '-- a draft nobody approved (expect ... only a draft staff approved or edited is saved)'
savepoint s2;
select save_past_ticket(:'clean_waiting', 'Check title', 'Cause: x. Fix: y.', '', array_fill(0.01::real, array[1536])::vector);
rollback to s2;
\echo '-- a ticket id instead of a draft id, as before 015 (expect ... unknown draft)'
savepoint s3;
select save_past_ticket(:'clean_ticket', 'Check title', 'Cause: x. Fix: y.', '', array_fill(0.01::real, array[1536])::vector);
rollback to s3;
\echo '-- text holding an email address (expect ... email address or a phone number)'
savepoint s4;
select save_past_ticket(:'clean_edited', 'Check title', 'Write to jane@example.com', '', array_fill(0.01::real, array[1536])::vector);
rollback to s4;

reset role;
\echo '== as admin: one past ticket saved, for the clean ticket only (expect t | f)'
select exists (select 1 from documents where metadata->>'doc_id' = 'ticket:' || :'clean_ticket') as clean_saved,
       exists (select 1 from documents where metadata->>'doc_id' = 'ticket:' || :'race_ticket') as race_saved;
\echo '== as admin: only n8n_support may run it (expect n8n_support | t, the other three f)'
select r.rolname, has_function_privilege(r.oid, 'save_past_ticket(uuid, text, text, text, vector)', 'execute') as save
from pg_roles r where r.rolname in ('n8n_support', 'agent_reader', 'kb_sync', 'backup') order by 1;

rollback;
\echo '== done: rolled back, nothing kept'
