-- 019: the accuracy test finds its clients in the database, now that GHL keys no longer sit in .env.
-- The agent's read-only login gets one function listing the active clients' HighLevel locations, not the client
-- table itself.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 019_active_client_locations.sql

insert into schema_migrations (version) values ('019');  -- a second run fails here instead of half-applying

create function active_client_locations()
returns setof text
language sql
stable
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
  select c.location_id from client_map c where c.status = 'active' order by c.location_id;
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function active_client_locations() from public;
grant execute on function active_client_locations() to agent_reader;
