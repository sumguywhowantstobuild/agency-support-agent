"""deploy/restore.sh, run against a stand-in `docker` that only records what it was asked to do. The staged mode
(a new server restored with everything that reaches outside switched off: a restore rehearsal and step 3 of
a hand-over) must take the test copy's safety steps, on the server's own project, and never start the tunnel or the
backup service; the server mode must still start everything."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

PASSWORDS = ("POSTGRES_PASSWORD", "AGENT_DB_PASSWORD", "KB_SYNC_DB_PASSWORD", "N8N_SUPPORT_DB_PASSWORD",
             "N8N_DB_PASSWORD", "BACKUP_DB_PASSWORD", "ADMIN_DB_PASSWORD")


def run_restore(tmp_path, mode, failing="", expect_ok=True, passwords=PASSWORDS):
    """Runs the real script with a fake .env and a stand-in docker (a call containing `failing`, when given, fails)
    and a stand-in sleep (no waiting); returns each docker call as one line, or the result when not `expect_ok`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text('#!/usr/bin/env bash\nall="$*"\necho "${all//$\'\\n\'/ }" >> "$DOCKER_LOG"\n'
                    '[[ $all != *restore_job* ]] || echo "restore env: RESTORE_AT=${RESTORE_AT-unset} '
                    'RESTORE_NOT_BEFORE=${RESTORE_NOT_BEFORE-unset}" >> "${DOCKER_LOG%.log}-env.log"\n'
                    f'[ -z "{failing}" ] || [[ $all != *"{failing}"* ]]\n',
                    newline="\n")  # one line per call, even for a multi-line SQL argument
    fake.chmod(0o755)
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\n", newline="\n")
    (bin_dir / "sleep").chmod(0o755)
    (tmp_path / ".env").write_text("".join(f"{name}=test-{name.lower()}\n" for name in passwords), newline="\n")
    log = tmp_path / "docker.log"
    script = (ROOT / "deploy" / "restore.sh").as_posix()
    command = f'export PATH="$(cygpath -u "{bin_dir}" 2>/dev/null || echo "{bin_dir}"):$PATH"; ' \
              f'export DOCKER_LOG="$(cygpath -u "{log}" 2>/dev/null || echo "{log}")"; bash "{script}" {mode}'
    result = subprocess.run([BASH, "-c", command], cwd=tmp_path, input="AGE-SECRET-KEY-TEST\n", text=True,
                            capture_output=True, timeout=180)
    if not expect_ok:
        return result
    assert result.returncode == 0, result.stderr
    return log.read_text().splitlines()


def test_staged_takes_the_copy_safety_steps_on_the_server_project(tmp_path):
    calls = run_restore(tmp_path, "staged")
    assert all(c.startswith("compose -p support-agent -f deploy/compose.yaml -f deploy/compose.staged.yaml")
               for c in calls), calls
    joined = "\n".join(calls)
    assert "unpublish:workflow --all" in joined
    assert "delete from credentials_entity where type = 'slackApi'" in joined
    assert "healthchecks_ping_url" in joined
    assert "slack_private.signing_secret" in joined
    assert "backup_job --now" not in joined  # no fresh backup: there is no live system on this machine


def test_staged_never_starts_the_tunnel_or_the_backup_service(tmp_path):
    starts = [c for c in run_restore(tmp_path, "staged") if " up " in f" {c} "]
    assert starts[-1].endswith("up -d --build n8n agent"), starts
    assert not any(c.endswith("up -d --build") for c in starts)


def test_server_still_starts_everything(tmp_path):
    calls = run_restore(tmp_path, "server")
    assert calls[-1 - sum(1 for c in calls if " exec " in f" {c} ")].endswith("up -d --build")
    assert "unpublish:workflow" not in "\n".join(calls)


def test_the_admin_apps_password_goes_to_the_restore(tmp_path):
    """The restore sets the admin app's login password (1.1.0) from .env, like every other login's. A backup from
    before 1.1.0 has no such login; the restore job skips it (test_backup)."""
    restore = next(c for c in run_restore(tmp_path, "staged") if "restore_job" in c)
    assert "-e ADMIN_DB_PASSWORD" in restore


