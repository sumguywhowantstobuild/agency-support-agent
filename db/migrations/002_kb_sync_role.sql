-- 002: a login for n8n's knowledge sync that can only touch the documents table.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 002_kb_sync_role.sql
-- The password is set separately, never in Git (docs/SETUP.md).

insert into schema_migrations (version) values ('002');  -- a second run fails here instead of half-applying

create role kb_sync login;

-- Only roles granted here (and the admin) may connect. Without this, every login on the server could connect.
do $$
begin
  execute format('revoke connect on database %I from public', current_database());
  execute format('grant connect on database %I to kb_sync', current_database());
end $$;

grant usage on schema public to kb_sync;
grant select, insert, update, delete on documents to kb_sync;