-- 022: the admin app's chart of drafts today. One view gives the time of every real draft per client
-- for the last 48 hours: the client's key and the moment, nothing of the ticket or the draft. The admin app logs in as
-- admin_app, which can read no ticket or draft table; it gets this view and nothing else, as with admin_clients and
-- admin_history (017, 018).
-- A row counts by the approval report's rule (013): the draft has a confidence, so there was a real draft, and it is not
-- test_closed. Cards for an agent outage, a limit or a paused client carry no confidence, so they stay out even when a
-- person took them over later. A removed client's drafts stay out.
-- The 48 hours are a bound on what the view can show, not the chart's day: the app asks from its own midnight, which in
-- any time zone, clock change included, is less than 48 hours back.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 022_drafts_today.sql

insert into schema_migrations (version) values ('022');  -- a second run fails here instead of half-applying

-- security_barrier: a condition the admin app puts on the view (even one that prints what it sees) runs only on rows the
-- view's own filters already let through, so excluded drafts' times can't be read that way.
create view admin_drafts_today with (security_barrier = true) as
  select t.client_key, d.created_at
  from drafts d
  join tickets t on t.id = d.ticket_id
  join client_map c on c.client_key = t.client_key
  where d.draft->>'confidence' is not null
    and d.status <> 'test_closed'
    and c.status <> 'removed'
    and d.created_at > now() - interval '48 hours';
comment on view admin_drafts_today is
  'When each real draft was made, per client, for the last 48 hours: the admin app''s chart of drafts today. No ticket '
  'or draft text. Counts as the approval report does (a confidence, not test traffic); removed clients leave.';

grant select on admin_drafts_today to admin_app;
