-- Check for migration 014: save_past_ticket() refuses a draft the agent marked as naming a customer (only the
-- ticket's newest draft counts), and delete_old_past_tickets() removes past tickets older than 12 months or with no
-- readable date, never a guide or a newer past ticket. Only n8n_support can run either.
-- Valid right after 014 only: from 015 save_past_ticket() takes the approved draft's id (its check covers saving).
-- Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/014_past_ticket_privacy.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

-- Two made-up tickets: "named" has an older clean draft and a newer marked one; "clean" has an older marked draft
-- and a newer clean one.
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select 'check-014-' || k, channel_id, 'check-014-' || k, client_key, location_id, 'check ticket'
from (select * from client_map order by client_key limit 1) c, (values ('named'), ('clean')) v(k);
select id as named_id from tickets where slack_event_id = 'check-014-named' \gset
select id as clean_id from tickets where slack_event_id = 'check-014-clean' \gset
insert into drafts (ticket_id, draft, created_at) values
  (:'named_id', '{"keep_out_of_past_tickets": false}', now() - interval '1 hour'),
  (:'named_id', '{"keep_out_of_past_tickets": true}', now()),
  (:'clean_id', '{"keep_out_of_past_tickets": true}', now() - interval '1 hour'),
  (:'clean_id', '{"keep_out_of_past_tickets": false}', now());

-- Past tickets of different ages (as admin, so their dates can be set), and an old guide that must stay.
insert into documents (content, metadata) values
  ('check 014', '{"source": "past_ticket", "doc_id": "check-014:13-months"}'::jsonb
                || jsonb_build_object('updated_at', (now() - interval '13 months')::text)),
  ('check 014', '{"source": "past_ticket", "doc_id": "check-014:11-months"}'::jsonb
                || jsonb_build_object('updated_at', (now() - interval '11 months')::text)),
  ('check 014', '{"source": "past_ticket", "doc_id": "check-014:no-date"}'),
  ('check 014', '{"source": "past_ticket", "doc_id": "check-014:junk-date", "updated_at": "last spring"}'),
  ('check 014', '{"source": "guide", "doc_id": "check-014:old-guide"}'::jsonb
                || jsonb_build_object('updated_at', (now() - interval '3 years')::text));

set local role n8n_support;

\echo '== can: save a past ticket whose newest draft is clean (expect a blank line, no ERROR)'
select save_past_ticket(:'clean_id', 'Check title', 'Cause: x. Fix: y.', '', array_fill(0.01::real, array[1536])::vector);

\echo '== cannot: save a past ticket whose newest draft names a customer (expect ERROR ... names one of the client''s customers)'
savepoint s;
select save_past_ticket(:'named_id', 'Check title', 'Cause: x. Fix: y.', '', array_fill(0.01::real, array[1536])::vector);
rollback to s;

\echo '== can: run the past-ticket clean-up (expect 3, or more if real past tickets are over 12 months old)'
select delete_old_past_tickets() as removed;

\echo '== cannot (each must print ERROR):'
\echo '-- delete a past ticket directly'
savepoint s;
delete from documents where metadata->>'source' = 'past_ticket';
rollback to s;

reset role;
\echo '== as admin: which check documents are left (expect check-014:11-months, check-014:old-guide)'
select metadata->>'doc_id' as kept from documents where metadata->>'doc_id' like 'check-014:%' order by 1;
\echo '== as admin: the clean ticket was saved, the named one was not (expect t | f)'
select exists (select 1 from documents where metadata->>'doc_id' = 'ticket:' || :'clean_id') as clean_saved,
       exists (select 1 from documents where metadata->>'doc_id' = 'ticket:' || :'named_id') as named_saved;
\echo '== as admin: only n8n_support may run the two functions (expect n8n_support | t | t, others f | f)'
select r.rolname,
       has_function_privilege(r.oid, 'save_past_ticket(uuid, text, text, text, vector)', 'execute') as save,
       has_function_privilege(r.oid, 'delete_old_past_tickets()', 'execute') as clean_up
from pg_roles r where r.rolname in ('n8n_support', 'agent_reader', 'kb_sync', 'backup') order by 1;

rollback;
\echo '== done: rolled back, nothing kept'
