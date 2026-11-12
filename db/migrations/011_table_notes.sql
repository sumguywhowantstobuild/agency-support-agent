-- 011: a plain-words description on every table and view, so anyone opening the database sees what each one holds
-- (goes live through the test copy routine, docs/SETUP.md section 12).
-- Changes no data and nothing about how the system runs.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 011_table_notes.sql

insert into schema_migrations (version) values ('011');  -- a second run fails here instead of half-applying

comment on table schema_migrations is
  'Which numbered database changes (db/migrations) have been applied. Each change adds its own row first.';
comment on table client_map is
  'Which Slack channel belongs to which client and GHL sub-account, and each client''s drafts-per-hour limit. '
  'n8n picks the client from here, never the AI.';
comment on table documents is
  'What the agent searches: agency guides, GHL help summaries and approved past tickets, split into passages with '
  'their embeddings. Written by the hourly KB sync and by the approval workflow.';
comment on table tickets is
  'One row per client conversation (one Slack thread): the client, the channel and the first message. Deleted 90 days '
  'after the last activity (delete_old_tickets); its numbers are kept in ticket_stats.';
comment on table ticket_messages is
  'Every client message in a ticket''s thread, in order. Deleted with its ticket.';
comment on table drafts is
  'The agent''s drafts for each ticket and what the support team did with them (approved, edited, taken over), by '
  'whom and when. Deleted with its ticket.';
comment on view draft_stats is
  'Numbers only, one row per draft (no client text): what reports read for recent tickets.';
comment on table ticket_stats is
  'The kept archive of draft_stats: numbers only, copied here before a ticket is deleted. Kept for good.';
comment on table draft_claims is
  'One row per draft slot asked for: which client, when, allowed or over the limit. No text. Rows older than a day '
  'are removed automatically.';
comment on table limit_changes is
  'Every change to a client''s drafts-per-hour limit: old and new value, who (Slack user ID) and when. Kept for good.';
comment on table backup_runs is
  'One row per nightly backup try: when, whether it worked, its size, or why it failed. Read by the morning check.';
