-- 016: the database checks Slack's signature on button clicks. Until now n8n asked the
-- agent, so while the agent was down every button on every card did nothing, without a word: "Take over" on the
-- "agent unavailable" card too, the human fallback for exactly that moment. Every click needs the database anyway,
-- so the check moves here: n8n's "Ticket approval" calls verify_slack_request() with Slack's two headers and the
-- body, base64 (exact bytes). Slack's method: https://docs.slack.dev/authentication/verifying-requests-from-slack
--
-- The signing secret sits in its own table (system_settings is readable by n8n_support, so not there). No application
-- login can read it (the database admin and the backup login can read everything): among the application roles only
-- slack_verifier can, a role nobody logs in as and nobody is a member of; it owns the two functions. n8n_support may
-- run the outer check and nothing else here. The secret is stored by hand with the hidden command in docs/SETUP.md
-- section 9; a test copy refresh replaces it with a made-up one.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 016_slack_click_check.sql

insert into schema_migrations (version) values ('016');  -- a second run fails here instead of half-applying

-- HMAC from pgcrypto, in a schema of its own that only the verifier may use.
create schema crypto;
revoke all on schema crypto from public;
create extension pgcrypto schema crypto;

create role slack_verifier nologin;

create schema slack_private;
revoke all on schema slack_private from public;
create table slack_private.signing_secret (
  id int primary key default 1 check (id = 1),  -- one secret
  secret text not null check (secret ~ '^[0-9A-Za-z]{16,128}$'),
  set_at timestamptz not null default now()
);
revoke all on slack_private.signing_secret from public;
grant usage on schema slack_private, crypto to slack_verifier;
grant select on slack_private.signing_secret to slack_verifier;

-- The signature rule alone: Slack's HMAC-SHA256 over "v0:<timestamp>:<body bytes>". The two signatures are compared
-- after hashing both again with a random key ("double HMAC"): Postgres has no constant-time comparison, and this way
-- how long "=" takes says nothing about the real signature, since the compared bytes change on every call.
create function slack_private.signature_matches(p_timestamp text, p_signature text, p_body bytea, p_secret text)
returns boolean
language sql
volatile
set search_path = pg_catalog, pg_temp
as $$
  select crypto.hmac(crypto.hmac(convert_to('v0:' || p_timestamp || ':', 'UTF8') || p_body,
                                 convert_to(p_secret, 'UTF8'), 'sha256'), r.k, 'sha256')
       = crypto.hmac(decode(substr(p_signature, 4), 'hex'), r.k, 'sha256')
  from (select crypto.gen_random_bytes(32) as k) r
$$;

-- What n8n calls. true only for a click Slack signed with our secret within 5 minutes either way; false for anything
-- missing, badly formed, too big, too old or wrong; an error only when the check itself can't run (no secret set).
create function verify_slack_request(p_timestamp text, p_signature text, p_body_b64 text)
returns boolean
language plpgsql
volatile
security definer
set search_path = pg_catalog, pg_temp  -- runs with slack_verifier's rights: picks up nobody else's objects
as $$
declare
  v_now bigint := floor(extract(epoch from clock_timestamp()));  -- the time now, not when the transaction began
  v_body bytea;
  v_secret text;
begin
  if p_timestamp is null or p_signature is null or p_body_b64 is null then
    return false;
  end if;
  -- Plain whole seconds only (no spaces, signs, decimals or two headers joined), and the signature's exact shape.
  if p_timestamp !~ '^[1-9][0-9]{0,10}$' or p_signature !~ '^v0=[0-9a-f]{64}$' then
    return false;
  end if;
  if abs(v_now - p_timestamp::bigint) > 300 then  -- Slack's advice: refuse anything over five minutes off
    return false;
  end if;
  if length(p_body_b64) > 349528 then  -- base64 of 256 KiB, far above any real click or pop-up
    return false;
  end if;
  begin
    v_body := decode(p_body_b64, 'base64');
  exception when invalid_parameter_value then
    return false;
  end;
  if length(v_body) > 262144 then  -- up to 2 bytes over 256 KiB have the same base64 length, so check the bytes too
    return false;
  end if;
  select s.secret into v_secret from slack_private.signing_secret s where s.id = 1;
  if v_secret is null then
    raise exception 'verify_slack_request: the Slack signing secret is not set (docs/SETUP.md section 9)';
  end if;
  return slack_private.signature_matches(p_timestamp, p_signature, v_body, v_secret);
end;
$$;

alter function slack_private.signature_matches(text, text, bytea, text) owner to slack_verifier;
alter function verify_slack_request(text, text, text) owner to slack_verifier;
revoke all on function slack_private.signature_matches(text, text, bytea, text) from public;
revoke all on function verify_slack_request(text, text, text) from public;
grant execute on function verify_slack_request(text, text, text) to n8n_support;

-- n8n fills its values into the statement text, so a statement of n8n's that fails would be written to the
-- database log with them: a click (client text), a reply. For n8n's login, failed statements are not logged (the
-- error line still is). Only a superuser can change this setting. Applies to n8n's next connections.
alter role n8n_support set log_min_error_statement = 'panic';

comment on table slack_private.signing_secret is
  'Slack app signing secret: no application login reads it, only slack_verifier (verify_slack_request). Set with the hidden command in SETUP section 9.';
comment on function verify_slack_request(text, text, text) is
  'True only for a Slack request signed with our secret within 5 minutes. n8n "Ticket approval" calls it per click.';
