#!/usr/bin/env bash
# Every change goes to the test copy first, then the same way to the live system (SETUP section 12).
# Run from the repository folder.
#
#   deploy/apply.sh                            A menu: where things stand, then numbered choices (deploy/menu.sh)
#   deploy/apply.sh copy refresh               Replace the copy with a fresh backup of live (asks for the backup key)
#   deploy/apply.sh copy start | stop          Start the copy to test a change; stop it when done (its data is kept)
#   deploy/apply.sh copy agent                 Run the tests, build the agent, start it and the admin app in the copy
#   deploy/apply.sh copy evals [--runs 1 ...]  Accuracy test against the copy's agent (full set only counts)
#   deploy/apply.sh copy migration <file>      Apply a db/migrations file to the copy, then show its db/checks file
#   deploy/apply.sh copy workflow <id>         A workflow edited and tried in the copy's n8n: safety scan, mark tested
#   deploy/apply.sh copy images                New database / n8n / tunnel versions (image lines in both compose
#                                              files): start the copy on them, fix sorting rules, check, mark tested
#   deploy/apply.sh live agent | migration <file> | workflow <id> | images
#                                              The same change on the live system. Refused unless the copy passed
#                                              exactly this change: the same agent image (moved over, never rebuilt)
#                                              and model, the same migration file, the same workflow version, the
#                                              same image versions. Every live change that writes data (migration,
#                                              workflow, images) takes a fresh backup of live first, after its
#                                              refusals and before its first write, and changes nothing if that
#                                              backup fails; the backup's time goes in the notebook line. `live agent`
#                                              swaps the program only, so it takes none.
#
# What passed on the copy, and what went live, is written to deploy/.tested (this machine only, not in Git). Settings that
# should differ in the copy only (for example OPENAI_MODEL while a new model is tried) go in .env.copy.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash: keep container paths as they are
[ $# -gt 0 ] || exec bash "$(dirname "$0")/menu.sh"

target=${1:-}
kind=${2:-}
[ -f .env ] || { echo ".env is missing: run from the repository folder" >&2; exit 1; }
case "$target" in
  copy)
    project=support-agent-copy
    files=(-f deploy/compose.yaml -f deploy/compose.copy.yaml)
    envs=(--env-file .env)
    [ -f .env.copy ] && envs+=(--env-file .env.copy) ;;
  live)
    # The live system on a PC (compose.dev.yaml). On a server it would be -p support-agent -f deploy/compose.yaml.
    project=support-agent-dev
    files=(-f compose.dev.yaml)
    envs=(--env-file .env) ;;
  *) sed -n '4,22p' "$0" >&2; exit 2 ;;
esac
dc() { docker compose -p "$project" "${files[@]}" "${envs[@]}" "$@"; }
copy() { docker compose -p support-agent-copy -f deploy/compose.yaml -f deploy/compose.copy.yaml --env-file .env \
           $([ -f .env.copy ] && echo --env-file .env.copy) "$@"; }

STATE=deploy/.tested
touch "$STATE"
record() { printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "$2" "$3" "${4:-}" >>"$STATE"; } # kind key print note
# This machine's Docker: the version and the image store. deploy/menu.sh has the same line, and warns when it differs
# from the one at the newest pass (a Docker Desktop update can switch the image store, so earlier images may be gone).
docker_now() { echo "$(docker version -f '{{.Server.Platform.Name}} | engine {{.Server.Version}}') | store $(docker info -f '{{.Driver}}')"; }
record_pass() { record "$@"; record platform docker "$(docker_now)"; } # a pass on the copy, and the Docker it ran on
# (docker compose exec passes this script's input on, so steps that need none get </dev/null: the answer to confirm
# must stay for confirm.)
passed() { awk -F'\t' -v k="$1" -v n="$2" -v p="$3" '$2 == k && $3 == n && $4 == p' "$STATE" | tail -1; }
confirm() {
  read -r -p "$1 Type yes to record it as passed: " answer || answer=""
  [ "$answer" = yes ] || { echo "Not recorded: nothing goes live until this passes." >&2; exit 1; }
}
refuse() { echo "REFUSED: $1" >&2; exit 1; }

