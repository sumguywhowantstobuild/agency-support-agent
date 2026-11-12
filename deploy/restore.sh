#!/usr/bin/env bash
# Rebuild the system from the newest complete backup in Cloudflare R2. Run from the repository
# folder, with .env present (after a disaster: the copy kept off this machine).
#
#   deploy/restore.sh copy     A separate test copy on the live PC: own database and storage, ports on 127.0.0.1 only,
#                              no tunnel, no backup service, every n8n workflow unpublished. First takes a fresh
#                              backup of live (live keeps running) and restores exactly that. Running it again
#                              replaces the old copy.
#   deploy/restore.sh staged   A new, empty server with everything that reaches outside switched off: no tunnel, no
#                              backup service, every n8n workflow unpublished, no Slack login, no Healthchecks
#                              address. For a trial on a rented server, or moving the system (SETUP section 11).
#   deploy/restore.sh server   A real recovery on a new, empty machine: starts everything, tunnel and backups
#                              included. Refuses a machine that already has data.
#
#   staged|server --at <time>  That exact backup instead of the newest (2026-11-09T10:00:00Z, as a live change printed
#                              it): the way back to before that change. Not for copy, which always starts from a
#                              fresh backup of live.
#
# The backup private key is typed at a hidden prompt and handed to the restore on standard input: never saved, never
# shown, never in a setting.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash: keep container paths as they are

usage="usage: deploy/restore.sh copy|staged|server, or staged|server --at <time a backup was taken, e.g. 2026-11-09T10:00:00Z>"
mode=${1:-}
case "$mode" in # safe: the copy's safety steps after the restore (nothing may reach Slack or the outside watcher)
  copy) project=support-agent-copy; files=(-f deploy/compose.yaml -f deploy/compose.copy.yaml); safe=yes ;;
  staged) project=support-agent; files=(-f deploy/compose.yaml -f deploy/compose.staged.yaml); safe=yes ;;
  server) project=support-agent; files=(-f deploy/compose.yaml); safe=no ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
