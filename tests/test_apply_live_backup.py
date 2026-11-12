"""deploy/apply.sh: every live change that writes data takes a fresh backup first.

The real script runs in a temporary folder holding a copy of deploy/ (never the real notebook or .env), with a
stand-in `docker` that logs each call as one line and answers by call pattern, a stand-in `uv` that runs this test's
own Python, and a stand-in `sleep`. Nothing here reaches a real container."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

sys.path.insert(0, str(ROOT / "deploy"))
from notebook import parse  # noqa: E402

TAKEN = "2026-11-06T09:00:00Z"
MIGRATION = "900_test_change.sql"
WORKFLOW = "wf1"
KINDS = ("migration", "workflow", "images")
# The first thing each command writes to live (a call containing this text means live was touched).
FIRST_WRITE = {"migration": "ON_ERROR_STOP=1", "workflow": "cat > /tmp/apply-wf-", "images": "stop tunnel n8n"}
LIVE_LINE = {"migration": "live-migration", "workflow": "live-workflow", "images": "live-images"}

DOCKER = r'''#!/usr/bin/env bash
all="$*"
echo "${all//$'\n'/ }" >> "$DOCKER_LOG"
cat >/dev/null # set -e and pipes: a stand-in that does not read its input can break the writer
case "$all" in
  *backup_job*)
    case "$BACKUP_MODE" in
      ok) echo "backup taken: 2026-11-06T09:00:00Z" ;;
      noline) echo "uploaded, but no line to read" ;;
      fail) echo "the backup job crashed" >&2; exit 1 ;;
    esac ;;
  *"export:workflow"*) echo "v1" ;;
  *"exec -T n8n cat /tmp/apply-wf-"*) # a workflow; with BAD_WORKFLOW a Slack step without its Slack login
    if [ -n "$BAD_WORKFLOW" ]; then
      echo '[{"name": "Workflow One", "nodes": [{"name": "Post", "type": "n8n-nodes-base.slack", "parameters": {}}]}]'
    else echo '[{"name": "Workflow One", "nodes": []}]'; fi ;;
  *"from workflow_entity where id = "*) # the live version: one answer before the import, another after
    n=$(cat "$SA_STATE/versions" 2>/dev/null || echo 0); echo $((n + 1)) > "$SA_STATE/versions"
    if [ "$n" = 0 ]; then echo "old|old"; else echo "new|new"; fi ;;
  *"count(*) from workflow_entity"*) echo 3 ;;
  *"list:workflow"*) echo "wf1|Workflow One" ;;
  *"ps -q"*) echo "cid-${!#}" ;;
  *"inspect -f {{.Config.Image}}"*) # the image each live service was created from: old until n8n is stopped
    svc=${!#}; svc=${svc#cid-}
    if [ -e "$SA_STATE/switched" ] || [ -n "$ALREADY_LIVE" ]; then v="WANT_${svc^^}"; echo "${!v}"; else echo "old/$svc"; fi ;;
  *"stop tunnel n8n"*) touch "$SA_STATE/switched" ;;
esac
exit 0
'''
UV = '''#!/usr/bin/env bash
[ "$1" = run ] && shift
[ "$1" = --quiet ] && shift
[ "$1" = python ] && shift
exec "$TEST_PYTHON" "$@"
'''


def posix(path):
    return Path(path).as_posix()


def bash_path(path):
    """A folder as bash on this machine names it. On Windows only Git Bash's cygpath will do: a Windows path would split
    at its colon on PATH, so no cygpath means no run (exit 98). Elsewhere (Linux CI, a Mac) the path is already one."""
    return f'$(cygpath -u "{path}")' if sys.platform == "win32" else f'"{posix(path)}"'


def row(kind, key, fingerprint, note=""):
    return f"2026-11-06T08:00:00Z\t{kind}\t{key}\t{fingerprint}\t{note}\n"


def wanted_images():
    out = subprocess.run([sys.executable, str(ROOT / "deploy" / "images.py"), str(ROOT / "compose.dev.yaml")],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out, dict(part.split("=", 1) for part in out.split())


def run_apply(tmp_path, kind, passed=True, backup="ok", bad_workflow=False, already_live=False, restored=True,
              decoy=False):
    """`deploy/apply.sh live <kind> ...` in a temporary folder. `passed`: the notebook says this exact change passed
    on the copy. `bad_workflow`: the workflow has a Slack step without its login. `already_live`: live already runs
    the wanted versions. `restored`: the notebook holds the restore test for the new database version. `decoy`: a
    different `docker` comes first on PATH than the stand-in (the guard must stop the run before apply.sh starts).
    Returns the result, each docker call, and the notebook after the run.

    Safety: the bash command stops unless `docker` resolves to the stand-in, and DOCKER_HOST points at nothing, so a
    wrong bash (WSL's, say) or a wrong PATH can never reach the real live system."""
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    shutil.copytree(ROOT / "deploy", work / "deploy", ignore=shutil.ignore_patterns(".tested", "__pycache__"))
    shutil.copy(ROOT / "compose.dev.yaml", work / "compose.dev.yaml")
    shutil.copy(ROOT / "tests" / "test_n8n_queries.py", work / "tests" / "test_n8n_queries.py")
    (work / ".env").write_text("DUMMY=1\n", newline="\n")
    state = tmp_path / "state"
    state.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("docker", DOCKER), ("uv", UV), ("sleep", "#!/usr/bin/env bash\n")):
        (bin_dir / name).write_text(text, newline="\n")
        (bin_dir / name).chmod(0o755)

    rows = ""
    want, wants = wanted_images()
    if kind == "migration":
        (work / "db" / "migrations").mkdir(parents=True)
        content = b"create table if not exists follow_up_test (id int);\n"
        (work / "db" / "migrations" / MIGRATION).write_bytes(content)
        (work / "db" / "checks").mkdir()
        (work / "db" / "checks" / MIGRATION).write_bytes(b"select 1; -- expect 1\n")
        args = ["migration", MIGRATION]
        rows = row("migration", MIGRATION, hashlib.sha256(content).hexdigest()[:16]) if passed else ""
    elif kind == "workflow":
        args = ["workflow", WORKFLOW]
        rows = row("workflow", WORKFLOW, "v1") if passed else ""
    else:
        args = ["images"]
        rows = row("images", "images", want) + (row("restored", "images", want) if restored else "") if passed else ""
    (work / "deploy" / ".tested").write_text(rows, newline="\n")

    log = tmp_path / "docker.log"
    env = os.environ | {"BACKUP_MODE": backup, "SA_STATE": posix(state), "TEST_PYTHON": posix(sys.executable),
                        "DOCKER_LOG": posix(log), "DOCKER_HOST": "tcp://127.0.0.1:9",  # nothing listens there
                        "BAD_WORKFLOW": "1" if bad_workflow else "", "ALREADY_LIVE": "1" if already_live else ""} | {
                        f"WANT_{k.upper()}": v for k, v in wants.items()}
    first_dir = "$bin"
    decoy_setup = ""
    if decoy:
        (tmp_path / "decoy").mkdir()
        (tmp_path / "decoy" / "docker").write_text("#!/usr/bin/env bash\nexit 0\n", newline="\n")
        (tmp_path / "decoy" / "docker").chmod(0o755)
        decoy_setup = f'decoy={bash_path(tmp_path / "decoy")} || exit 98; '
        first_dir = "$decoy:$bin"
    # Then docker must be the stand-in, wherever this runs.
    command = (f'bin={bash_path(bin_dir)} || {{ echo "cygpath is missing: not Git Bash" >&2; exit 98; }}; '
               f'{decoy_setup}export PATH="{first_dir}:$PATH"; '
               '[ "$(command -v docker)" = "$bin/docker" ] || { echo "stand-in docker is not first on PATH" >&2; exit 99; }; '
               f'bash deploy/apply.sh live {" ".join(args)}')
    result = subprocess.run([BASH, "-c", command], cwd=work, env=env, input="", text=True, capture_output=True,
                            timeout=180)
    calls = log.read_text().splitlines() if log.exists() else []
    notebook = (work / "deploy" / ".tested").read_text()
    return SimpleNamespace(result=result, calls=calls, notebook=notebook)


