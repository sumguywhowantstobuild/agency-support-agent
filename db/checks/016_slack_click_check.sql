-- Check for migration 016: the database checks Slack's signature on button clicks, so the buttons don't depend on
-- the agent. Every assertion stops the script with an error (exit status not 0) when it fails; the last line says
-- PASS only when all of them held. Runs in one transaction that is rolled back (the secret row too).
-- Run:  docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge < db/checks/016_slack_click_check.sql
\set ON_ERROR_STOP 1
\set QUIET 1
begin;

-- 1. The signature rule itself, against Slack's own published example (an independent fixture: its secret,
-- timestamp, body and expected signature come from Slack's documentation, checked with Python's hmac first).
-- They are public example values, not a real workspace's: the secret's line is marked for the secret scanner.
do $$
declare
  body bytea := convert_to(
    'token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&channel_id=G8PSS9T3V'
    || '&channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner&command=%2Fwebhook-collect&text='
    || '&response_url=https%3A%2F%2Fhooks.slack.com%2Fcommands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN'
    || '&trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c', 'UTF8');
  sig text := 'v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503';
  secret text := '8f742231b10e8888abcd99yyyzzz85a5'; -- gitleaks:allow
begin
  if not slack_private.signature_matches('1531420618', sig, body, secret) then
    raise exception 'FAIL: Slack''s published example does not match';
  end if;
  if slack_private.signature_matches('1531420618', sig, body || 'x'::bytea, secret) then
    raise exception 'FAIL: a changed body still matched';
  end if;
  if slack_private.signature_matches('1531420619', sig, body, secret) then
    raise exception 'FAIL: a changed timestamp still matched';
  end if;
  if slack_private.signature_matches('1531420618', sig, body, secret || 'x') then
    raise exception 'FAIL: another secret still matched';
  end if;
  raise notice 'ok: Slack''s example matches; changed body, timestamp or secret don''t';
end $$;

-- 2. The full check n8n calls, with a made-up secret for this transaction and signatures made now.
insert into slack_private.signing_secret (id, secret) values (1, 'checkSecret016NotForSlack0000000')
on conflict (id) do update set secret = excluded.secret;

create function pg_temp.signed(p_ts bigint, p_body bytea) returns text language sql as $$
  select 'v0=' || encode(crypto.hmac(convert_to('v0:' || p_ts || ':', 'UTF8') || p_body,
                                     convert_to('checkSecret016NotForSlack0000000', 'UTF8'), 'sha256'), 'hex')
$$;

