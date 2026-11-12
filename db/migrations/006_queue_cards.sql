-- 006: the support-queue card. Each draft remembers where its card sits in #support-queue, so a newer draft for
-- the same ticket can mark the older card "replaced" (no buttons). A replaced draft can't be approved: the buttons'
-- "only if still pending" rule refuses it.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 006_queue_cards.sql

insert into schema_migrations (version) values ('006');  -- a second run fails here instead of half-applying

alter table drafts add column queue_ts text;  -- the card's Slack message time in #support-queue

alter table drafts drop constraint drafts_status_check;
alter table drafts add constraint drafts_status_check
  check (status in ('pending', 'approved', 'edited', 'taken_over', 'agent_unavailable', 'replaced'));