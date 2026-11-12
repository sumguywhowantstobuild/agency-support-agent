#!/usr/bin/env bash
# Writes .env for a new server (SETUP section 11: a trial on a rented server, or moving the system). Run once from the
# repository folder.
#
#   deploy/new-env.sh staged   For deploy/restore.sh staged: the tunnel token and the webhook address get
#                              placeholders (a staged server never reaches Cloudflare or Slack).
#   deploy/new-env.sh server   A server that goes live: asks for those too.
#
# Every internal password is made up here (64 hex characters, never shown); outside keys and the old system's n8n
# encryption key are typed at hidden prompts.
# Moving an existing system to a server keeps its old .env instead.
set -euo pipefail
# Plain letter order for every check below: with a language such as en_US.UTF-8, a-f follows dictionary order
# (a A b B ... f), so capitals A-E would pass (seen in Git Bash).
export LC_ALL=C

mode=${1:-}
case "$mode" in staged|server) ;; *) echo "usage: deploy/new-env.sh staged|server" >&2; exit 2 ;; esac
[ ! -e .env ] || { echo ".env already exists: this helper only writes a new one (nothing was changed)" >&2; exit 1; }

# Each answer must match its pattern, or it is asked again: a wrong paste is caught here, and no answer can carry a
# space, quote, $ or backslash into .env (Docker Compose would read those as part of the file's syntax).
stop() { echo "Stopped before every answer was in: no .env was written." >&2; exit 1; }
ask() { # ask NAME "question" hidden|shown PATTERN "what it should look like"
  local how=$3 pattern=$4 value
  while true; do
    if [ "$how" = hidden ]; then read -r -s -p "$2: " value || stop; echo >&2; else read -r -p "$2: " value || stop; fi
    [[ $value =~ $pattern ]] && break
    echo "That doesn't look right ($5). Try again." >&2
  done
  printf -v "$1" '%s' "$value"
}
made_up() { openssl rand -hex 32; }
MODEL='^[A-Za-z0-9._:-]+$'
# Each key's real shape (checked against real keys), so a paste that lost its end is caught too.
ask OPENAI_API_KEY "OpenAI key (sk-...)" hidden '^sk-[A-Za-z0-9_-]{40,}$' "sk- and the whole key after it"
ask OPENAI_MODEL "OpenAI model for the agent" shown "$MODEL" "a model name such as gpt-5-mini"
ask JUDGE_MODEL "OpenAI model that grades the accuracy test (blank: none)" shown "^$|$MODEL" "a model name, or blank"
ask R2_ACCESS_KEY_ID "Cloudflare R2 access key ID" hidden '^[0-9a-f]{32}$' "32 characters, 0-9 and a-f"
ask R2_SECRET_ACCESS_KEY "Cloudflare R2 secret access key" hidden '^[0-9a-f]{64}$' "64 characters, 0-9 and a-f"
ask R2_ENDPOINT "R2 endpoint (https://<account id>.r2.cloudflarestorage.com)" shown \
  '^https://[0-9a-f]{32}\.((eu|fedramp)\.)?r2\.cloudflarestorage\.com$' \
  "https://, the 32-character account ID, .r2.cloudflarestorage.com, no bucket name at the end"
ask R2_BUCKET "R2 bucket" shown '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$' "lower-case letters, digits and dashes"
# An age public key: age1 and 58 characters of its 32-letter alphabet (no b, i, o or 1 after the prefix).
ask BACKUP_AGE_RECIPIENT "Backup public key (age1...)" shown '^age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58}$' \
  "age1 and 58 more characters, the whole line age-keygen printed"
# n8n 2.x keeps its own keys in its database, locked with this one: a restored n8n with a new key refuses to start.
# So it is the old system's, never made up here.
ask N8N_ENCRYPTION_KEY "n8n encryption key of the system being restored (N8N_ENCRYPTION_KEY in its .env)" hidden \
  '^[0-9a-f]{64}$' "64 characters, 0-9 and a-f"