do $$
declare
  now_s bigint := floor(extract(epoch from clock_timestamp()));
  body bytea := convert_to('payload=%7B%22type%22%3A%22block_actions%22%7D', 'UTF8');
  b64 text := encode(body, 'base64');
  -- One byte over 256 KiB, in base64 without line breaks (Postgres's encode() adds them; Node's doesn't). Its
  -- base64 is exactly as long as 256 KiB's, so only a check on the decoded size catches it.
  big text := replace(encode(convert_to(repeat('a', 262145), 'UTF8'), 'base64'), E'\n', '');
begin
  if verify_slack_request(now_s::text, pg_temp.signed(now_s, body), b64) is not true then
    raise exception 'FAIL: a genuine click made now was refused';
  end if;
  if verify_slack_request((now_s - 290)::text, pg_temp.signed(now_s - 290, body), b64) is not true then
    raise exception 'FAIL: a genuine click 290 s old was refused';
  end if;
  -- Each of these must be refused with false (not an error, not null). 310 s, not 301: the check reads the clock
  -- again, and a second boundary crossed in between must not make a correct check fail.
  if verify_slack_request((now_s - 310)::text, pg_temp.signed(now_s - 310, body), b64) is not false then
    raise exception 'FAIL: a click older than 5 minutes was accepted';
  end if;
  if verify_slack_request((now_s + 310)::text, pg_temp.signed(now_s + 310, body), b64) is not false then
    raise exception 'FAIL: a click dated more than 5 minutes ahead was accepted';
  end if;
  if verify_slack_request(now_s::text, pg_temp.signed(now_s, body), encode(body || 'x'::bytea, 'base64'))
     is not false then
    raise exception 'FAIL: a changed body was accepted';
  end if;
  if verify_slack_request(now_s::text, upper(pg_temp.signed(now_s, body)), b64) is not false
     or verify_slack_request(now_s::text, 'v1=' || substr(pg_temp.signed(now_s, body), 4), b64) is not false
     or verify_slack_request(now_s::text, left(pg_temp.signed(now_s, body), 66), b64) is not false
     or verify_slack_request(now_s::text, '', b64) is not false
     or verify_slack_request(now_s::text, null, b64) is not false then
    raise exception 'FAIL: a badly formed signature was not refused with false';
  end if;
  if verify_slack_request(' ' || now_s, pg_temp.signed(now_s, body), b64) is not false
     or verify_slack_request('+' || now_s, pg_temp.signed(now_s, body), b64) is not false
     or verify_slack_request(now_s || '.0', pg_temp.signed(now_s, body), b64) is not false
     or verify_slack_request(now_s || ', ' || now_s, pg_temp.signed(now_s, body), b64) is not false
     or verify_slack_request('99999999999999999999', pg_temp.signed(now_s, body), b64) is not false
     or verify_slack_request(null, pg_temp.signed(now_s, body), b64) is not false then
    raise exception 'FAIL: a badly formed timestamp was not refused with false';
  end if;
  if verify_slack_request(now_s::text, pg_temp.signed(now_s, body), '!!not base64!!') is not false
     or verify_slack_request(now_s::text, pg_temp.signed(now_s, body), null) is not false then
    raise exception 'FAIL: a badly formed body was not refused with false';
  end if;
  if verify_slack_request(now_s::text, pg_temp.signed(now_s, decode(big, 'base64')), big) is not false then
    raise exception 'FAIL: a body over 256 KiB was accepted';
  end if;
  raise notice 'ok: genuine clicks pass; old, future, changed and badly formed ones are refused';
end $$;

-- 3. No secret set: an error that says so (n8n's "can't check" path), never a quiet false.
delete from slack_private.signing_secret;
do $$
declare
  now_s bigint := floor(extract(epoch from clock_timestamp()));
begin
  perform verify_slack_request(now_s::text, 'v0=' || repeat('0', 64), encode('x'::bytea, 'base64'));
  raise exception 'FAIL: a missing secret did not raise an error';
exception when raise_exception then
  if sqlerrm not like '%secret is not set%' then
    raise exception 'FAIL: unexpected error without a secret: %', sqlerrm;
  end if;
  raise notice 'ok: a missing secret raises "secret is not set"';
end $$;
insert into slack_private.signing_secret (id, secret) values (1, 'checkSecret016NotForSlack0000000');

-- 4. Who can do what.
do $$
begin
  if (select rolcanlogin from pg_roles where rolname = 'slack_verifier') then
    raise exception 'FAIL: slack_verifier can log in';
  end if;
  if pg_has_role('n8n_support', 'slack_verifier', 'member') then
    raise exception 'FAIL: n8n_support is a member of slack_verifier';
  end if;
  if (select p.proowner::regrole::text <> 'slack_verifier' or not p.prosecdef
            or not coalesce(p.proconfig @> array['search_path=pg_catalog, pg_temp'], false)
      from pg_proc p where p.oid = 'verify_slack_request(text, text, text)'::regprocedure) then
    raise exception 'FAIL: verify_slack_request must be security definer, owned by slack_verifier, fixed search_path';
  end if;
  if not has_function_privilege('n8n_support', 'verify_slack_request(text, text, text)', 'execute')
     or exists (select 1 from pg_roles r
                where r.rolname in ('agent_reader', 'kb_sync', 'backup')
                  and has_function_privilege(r.oid, 'verify_slack_request(text, text, text)', 'execute')) then
    raise exception 'FAIL: only n8n_support (of the logins) may run verify_slack_request';
  end if;
  if has_function_privilege('public', 'verify_slack_request(text, text, text)', 'execute') then
    raise exception 'FAIL: everyone may run verify_slack_request';
  end if;
  -- n8n fills its values into the statement text, so a failed statement would land in the database log with the
  -- click (client text) in it. For n8n's login, failed statements are not logged (the error line still is).
  if not exists (select 1 from pg_db_role_setting s join pg_roles r on r.oid = s.setrole
                 where r.rolname = 'n8n_support' and s.setdatabase = 0
                   and 'log_min_error_statement=panic' = any(s.setconfig)) then
    raise exception 'FAIL: failed statements from n8n_support would be logged with their values';
  end if;
  raise notice 'ok: owner, settings and grants as intended';
end $$;

set local role n8n_support;
do $$
declare
  now_s bigint := floor(extract(epoch from clock_timestamp()));
  body bytea := convert_to('payload=%7B%7D', 'UTF8');
  sig text := 'v0=' || encode(sha256(convert_to('placeholder', 'UTF8')), 'hex');
begin
  -- n8n's login may run the check (a wrong signature gives false, no error) ...
  if verify_slack_request(now_s::text, sig, encode(body, 'base64')) is not false then
    raise exception 'FAIL: n8n_support got something other than false for a wrong signature';
  end if;
  -- ... but can't read the secret, run the inner rule, or change either.
  begin
    perform 1 from slack_private.signing_secret;
    raise exception 'FAIL: n8n_support read the secret table';
  exception when insufficient_privilege then null;
  end;
  begin
    perform slack_private.signature_matches('1', sig, body, 'x');
    raise exception 'FAIL: n8n_support ran the inner signature rule';
  exception when insufficient_privilege then null;
  end;
  begin
    update slack_private.signing_secret set secret = 'x';
    raise exception 'FAIL: n8n_support changed the secret';
  exception when insufficient_privilege then null;
  end;
  begin
    create function public.verify_slack_request(text, text, text, text) returns boolean language sql as 'select true';
    raise exception 'FAIL: n8n_support created a function in public';
  exception when insufficient_privilege then null;
  end;
  raise notice 'ok: n8n_support runs the check but can''t read, run around or change the secret';
end $$;
reset role;

set local role agent_reader;
do $$
begin
  perform verify_slack_request('1', 'v0=' || repeat('0', 64), '');
  raise exception 'FAIL: agent_reader ran verify_slack_request';
exception when insufficient_privilege then
  raise notice 'ok: the agent''s login can''t run the check';
end $$;
reset role;

rollback;
\echo 'PASS: every 016 check held (rolled back, nothing kept)'
