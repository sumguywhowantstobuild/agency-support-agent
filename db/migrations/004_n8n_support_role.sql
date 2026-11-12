-- 004: a login for n8n's ticket workflows (intake, approval, clean-up). It reads the client list, keeps tickets and
-- drafts, and saves past tickets only through save_past_ticket(), which forces the label, so it can never change or
-- delete guides or help summaries.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 004_n8n_support_role.sql
-- The password is set separately, never in Git (docs/SETUP.md).

insert into schema_migrations (version) values ('004');  -- a second run fails here instead of half-applying

create role n8n_support login;
alter role n8n_support set statement_timeout = '5s';  -- a stuck query fails fast instead of holding up a ticket

do $$
begin
  execute format('grant connect on database %I to n8n_support', current_database());
end $$;

grant usage on schema public to n8n_support;
grant select on client_map to n8n_support;
grant select, insert, update, delete on tickets, drafts to n8n_support;  -- delete: the 90-day clean-up

-- Saves an approved answer as a past ticket the agent can search. It runs with the admin's rights (security
-- definer), so n8n_support needs no rights on documents at all. Saving the same ticket again replaces it.
-- Last line of defence for privacy: refuses text that still holds an email address or a phone number.
create function save_past_ticket(
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

-- Postgres lets every login run new functions by default; close that.
revoke execute on function save_past_ticket(uuid, text, text, text, vector) from public;
grant execute on function save_past_ticket(uuid, text, text, text, vector) to n8n_support;