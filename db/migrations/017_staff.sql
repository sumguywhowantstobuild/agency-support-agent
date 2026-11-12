-- 017: the staff list moves into the database (admin app). One list decides who may sign in to the admin
-- app and who may approve drafts in Slack (until now, Slack user IDs typed into two n8n workflows). The admin app
-- logs in as admin_app and changes the list only through the admin_* functions below, each of which needs an active
-- member as "who" and logs the change. n8n only asks is_staff(). Members are never deleted, only deactivated, so the
-- history keeps their names.
-- The first member is added once by the database admin (docs/SETUP.md): nobody can sign in before the list has one.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 017_staff.sql
-- The admin_app password is set separately, never in Git (docs/SETUP.md).

insert into schema_migrations (version) values ('017');  -- a second run fails here instead of half-applying

create table staff (
  id bigint generated always as identity primary key,
  name text not null check (btrim(name) <> '' and length(name) <= 80),
  email text not null unique check (email = lower(email) and email ~ '^[^@\s]+@[^@\s]+$' and length(email) <= 254),
  slack_user_id text not null unique check (slack_user_id ~ '^[UW][A-Z0-9]{2,}$'),
  active boolean not null default true,
  added_at timestamptz not null default now()
);

-- Every change to the list: who made it, when, to whom, and the values before and after. Kept for good.
create table staff_changes (
  id bigint generated always as identity primary key,
  changed_by bigint not null references staff,
  changed_at timestamptz not null default now(),
  staff_id bigint not null references staff,
  action text not null check (action in ('added', 'edited', 'deactivated', 'reactivated')),
  before jsonb,
  after jsonb
);

comment on table staff is
  'The support team: who may sign in to the admin app and approve drafts in Slack. Changed only in the admin app; '
  'members are deactivated, never deleted.';
comment on table staff_changes is
  'Every change to the staff list: who made it, when, and the values before and after.';

create view admin_staff_list as
  select id, name, email, slack_user_id, active, added_at from staff;
comment on view admin_staff_list is 'The staff list as the admin app shows it.';

-- The member a "who" argument names, refused unless active. Every admin_* function that changes something calls it
-- first. It takes one lock that all admin changes share, held to the end of the transaction, so changes run one at a
-- time: a member being deactivated can't slip a change in alongside, and two people can't deactivate the last two
-- members at once. Admin changes are rare, so the wait is never felt; reads (sign-in, is_staff) don't take it.
create function admin_actor(p_by bigint)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp  -- a function with the admin's rights must not pick up anyone else's objects
as $$
begin
  perform pg_advisory_xact_lock(hashtext('support-agent admin changes'));
  if not exists (select 1 from staff s where s.id = p_by and s.active) then
    raise exception 'admin: only an active staff member can make changes';
  end if;
  return p_by;
end;
$$;

-- Who is signing in: the active member with this email (any capitals), or no row.
create function admin_staff_for_email(p_email text)
returns table (id bigint, name text, slack_user_id text)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select s.id, s.name, s.slack_user_id from staff s where s.email = lower(btrim(p_email)) and s.active;
$$;

create function admin_add_staff(p_by bigint, p_name text, p_email text, p_slack_user_id text)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_id bigint;
begin
  perform admin_actor(p_by);
  insert into staff (name, email, slack_user_id)
  values (btrim(p_name), lower(btrim(p_email)), btrim(p_slack_user_id))
  returning staff.id into v_id;
  insert into staff_changes (changed_by, staff_id, action, after)
  select p_by, v_id, 'added', jsonb_build_object('name', s.name, 'email', s.email, 'slack_user_id', s.slack_user_id)
  from staff s where s.id = v_id;
  return v_id;
end;
$$;

create function admin_edit_staff(p_by bigint, p_id bigint, p_name text, p_email text, p_slack_user_id text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_before jsonb;
  v_after jsonb;
begin
  perform admin_actor(p_by);
  select jsonb_build_object('name', s.name, 'email', s.email, 'slack_user_id', s.slack_user_id) into v_before
  from staff s where s.id = p_id for update;
  if not found then
    raise exception 'admin_edit_staff: unknown staff member';
  end if;
  update staff s set name = btrim(p_name), email = lower(btrim(p_email)), slack_user_id = btrim(p_slack_user_id)
  where s.id = p_id
  returning jsonb_build_object('name', s.name, 'email', s.email, 'slack_user_id', s.slack_user_id) into v_after;
  if v_after <> v_before then
    insert into staff_changes (changed_by, staff_id, action, before, after)
    values (p_by, p_id, 'edited', v_before, v_after);
  end if;
end;
$$;

create function admin_set_staff_active(p_by bigint, p_id bigint, p_active boolean)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_was boolean;
begin
  perform admin_actor(p_by);
  if p_active is null then
    raise exception 'admin_set_staff_active: active must be true or false';
  end if;
  select s.active into v_was from staff s where s.id = p_id;
  if not found then
    raise exception 'admin_set_staff_active: unknown staff member';
  end if;
  if v_was = p_active then
    return;
  end if;
  if not p_active and (select count(*) from staff s where s.active) = 1 then
    raise exception 'admin_set_staff_active: the last active member can''t be deactivated';
  end if;
  update staff s set active = p_active where s.id = p_id;
  insert into staff_changes (changed_by, staff_id, action)
  values (p_by, p_id, case when p_active then 'reactivated' else 'deactivated' end);
end;
$$;

-- For n8n: may this Slack user approve drafts? Active members only.
create function is_staff(p_slack_user_id text)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (select 1 from staff s where s.slack_user_id = p_slack_user_id and s.active);
$$;

-- Postgres lets every login run new functions by default; close that.
revoke execute on function admin_actor(bigint) from public;
revoke execute on function admin_staff_for_email(text) from public;
revoke execute on function admin_add_staff(bigint, text, text, text) from public;
revoke execute on function admin_edit_staff(bigint, bigint, text, text, text) from public;
revoke execute on function admin_set_staff_active(bigint, bigint, boolean) from public;
revoke execute on function is_staff(text) from public;

-- The admin app's login: the staff functions and the list view, nothing else.
create role admin_app login;
alter role admin_app set statement_timeout = '5s';  -- a stuck query fails fast instead of holding up a page

do $$
begin
  execute format('grant connect on database %I to admin_app', current_database());
end $$;

grant usage on schema public to admin_app;
grant select on admin_staff_list to admin_app;
grant execute on function admin_staff_for_email(text) to admin_app;
grant execute on function admin_add_staff(bigint, text, text, text) to admin_app;
grant execute on function admin_edit_staff(bigint, bigint, text, text, text) to admin_app;
grant execute on function admin_set_staff_active(bigint, bigint, boolean) to admin_app;

grant execute on function is_staff(text) to n8n_support;