if [ "$mode" = staged ]; then
  N8N_WEBHOOK_URL=https://staged.invalid/
  CLOUDFLARE_TUNNEL_TOKEN=staged-placeholder-not-a-tunnel-token
  # The admin app starts but nobody can sign in: test.invalid can never be reached, so no pass is ever checked.
  CF_TEAM_DOMAIN=test.invalid
  CF_AUDIENCE=staged-placeholder-not-an-audience-tag
  ADMIN_ORIGIN=https://admin.staged.invalid
  SLACK_BOT_TOKEN=staged-placeholder-not-a-slack-token
else
  ask N8N_WEBHOOK_URL "Public address of n8n (the tunnel's hostname, https://.../)" shown '^https://[A-Za-z0-9.-]+/$' \
    "https://, ending in /"
  ask CLOUDFLARE_TUNNEL_TOKEN "Cloudflare tunnel token" hidden '^[A-Za-z0-9+/=_-]+$' "the part after --token"
  # The admin app's sign-in gate (SETUP section 14): the Zero Trust team and the Access application's tag.
  ask CF_TEAM_DOMAIN "Cloudflare Access team domain (<team>.cloudflareaccess.com)" shown \
    '^[a-z0-9][a-z0-9-]{0,62}\.cloudflareaccess\.com$' "your team name, then .cloudflareaccess.com"
  ask CF_AUDIENCE "Admin app's Access audience (AUD) tag" shown '^[0-9a-f]{64}$' "64 characters, 0-9 and a-f"
  ask ADMIN_ORIGIN "Admin app's address (https://admin.<domain>)" shown '^https://[a-z0-9.-]+$' \
    "https:// and the hostname, with no / at the end"
  ask SLACK_BOT_TOKEN "Slack bot token (xoxb-...)" hidden '^xoxb-[0-9]+-[0-9]+-[A-Za-z0-9]{20,}$' \
    "xoxb- and the whole token after it (the Bot User OAuth Token)"
fi

# Made up before anything is written: a failing `openssl` stops here instead of leaving an empty password in .env.
for name in AGENT_API_KEY POSTGRES_PASSWORD AGENT_DB_PASSWORD N8N_DB_PASSWORD KB_SYNC_DB_PASSWORD \
            N8N_SUPPORT_DB_PASSWORD BACKUP_DB_PASSWORD ADMIN_DB_PASSWORD; do
  value=$(made_up)
  [[ $value =~ ^[0-9a-f]{64}$ ]] ||
    { echo "Couldn't make up a password (is openssl installed?): no .env was written." >&2; exit 1; }
  printf -v "$name" '%s' "$value"
done
# The key pair that locks client keys (src/support_agent/keys.py): an X25519 pair, the kind PyNaCl's sealed boxes use, as the
# raw 32 bytes of each half in base64 (the last 32 bytes of openssl's DER forms). Kept in variables, never in a file.
pem=$(openssl genpkey -algorithm X25519 2>/dev/null) &&
  GHL_KEY_PRIVATE=$(openssl pkey -outform DER <<<"$pem" | tail -c 32 | openssl base64 -A) &&
  GHL_KEY_PUBLIC=$(openssl pkey -pubout -outform DER <<<"$pem" | tail -c 32 | openssl base64 -A) || GHL_KEY_PRIVATE=""
unset pem
[[ $GHL_KEY_PRIVATE =~ ^[A-Za-z0-9+/]{43}=$ && $GHL_KEY_PUBLIC =~ ^[A-Za-z0-9+/]{43}=$ ]] ||
  { echo "Couldn't make the key pair (is openssl 3 installed?): no .env was written." >&2; exit 1; }