# --at: that exact backup instead of the newest (going back to before a live change: apply.sh prints the time)
export RESTORE_AT=""
case "${2:-}" in
  "") [ $# -le 1 ] || { echo "$usage" >&2; exit 2; } ;;
  --at) RESTORE_AT=${3:-}
    [ $# -eq 3 ] && [[ $RESTORE_AT =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ||
      { echo "$usage" >&2; exit 2; } ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
if [ "$mode" = copy ] && [ -n "$RESTORE_AT" ]; then
  # An older copy would show as fresh in the menu, and a change tried on it could undo newer live work.
  echo "--at is for staged or server (a new, empty system): the copy always starts from a fresh backup of live" >&2
  exit 2
fi
[ -f .env ] || { echo ".env is missing: run from the repository folder" >&2; exit 1; }
dc() { docker compose -p "$project" "${files[@]}" --env-file .env "$@"; }
# The copy's Slack signing secret: made up, not a secret (the copy has no Slack app); clicks for testing the copy's
# "Ticket approval" are signed with it.
COPY_SLACK_SECRET=copyTestSecretNotForSlack0000000

# The passwords the restore sets on each login, read from .env into this shell only (passed on by name, so the
# values never appear in a command line).
for name in POSTGRES_PASSWORD AGENT_DB_PASSWORD KB_SYNC_DB_PASSWORD N8N_SUPPORT_DB_PASSWORD N8N_DB_PASSWORD \
            BACKUP_DB_PASSWORD ADMIN_DB_PASSWORD; do # ADMIN_DB_PASSWORD: a backup from before 1.1.0 has no such login; the restore job skips it
  value=$(grep "^$name=" .env | cut -d= -f2- | tr -d '\r' || true) # tr: .env saved with Windows line endings
  [ -n "$value" ] || { echo "$name is not set in .env" >&2; exit 1; }
  export "$name=$value"
done
export PGPASSWORD=$POSTGRES_PASSWORD # the restore signs in as the database admin

export RESTORE_NOT_BEFORE=""
if [ "$mode" = copy ]; then
  # A fresh backup first, so the copy starts from live exactly as it is now: a change tried on an older copy could
  # undo a newer live one when it goes live. Taken by the live backup service, so it is a real backup
  # too (in R2 and backup_runs, deleted after 15 days like the others).
  echo "== taking a fresh backup of the live system"
  taken=$(docker compose -p support-agent-dev -f compose.dev.yaml --env-file .env \
            exec -T backup python3 -m support_agent.backup_job --now </dev/null) ||
    { echo "The fresh backup failed (see above): the copy is left as it was." >&2; exit 1; }
  RESTORE_NOT_BEFORE=$(sed -n 's/^backup taken: //p' <<<"$taken")
  [ -n "$RESTORE_NOT_BEFORE" ] || { echo "The backup didn't say when it was taken: the copy is left as it was." >&2; exit 1; }
  echo "taken: $RESTORE_NOT_BEFORE"
  echo "== removing the old copy, if any (its own database and storage only)"
  dc down -v --remove-orphans
fi

read -r -s -p "Paste the backup private key (AGE-SECRET-KEY-...), then press Enter: " key
echo
started=$(date +%s)

echo "== starting an empty database"
dc up -d --build --wait postgres

echo "== restoring"
printf '%s\n' "$key" | dc run --rm --no-deps -T --build \
  -e PGUSER=postgres -e PGPASSWORD -e AGENT_DB_PASSWORD -e KB_SYNC_DB_PASSWORD -e N8N_SUPPORT_DB_PASSWORD \
  -e N8N_DB_PASSWORD -e BACKUP_DB_PASSWORD -e ADMIN_DB_PASSWORD -e RESTORE_NOT_BEFORE -e RESTORE_AT \
  backup python3 -m support_agent.restore_job
unset key

if [ "$safe" = yes ]; then
  echo "== unpublishing every workflow in the $mode system (it must never post to Slack or run on a schedule)"
  dc run --rm --no-deps -T n8n unpublish:workflow --all # the n8n image adds "n8n" in front itself
  echo "== removing the Slack login (even a run started by hand must never reach Slack from here)"
  dc exec -T postgres psql -U postgres -d n8n -v ON_ERROR_STOP=1 -c "delete from credentials_entity where type = 'slackApi';"
  echo "== removing the outside watcher's ping address (this system must never tell it that all is well)"
  dc exec -T postgres psql -U postgres -d knowledge -v ON_ERROR_STOP=1 -c "do \$\$ begin
    if to_regclass('system_settings') is not null then delete from system_settings where name = 'healthchecks_ping_url';
    end if; end \$\$;" # the table exists from migration 012 on
  echo "== replacing the Slack signing secret with a made-up one (only clicks signed for testing are accepted)"
  dc exec -T postgres psql -U postgres -d knowledge -v ON_ERROR_STOP=1 -c "do \$\$ begin
    if to_regclass('slack_private.signing_secret') is not null then
      insert into slack_private.signing_secret (id, secret) values (1, '$COPY_SLACK_SECRET')
      on conflict (id) do update set secret = excluded.secret, set_at = now();
    end if; end \$\$;" # the table exists from migration 016 on
  dc up -d --build n8n agent
else
  dc up -d --build
fi

echo "== waiting for the agent and n8n"
# Each check gives up after 3 s, so an n8n that takes the connection but never answers can't stall the wait.
n8n_ready() { dc exec -T n8n wget -q -T 3 -O /dev/null http://127.0.0.1:5678/healthz/readiness 2>/dev/null; }
waited_from=$SECONDS
for _ in $(seq 1 60); do
  if dc exec -T agent python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" \
     2>/dev/null && n8n_ready; then break; fi
  sleep 2
done
dc exec -T agent python -c "import urllib.request; print('agent health:', urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status)"
# Checked as its own step: an n8n that crashes at every start would otherwise still end in "done".
n8n_ready || { echo "n8n isn't ready after $(( SECONDS - waited_from )) s: see its log" \
                    "(docker compose -p $project logs n8n)." >&2
               exit 1; }
echo "n8n ready"
echo "== done in $(( $(date +%s) - started )) s from the key prompt to a running system"