def first(calls, text):
    """The position of the first call containing `text`; fails the test if there is none."""
    for i, call in enumerate(calls):
        if text in call:
            return i
    raise AssertionError(f"no call contains {text!r}: {calls}")


def backups(calls):
    return [i for i, call in enumerate(calls) if "backup_job --now" in call]


def test_live_migration_takes_a_backup_before_the_change(tmp_path):
    run = run_apply(tmp_path, "migration")
    assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0
    assert len(backups(run.calls)) == 1
    assert backups(run.calls)[0] < first(run.calls, FIRST_WRITE["migration"])
    assert f"backup taken: {TAKEN}" in run.result.stdout
    assert "SETUP section 11" in run.result.stdout  # the way back for damaged data is the restore section


@pytest.mark.parametrize("kind", KINDS)
def test_a_failed_backup_changes_nothing(tmp_path, kind):
    run = run_apply(tmp_path, kind, backup="fail")
    assert run.result.returncode == 1, run.result.stdout  # expected exit code: 1
    assert "the backup failed: nothing changed" in run.result.stderr
    assert not any(FIRST_WRITE[kind] in call for call in run.calls), run.calls
    assert LIVE_LINE[kind] not in run.notebook


@pytest.mark.parametrize("kind", KINDS)
def test_a_backup_without_its_line_changes_nothing(tmp_path, kind):
    run = run_apply(tmp_path, kind, backup="noline")
    assert run.result.returncode == 1, run.result.stdout  # expected exit code: 1
    assert "the backup didn't say it was taken: nothing changed" in run.result.stderr
    assert not any(FIRST_WRITE[kind] in call for call in run.calls), run.calls
    assert LIVE_LINE[kind] not in run.notebook


