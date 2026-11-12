-- Check for migration 024: a client moves to another Slack channel only through admin_change_channel, and only the
-- admin login can call it. Tickets keep the channel they were written in and stay the client's by client_key
-- (tickets_client_key_fkey replaces tickets_channel_id_fkey). A channel belongs to one client, ever: client_channels
-- records every channel a client has had, written only by admin_change_channel and admin_add_client, readable by the
-- admin login, by nobody else. Runs in one transaction that is rolled back.
-- Every check prints one line, "NOTICE:  PASS ..." or "NOTICE:  FAIL ...", and a missing refusal is a FAIL, never a
-- quiet line. (The "cannot" lines name the refusal they expect, so a wrong refusal, such as "function does not exist"
-- before the migration, fails too.) Run it, then search the output for "NOTICE:  FAIL": there must be none.
-- It writes made-up clients as the database admin, so it is for the dev database (rolled back) or a throwaway one.
-- Until the migration is applied for real, run it with the migration in the SAME transaction, so nothing persists:
--   { echo 'begin;'; cat db/migrations/024_change_channel.sql db/checks/024_change_channel.sql; } | docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge
-- (the "there is already a transaction in progress" warning from this file's own begin is expected there). After the
-- release, run it alone:
--   docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/024_change_channel.sql
-- The migration's own refusal of a ticket that doesn't match its channel's client is checked with -v refuse=1, the
-- check first and the migration after it, still in the transaction the check opens (it ends with a rollback):
--   { cat db/checks/024_change_channel.sql db/migrations/024_change_channel.sql; echo 'rollback;'; } | docker compose -f compose.dev.yaml exec -T postgres psql -v refuse=1 -U postgres -d knowledge
-- That run must print the migration's "024: ... does not match" error and then nothing but "transaction is aborted".
-- Any unexpected error stops the run (expected refusals are caught inside the helpers and print PASS), so a broken
-- migration can't end in a quiet "no FAIL": a passing run ends with the "== done" line and psql exits 0. Before the
-- migration it stops at the first missing piece. Only the -v refuse=1 run lets errors through, as it expects them.
\if :{?refuse}
\set ON_ERROR_STOP 0
\else
\set ON_ERROR_STOP 1
\endif
\set QUIET 1
\set VERBOSITY terse
\t on
\pset format unaligned

\if :{?refuse}
begin;
\echo '== setup (as admin): a ticket that names another client than its channel does'
insert into client_map (channel_id, client_key, display_name, location_id)
values ('C0CHK024M1', 'check-024-m1', 'Check 024 M1', 'LOCCHK024M1'),
       ('C0CHK024M2', 'check-024-m2', 'Check 024 M2', 'LOCCHK024M2');
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
values ('chk024-mismatch', 'C0CHK024M1', '1700000000.000900', 'check-024-m2', 'LOCCHK024M2', 'Check ticket');
\echo '== the migration must now stop with an ERROR that says "does not match" (see its output below)'
\else

begin;

\echo '== helpers (they vanish with the rollback): each prints one PASS or FAIL line'
create function check_024_me() returns bigint language sql security definer as
$f$ select id from staff where email = 'check.admin24@example.com' $f$;

create function check_024_ok(p_name text, p_sql text) returns void language plpgsql as $f$
begin
  execute p_sql;
  raise notice 'PASS  %', p_name;
exception when others then
  raise notice 'FAIL  %: %', p_name, sqlerrm;
end
$f$;

create function check_024_refused(p_name text, p_sql text, p_like text) returns void language plpgsql as $f$
declare
  v_msg text;
begin
  begin
    execute p_sql;
    raise exception 'ALLOWED';  -- undoes what an allowed statement did, so one failure doesn't spoil the next tests
  exception when others then
    v_msg := sqlerrm;
  end;
  if v_msg = 'ALLOWED' then
    raise notice 'FAIL  %: it was allowed', p_name;
  elsif v_msg like p_like then
    raise notice 'PASS  %  (%)', p_name, v_msg;
  else
    raise notice 'FAIL  %: refused, but not as expected: %', p_name, v_msg;
  end if;
end
$f$;

create function check_024_is(p_name text, p_query text, p_expected text) returns void language plpgsql as $f$
declare
  v_got text;
begin
  execute p_query into v_got;
  if v_got is not distinct from p_expected then
    raise notice 'PASS  %', p_name;
  else
    raise notice 'FAIL  %: expected [%] but got [%]', p_name, p_expected, v_got;
  end if;
exception when others then
  raise notice 'FAIL  %: %', p_name, sqlerrm;
end
$f$;

-- Transaction advisory locks live to the end of the transaction, and this whole check is one, so a lock a function took
-- shows in pg_locks for this session afterwards. The key is the one the functions use: hashtext of a text naming the channel.
create function check_024_channel_locked(p_name text, p_channel text) returns void language plpgsql as $f$
declare
  v_key integer := hashtext('support-agent channel ' || p_channel);
  v_held boolean;
begin
  select exists (select 1 from pg_locks l
                 where l.locktype = 'advisory' and l.pid = pg_backend_pid() and l.granted and l.objsubid = 1
                   and l.classid::text::bigint = case when v_key < 0 then 4294967295 else 0 end
                   and l.objid::text::bigint = v_key::bigint & 4294967295) into v_held;
  raise notice '%  %', case when v_held then 'PASS' else 'FAIL' end,
    p_name || case when v_held then '' else ': this session holds no lock for that channel' end;
end
$f$;

-- The lock must come before anything that locks or writes a row: the function's own text says so.
create function check_024_lock_comes_first(p_name text, p_function text) returns void language plpgsql as $f$
declare
  v_text text;
  v_lock integer;
  v_first integer;
begin
  begin
    v_text := pg_get_functiondef(p_function::regprocedure);
  exception when others then
    raise notice 'FAIL  %: %', p_name, sqlerrm;  -- before the migration, the function isn't there
    return;
  end;
  v_lock := position('pg_advisory_xact_lock(hashtext(''support-agent channel' in v_text);
  select min(position(marker in v_text)) filter (where position(marker in v_text) > 0) into v_first
  from unnest(array['admin_client_status(', 'for update', 'for no key update', 'insert into', 'update client_map']) marker;
  raise notice '%  %', case when v_lock > 0 and v_lock < v_first then 'PASS' else 'FAIL' end,
    p_name || case when v_lock > 0 and v_lock < v_first then '' else ': lock at ' || v_lock || ', first lock or write at ' || v_first end;
end
$f$;

\echo '== the seed, on the data already here: every client channel and every ticket channel is in client_channels'
select check_024_is('seed: no channel or ticket channel missing from client_channels under its client',
  $q$select count(*)::text from (select channel_id, client_key from client_map union
                                  select channel_id, client_key from tickets) s
     where not exists (select 1 from client_channels c
                       where c.channel_id = s.channel_id and c.client_key = s.client_key)$q$, '0');
select check_024_is('seed: at least one row for every client',
  $q$select ((select count(*) from client_channels) >= (select count(*) from client_map))::text$q$, 'true');

\echo '== setup (as admin): a staff member, then four made-up clients through admin_add_client'
insert into staff (name, email, slack_user_id) values ('Check Admin', 'check.admin24@example.com', 'U0CHECK024A');
select check_024_me() as me \gset
select decode(repeat('ab', 60), 'hex') as key1 \gset

set local role admin_app;
select admin_add_client(:me, 'C0CHK024A', 'Check 024 A', 'LOCCHK024A', 20, :'key1');
select admin_add_client(:me, 'C0CHK024P', 'Check 024 P', 'LOCCHK024P', 20, :'key1');
select admin_add_client(:me, 'C0CHK024R', 'Check 024 R', 'LOCCHK024R', 20, :'key1');
select admin_add_client(:me, 'C0CHK024X', 'Check 024 X', 'LOCCHK024X', 20, :'key1');
select admin_pause_client(:me, 'check-024-p', 'client_asked', null);
select admin_remove_client(:me, 'check-024-r');
reset role;
-- A has a ticket in its channel; P has none (a channel with no ticket still belongs to its client).
insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
values ('chk024-t1', 'C0CHK024A', '1700000000.000100', 'check-024-a', 'LOCCHK024A', 'Check ticket');

\echo '== the tables: the new rows, and what the foreign keys now say'
select check_024_is('a new client gets its channel recorded',
  $q$select string_agg(channel_id || ' ' || client_key, ', ' order by channel_id) from client_channels
     where channel_id like 'C0CHK024%'$q$,
  'C0CHK024A check-024-a, C0CHK024P check-024-p, C0CHK024R check-024-r, C0CHK024X check-024-x');
select check_024_refused('a second owner for one channel', $q$insert into client_channels values ('C0CHK024A', 'check-024-x')$q$,
  '%duplicate key%');
select check_024_refused('a channel recorded for no client', $q$insert into client_channels values ('C0CHK024Q', 'no-such-client')$q$,
  '%foreign key%');
select check_024_refused('a ticket naming an unknown client key',
  $q$insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
     values ('chk024-t0', 'C0CHK024A', '1700000000.000050', 'no-such-client', 'LOCNONE', 'Check ticket')$q$,
  '%foreign key%');

select check_024_is('no foreign key references client_map''s channel_id (a move would fail on it)',
  $q$select count(*)::text from pg_constraint c
     where c.contype = 'f' and c.confrelid = 'client_map'::regclass
       and (select a.attnum from pg_attribute a where a.attrelid = 'client_map'::regclass and a.attname = 'channel_id')
           = any (c.confkey)$q$, '0');
-- (The foreign keys the migration adds take this same lock, so this line cannot tell the explicit "lock table" apart
-- from them; it says client_map was held share row exclusive for the migration's whole run.)
select check_024_is('client_map was held share row exclusive while the migration ran, by its own lock or its foreign keys (this line cannot tell which; looked at only when it ran in this transaction)',
  $q$select case when not exists (select 1 from schema_migrations where version = '024' and xmin = pg_current_xact_id()::xid)
                 then 'ok'
                 when exists (select 1 from pg_locks where locktype = 'relation' and relation = 'client_map'::regclass
                              and mode = 'ShareRowExclusiveLock' and pid = pg_backend_pid() and granted)
                 then 'ok' else 'not locked' end$q$, 'ok');

\echo '== both functions take a lock on the channel first, so Add and Move for one channel run one after the other'
select check_024_lock_comes_first('admin_change_channel locks the channel before any row lock or write',
  'admin_change_channel(bigint, text, text, text)');
select check_024_lock_comes_first('admin_add_client locks the channel before any row lock or write',
  'admin_add_client(bigint, text, text, text, integer, bytea)');

\echo '== can: move an active client (A), and a paused one (P)'
set local role admin_app;
select check_024_ok('move the active client', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024N1', 'check-a-new')$q$);
select check_024_is('the active client is on its new channel with its new name, still active',
  $q$select channel_id || ' ' || channel_name || ' ' || status from admin_clients where client_key = 'check-024-a'$q$,
  'C0CHK024N1 check-a-new active');
select check_024_ok('move the paused client (no name from Slack)', $q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024N2', null)$q$);
select check_024_is('the paused client moved and is still paused, with no name',
  $q$select channel_id || ' ' || coalesce(channel_name, '(none)') || ' ' || status from admin_clients where client_key = 'check-024-p'$q$,
  'C0CHK024N2 (none) paused');
reset role;

select check_024_channel_locked('the move took the lock on the channel it moved the client to', 'C0CHK024N1');
select check_024_channel_locked('and on the paused client''s channel too', 'C0CHK024N2');

\echo '== the tickets stay in the channel they were written in, and stay the client''s by client_key'
select check_024_is('the old ticket keeps its channel and client key',
  $q$select channel_id || ' ' || client_key from tickets where slack_event_id = 'chk024-t1'$q$, 'C0CHK024A check-024-a');
select check_024_is('and by client_key it joins the client, now on the new channel',
  $q$select c.channel_id from tickets t join client_map c on c.client_key = t.client_key
     where t.slack_event_id = 'chk024-t1'$q$, 'C0CHK024N1');
select check_024_ok('a new ticket in the new channel saves',
  $q$insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
     values ('chk024-t2', 'C0CHK024N1', '1700000000.000200', 'check-024-a', 'LOCCHK024A', 'Check ticket')$q$);
select check_024_ok('an old thread can still get a ticket (a draft in flight during the move)',
  $q$insert into tickets (slack_event_id, channel_id, thread_ts, client_key, location_id, text)
     values ('chk024-t3', 'C0CHK024A', '1700000000.000300', 'check-024-a', 'LOCCHK024A', 'Check ticket')$q$);
select check_024_is('the channels are recorded under their client for good',
  $q$select string_agg(channel_id || ' ' || client_key, ', ' order by channel_id) from client_channels
     where channel_id like 'C0CHK024%' and client_key in ('check-024-a', 'check-024-p')$q$,
  'C0CHK024A check-024-a, C0CHK024N1 check-024-a, C0CHK024N2 check-024-p, C0CHK024P check-024-p');

\echo '== can: a second move gives the history old and new labels; moving back to its own old channel is allowed'
set local role admin_app;
select check_024_ok('move A again', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024N3', 'check-a-third')$q$);
select check_024_ok('move A back to the channel it first had', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024A', null)$q$);
select check_024_is('A is on its first channel again',
  $q$select channel_id from admin_clients where client_key = 'check-024-a'$q$, 'C0CHK024A');
reset role;  -- client_changes itself is the admin's to read; the admin app reads admin_history (next)
select check_024_is('each move is in the history: old label to new label (a name, or the ID with none)',
  $q$select string_agg(note, ' ; ' order by id) from client_changes
     where client_key = 'check-024-a' and action = 'channel_changed'$q$,
  'C0CHK024A to #check-a-new ; #check-a-new to #check-a-third ; #check-a-third to C0CHK024A');
set local role admin_app;
select check_024_is('the admin app reads it from admin_history, with the client key',
  $q$select count(*)::text from admin_history where client_key = 'check-024-a' and action = 'channel_changed'
     and detail = 'C0CHK024A to #check-a-new'$q$, '1');
select check_024_is('who made it', $q$select staff_name from admin_history where client_key = 'check-024-p' and action = 'channel_changed'$q$,
  'Check Admin');

\echo '== a client whose channel was never recorded (an old admin_add_client still running while 024 ran) records it as it moves away'
reset role;
insert into client_map (channel_id, client_key, display_name, location_id)
values ('C0CHK024U', 'check-024-u', 'Check 024 U', 'LOCCHK024U');  -- straight in, as admin: no client_channels row
set local role admin_app;
select check_024_ok('U moves away', $q$select admin_change_channel(check_024_me(), 'check-024-u', 'C0CHK024U2', null)$q$);
select check_024_is('U''s old channel is now recorded under U',
  $q$select coalesce((select client_key from client_channels where channel_id = 'C0CHK024U'), '(none)')$q$, 'check-024-u');
select check_024_refused('another client takes U''s old channel',
  $q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024U', null)$q$, '%belonged to another client%');

\echo '== the name is Slack''s and only if it follows 023''s rules: 1 to 80 characters, no control character'
select check_024_ok('P moves with an 81-character name', $q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024N4', repeat('x', 81))$q$);
select check_024_is('...and gets no name', $q$select coalesce(channel_name, '(none)') from admin_clients where client_key = 'check-024-p'$q$, '(none)');
select check_024_ok('P moves with a name holding a control character', $q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024N5', 'a' || chr(7) || 'b')$q$);
select check_024_is('...and gets no name', $q$select coalesce(channel_name, '(none)') from admin_clients where client_key = 'check-024-p'$q$, '(none)');
select check_024_ok('P moves with an 80-character name', $q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024N6', repeat('y', 80))$q$);
select check_024_is('...and keeps all 80', $q$select length(channel_name)::text from admin_clients where client_key = 'check-024-p'$q$, '80');
select check_024_ok('P moves with an empty name', $q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024N7', '')$q$);
select check_024_is('...and gets no name', $q$select coalesce(channel_name, '(none)') from admin_clients where client_key = 'check-024-p'$q$, '(none)');
select check_024_ok('P moves with no name at all (null)',$q$select admin_change_channel(check_024_me(), 'check-024-p', 'C0CHK024N8', null)$q$);

\echo '== cannot: the move'
select check_024_refused('a removed client', $q$select admin_change_channel(check_024_me(), 'check-024-r', 'C0CHK024N9', 'n9')$q$,
  '%unknown or removed client%');
select check_024_refused('an unknown client', $q$select admin_change_channel(check_024_me(), 'no-such-client', 'C0CHK024N9', 'n9')$q$,
  '%unknown or removed client%');
select check_024_refused('its own channel', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024A', 'a')$q$,
  '%already on that channel%');
select check_024_refused('a channel an active client holds', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024X', 'x')$q$,
  '%already belongs to a client%');
select check_024_refused('a channel a paused client holds (P is on N8)', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024N8', 'p')$q$,
  '%already belongs to a client%');
select check_024_refused('a channel a removed client holds', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C0CHK024R', 'r')$q$,
  '%already belongs to a client%');
select check_024_refused('a channel that belonged to another client, and had tickets (A''s N1, with a ticket)',
  $q$select admin_change_channel(check_024_me(), 'check-024-x', 'C0CHK024N1', 'n1')$q$, '%belonged to another client%');
select check_024_refused('a channel that belonged to another client, and never had a ticket (P''s first channel)',
  $q$select admin_change_channel(check_024_me(), 'check-024-x', 'C0CHK024P', 'p')$q$, '%belonged to another client%');
select check_024_refused('a channel ID in lower case', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'c0chk024low', 'n')$q$,
  '%must be a Slack channel ID%');
select check_024_refused('a name instead of an ID', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'general', 'n')$q$,
  '%must be a Slack channel ID%');
select check_024_refused('an ID that is too short', $q$select admin_change_channel(check_024_me(), 'check-024-a', 'C1', 'n')$q$,
  '%must be a Slack channel ID%');
select check_024_refused('an empty ID', $q$select admin_change_channel(check_024_me(), 'check-024-a', '', 'n')$q$,
  '%must be a Slack channel ID%');
select check_024_refused('no ID', $q$select admin_change_channel(check_024_me(), 'check-024-a', null, 'n')$q$,
  '%must be a Slack channel ID%');
select check_024_refused('a "who" that is not an active staff member', $q$select admin_change_channel(0, 'check-024-a', 'C0CHK024N9', 'n')$q$,
  '%only an active staff member%');
select check_024_refused('an ID with a trailing line break', $q$select admin_change_channel(check_024_me(), 'check-024-a', E'C0CHK024N9\n', 'n')$q$,
  '%must be a Slack channel ID%');
select check_024_is('nothing of the refused moves stuck: A is where it was, X too',
  $q$select string_agg(client_key || ' ' || channel_id, ', ' order by client_key) from admin_clients
     where client_key in ('check-024-a', 'check-024-x')$q$, 'check-024-a C0CHK024A, check-024-x C0CHK024X');

\echo '== cannot: Add client on a channel that belonged to another client (it never had a ticket, or its client is removed)'
select check_024_refused('a new client on P''s first channel (never a ticket)',
  $q$select admin_add_client(check_024_me(), 'C0CHK024P', 'Check 024 New', 'LOCCHK024NEW', 20, decode(repeat('ab', 60), 'hex'))$q$,
  '%belonged to another client%');
select check_024_refused('a new client on A''s N1 (it had a ticket)',
  $q$select admin_add_client(check_024_me(), 'C0CHK024N1', 'Check 024 New', 'LOCCHK024NEW', 20, decode(repeat('ab', 60), 'hex'))$q$,
  '%belonged to another client%');
select check_024_refused('the removed client''s location on another client''s old channel',
  $q$select admin_add_client(check_024_me(), 'C0CHK024P', 'Check 024 R', 'LOCCHK024R', 20, decode(repeat('ab', 60), 'hex'))$q$,
  '%belong%');
select check_024_refused('a channel an active client holds still refuses as before',
  $q$select admin_add_client(check_024_me(), 'C0CHK024X', 'Check 024 New', 'LOCCHK024NEW', 20, decode(repeat('ab', 60), 'hex'))$q$,
  '%already belongs to a client%');
select check_024_is('nothing was added by the refused ones',
  $q$select count(*)::text from admin_clients where display_name = 'Check 024 New'$q$, '0');

\echo '== can: a removed client comes back through Add client, on its own channel, under its old key'
select check_024_is('Check 024 R is back under its old key',
  $q$select admin_add_client(check_024_me(), 'C0CHK024R', 'Check 024 R', 'LOCCHK024R', 20, decode(repeat('ab', 60), 'hex'))$q$,
  'check-024-r');
select check_024_is('and active again',
  $q$select status from admin_clients where client_key = 'check-024-r'$q$, 'active');
select check_024_ok('a brand new client on a channel nobody has had',
  $q$select admin_add_client(check_024_me(), 'C0CHK024F', 'Check 024 F', 'LOCCHK024F', 20, decode(repeat('ab', 60), 'hex'))$q$);
select check_024_is('and its channel is recorded',
  $q$select client_key from client_channels where channel_id = 'C0CHK024F'$q$, 'check-024-f');
select check_024_channel_locked('Add client took the lock on the channel it gave the new client', 'C0CHK024F');
reset role;

\echo '== who may do what'
set local role admin_app;
select check_024_ok('admin_app can read client_channels', $q$select count(*) from client_channels$q$);
select check_024_refused('admin_app cannot insert into it', $q$insert into client_channels values ('C0CHK024Z', 'check-024-a')$q$,
  '%permission denied%');
select check_024_refused('admin_app cannot update it', $q$update client_channels set client_key = 'check-024-x' where channel_id = 'C0CHK024A'$q$,
  '%permission denied%');
select check_024_refused('admin_app cannot delete from it', $q$delete from client_channels where channel_id = 'C0CHK024A'$q$,
  '%permission denied%');
select check_024_refused('admin_app cannot update client_map directly',
  $q$update client_map set channel_id = 'C0CHK024Z' where client_key = 'check-024-a'$q$, '%permission denied%');
select check_024_refused('admin_app cannot read the tickets', $q$select count(*) from tickets$q$, '%permission denied%');
reset role;

set local role n8n_support;
select check_024_refused('n8n''s login cannot call the move', $q$select admin_change_channel(1, 'check-024-a', 'C0CHK024N9', 'n')$q$,
  '%permission denied%');
select check_024_refused('n8n''s login cannot read client_channels', $q$select count(*) from client_channels$q$, '%permission denied%');
select check_024_refused('n8n''s login cannot write client_channels', $q$insert into client_channels values ('C0CHK024Z', 'check-024-a')$q$,
  '%permission denied%');
reset role;

set local role agent_reader;
select check_024_refused('the agent''s reader cannot call the move', $q$select admin_change_channel(1, 'check-024-a', 'C0CHK024N9', 'n')$q$,
  '%permission denied%');
select check_024_refused('the agent''s reader cannot read client_channels', $q$select count(*) from client_channels$q$, '%permission denied%');
reset role;

select check_024_is('the move is granted to admin_app', $q$select has_function_privilege('admin_app', 'admin_change_channel(bigint, text, text, text)', 'execute')::text$q$, 'true');
select check_024_is('and not to everyone', $q$select has_function_privilege('public', 'admin_change_channel(bigint, text, text, text)', 'execute')::text$q$, 'false');
select check_024_is('Add client is still admin_app''s alone', $q$select has_function_privilege('public', 'admin_add_client(bigint, text, text, text, integer, bytea)', 'execute')::text$q$, 'false');

\echo '== the action list: channel_changed is allowed, and the others still are, an unknown one is not'
select check_024_ok('channel_changed in client_changes', $q$insert into client_changes (changed_by, client_key, action, note)
  values (check_024_me(), 'check-024-a', 'channel_changed', 'x to y')$q$);
select check_024_ok('key_replaced still allowed', $q$insert into client_changes (changed_by, client_key, action)
  values (check_024_me(), 'check-024-a', 'key_replaced')$q$);
select check_024_refused('an unknown action', $q$insert into client_changes (changed_by, client_key, action)
  values (check_024_me(), 'check-024-a', 'channel_moved')$q$, '%client_changes_action_check%');

rollback;
\echo '== done: rolled back, nothing kept. Any FAIL notice above is a failure; there must be none.'
\endif
