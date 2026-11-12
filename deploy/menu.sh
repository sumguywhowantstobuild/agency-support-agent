#!/usr/bin/env bash
# The menu for deploy/apply.sh: `deploy/apply.sh` with nothing after it opens it. It only changes how
# you ask: every choice runs deploy/apply.sh with the same words you could type, so every check, refusal and "did you
# try it?" stays there. What is waiting to go live, which workflows were edited and the copy's data age come from
# deploy/notebook.py (tested in tests/test_notebook.py).
set -uo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash: keep container paths as they are
cd "$(dirname "$0")/.." || exit 1

apply() {
  echo "-> deploy/apply.sh $*"
  bash deploy/apply.sh "$@" && return 0
  echo "Stopped: see the message above. Nothing after that step was done." >&2
  return 1
}
copy() { docker compose -p support-agent-copy -f deploy/compose.yaml -f deploy/compose.copy.yaml --env-file .env \
           $([ -f .env.copy ] && echo --env-file .env.copy) "$@"; }
live() { docker compose -p support-agent-dev -f compose.dev.yaml --env-file .env "$@"; }
notebook() { uv run --quiet python deploy/notebook.py "$@"; }
copy_running() { [ -n "$(copy ps --status running -q n8n 2>/dev/null)" ]; }
workflows() { # id, name, version, one workflow a line by name, from the copy's or live's n8n
  "$1" exec -T postgres psql -U postgres -d n8n -At -F $'\t' -c 'select id, name, "versionId" from workflow_entity
    order by name' </dev/null
}
# The same line as in deploy/apply.sh: this machine's Docker version and image store.
docker_now() { echo "$(docker version -f '{{.Server.Platform.Name}} | engine {{.Server.Version}}') | store $(docker info -f '{{.Driver}}')"; }
pause() { read -r -p "Press Enter for the menu " _ || exit 0; }
ask_yes() { local a; read -r -p "$1 Type yes, or just Enter for no: " a || a=""; [ "$a" = yes ]; }
need_copy() { # the copy must run for this choice; offer to start it
  copy_running && return 0
  ask_yes "The test copy is stopped. Start it?" && apply copy start
}
offer_live() { ask_yes "Passed on the copy. Put it live now?" && apply live "$@"; }
short_versions() { # "postgres=pgvector/pgvector:0.8.7-pg18-trixie@sha256:..." -> "postgres 0.8.7-pg18-trixie"
  local x out=""
  for x in $1; do x=${x%%@*}; out+="${x%%=*} ${x##*:}, "; done
  echo "${out%, }"
}

status() {
  local version from
  version=$(live exec -T agent python -c "import json, urllib.request
print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5))['version'])" </dev/null 2>/dev/null) ||
    version="NOT ANSWERING"
  echo
  echo "== Where things stand"
  echo "  Live agent:  version $version"
  if copy_running; then echo "  Test copy:   running (its n8n: http://localhost:5680)"; else echo "  Test copy:   stopped"; fi
  from=$(notebook data-from) || exit 1
  if [ -n "$from" ]; then
    echo "  Copy's data: from the backup of $from, $(( ($(date -u +%s) - $(date -u -d "$from" +%s)) / 3600 )) hours old"
  else
    echo "  Copy's data: age not recorded yet (refresh the copy to record it)"
  fi
  local docker was
  docker=$(docker_now)
  was=$(notebook docker-changed --now "$docker") || exit 1
  if [ -n "$was" ]; then
    echo "  DOCKER CHANGED since the last tested change:"
    echo "    was: $was"
    echo "    now: $docker"
    echo "    Put a test change through the copy (3, then 7) before anything else goes live."
  fi
}

