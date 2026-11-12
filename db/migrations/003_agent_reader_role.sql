-- 003: a read-only login for the agent: it can search the knowledge base and nothing else.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 003_agent_reader_role.sql
-- The password is set separately, never in Git (docs/SETUP.md).

insert into schema_migrations (version) values ('003');  -- a second run fails here instead of half-applying

create role agent_reader login;
alter role agent_reader set statement_timeout = '5s';  -- a stuck search fails fast instead of holding up a ticket

do $$
begin
  execute format('grant connect on database %I to agent_reader', current_database());
end $$;

grant usage on schema public to agent_reader;
grant select on documents to agent_reader;

-- Only the agent needs the search function. Postgres lets every login run new functions by default; close that.
revoke execute on function hybrid_search(text, vector, int, text, int) from public;
grant execute on function hybrid_search(text, vector, int, text, int) to agent_reader;