# The parts of the agent that decide what the AI writes. A change here (or a new model) needs the accuracy test.
# ghl/move_tokens.py is left out: a one-time tool that nothing in drafting imports (only its own test does).
# (No single quotes inside the python below: it sits in single quotes.)
ai_print() {
  docker run --rm --network none --entrypoint python "$1" -c '
import hashlib, pathlib, support_agent
root = pathlib.Path(support_agent.__file__).parent
h = hashlib.sha256()
for p in sorted(q for d in ("agent", "knowledge", "ghl") for q in (root / d).rglob("*.py") if q != root / "ghl" / "move_tokens.py"):
    h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
print(h.hexdigest()[:16])'
}
# The image a container runs. Only trustworthy for a container created from that image: since Docker Desktop 4.95's
# image store, rebuilding unchanged code gives a new image ID while the running container stays on the old one (same
# content), so every agent start here uses --force-recreate.
running_image() { docker inspect -f '{{.Image}}' "$(dc ps -q "$1")"; }
# The admin app runs the agent's image (tagged under its own name, never built apart), so every agent start below
# starts it too. It is left out when its tag isn't the agent's image: before the first `copy agent` (no admin image
# yet), and after a `copy refresh`, which rebuilds the agent only.
with_admin() {
  local admin agent
  admin=$(docker image inspect -f '{{.Id}}' "$project-admin:latest" 2>/dev/null) || return 0
  agent=$(docker image inspect -f '{{.Id}}' "$project-agent:latest" 2>/dev/null) || agent=""
  if [ "$admin" = "$agent" ]; then echo admin
  else echo "(admin app left out: its image isn't the agent's; deploy/apply.sh copy agent starts both on one)" >&2; fi
}
has_admin() { # the image holds the admin app (images made before 1.1.0 don't)
  docker run --rm --network none --entrypoint python "$1" \
    -c 'import importlib.util, sys; sys.exit(importlib.util.find_spec("support_agent.admin.app") is None)' </dev/null >/dev/null 2>&1
}
admin_image() { local id; id=$(dc ps -q admin </dev/null); [ -n "$id" ] && docker inspect -f '{{.Image}}' "$id" || echo none; }
compose_model() { # the model the agent gets, as Compose resolves it (a value exported in the shell beats .env)
  dc config --format json </dev/null |
    uv run python -c 'import json, sys; print(json.load(sys.stdin)["services"]["agent"]["environment"]["OPENAI_MODEL"])'
}
snapshot() { # freeze a file before it is fingerprinted and applied, so what runs is exactly what is recorded
  snap=$(mktemp); trap 'rm -f "$snap"' EXIT
  cp "$1" "$snap"; fp=$(sha256sum "$snap" | cut -c1-16)
}
live_n8n_versions() { # the live workflow's saved version and published version
  dc exec -T postgres psql -U postgres -d n8n -Atc "select \"versionId\" || '|' || coalesce(\"activeVersionId\", '')
    from workflow_entity where id = '$1'" </dev/null
}

migration_file() {
  local name; name=$(basename "${1:?usage: deploy/apply.sh $target migration <file in db/migrations>}")
  [ -f "db/migrations/$name" ] || { echo "db/migrations/$name not found" >&2; exit 1; }
  echo "db/migrations/$name"
}
# This run's own export file, from the version read through the checks to the import: with one shared file, a second
# run during the backup could swap in a workflow that never passed.
wf_file="/tmp/apply-wf-$$.json"
workflow_version() { # the version of a workflow in the copy's n8n
  copy exec -T n8n sh -c "n8n export:workflow --id='$1' --output=$wf_file >/dev/null 2>&1 &&
    node -e 'console.log(require(\"$wf_file\")[0].versionId)'" </dev/null || refuse "workflow $1 not found in the copy"
}
live() { docker compose -p support-agent-dev -f compose.dev.yaml --env-file .env "$@"; }

