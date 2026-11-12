-- Check for migration 006: queue cards and replaced drafts. Runs in one transaction that is rolled back, so nothing
-- is left behind. The "can't" line must print an ERROR.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/006_queue_cards.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;
set local role n8n_support;

\echo '== setup: a ticket with an older draft (card posted) and a newer one'
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
select channel_id || ':900.1', channel_id, '900.1', client_key, location_id, 'check ticket'
from client_map order by client_key limit 1
returning id as tid \gset
insert into drafts (ticket_id, draft, queue_ts, created_at) values (:'tid', '{}', '111.1', now() - interval '1 minute')
returning id as old_id \gset
insert into drafts (ticket_id, draft) values (:'tid', '{}') returning id as new_id \gset

\echo '== can: remember the new card and replace the older one (expect one row: the old card, 111.1)'
with card as (
  update drafts set queue_ts = '222.2' where id = :'new_id' returning ticket_id, created_at
),
replaced as (
  update drafts d set status = 'replaced'
  from card
  where d.ticket_id = card.ticket_id and d.id <> :'new_id'
    and d.created_at < card.created_at and d.status in ('pending', 'agent_unavailable')
  returning d.id, d.queue_ts
)
select queue_ts from replaced where queue_ts is not null;

\echo '== can: the approve rule refuses the replaced draft (expect 0 rows) and accepts the new one (expect approved)'
update drafts set status = 'approved', decided_by = 'check', decided_at = now()
where id = :'old_id' and status = 'pending' returning status;
update drafts set status = 'approved', decided_by = 'check', decided_at = now()
where id = :'new_id' and status = 'pending' returning status;

\echo '== refused (must print ERROR): a status that does not exist'
savepoint s;
update drafts set status = 'deleted' where id = :'new_id';
rollback to s;

rollback;
\echo '== done: rolled back, nothing kept'