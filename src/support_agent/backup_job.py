"""The backup container's loop: right after it starts and then every hour, if a backup is due (backup.py), dump both
databases and the logins, encrypt each file with the public key, upload to R2 and record the run in backup_runs.
With --now: one backup right away, then stop, printing "backup taken: <time>". A test copy refresh asks for it, so the
copy starts from live as it is; so does every live data change in deploy/apply.sh, so it has a way back.

Runs only inside the backup container (Dockerfile.backup): it needs pg_dump, age, psycopg and boto3, which the agent
doesn't have. Settings come from the environment (compose.dev.yaml); the private key is never here.
"""

import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import psycopg
from botocore.config import Config

from support_agent.backup import BACKUPS, backup_due, backup_key, check_dump_size, short_error

CHECK_EVERY_SECONDS = 3600
SECRET_SETTINGS = ("PGPASSWORD", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
REQUIRED = SECRET_SETTINGS + ("PGHOST", "PGUSER", "R2_ENDPOINT", "R2_BUCKET", "BACKUP_AGE_RECIPIENT")

log = logging.getLogger("backup")


def secrets() -> list[str]:
    return [os.environ.get(name, "") for name in SECRET_SETTINGS]


def run(cmd: list[str]) -> None:
    """Run a tool; on failure, raise with its own last line of complaint (pg_dump and age say what went wrong)."""
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if done.returncode != 0:
        last_line = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise RuntimeError(f"{cmd[0]} failed: {last_line}")


def dump(name: str, path: Path) -> None:
    if name == "roles":  # the logins and their settings, without passwords (those live in .env)
        run(["pg_dumpall", "--globals-only", "--no-role-passwords", "--database=knowledge", f"--file={path}"])
    else:  # custom format: compressed, and pg_restore can restore one table if ever needed
        run(["pg_dump", "--format=custom", f"--dbname={name}", f"--file={path}"])


def make_backup(started: datetime) -> int:
    """Dump and encrypt everything first, check the sizes, then upload: a half-made backup is never uploaded."""
    with tempfile.TemporaryDirectory(dir="/tmp") as work:
        files: list[tuple[str, Path]] = []
        for name in BACKUPS:
            plain = Path(work) / f"{name}.dump"
            dump(name, plain)
            check_dump_size(plain.stat().st_size)
            locked = plain.with_suffix(".dump.age")
            run(["age", "--encrypt", "--recipient", os.environ["BACKUP_AGE_RECIPIENT"], f"--output={locked}", str(plain)])
            plain.unlink()  # only the encrypted copy stays, even on the container's temporary disk
            files.append((name, locked))
        total = sum(path.stat().st_size for _, path in files)
        check_dump_size(total)

        r2 = boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(connect_timeout=10, read_timeout=120, retries={"max_attempts": 3, "mode": "standard"}),
        )
        for name, path in files:
            r2.upload_file(str(path), os.environ["R2_BUCKET"], backup_key(name, started))
        return total


def run_once(now: datetime, asked: bool = False) -> bool | None:
    """One backup if due (or asked for): True done, False failed, None not due."""
    with psycopg.connect(dbname="knowledge", autocommit=True) as conn:
        last_success, last_attempt = conn.execute(
            "select max(finished_at) filter (where ok), max(started_at) from backup_runs"
        ).fetchone()
        if not backup_due(last_success, last_attempt, now, asked):
            return None
        log.info("backup %s: starting", "asked for" if asked else "due")
        try:
            total = make_backup(now)
        except Exception as exc:
            reason = short_error(exc, secrets())
            conn.execute("insert into backup_runs (started_at, ok, error) values (%s, false, %s)", (now, reason))
            log.error("backup failed: %s", reason)
            return False
        conn.execute("insert into backup_runs (started_at, ok, bytes) values (%s, true, %s)", (now, total))
        log.info("backup done: %d files, %.1f MB uploaded", len(BACKUPS), total / 1024 / 1024)
        return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:  # names only, never values
        raise SystemExit(f"backup settings missing: {', '.join(missing)}")
    if sys.argv[1:] == ["--now"]:  # one backup now, then stop: a test copy refresh or a live data change
        now = datetime.now(timezone.utc).replace(microsecond=0)  # the stamp in the file names has whole seconds
        if not run_once(now, asked=True):
            raise SystemExit("the backup failed: see the line above")
        # Keep this line's words and format: deploy/restore.sh copy and every live data change in deploy/apply.sh
        # read it and refuse to go on without it, and its time is the file names' stamp that restore.sh --at finds.
        print(f"backup taken: {now:%Y-%m-%dT%H:%M:%SZ}", flush=True)
        return
    log.info("backup service started: checks now, then every hour")
    while True:
        try:
            run_once(datetime.now(timezone.utc))
        except Exception as exc:  # the database itself unreachable, say: no row can be written, so log it
            log.error("backup check failed: %s", short_error(exc, secrets()))
        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main()