try_workflow() {
  need_copy || return 1
  local -a ids names args
  local id name version edited n i
  while IFS=$'\t' read -r id name version; do
    ids+=("$id"); names+=("$name"); args+=(--copy "$id=$version")
  done < <(workflows copy)
  edited=$(notebook edited "${args[@]}") || return 1
  echo "Workflows in the test copy (* = saved there since the refresh and not yet passed):"
  for i in "${!ids[@]}"; do
    grep -qx "${ids[$i]}" <<<"$edited" && n="*" || n=" "
    printf "  %2d) %s %s\n" $((i + 1)) "$n" "${names[$i]}"
  done
  read -r -p "Which one did you change? Number (Enter to go back): " n || return 1
  [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le ${#ids[@]} ] || return 0
  apply copy workflow "${ids[$((n - 1))]}" && offer_live workflow "${ids[$((n - 1))]}"
}

try_migration() {
  need_copy || return 1
  local -a files=(db/migrations/*.sql)
  local i n
  echo "Database changes (db/migrations), newest last:"
  for i in "${!files[@]}"; do printf "  %2d) %s\n" $((i + 1)) "$(basename "${files[$i]}")"; done
  read -r -p "Which one? Number (Enter to go back): " n || return 1
  [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le ${#files[@]} ] || return 0
  apply copy migration "$(basename "${files[$((n - 1))]}")" && offer_live migration "$(basename "${files[$((n - 1))]}")"
}

put_live() {
  local -a args items
  local f id name version kind key fp time n out stopped=""
  local -A wf_names=()
  for f in db/migrations/*.sql; do args+=(--file "$(basename "$f")=$(sha256sum "$f" | cut -c1-16)"); done
  # The versions deploy/compose.yaml names now (`live images` also checks that compose.dev.yaml names the same).
  args+=(--images "$(uv run --quiet python deploy/images.py deploy/compose.yaml 2>/dev/null)")
  while IFS=$'\t' read -r id name version; do wf_names[$id]=$name; done < <(workflows live)
  if copy_running; then
    while IFS=$'\t' read -r id name version; do args+=(--workflow "$id=$version"); wf_names[$id]=$name; done \
      < <(workflows copy)
  else
    args+=(--copy-stopped) # versions unknown: passed workflows are listed, and `live workflow` checks on the way
    stopped=" (the copy will be started: a workflow goes live from it)"
  fi
  # Captured first, so a damaged notebook stops here instead of looking like "nothing is waiting".
  out=$(notebook waiting "${args[@]}") || { echo "Stopped: the notebook couldn't be read (see above)." >&2; return 1; }
  [ -n "$out" ] && mapfile -t items <<<"$out"
  if [ ${#items[@]} -eq 0 ]; then echo "Nothing is waiting: every change that passed on the copy is live."; return 0; fi
  echo "Passed on the test copy, not live yet:"
  for n in "${!items[@]}"; do
    IFS=$'\t' read -r kind key fp time <<<"${items[$n]}"
    case $kind in
      agent) printf "  %d) Agent: image %s, model %s" $((n + 1)) "${fp:7:12}" "${fp#*|}" ;;
      migration) printf "  %d) Database change: %s" $((n + 1)) "$key" ;;
      workflow) printf "  %d) Workflow: %s%s" $((n + 1)) "${wf_names[$key]:-$key}" "$stopped" ;;
      images) printf "  %d) Software versions: %s" $((n + 1)) "$(short_versions "$fp")" ;;
    esac
    printf " (passed %s)\n" "${time%:*}"
  done
  read -r -p "Which one goes live? Number (Enter to go back): " n || return 1
  [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le ${#items[@]} ] || return 0
  IFS=$'\t' read -r kind key fp time <<<"${items[$((n - 1))]}"
  case $kind in
    agent) apply live agent ;;
    workflow) { copy_running || apply copy start; } && apply live workflow "$key" ;;
    *) apply live "$kind" "$key" ;;
  esac
}

while true; do
  status
  echo
  echo "What do you want to do?"
  echo "  1) Refresh the test copy from a fresh backup of live (asks for the backup key)"
  if copy_running; then echo "  2) Stop the test copy (its data is kept)"; else echo "  2) Start the test copy"; fi
  echo "  3) Try an agent change on the copy (code, prompt or model)"
  echo "  4) Accuracy test on the copy (needed when the AI part or the model changed; about 20 minutes)"
  echo "  5) Try a workflow change on the copy"
  echo "  6) Try a database change on the copy"
  echo "  7) Put a tested change live (a database change, workflow or software versions take a fresh backup first)"
  echo "  8) Try new software versions on the copy (database, n8n, tunnel: the image lines in both compose files)"
  echo "  q) Quit"
  read -r -p "Choose: " choice || exit 0
  case $choice in
    1) apply copy refresh ;;
    2) if copy_running; then apply copy stop; else apply copy start; fi ;;
    3) apply copy agent && offer_live agent ;;
    4) need_copy && apply copy evals ;;
    5) try_workflow ;;
    6) try_migration ;;
    7) put_live ;;
    8) apply copy images && offer_live images ;; # starts the copy itself, database first (not through `copy start`)
    q | Q) exit 0 ;;
    *) echo "Choose 1 to 8, or q." ;;
  esac
  pause
done
