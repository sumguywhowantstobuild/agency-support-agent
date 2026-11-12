-- Check for migration 020: admin_history names the client by its key, and keeps a pause's reason and note apart, so
-- a client sheet shows only that client's changes (two clients can share a name) and Undo after Resume can pause
-- again as it was. Runs in one transaction that is rolled back.
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/020_history_by_client.sql
\set ON_ERROR_STOP 0
\set QUIET 1
begin;

\echo '== setup (as admin): a staff member and two new clients with the same name'
insert into staff (name, email, slack_user_id) values ('Check Admin', 'check.admin20@example.com', 'U0CHECK020A')
returning id as me \gset
select decode(repeat('ab', 60), 'hex') as key1 \gset

set local role admin_app;
select admin_add_client(:me, 'C0CHECK020', 'Same Name Co', 'LOCCHECK020', 20, :'key1') as first \gset
select admin_add_client(:me, 'C0CHECK020B', 'Same Name Co', 'LOCCHECK020B', 20, :'key1') as second \gset
select admin_pause_client(:me, :'first', 'privacy_hold', 'waiting: for the BAA');
select admin_resume_client(:me, :'first');
select admin_set_draft_limit(:me, :'first', 35);

\echo '== the first client''s history by its key (one transaction, one moment, so by action; expect 4 rows, all t:'
\echo '   added | | ; limit_changed | | ; paused | privacy_hold | waiting: for the BAA ; resumed | | )'
select action, reason, note, client_key = :'first' as its_key from admin_history
where client_key = :'first' order by action;
\echo '== the second client, same name, has only its own add (expect 1)'
select count(*) from admin_history where client_key = :'second';
\echo '== by name alone both would mix (expect 5)'
select count(*) from admin_history where subject = 'Same Name Co';
\echo '== no staff change names a client (expect 0)'
select count(*) from admin_history where kind = 'staff' and client_key is not null;
\echo '== the old columns are unchanged (expect changed_at,staff_name,kind,subject,action,detail,client_key,reason,note)'
select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns
where table_name = 'admin_history';

rollback;
