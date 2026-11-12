-- 014: past tickets keep no customer and don't live forever. Past tickets are searched
-- for every client, so save_past_ticket() now also refuses a draft the agent marked as naming a customer GHL showed
-- (keep_out_of_past_tickets), and delete_old_past_tickets() removes past tickets 12 months after they were saved.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 014_past_ticket_privacy.sql

insert into schema_migrations (version) values ('014');  -- a second run fails here instead of half-applying

-- Same as 004 plus the customer mark. "create or replace" keeps the grants: only n8n_support can run it.
create or replace function save_past_ticket(
  p_ticket_id uuid,
  p_title text,
  p_content text,
  p_url text,
  p_embedding vector(1536)
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
begin
  if p_ticket_id is null or p_embedding is null
     or coalesce(btrim(p_title), '') = '' or coalesce(btrim(p_content), '') = '' then
    raise exception 'save_past_ticket: ticket id, title, content and embedding are required';
  end if;
  if not exists (select 1 from tickets where id = p_ticket_id) then
    raise exception 'save_past_ticket: unknown ticket';
  end if;
  -- The agent compares the cause, fix steps and reply with the customers GHL showed it; the database never sees
  -- those names. Approval only ever decides a ticket's newest draft, so that is the one to ask.
  if (select (d.draft->>'keep_out_of_past_tickets')::boolean from drafts d
      where d.ticket_id = p_ticket_id order by d.created_at desc limit 1) then
    raise exception 'save_past_ticket: the answer names one of the client''s customers, so other clients must never see it';
  end if;
  -- Phone: 10+ digits in a row, allowing spaces, dots, dashes and brackets. Dates (2026-10-08) are set aside
  -- first, so "2026-10-08 09:15" isn't mistaken for one.
  if (p_title || ' ' || p_content) ~* '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'
     or regexp_replace(p_title || ' ' || p_content, '\d{4}-\d{2}-\d{2}', 'date', 'g') ~ '(\d[\s().-]*){10,}' then
    raise exception 'save_past_ticket: text still holds an email address or a phone number';
  end if;

  delete from documents
  where metadata->>'source' = 'past_ticket' and metadata->>'doc_id' = 'ticket:' || p_ticket_id;

  insert into documents (content, metadata, embedding)
  values (
    p_content,
    jsonb_build_object(
      'doc_id', 'ticket:' || p_ticket_id, 'title', p_title, 'heading', 'Problem, cause and fix',
      'url', coalesce(p_url, ''), 'source', 'past_ticket', 'updated_at', now()::text),
    p_embedding);
end;
$$;

-- Run nightly by the "Ticket cleanup" workflow, after delete_old_tickets(). Saving the same ticket again resets its
-- date. A past ticket with no readable date is removed too: none should exist, and keeping it would mean forever.
create function delete_old_past_tickets()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  removed integer;
begin
  delete from documents
  where metadata->>'source' = 'past_ticket'
    and case when pg_input_is_valid(coalesce(metadata->>'updated_at', ''), 'timestamptz')  -- case: never cast junk
             then (metadata->>'updated_at')::timestamptz < now() - interval '12 months'
             else true end;
  get diagnostics removed = row_count;
  return removed;
end;
$$;

revoke execute on function delete_old_past_tickets() from public;
grant execute on function delete_old_past_tickets() to n8n_support;

comment on function delete_old_past_tickets() is
  'Deletes past tickets saved more than 12 months ago (or with no readable date). Run nightly by Ticket cleanup.';
