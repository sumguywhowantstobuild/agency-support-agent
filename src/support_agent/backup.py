"""Nightly backups: the rules for when one is due and what it may upload.

The backup container checks every hour; these rules decide whether it does anything. They are kept apart from the
dumping and uploading so they can be tested without a database or Cloudflare.
"""

from datetime import datetime, timedelta, timezone

EVERY = timedelta(hours=24)  # one good backup a day
RETRY_GAP = timedelta(hours=1)  # at most one try an hour, so a failing backup can't loop (or run up costs)
MAX_DUMP_BYTES = 500 * 1024 * 1024  # far above the databases' normal size: a dump this big means something ran away
BACKUPS = ("knowledge", "n8n", "roles")  # the two databases, and the list of logins (no passwords)

# Each login a restore gives its password back (from .env), and where it signs in to prove the password works.
LOGINS = {
    "agent_reader": ("AGENT_DB_PASSWORD", "knowledge"),
    "kb_sync": ("KB_SYNC_DB_PASSWORD", "knowledge"),
    "n8n_support": ("N8N_SUPPORT_DB_PASSWORD", "knowledge"),
    "backup": ("BACKUP_DB_PASSWORD", "n8n"),
    "n8n": ("N8N_DB_PASSWORD", "n8n"),
}
# Logins only newer backups hold: the admin app's (migration 017, release 1.1.0).
LATER_LOGINS = {"admin_app": ("ADMIN_DB_PASSWORD", "knowledge")}


def logins_to_set(restored_roles: set[str]) -> dict[str, tuple[str, str]]:
    """The logins whose passwords a restore sets: every one in LOGINS, and a later one only if the backup has it."""
    return LOGINS | {role: login for role, login in LATER_LOGINS.items() if role in restored_roles}


def backup_due(last_success: datetime | None, last_attempt: datetime | None, now: datetime,
               asked: bool = False) -> bool:
    """asked: someone is waiting for one right now (a test copy refresh), so the daily and retry gaps don't apply."""
    for t in (last_success, last_attempt, now):
        if t is not None and t.utcoffset() is None:
            raise ValueError("backup times need a time zone (the database keeps UTC, the PC runs on local time)")
    if asked:
        return True
    if last_attempt is not None and now - last_attempt < RETRY_GAP:
        return False
    return last_success is None or now - last_success >= EVERY


def check_dump_size(size_bytes: int) -> None:
    """Refuse a dump that can't be right before it's uploaded."""
    if size_bytes <= 0:
        raise ValueError("the dump is empty: the database dump failed")
    if size_bytes > MAX_DUMP_BYTES:
        raise ValueError(f"the dump is {size_bytes / 1024 / 1024:.0f} MB, over the 500 MB cap: check what grew")


def backup_key(name: str, now: datetime) -> str:
    """Where one backup file goes in the bucket: by date, then a UTC timestamp, so no two runs share a name."""
    if name not in BACKUPS:
        raise ValueError(f"unknown backup {name!r}; expected one of {', '.join(BACKUPS)}")
    utc = now.astimezone(timezone.utc)
    return f"{utc:%Y/%m/%d}/{utc:%Y%m%dT%H%M%SZ}-{name}.dump.age"


def short_error(exc: BaseException, secrets: list[str]) -> str:
    """Why a backup failed, fit for backup_runs and a Slack alert: one line, at most 300 characters."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    for secret in secrets:
        if secret:  # an unset setting is blank; replacing "" would scatter *** everywhere
            text = text.replace(secret, "***")  # before cutting, so a secret can't survive half-cut
    return text[:300]


def _runs(keys: list[str]) -> dict[str, dict[str, str]]:
    """Bucket keys grouped by backup run: {"20261109T100000Z": {"knowledge": key, "n8n": key, "roles": key}}."""
    runs: dict[str, dict[str, str]] = {}
    for key in keys:
        stamp, _, rest = key.rsplit("/", 1)[-1].partition("-")
        runs.setdefault(stamp, {})[rest.removesuffix(".dump.age")] = key
    return runs


def complete_set_at(keys: list[str], at: datetime) -> tuple[datetime, dict[str, str]]:
    """The backup run taken at exactly `at` (the time a live change's fresh backup printed): the way back to before
    that change, even when newer backups exist."""
    files = _runs(keys).get(f"{at:%Y%m%dT%H%M%SZ}")
    if files is None:  # never another backup instead: a mistyped time must not restore the wrong data
        raise ValueError(f"no backup was taken at {at:%Y-%m-%d %H:%M:%S} UTC (copy the time the change printed)")
    if set(files) != set(BACKUPS):
        raise ValueError(f"the backup taken at {at:%Y-%m-%d %H:%M:%S} UTC is missing a file (its upload didn't finish): "
                         f"it can't rebuild the system")
    return at, files


def chosen_set(keys: list[str], at: str, not_before: str) -> tuple[datetime, dict[str, str]]:
    """The backup restore_job restores, from its two settings as text in the 'backup taken:' line's format (blank when
    unset): at, that exact backup (deploy/restore.sh --at, going back to before a live change); otherwise the newest
    complete one, no older than not_before (a test copy refresh's own fresh backup)."""
    def parsed(text: str) -> datetime:
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError(f"'{text}' isn't a backup time: copy it as printed, like 2026-11-09T10:00:00Z") from None

    if at:
        return complete_set_at(keys, parsed(at))
    return newest_complete_set(keys, parsed(not_before) if not_before else None)


def newest_complete_set(keys: list[str], not_before: datetime | None = None) -> tuple[datetime, dict[str, str]]:
    """The newest backup run that has every file (an upload that failed halfway leaves an incomplete set).

    not_before: a test copy refresh's own fresh backup; anything older would give the copy older data than live has.
    """
    runs = _runs(keys)
    complete = [s for s, files in runs.items() if set(files) == set(BACKUPS)]
    if not complete:
        raise ValueError(f"no complete backup in the bucket (each run needs: {', '.join(BACKUPS)})")
    stamp = max(complete)
    when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if not_before is not None and when < not_before:
        raise ValueError(f"the newest complete backup ({when:%Y-%m-%d %H:%M:%S} UTC) is older than the backup just "
                         f"taken ({not_before:%Y-%m-%d %H:%M:%S} UTC): its upload didn't finish")
    return when, runs[stamp]