@pytest.mark.parametrize("kind", KINDS)
def test_a_refused_migration_takes_no_backup(tmp_path, kind):
    """A change that did not pass on the copy is refused before the backup (no point backing up for nothing)."""
    run = run_apply(tmp_path, kind, passed=False)
    assert run.result.returncode == 1, run.result.stdout  # expected exit code: 1
    assert "REFUSED" in run.result.stderr
    assert backups(run.calls) == []
    assert not any(FIRST_WRITE[kind] in call for call in run.calls), run.calls


def test_a_workflow_that_fails_its_checks_takes_no_backup(tmp_path):
    """check_workflow is `live workflow`'s last refusal: the backup comes after it, not before."""
    run = run_apply(tmp_path, "workflow", bad_workflow=True)
    assert run.result.returncode == 1, run.result.stdout  # expected exit code: 1
    assert "has no Slack login" in run.result.stdout
    assert "REFUSED" in run.result.stderr
    assert backups(run.calls) == []
    assert not any(FIRST_WRITE["workflow"] in call for call in run.calls), run.calls


def test_live_images_without_its_restore_test_takes_no_backup(tmp_path):
    """A new database version without the restore test on the copy is refused before the backup."""
    run = run_apply(tmp_path, "images", restored=False)
    assert run.result.returncode == 1, run.result.stdout  # expected exit code: 1
    assert "restore test" in run.result.stderr
    assert backups(run.calls) == []
    assert not any(FIRST_WRITE["images"] in call for call in run.calls), run.calls


def test_live_images_that_already_run_take_no_backup(tmp_path):
    """"Live already runs these versions" exits before the backup: nothing is about to change."""
    run = run_apply(tmp_path, "images", already_live=True)
    assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0 (nothing to do)
    assert "Live already runs these versions." in run.result.stdout
    assert backups(run.calls) == []
    assert not any(FIRST_WRITE["images"] in call for call in run.calls), run.calls


def test_the_run_stops_unless_the_stand_in_docker_is_first_on_path(tmp_path):
    """The guard that keeps every test off the real live system: another `docker` first on PATH ends the run (99)
    before apply.sh starts, so no docker call is made at all."""
    run = run_apply(tmp_path, "migration", decoy=True)
    assert run.result.returncode == 99, (run.result.stdout, run.result.stderr)  # expected exit code: 99
    assert "stand-in docker is not first on PATH" in run.result.stderr
    assert run.calls == []


def test_live_workflow_takes_a_backup_before_the_import(tmp_path):
    run = run_apply(tmp_path, "workflow")
    assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0
    assert len(backups(run.calls)) == 1
    assert backups(run.calls)[0] < first(run.calls, FIRST_WRITE["workflow"])
    assert backups(run.calls)[0] < first(run.calls, "import:workflow")
    assert f"backup taken: {TAKEN}" in run.result.stdout


def test_live_images_still_takes_its_backup_first(tmp_path):
    run = run_apply(tmp_path, "images")
    assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0
    # one before the switch, one through the rebuilt backup service
    assert len(backups(run.calls)) == 2
    assert backups(run.calls)[0] < first(run.calls, FIRST_WRITE["images"])
    assert f"backup taken: {TAKEN}" in run.result.stdout


def test_each_workflow_run_keeps_its_own_export_file_from_checks_to_import(tmp_path):
    """The backup lengthens the wait between the checks and the import: with one shared export
    file, a second run could swap in a workflow that never passed. Two runs, one after the other: each uses one file
    from export to import, and not the other's, so a run alongside can't replace what was checked."""
    names = []
    for attempt in ("one", "two"):
        run = run_apply(tmp_path / attempt, "workflow")
        assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0
        used = set(re.findall(r"/tmp/apply-wf[^\s'\";&]*", "\n".join(run.calls)))
        assert len(used) == 1, used  # export, version, checks, move and import: the same file
        names.append(used.pop())
    assert "/tmp/apply-wf.json" not in names
    assert names[0] != names[1]


@pytest.mark.parametrize("kind", KINDS)
def test_the_way_back_names_the_backup_by_its_time(tmp_path, kind):
    """Newer backups follow within minutes (the images switch takes one itself, then the nightly run), and the restore
    takes the newest unless told which: the way back is the restore with --at and this backup's time."""
    run = run_apply(tmp_path, kind)
    assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0
    line = next(line for line in run.result.stdout.splitlines() if line.strip().startswith(f"backup taken: {TAKEN}"))
    assert f"--at {TAKEN}" in line and "SETUP section 11" in line
    # No command to copy: on a single machine the one mode that fits, server, would start a second
    # system on live's tunnel; which restore applies is the guide's to say.
    assert "restore.sh" not in line


@pytest.mark.parametrize("kind", KINDS)
def test_the_notebook_names_the_backup_time(tmp_path, kind):
    run = run_apply(tmp_path, kind)
    assert run.result.returncode == 0, run.result.stderr  # expected exit code: 0
    live = [e for e in parse(run.notebook) if e.kind == LIVE_LINE[kind]]
    assert len(live) == 1
    assert f"backup before: {TAKEN}" in live[0].note
    assert "LIVE:" in run.result.stdout and TAKEN in run.result.stdout.split("LIVE:")[1]  # the closing line names it
