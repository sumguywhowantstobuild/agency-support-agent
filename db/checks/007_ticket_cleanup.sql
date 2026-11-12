-- Check for migration 007: the clean-up removes only tickets idle for 90 days, with their drafts and follow-ups,
-- leaves one row of numbers per draft in ticket_stats, and n8n_support can't delete anything directly.
-- Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/007_ticket_cleanup.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

-- Three made-up tickets (as admin, so their dates can be set in the past):
--   old: opened 100 days ago, drafted 24 s later, approved 3 min after that, one follow-up -> removed, stats kept
--   busy: opened 100 days ago, a follow-up 10 days ago                                    -> kept (still active)
--   new: opened today                                                                     -> kept
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text, created_at)
select 'check-007-' || k, channel_id, 'check-007-' || k, client_key, location_id, 'check ticket', now() - age
from (select * from client_map order by client_key limit 1) c,
     (values ('old', interval '100 days'), ('busy', interval '100 days'), ('new', interval '0 days')) v(k, age);
select id as old_id from tickets where slack_event_id = 'check-007-old' \gset
select id as busy_id from tickets where slack_event_id = 'check-007-busy' \gset
insert into drafts (ticket_id, draft, status, decided_by, decided_at, created_at) values (
  :'old_id',
  '{"confidence": "medium", "needs_person": false, "client_reply": "secret words",
    "sources": [{"title": "Check guide", "url": "x"}]}',
  'approved', 'check',
  now() - interval '100 days' + interval '24 seconds' + interval '3 minutes',
  now() - interval '100 days' + interval '24 seconds');
insert into ticket_messages (ticket_id, slack_key, slack_user, text, created_at) values
  (:'old_id', 'check-007:old', 'U0', 'old follow-up', now() - interval '99 days'),
  (:'busy_id', 'check-007:busy', 'U0', 'recent follow-up', now() - interval '10 days');

set local role n8n_support;

\echo '== can: run the clean-up (expect 1, or more if real tickets are over 90 days old)'
select delete_old_tickets() as removed;

\echo '== cannot (each must print ERROR):'
\echo '-- delete a ticket directly'
savepoint s;
delete from tickets where slack_event_id = 'check-007-new';
rollback to s;
\echo '-- delete a draft directly'
savepoint s;
delete from drafts where false;
rollback to s;

reset role;
\echo '== as admin: which check tickets are left (expect busy, new)'
select replace(slack_event_id, 'check-007-', '') as kept from tickets where slack_event_id like 'check-007-%' order by 1;
\echo '== as admin: the old ticket''s draft and follow-up went with it (expect 0 | 0)'
select (select count(*) from drafts where ticket_id = :'old_id') as drafts,
       (select count(*) from ticket_messages where ticket_id = :'old_id') as follow_ups;
\echo '== as admin: its numbers were kept (expect Check guide | medium | f | approved | 24 | 180 | 1)'
select main_source, confidence, needs_person, status, seconds_to_draft, seconds_to_decision, follow_ups
from ticket_stats where ticket_id = :'old_id';
\echo '== as admin: ticket_stats has no text columns from the ticket (expect no text, reply or message column)'
select string_agg(column_name, ', ' order by ordinal_position) from information_schema.columns
where table_name = 'ticket_stats';

rollback;
\echo '== done: rolled back, nothing kept'
