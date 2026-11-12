#!/usr/bin/env bash
# Security scan of every image the system runs: part of the monthly update routine (SETUP section 12), and before a
# new version goes live. Trivy looks each image's packages up in public vulnerability lists and prints a count of
# critical and high findings per image (and how many have a fix); the full tables go to deploy/.scans/<date>/.
# Trivy is online only to download its list of known weaknesses (into the trivy-cache volume); each scan then runs
# with networking off, so nothing from an image can leave the PC even if Trivy were tampered with. Each image is
# handed over as a saved copy on standard input: Trivy never gets the Docker socket, so it can't start, stop or
# change anything. Trivy itself is pinned by digest (0.74.0 came out after the March 2026 Trivy
# compromise, CVE-2026-33634; move it on only after a release is a week old).
#
#   deploy/scan.sh            what live runs now: database, n8n, tunnel, agent, backup service
#   deploy/scan.sh IMAGE ...  only these (a name:tag or an image ID), for example a version you are thinking of
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash: keep container paths as they are
cd "$(dirname "$0")/.."
TRIVY=aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969
live() { docker compose -p support-agent-dev -f compose.dev.yaml --env-file .env "$@"; }

if [ $# -gt 0 ]; then
  images=("$@")
else # what live runs now (not what the compose files name: new versions may be written there, not live yet)
  images=()
  for service in postgres n8n tunnel agent backup; do
    id=$(live ps -q "$service")
    [ -n "$id" ] && images+=("$(docker inspect -f '{{.Image}}' "$id")") || echo "live $service isn't running: not scanned"
  done
fi

echo "== Trivy's list of known weaknesses: downloaded or refreshed (the only step with internet)"
docker run --rm -v trivy-cache:/root/.cache/trivy "$TRIVY" image --quiet --download-db-only
out="deploy/.scans/$(date +%F)"
mkdir -p "$out"
echo "Critical and high findings per image (fixable = a newer version of that package fixes it):"
for image in "${images[@]}"; do
  name=$(docker image inspect -f '{{if .RepoTags}}{{index .RepoTags 0}}{{else}}{{.Id}}{{end}}' "$image" 2>/dev/null) ||
    { docker pull -q "$image" >/dev/null && name=$image; }
  file="$out/$(tr -c 'A-Za-z0-9._-' '_' <<<"${name:0:60}").json"
  docker save "$image" | docker run -i --rm --network none -v trivy-cache:/root/.cache/trivy:ro --entrypoint sh \
    "$TRIVY" -c 'cat > /tmp/image.tar && trivy image --quiet --skip-db-update --offline-scan --cache-backend memory \
      --scanners vuln --severity CRITICAL,HIGH --format json --input /tmp/image.tar' >"$file"
  uv run --quiet python -c '
import json, sys
found = [v for r in json.load(open(sys.argv[1])).get("Results") or [] for v in r.get("Vulnerabilities") or []]
critical, high = (sum(v["Severity"] == s for v in found) for s in ("CRITICAL", "HIGH"))
fixable = sum(bool(v.get("FixedVersion")) for v in found)
print(f"  {sys.argv[2]}: critical {critical}, high {high} ({fixable} fixable)")' "$file" "${name:0:60}"
done
echo "Full findings: $out/ (this machine only, not in Git). Each finding is fixed, or written down with why it can't reach us."