def test_an_env_without_the_admin_apps_password_is_refused(tmp_path):
    """Compose itself stops without it (both compose files require it), so the restore says so first, by name."""
    result = run_restore(tmp_path, "staged", expect_ok=False,
                         passwords=tuple(p for p in PASSWORDS if p != "ADMIN_DB_PASSWORD"))
    assert result.returncode != 0
    assert "ADMIN_DB_PASSWORD is not set in .env" in result.stderr


def test_an_n8n_that_never_gets_ready_fails_the_restore(tmp_path):
    """A restore rehearsal once printed "done" while n8n was crashing at every start: it was checked once, a second
    after starting, and its failure didn't stop the script."""
    result = run_restore(tmp_path, "staged", failing="healthz/readiness", expect_ok=False)
    assert result.returncode != 0
    assert "n8n isn't ready" in result.stderr
    assert "== done" not in result.stdout


def test_every_readiness_check_has_a_time_limit(tmp_path):
    """An n8n that takes the connection but never answers must not stall the wait: the agent's
    check already gives up after 3 s, n8n's must too."""
    calls = [c for c in run_restore(tmp_path, "staged") if "healthz/readiness" in c or "/health" in c]
    assert calls and all("-T 3 " in c or "timeout=3" in c for c in calls)


def restore_env(tmp_path):
    """The two settings the stand-in docker saw when it ran the restore job (they go by name, not on its command)."""
    return (tmp_path / "docker-env.log").read_text().strip()


def test_at_hands_the_backups_time_to_the_restore(tmp_path):
    """Going back after a live change restores the backup it took first, by the time it printed (apply.sh); without
    --at the restore takes the newest complete backup, as before."""
    calls = run_restore(tmp_path, "server --at 2026-11-09T10:00:00Z")
    assert "-e RESTORE_AT" in next(c for c in calls if "restore_job" in c)
    assert restore_env(tmp_path) == "restore env: RESTORE_AT=2026-11-09T10:00:00Z RESTORE_NOT_BEFORE="
    for leftover in ("docker.log", "docker-env.log"):
        (tmp_path / leftover).unlink()
    shutil.rmtree(tmp_path / "bin")
    run_restore(tmp_path, "staged")
    assert restore_env(tmp_path) == "restore env: RESTORE_AT= RESTORE_NOT_BEFORE="


def test_the_copy_refuses_at_and_keeps_its_old_copy(tmp_path):
    """The copy always starts from a fresh backup of live: an older one would let the menu show
    an old copy as fresh and a change tried there undo newer live work, and a mistyped time removed the copy before the
    backup was looked for. --at is for staged and server, which restore into a new, empty system."""
    result = run_restore(tmp_path, "copy --at 2026-11-09T10:00:00Z", expect_ok=False)
    assert result.returncode == 2
    assert "--at is for staged or server" in result.stderr
    assert not (tmp_path / "docker.log").exists()  # nothing touched: the old copy stays


@pytest.mark.parametrize("args", ["server --at 2026-11-09", "server --at", "server --at 2026-11-09T10:00:00Z extra",
                                  "server 2026-11-09T10:00:00Z"])
def test_a_mistyped_at_is_refused_before_anything_is_touched(tmp_path, args):
    result = run_restore(tmp_path, args, expect_ok=False)
    assert result.returncode == 2
    assert "usage: deploy/restore.sh" in result.stderr
    assert not (tmp_path / "docker.log").exists()


def test_the_staged_file_keeps_the_tunnel_and_backups_off_and_opens_no_port():
    services = yaml.safe_load((ROOT / "deploy/compose.staged.yaml").read_text(encoding="utf-8"))["services"]
    assert services["tunnel"].get("profiles") and services["backup"].get("profiles")
    assert not any("ports" in spec for spec in services.values())