# Written to a file only its owner can read (from its first byte, not chmod after), then renamed into place whole: a
# failure halfway leaves no .env at all.
umask 077
new=$(mktemp .env.new.XXXXXX)
trap 'rm -f "$new"' EXIT
{
  echo "OPENAI_API_KEY=$OPENAI_API_KEY"
  echo "OPENAI_MODEL=$OPENAI_MODEL"
  echo "JUDGE_MODEL=$JUDGE_MODEL"
  echo "DATABASE_URL=postgresql://agent_reader:$AGENT_DB_PASSWORD@postgres:5432/knowledge"
  echo "AGENT_API_KEY=$AGENT_API_KEY"
  echo "GHL_KEY_PRIVATE=$GHL_KEY_PRIVATE"
  echo "GHL_KEY_PUBLIC=$GHL_KEY_PUBLIC"
  echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
  echo "AGENT_DB_PASSWORD=$AGENT_DB_PASSWORD"
  echo "N8N_DB_PASSWORD=$N8N_DB_PASSWORD"
  echo "N8N_ENCRYPTION_KEY=$N8N_ENCRYPTION_KEY"
  echo "KB_SYNC_DB_PASSWORD=$KB_SYNC_DB_PASSWORD"
  echo "N8N_SUPPORT_DB_PASSWORD=$N8N_SUPPORT_DB_PASSWORD"
  echo "CLOUDFLARE_TUNNEL_TOKEN=$CLOUDFLARE_TUNNEL_TOKEN"
  echo "N8N_WEBHOOK_URL=$N8N_WEBHOOK_URL"
  echo "BACKUP_DB_PASSWORD=$BACKUP_DB_PASSWORD"
  echo "R2_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID"
  echo "R2_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY"
  echo "R2_ENDPOINT=$R2_ENDPOINT"
  echo "R2_BUCKET=$R2_BUCKET"
  echo "BACKUP_AGE_RECIPIENT=$BACKUP_AGE_RECIPIENT"
  echo "ADMIN_DB_PASSWORD=$ADMIN_DB_PASSWORD"
  echo "CF_TEAM_DOMAIN=$CF_TEAM_DOMAIN"
  echo "CF_AUDIENCE=$CF_AUDIENCE"
  echo "ADMIN_ORIGIN=$ADMIN_ORIGIN"
  echo "TIME_ZONE=America/New_York"  # the agency's zone for the admin app's chart and times: not asked, edit the line
  echo "SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN"
} > "$new"
# A hard link never replaces an existing name, so an .env written while this helper waited for answers stays as it is
# (mv would replace it).
if ! ln "$new" .env 2>/dev/null; then
  [ -e .env ] && echo ".env already exists: this helper only writes a new one (nothing was changed)" >&2 ||
    echo "Couldn't put .env in place: no .env was written." >&2
  exit 1
fi
rm -f "$new"
trap - EXIT

echo ".env written ($mode), readable by its owner only. Filled (values not shown):"
cut -d= -f1 .env | paste -sd' ' | fold -s -w 110 | sed 's/^/  /'
[ "$mode" = server ] ||
  echo "Placeholders (staged): N8N_WEBHOOK_URL, CLOUDFLARE_TUNNEL_TOKEN, CF_TEAM_DOMAIN, CF_AUDIENCE, ADMIN_ORIGIN, SLACK_BOT_TOKEN."
echo "Next: keep a copy of .env in the password manager (it holds N8N_ENCRYPTION_KEY: n8n's saved logins can't be"
echo "opened without it, and a restore needs every password in it; and GHL_KEY_PRIVATE: without it every client key"
echo "must be entered again)."
echo "The key pair is new, so client keys restored from the old system can't be opened here: enter each client's"
echo "HighLevel key again in the admin app (client sheet: Replace key) before the workflows go live."
echo "N8N_ENCRYPTION_KEY is the old system's, so the restored n8n starts and its saved logins open here. Three of them"
echo "still hold the old system's passwords, so restore with deploy/restore.sh staged (every workflow unpublished)"
echo "and, before publishing any workflow, set the new ones from this .env in Knowledge DB (kb_sync)"
echo "(KB_SYNC_DB_PASSWORD), Support DB (n8n_support) (N8N_SUPPORT_DB_PASSWORD) and Agent API (AGENT_API_KEY)."
