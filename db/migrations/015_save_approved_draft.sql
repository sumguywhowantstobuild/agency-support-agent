-- 015: save_past_ticket() checks the draft that was approved, not the ticket's newest draft.
-- With 014 it looked up the ticket's newest draft, so a follow-up's draft created between the
-- click and the save could hide the approved draft's "names a customer" mark. It now takes the approved draft's id,
-- finds its ticket itself, and saves only a draft staff approved or edited.
-- Goes live together with "Ticket approval" (its "Save past ticket" step sends the draft id), when no tickets are
-- expected. Either order fails safe: until both are live, a save is refused and the card says "not saved".
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 015_save_approved_draft.sql

insert into schema_migrations (version) values ('015');  -- a second run fails here instead of half-applying

-- Same argument types, new meaning (draft id, not ticket id): drop, so no caller can keep the old meaning by mistake.
drop function save_past_ticket(uuid, text, text, text, vector);

create function save_past_ticket(
  p_draft_id uuid,
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
declare
  v_ticket_id uuid;
  v_status text;
  v_names_customer boolean;
begin
  if p_draft_id is null or p_embedding is null
     or coalesce(btrim(p_title), '') = '' or coalesce(btrim(p_content), '') = '' then
    raise exception 'save_past_ticket: draft id, title, content and embedding are required';
  end if;
  select d.ticket_id, d.status, coalesce((d.draft->>'keep_out_of_past_tickets')::boolean, false)
  into v_ticket_id, v_status, v_names_customer
  from drafts d where d.id = p_draft_id;
  if not found then
    raise exception 'save_past_ticket: unknown draft';
  end if;
  if v_status not in ('approved', 'edited') then
    raise exception 'save_past_ticket: only a draft staff approved or edited is saved';
  end if;
  -- The agent compares the cause, fix steps and reply with the customers GHL showed it; the database never sees
  -- those names.
  if v_names_customer then
    raise exception 'save_past_ticket: the answer names one of the client''s customers, so other clients must never see it';
  end if;
  -- Phone: 10+ digits in a row, allowing spaces, dots, dashes and brackets. Dates (2026-10-08) are set aside
  -- first, so "2026-10-08 09:15" isn't mistaken for one.
  if (p_title || ' ' || p_content) ~* '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'
     or regexp_replace(p_title || ' ' || p_content, '\d{4}-\d{2}-\d{2}', 'date', 'g') ~ '(\d[\s().-]*){10,}' then
    raise exception 'save_past_ticket: text still holds an email address or a phone number';
  end if;

  delete from documents
  where metadata->>'source' = 'past_ticket' and metadata->>'doc_id' = 'ticket:' || v_ticket_id;

  insert into documents (content, metadata, embedding)
  values (
    p_content,
    jsonb_build_object(
      'doc_id', 'ticket:' || v_ticket_id, 'title', p_title, 'heading', 'Problem, cause and fix',
      'url', coalesce(p_url, ''), 'source', 'past_ticket', 'updated_at', now()::text),
    p_embedding);
end;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function save_past_ticket(uuid, text, text, text, vector) from public;
grant execute on function save_past_ticket(uuid, text, text, text, vector) to n8n_support;

comment on function save_past_ticket(uuid, text, text, text, vector) is
  'Saves an approved or edited draft as a past ticket (by draft id). Refuses drafts the agent marked as naming a '
  'customer, and any text with an email or phone. The only way n8n can add a past ticket.';
