-- 005: follow-up messages. A client's replies in a ticket's thread are stored one row each, so a message Slack
-- delivers twice is refused instead of being added (and drafted) twice. The opening message stays in tickets.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 005_ticket_messages.sql

insert into schema_migrations (version) values ('005');  -- a second run fails here instead of half-applying

-- One ticket per Slack thread: a reply finds its ticket by channel + thread, and a second ticket for the same
-- thread is refused.
create unique index tickets_thread_idx on tickets (channel_id, thread_ts);

create table ticket_messages (
  id uuid primary key default gen_random_uuid(),
  ticket_id uuid not null references tickets on delete cascade,  -- the 90-day clean-up removes them with the ticket
  slack_key text not null unique,  -- channel:message time, unique per Slack message
  slack_user text not null,
  text text not null,
  created_at timestamptz not null default now()
);
create index ticket_messages_ticket_id_idx on ticket_messages (ticket_id, created_at);

-- Add and read only: messages are never edited, and they leave only with their ticket.
grant select, insert on ticket_messages to n8n_support;