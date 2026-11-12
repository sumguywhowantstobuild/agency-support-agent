-- Check for migration 021: a paused client's message gets a "client paused" card of its own (no AI), which a person can
-- take over like a "limit reached" card. Runs in one transaction that is rolled back. Each "can't" line must print an
-- ERROR.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/021_client_paused_cards.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): a paused client'
insert into client_map (channel_id, client_key, display_name, location_id, status, pause_reason)
values ('C0CHECK021', 'check-021', 'Check Paused', 'LOCCHECK021', 'paused', 'privacy_hold');

set local role n8n_support;
\echo '== can: record the ticket and its "client paused" card (expect client_paused)'
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
values ('C0CHECK021:1.1', 'C0CHECK021', '1.1', 'check-021', 'LOCCHECK021', 'check message')
returning id as ticket \gset
insert into drafts (ticket_id, draft, status)
values (:'ticket', jsonb_build_object('paused', 'privacy_hold'), 'client_paused')
returning status;
\echo '== can: a person takes it over (expect taken_over)'
update drafts set status = 'taken_over', decided_by = 'U0CHECK021', decided_at = now()
where ticket_id = :'ticket' returning status;
\echo '-- a made-up status (must print ERROR)'
savepoint s;
insert into drafts (ticket_id, draft, status) values (:'ticket', '{}', 'made_up');
rollback to s;

rollback;
\echo '== done: rolled back, nothing kept'
