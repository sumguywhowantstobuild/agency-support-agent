-- Check for migration 017: the staff list. admin_app changes it only through the admin_* functions, every change is
-- logged with who made it, the last active member can't be deactivated, and n8n_support only asks is_staff().
-- Runs in one transaction that is rolled back, so nothing is left behind. Each "can't" line must print an ERROR.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/017_staff.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): the first member, as SETUP adds them'
insert into staff (name, email, slack_user_id) values ('Check Admin', 'check.admin@example.com', 'U0CHECK017A')
returning id as first \gset

set local role admin_app;

\echo '== can: the first member signs in (expect their id | Check Admin | U0CHECK017A)'
select * from admin_staff_for_email('Check.Admin@Example.com');
\echo '== can: add a member (expect one id)'
select admin_add_staff(:first, 'Check Member', 'Check.Member@Example.com', 'U0CHECK017B') as second \gset
select :second as second;
\echo '== the email is kept lower-case (expect check.member@example.com | t)'
select email, active from admin_staff_list where id = :second;
\echo '== can: edit them (expect Check Member Two)'
select admin_edit_staff(:first, :second, 'Check Member Two', 'check.member@example.com', 'U0CHECK017B');
select name from admin_staff_list where id = :second;
\echo '== can: deactivate them; they can no longer sign in (expect no row)'
select admin_set_staff_active(:first, :second, false);
select * from admin_staff_for_email('check.member@example.com');

\echo '== cannot (each must print ERROR):'
\echo '-- a deactivated member makes a change'
savepoint s;
select admin_add_staff(:second, 'Someone', 'someone@example.com', 'U0CHECK017C');
rollback to s;
\echo '-- deactivate the last active member'
savepoint s;
select admin_set_staff_active(:first, :first, false);
rollback to s;
\echo '-- add an email already on the list, in other capitals'
savepoint s;
select admin_add_staff(:first, 'Copy', 'CHECK.ADMIN@example.com', 'U0CHECK017D');
rollback to s;
\echo '-- add a Slack ID already on the list'
savepoint s;
select admin_add_staff(:first, 'Copy', 'copy@example.com', 'U0CHECK017A');
rollback to s;
\echo '-- a Slack ID that is not one'
savepoint s;
select admin_add_staff(:first, 'Bob', 'bob@example.com', 'bob');
rollback to s;
\echo '-- an empty name'
savepoint s;
select admin_add_staff(:first, ' ', 'empty@example.com', 'U0CHECK017E');
rollback to s;
\echo '-- write the list directly'
savepoint s;
insert into staff (name, email, slack_user_id) values ('Direct', 'direct@example.com', 'U0CHECK017F');
rollback to s;
\echo '-- read the change log directly'
savepoint s;
select * from staff_changes;
rollback to s;

\echo '== can: reactivate them (expect their id | Check Member Two | U0CHECK017B)'
select admin_set_staff_active(:first, :second, true);
select * from admin_staff_for_email('check.member@example.com');

\echo '== deactivate them again, for the n8n check below'
select admin_set_staff_active(:first, :second, false);

reset role;
set local role n8n_support;
\echo '== n8n: is_staff for an active member, a deactivated one, an unknown ID, nothing (expect t | f | f | f)'
select is_staff('U0CHECK017A'), is_staff('U0CHECK017B'), is_staff('U0NOBODY'), is_staff(null);
\echo '-- n8n changes the list (must print ERROR)'
savepoint s;
select admin_add_staff(:first, 'N8n', 'n8n@example.com', 'U0CHECK017G');
rollback to s;
\echo '-- n8n reads the list (must print ERROR)'
savepoint s;
select * from staff;
rollback to s;

reset role;
\echo '== as admin: the log (expect added, edited, deactivated, reactivated, deactivated, all by Check Admin)'
select c.action, s.name as by_whom from staff_changes c join staff s on s.id = c.changed_by
where c.staff_id = :second order by c.id;
\echo '== the edit kept both sides (expect Check Member | Check Member Two | the same email both sides)'
select before->>'name' as name_before, after->>'name' as name_after, before->>'email' as email_before,
       after->>'email' as email_after
from staff_changes where staff_id = :second and action = 'edited';
\echo '== the add kept the new values (expect Check Member | check.member@example.com | U0CHECK017B)'
select after->>'name', after->>'email', after->>'slack_user_id' from staff_changes
where staff_id = :second and action = 'added';

rollback;
\echo '== done: rolled back, nothing kept'
