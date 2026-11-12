"""Rebuild both databases from the newest complete backup in R2, or the one taken at RESTORE_AT (for
going back to before a live change). deploy/restore.sh runs it inside
the backup image, against a new, empty Postgres.

The private key comes in on standard input only: never from a file or a setting, never printed. Steps: download,
decrypt, recreate the logins, restore both databases, set each login's password from the environment (.env), then
prove every login can sign in. It refuses to touch a database that already holds data, so it can't overwrite the
live system by mistake.
"""

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import psycopg
from psycopg import sql

from support_agent.backup import LOGINS, chosen_set, logins_to_set

REQUIRED = ("PGHOST", "PGUSER", "PGPASSWORD", "R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def run(cmd: list[str], stdin: str | None = None) -> None:
    done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=1800)
    if done.returncode != 0:
        last_line = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise SystemExit(f"{cmd[0]} failed: {last_line}")


def refuse_if_not_empty() -> None:
    """Only an empty Postgres may be restored into: a fresh 'knowledge' with no tables, and no 'n8n' database."""
    with psycopg.connect(dbname="postgres", autocommit=True) as conn:
        if conn.execute("select 1 from pg_database where datname = 'n8n'").fetchone():
            raise SystemExit("refused: this Postgres already has an n8n database (restore only into an empty one)")
    with psycopg.connect(dbname="knowledge", autocommit=True) as conn:
        tables = conn.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0]
        if tables:
            raise SystemExit(f"refused: 'knowledge' already has {tables} tables (restore only into an empty one)")


def main() -> None:
    started = time.monotonic()
    missing = [name for name in REQUIRED + tuple(var for var, _ in LOGINS.values()) if not os.environ.get(name)]
    if missing:  # names only, never values
        raise SystemExit(f"restore settings missing: {', '.join(missing)}")
    key = sys.stdin.read().strip()
    if not key.startswith("AGE-SECRET-KEY-"):
        raise SystemExit("that isn't the backup private key (it starts with AGE-SECRET-KEY-)")
    refuse_if_not_empty()

    r2 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
    bucket = os.environ["R2_BUCKET"]
    keys = [o["Key"] for page in r2.get_paginator("list_objects_v2").paginate(Bucket=bucket)
            for o in page.get("Contents", [])]
    # RESTORE_AT (restore.sh --at): that exact backup, the way back to before a live change. Otherwise the newest
    # complete one; a test copy refresh passes the time of the backup it just took as RESTORE_NOT_BEFORE, since an
    # older one would make the copy older than live.
    at = os.environ.get("RESTORE_AT", "")
    try:
        stamp, files = chosen_set(keys, at=at, not_before=os.environ.get("RESTORE_NOT_BEFORE", ""))
    except ValueError as problem:
        raise SystemExit(f"refused: {problem}") from None
    print(f"{'the backup asked for' if at else 'newest complete backup'}: {stamp:%Y-%m-%d %H:%M:%S} UTC", flush=True)

    with tempfile.TemporaryDirectory(dir="/tmp") as work:
        plain: dict[str, Path] = {}
        for name, object_key in files.items():
            locked = Path(work) / f"{name}.dump.age"
            r2.download_file(bucket, object_key, str(locked))
            plain[name] = Path(work) / f"{name}.dump"
            run(["age", "--decrypt", "--identity", "-", f"--output={plain[name]}", str(locked)], stdin=key + "\n")
            locked.unlink()
        print("downloaded and decrypted", flush=True)

        # Logins first (the databases belong to them). The admin login exists already in a new Postgres.
        roles = plain["roles"].read_text().replace("CREATE ROLE postgres;\n", "")
        run(["psql", "-v", "ON_ERROR_STOP=1", "--quiet", "--dbname=postgres"], stdin=roles)
        for db in ("knowledge", "n8n"):
            run(["pg_restore", "--clean", "--if-exists", "--create", "--exit-on-error", "--dbname=postgres", str(plain[db])])
        print("logins and both databases restored", flush=True)

    with psycopg.connect(dbname="postgres", autocommit=True) as conn:
        logins = logins_to_set({row[0] for row in conn.execute("select rolname from pg_roles")})
        missing = [var for var, _ in logins.values() if not os.environ.get(var)]
        if missing:  # a newer backup's login with no password in .env (names only, never values)
            raise SystemExit(f"restore settings missing: {', '.join(missing)} (the databases are restored; set it in "
                             f".env and set the login's password as in docs/SETUP.md section 3)")
        for role, (var, _) in logins.items():
            conn.execute(sql.SQL("alter role {} password {}").format(sql.Identifier(role), sql.Literal(os.environ[var])))
    for role, (var, db) in logins.items():  # each password from .env really opens its login
        with psycopg.connect(dbname=db, user=role, password=os.environ[var], connect_timeout=5) as conn:
            conn.execute("select 1")
    print(f"all {len(logins)} logins sign in with their .env passwords", flush=True)

    with psycopg.connect(dbname="knowledge") as conn:
        k = conn.execute("select (select count(*) from documents), (select count(*) from client_map), "
                         "(select count(*) from tickets), (select max(version) from schema_migrations)").fetchone()
    with psycopg.connect(dbname="n8n") as conn:
        n = conn.execute("select (select count(*) from workflow_entity), (select count(*) from credentials_entity)").fetchone()
    print(f"restored: {k[0]} guide passages, {k[1]} clients, {k[2]} tickets, migrations up to {k[3]}; "
          f"{n[0]} workflows, {n[1]} saved n8n logins", flush=True)
    age_h = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
    limit = " (the backup asked for)" if at else " (at most 24 with nightly backups)"
    print(f"data age: {age_h:.1f} hours{limit}; restore took {time.monotonic() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
