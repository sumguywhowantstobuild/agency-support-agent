-- 021: a paused client's message gets a card of its own, "client paused": no AI call, a person answers
-- by hand and takes it over like a "limit reached" card. Its own status, so reports and checks can tell it apart from
-- a limit or an agent outage. The approval report already counts a card with no draft under no_draft.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 021_client_paused_cards.sql

insert into schema_migrations (version) values ('021');  -- a second run fails here instead of half-applying

alter table drafts drop constraint drafts_status_check;
alter table drafts add constraint drafts_status_check
  check (status in ('pending', 'approved', 'edited', 'taken_over', 'agent_unavailable', 'replaced', 'limit_reached',
                    'test_closed', 'client_paused'));