# The upgrade path (`copy images`, `live images`): the outside software versions, pinned by digest.
wanted_versions() { # what the compose files name now; both must name the same (the drift test checks it too)
  local copy_file live_file
  copy_file=$(uv run --quiet python deploy/images.py deploy/compose.yaml) || refuse "fix deploy/compose.yaml first"
  live_file=$(uv run --quiet python deploy/images.py compose.dev.yaml) || refuse "fix compose.dev.yaml first"
  [ "$copy_file" = "$live_file" ] ||
    refuse "compose.dev.yaml and deploy/compose.yaml name different versions: make them the same (uv run pytest)"
  echo "$copy_file"
}
version_of() { tr ' ' '\n' <<<"$1" | sed -n "s/^$2=//p"; } # one service's image from a versions line
created_from() { # the image a running service's container was created from, as written in the compose file
  local id; id=$(dc ps -q "$1" </dev/null)
  [ -n "$id" ] && docker inspect -f '{{.Config.Image}}' "$id" || echo "not running"
}
fix_collation() { # each database whose sorting rules changed with the base system: rebuild its indexes, record it
  local stale db
  stale=$(dc exec -T postgres psql -U postgres -d postgres -Atc "select datname from pg_database where datallowconn
    and datcollversion is distinct from pg_database_collation_actual_version(oid) order by 1" </dev/null) ||
    refuse "couldn't read the databases' sorting-rules versions"
  if [ -z "$stale" ]; then echo "== sorting rules unchanged in every database: no index rebuild needed"; return 0; fi
  for db in $stale; do
    dc exec -T postgres psql -U postgres -d "$db" <deploy/collation.sql || refuse "the index rebuild in $db failed"
  done
  # template0 (no connections allowed, by design) is the untouched pattern new databases start from: left as it is.
  stale=$(dc exec -T postgres psql -U postgres -d postgres -Atc "select string_agg(datname, ', ') from pg_database
    where datallowconn and datcollversion is distinct from pg_database_collation_actual_version(oid)" </dev/null)
  [ -z "$stale" ] || refuse "still on the old sorting rules after the rebuild: $stale"
}
published_workflows() { dc exec -T postgres psql -U postgres -d n8n -Atc \
  'select count(*) from workflow_entity where "activeVersionId" is not null' </dev/null; }
check_workflow() { # the exported workflow ($wf_file in the copy's n8n) against the live n8n's logins
  echo "== workflow checks: SQL safety scan, every Slack step has its login, every login exists on live"
  local logins
  logins=$(live exec -T postgres psql -U postgres -d n8n -Atc "select id || ':' || type from credentials_entity" </dev/null)
  # shellcheck disable=SC2086  # one argument per login
  copy exec -T n8n cat "$wf_file" </dev/null | uv run python deploy/check_workflow.py $logins ||
    refuse "fix the steps above in the copy's n8n (http://localhost:5680), then run this again"
}
fresh_backup() { # a backup of live taken now, right before a live change that writes data; sets $taken
  # Called after the command's own refusals and before its first write, so a refused change takes none and a failed
  # backup changes nothing. The way back for damaged data is this backup, restored by its time (--at: newer backups
  # follow within minutes and the restore otherwise takes the newest). Printed without a command: which restore fits
  # depends on where live runs, and on the live PC `server` would start a second system on live's tunnel (SETUP section 11).
  local out
  echo "== a fresh backup of live first: the way back if anything below goes wrong (SETUP section 11, Restore)"
  out=$(dc exec -T backup python3 -m support_agent.backup_job --now </dev/null) || refuse "the backup failed: nothing changed"
  taken=$(sed -n 's/^backup taken: //p' <<<"$out")
  [ -n "$taken" ] || refuse "the backup didn't say it was taken: nothing changed"
  echo "   backup taken: $taken (the way back to before this change: that backup by its time, --at $taken, SETUP section 11)"
}
wait_n8n() {
  for _ in $(seq 1 45); do dc exec -T n8n wget -q -O /dev/null http://127.0.0.1:5678/healthz/readiness 2>/dev/null &&
    return 0; sleep 2; done
  echo "n8n did not come back within 90 s: check 'docker compose -p $project logs n8n'" >&2; exit 1
}

case "$target $kind" in
  "copy refresh")
    log=$(mktemp); trap 'rm -f "$log"' EXIT
    deploy/restore.sh copy | tee "$log"
    # The copy's data age for the menu: the backup the restore used ("newest complete backup: 2026-10-15 02:31:07 UTC").
    stamp=$(sed -n 's/^newest complete backup: \([0-9-]*\) \([0-9:]*\) UTC.*$/\1T\2Z/p' "$log" | tail -1)
    [ -n "$stamp" ] || refuse "the backup time wasn't found in the restore's output: the menu can't trust the copy"
    record refresh copy "$stamp" "restored from a fresh backup of live"
    # Each workflow's version now (live's, since the backup was just taken): the menu marks what changes after this.
    copy exec -T postgres psql -U postgres -d n8n -At -F $'\t' -c 'select id, "versionId" from workflow_entity' \
      </dev/null | while IFS=$'\t' read -r id version; do record baseline "$id" "$version"; done
    # The restore ran on the versions the compose files name (and the backup image built from them): `live images`
    # needs this before a new database version goes live, so the backups are proven to restore onto it.
    record restored images "$(wanted_versions)" "a fresh backup of live restored onto these versions" ;;
  "copy start") # the database first and its sorting rules checked, so n8n never writes through stale indexes
    dc up -d --no-build --no-deps --wait postgres </dev/null
    fix_collation
    dc up -d --no-build --wait n8n agent $(with_admin) </dev/null
    echo "copy running: n8n http://localhost:5680, admin app http://127.0.0.1:8003 (with a test pass)" ;;
  "copy stop") dc stop ;;

  "copy agent")
    # Without the file Docker would make an empty folder in its place and the admin app would never start.
    [ -f .admin-test-keys.json ] || refuse "the copy's admin app needs its test sign-in keys first (SETUP section 14):
  uv run python scripts/mint_test_pass.py <your email> .admin-test-keys.json <CF_AUDIENCE from .env.copy>"
    echo "== tests (this folder is what the image is built from)"
    uv run pytest -q -n 4 # 4 at once: at 8 an ordinary PC overloads and tests fail at random (CHANGELOG 1.4.1)
    echo "== building the agent and starting it in the copy, then the admin app on the same image"
    dc up -d --build --force-recreate --wait agent
    image=$(running_image agent)
    docker image inspect "$image" >/dev/null 2>&1 ||
      refuse "the copy's agent runs image ${image:7:12}, which Docker can't find by that ID: nothing recorded"
    docker tag "$image" "$project-admin:latest"
    dc up -d --no-build --no-deps --force-recreate --wait admin
    [ "$(admin_image)" = "$image" ] || refuse "the copy's admin app doesn't run the agent's image: nothing recorded"
    model=$(dc exec -T agent printenv OPENAI_MODEL)
    record_pass agent agent "$image|$model" "tests passed, health ok (agent and admin)"
    echo "PASSED on the copy: agent and admin image ${image:7:12}, model $model"
    echo "If it changes what the AI writes (agent/, knowledge/, ghl/ or the model): deploy/apply.sh copy evals" ;;

  "copy evals")
    image=$(running_image agent)
    model=$(dc exec -T agent printenv OPENAI_MODEL)
    report="evals/reports/$(date +%F)-copy-${image:7:7}.md"
    echo "== accuracy test against the copy's agent (about 20 minutes for the full set) -> $report"
    mkdir -p evals/reports # generated output, git-ignored
    dc exec -T agent python -m support_agent.evals.run "${@:3}" <evals/tickets.toml >"$report"
    sed -n '1,25p' "$report"
    if [ $# -gt 2 ]; then echo "Part of the set only (${*:3}): shown, not recorded."; exit 0; fi
    confirm "Does $report meet every pass mark for the accuracy test?"
    record_pass evals agent "$image|$model" "$report" ;;

  "copy migration")
    file=$(migration_file "${3:-}")
    snapshot "$file"
    echo "== applying $file to the copy (one transaction: all or nothing)"
    dc exec -T postgres psql -U postgres -d knowledge -v ON_ERROR_STOP=1 -1 <"$snap"
    check="db/checks/$(basename "$file")"
    if [ -f "$check" ]; then
      echo "== its check ($check; rolled back, changes nothing)"
      dc exec -T postgres psql -U postgres -d knowledge <"$check"
      confirm "Does every line above match its 'expect'?"
    else
      confirm "No check file for this migration. Did you check its effect in the copy by hand?"
    fi
    record_pass migration "$(basename "$file")" "$fp" "applied and checked on the copy" ;;

  "copy workflow")
    id=${3:?usage: deploy/apply.sh copy workflow <id> (list: docker compose -p support-agent-copy ... exec n8n n8n list:workflow)}
    version=$(workflow_version "$id")
    check_workflow
    confirm "Did you try workflow $id in the copy's n8n (http://localhost:5680) and see it do the right thing?"
    record_pass workflow "$id" "$version" "scanned and tried on the copy" ;;

  "copy images")
    want=$(wanted_versions)
    echo "== the copy on the versions in the compose files: the database first, its sorting rules, then the rest"
    dc stop n8n agent admin </dev/null # (an admin app that isn't there: nothing to stop)
    dc up -d --no-build --no-deps --wait postgres </dev/null
    fix_collation
    dc up -d --no-build --wait n8n agent $(with_admin) </dev/null
    wait_n8n
    for s in postgres n8n; do
      [ "$(created_from "$s")" = "$(version_of "$want" "$s")" ] ||
        refuse "the copy's $s doesn't run the version the compose file names (it runs $(created_from "$s"))"
    done
    echo "== the tunnel's version starts (only that: connected, it would take live's Slack traffic)"
    docker run --rm --network none "$(version_of "$want" tunnel)" --version </dev/null || refuse "the tunnel image didn't start"
    echo "== the copy now"
    dc exec -T postgres psql -U postgres -d knowledge -Atc "select 'database: ' || version()" </dev/null
    dc exec -T postgres psql -U postgres -d knowledge -Atc "select 'data: ' || (select count(*) from documents) ||
      ' passages, ' || (select count(*) from tickets) || ' tickets'" </dev/null
    echo "n8n: ready, $(dc exec -T postgres psql -U postgres -d n8n -Atc 'select count(*) from workflow_entity' \
      </dev/null) workflows; agent: $(dc exec -T agent python -c "import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).read().decode())" </dev/null)"
    confirm "Did you try the workflows on the copy (http://localhost:5680: Execute workflow, or signed Slack copies) and see them work?"
    record_pass images images "$want" "started healthy on the copy, sorting rules checked" ;;

  "live images")
    want=$(wanted_versions)
    [ -n "$(passed images images "$want")" ] ||
      refuse "these versions have not passed on the copy: deploy/apply.sh copy images"
    was="postgres=$(created_from postgres) n8n=$(created_from n8n) tunnel=$(created_from tunnel)"
    [ "$was" != "$want" ] || { echo "Live already runs these versions."; exit 0; }
    if [ "$(version_of "$want" postgres)" != "$(created_from postgres)" ]; then
      [ -n "$(passed restored images "$want")" ] ||
        refuse "a new database version needs the restore test on the copy first: deploy/apply.sh copy refresh (menu 1)"
    fi
    before=$(published_workflows)
    fresh_backup
    echo "== n8n stopped while the database changes (Slack retries what it sends meanwhile)"
    dc stop tunnel n8n </dev/null
    dc up -d --no-build --no-deps --wait postgres </dev/null
    fix_collation
    # --no-deps: the tunnel's other dependency is the admin app, and through it the agent; neither changes here (and
    # before the first `live agent` there is no admin image at all, so starting it would fail with Slack offline).
    dc --profile tunnel up -d --no-build --no-deps --wait n8n tunnel </dev/null
    wait_n8n
    after=$(published_workflows)
    [ "$after" = "$before" ] || refuse "published workflows were $before, now $after: check live n8n before anything else"
    dc exec -T agent python -c "import urllib.request
urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)" </dev/null || refuse "the live agent doesn't answer"
    echo "== the backup service rebuilt on the new database base, then one backup through it"
    dc up -d --build --no-deps backup </dev/null
    out=$(dc exec -T backup python3 -m support_agent.backup_job --now </dev/null) ||
      refuse "live runs the new versions, but a backup through the rebuilt backup service failed: fix that first"
    now="postgres=$(created_from postgres) n8n=$(created_from n8n) tunnel=$(created_from tunnel)"
    [ "$now" = "$want" ] || refuse "live doesn't run the tested versions after the switch: $now"
    record live-images images "$want" "was: $was; backup before: $taken"
    echo "LIVE: the new versions, $after workflows published, backup before the switch: $taken. Add it to CHANGELOG.md." ;;

  "live agent")
    model=$(compose_model)
    tested=$(awk -F'\t' '$2 == "agent" && $3 == "agent"' "$STATE" | tail -1 | cut -f4)
    [ -n "$tested" ] || refuse "no agent change has passed on the copy: deploy/apply.sh copy agent"
    image=${tested%|*}
    [ "${tested#*|}" = "$model" ] ||
      refuse "the copy passed with model ${tested#*|}, but live would get $model: test this model on the copy first"
    docker image inspect "$image" >/dev/null 2>&1 || refuse "the tested image is gone: deploy/apply.sh copy agent again"
    has_admin "$image" ||
      refuse "the newest pass (image ${image:7:12}) is from before the admin app: deploy/apply.sh copy agent again"
    old=$(running_image agent)
    old_admin=$(admin_image)
    old_model=$(dc exec -T agent printenv OPENAI_MODEL </dev/null)
    if [ "$old" = "$image" ] && [ "$old_admin" = "$image" ] && [ "$old_model" = "$model" ]; then
      # Same tested image and model: only a changed setting in .env (a new Slack token, say) can still need applying.
      # Without --force-recreate, Compose restarts a container only if its settings differ from what it runs. These
      # settings are live's own (tokens, Access, passwords): the copy has stand-ins and can't test them, so the guard
      # here is the health wait and the image check below.
      echo "Live already runs this image (agent and admin) and model; applying any changed settings."
      docker tag "$image" "$project-agent:latest" # a container recreated for a setting must get the tested image back,
      docker tag "$image" "$project-admin:latest" # even if a build moved a tag since
      dc up -d --no-build --no-deps --wait agent admin </dev/null ||
        refuse "a container didn't come back healthy after a settings change: docker compose -p $project logs admin agent"
      [ "$(running_image agent)" = "$image" ] && [ "$(admin_image)" = "$image" ] ||
        refuse "live is not running the tested image after applying settings: check 'docker compose -p $project ps'"
      exit 0
    fi
    if [ "$(ai_print "$image")" != "$(ai_print "$old")" ] || [ "$old_model" != "$model" ]; then
      echo "This changes what the AI writes."
      [ -n "$(passed evals agent "$image|$model")" ] ||
        refuse "it needs the full accuracy test on the copy first: deploy/apply.sh copy evals"
    fi
    echo "== moving the tested image to live, agent and admin app (no rebuild: live runs exactly what the copy tested)"
    docker tag "$image" "$project-agent:latest"
    docker tag "$image" "$project-admin:latest"
    if ! dc up -d --no-build --no-deps --force-recreate --wait agent admin; then
      back_admin="docker tag $old_admin $project-admin:latest && dc up -d --no-build --no-deps --force-recreate admin"
      [ "$old_admin" != none ] || back_admin="dc stop admin" # it wasn't running before
      echo "Live agent or admin app did not start healthy (docker compose -p $project logs admin agent). To go back," \
           "with dc = docker compose -p $project -f compose.dev.yaml --env-file .env:" \
           "docker tag $old $project-agent:latest && dc up -d --no-build --no-deps --force-recreate agent; $back_admin" >&2
      exit 1
    fi
    [ "$(running_image agent)" = "$image" ] && [ "$(admin_image)" = "$image" ] &&
      [ "$(dc exec -T agent printenv OPENAI_MODEL </dev/null)" = "$model" ] ||
      refuse "live is not running the tested image and model after the restart: check 'docker compose -p $project ps'"
    record live-agent agent "$image|$model" "was ${old:7:12} (admin ${old_admin:7:12}), $old_model"
    echo "LIVE: agent and admin image ${image:7:12}, model $model (before: ${old:7:12}). Add the change to CHANGELOG.md." ;;

  "live migration")
    file=$(migration_file "${3:-}")
    snapshot "$file"
    [ -n "$(passed migration "$(basename "$file")" "$fp")" ] ||
      refuse "this exact file has not passed on the copy: deploy/apply.sh copy migration $(basename "$file")"
    fresh_backup
    echo "== applying $file to live (one transaction: all or nothing)"
    dc exec -T postgres psql -U postgres -d knowledge -v ON_ERROR_STOP=1 -1 <"$snap"
    check="db/checks/$(basename "$file")"
    [ -f "$check" ] && dc exec -T postgres psql -U postgres -d knowledge <"$check"
    record live-migration "$(basename "$file")" "$fp" "backup before: $taken"
    echo "LIVE: $(basename "$file"), backup before the change: $taken. Compare the check with the copy's. Add the change to CHANGELOG.md." ;;

  "live workflow")
    id=${3:?usage: deploy/apply.sh live workflow <id>}
    version=$(workflow_version "$id")
    [ -n "$(passed workflow "$id" "$version")" ] ||
      refuse "this version of workflow $id has not passed on the copy (changed since?): deploy/apply.sh copy workflow $id"
    check_workflow
    fresh_backup
    echo "== moving workflow $id from the copy to live, then publishing it"
    copy exec -T n8n sh -c "cat $wf_file && rm -f $wf_file" | dc exec -T n8n sh -c "cat > $wf_file"
    before=$(live_n8n_versions "$id")
    dc exec -T n8n sh -c "n8n import:workflow --input=$wf_file && n8n publish:workflow --id='$id'; s=\$?
      rm -f $wf_file; exit \$s" </dev/null ||
      refuse "import or publish failed on live: nothing recorded, live keeps the version it had"
    after=$(live_n8n_versions "$id")
    [ "${after%%|*}" != "${before%%|*}" ] && [ "${after%%|*}" = "${after#*|}" ] ||
      refuse "live did not take the new version of workflow $id as its published one (before $before, after $after)"
    echo "== restarting live n8n so it runs the new version (Slack retries anything sent in the gap)"
    dc restart n8n
    wait_n8n
    dc exec -T n8n n8n list:workflow --active=true 2>/dev/null | grep -q "^$id|" || refuse "workflow $id is not published on live"
    record live-workflow "$id" "$version" "backup before: $taken"
    echo "LIVE: workflow $id published, backup before the change: $taken. Export it into n8n/ with the PASTE_ placeholders, run the tests, add it to CHANGELOG.md." ;;

  *) sed -n '4,22p' "$0" >&2; exit 2 ;;
esac
