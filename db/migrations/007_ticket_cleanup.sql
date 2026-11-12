-- 007: the 90-day clean-up (ticket logs are kept 90 days), keeping the numbers but not the words.
-- n8n_support loses the general delete rights 004 gave it for this job (no workflow used them) and gets one function
-- instead: it removes only tickets with no activity for 90 days. Their drafts and follow-ups go with them (on delete
-- cascade), but each draft first leaves one row of numbers in ticket_stats: no message text, names or replies.
-- Past tickets in documents stay as they are.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 007_ticket_cleanup.sql

insert into schema_migrations (version) values ('007');  -- a second run fails here instead of half-applying

revoke delete on tickets, drafts from n8n_support;

-- One row of numbers per draft, nothing the client wrote. Defined once: reports read it for recent tickets, and the
-- clean-up copies it into ticket_stats before a ticket is deleted.
create view draft_stats as
select
  d.id as draft_id,
  d.ticket_id,  -- groups the drafts of one ticket; points at nothing once the ticket is gone
  t.client_key,
  nullif(d.draft #>> '{sources,0,title}', '') as main_source,  -- first source the draft cited: the problem type for now
  d.draft->>'confidence' as confidence,
  (d.draft->>'needs_person')::boolean as needs_person,
  d.status,
  d.created_at as drafted_at,
  -- from the client's message (the opening one, or the follow-up that started this draft) to the draft
  extract(epoch from d.created_at - greatest(
    t.created_at,
    (select max(m.created_at) from ticket_messages m where m.ticket_id = t.id and m.created_at <= d.created_at)
  ))::integer as seconds_to_draft,
  extract(epoch from d.decided_at - d.created_at)::integer as seconds_to_decision,  -- null if no one decided
  (select count(*) from ticket_messages m where m.ticket_id = t.id)::integer as follow_ups
from drafts d
join tickets t on t.id = d.ticket_id;

-- The archive: same columns as the view, kept for good.
create table ticket_stats as select * from draft_stats with no data;
alter table ticket_stats add primary key (draft_id);  -- a draft can't be archived twice

-- The 90 days live here, not in n8n, so no workflow step can pass a smaller number. "Activity" is the newest of:
-- the ticket, any follow-up, any draft or decision, so a thread still in use is never cut off midway.
-- At most 5000 per run keeps each call inside n8n_support's 5-second limit; the next daily run takes the rest.
create function delete_old_tickets()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
declare
  cutoff timestamptz := now() - interval '90 days';
  removed integer;
begin
  -- One statement: the numbers are copied and the ticket deleted together, or neither happens.
  with old as (
    select t.id
    from tickets t
    where t.created_at < cutoff
      and not exists (select 1 from ticket_messages m where m.ticket_id = t.id and m.created_at >= cutoff)
      and not exists (
        select 1 from drafts d
        where d.ticket_id = t.id and greatest(d.created_at, d.decided_at) >= cutoff)
    order by t.created_at
    limit 5000
  ),
  archived as (
    insert into ticket_stats
    select s.* from draft_stats s join old on old.id = s.ticket_id
    on conflict (draft_id) do nothing
  )
  delete from tickets t using old where t.id = old.id;
  get diagnostics removed = row_count;
  return removed;
end;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function delete_old_tickets() from public;
grant execute on function delete_old_tickets() to n8n_support;
