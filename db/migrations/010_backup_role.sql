-- 010: a login for the nightly backup. It can read everything in both databases (to dump them) and
-- change nothing, except adding one row per run to backup_runs, which n8n's morning check reads to raise an alert.
-- Also closes n8n's database to every login except n8n and the backup (it was open to all, unlike this one: 002).
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 010_backup_role.sql
-- The password is set separately, never in Git (docs/SETUP.md).

insert into schema_migrations (version) values ('010');  -- a second run fails here instead of half-applying

create role backup login;
alter role backup set lock_timeout = '30s';  -- a dump waiting behind a stuck lock fails (and alerts) instead of hanging

-- pg_read_all_data: read every table, view and sequence in every database, write nothing. Exactly what a dump needs.
grant pg_read_all_data to backup;

do $$
begin
  execute format('grant connect on database %I to backup', current_database());
end $$;

-- n8n's database: only its owner (n8n) and the backup may connect from now on.
revoke connect on database n8n from public;
grant connect on database n8n to backup;

-- One row per backup try. A good run has a size and no error; a failed one must say why (short, no secrets).
-- Kept for good: at most 24 rows a day, usually one.
create table backup_runs (
  id bigint generated always as identity primary key,
  started_at timestamptz not null,
  finished_at timestamptz not null default now(),
  ok boolean not null,
  bytes bigint not null default 0,
  error text,
  check (finished_at >= started_at),
  check (bytes >= 0 and bytes <= 524288000),                -- the job's own 500 MB cap
  check (ok = (error is null)),                             -- success has no error; a failure must say why
  check (not ok or bytes > 0),                              -- a success with nothing in it isn't one
  check (error is null or char_length(error) between 1 and 300)
);

create index backup_runs_good_idx on backup_runs (finished_at desc) where ok;

-- The backup adds rows and reads them back (to decide whether a backup is due). No changes, no deletes.
grant select, insert on backup_runs to backup;
-- n8n's morning check reads them to alert on a failure or a missing night.
grant select on backup_runs to n8n_support;
