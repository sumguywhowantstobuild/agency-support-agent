"""Backups: when one is due, and the limits that stop a bug from piling up uploads (or costs)."""

from datetime import datetime, timedelta, timezone

import pytest

from support_agent.backup import (backup_due, backup_key, check_dump_size, chosen_set, complete_set_at,
                                  logins_to_set, newest_complete_set, short_error)

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)


def test_first_backup_is_due_right_away():
    assert backup_due(last_success=None, last_attempt=None, now=NOW) is True


def test_not_due_while_the_last_good_backup_is_under_24_hours_old():
    last = NOW - timedelta(hours=23)
    assert backup_due(last_success=last, last_attempt=last, now=NOW) is False


def test_a_failed_try_waits_an_hour_before_the_next():
    # No good backup yet, but the last try was 30 minutes ago: retrying now could loop (and cost) if it keeps failing.
    assert backup_due(last_success=None, last_attempt=NOW - timedelta(minutes=30), now=NOW) is False


def test_a_backup_asked_for_by_hand_runs_even_right_after_another():
    # A test copy refresh asks for one, so the copy starts from what live has now.
    just_now = NOW - timedelta(minutes=5)
    assert backup_due(last_success=just_now, last_attempt=just_now, now=NOW, asked=True) is True


def test_times_without_a_time_zone_are_refused():
    # The PC's clock is local time and the database keeps UTC: a time with no zone could be hours off, unnoticed.
    no_zone = datetime(2026, 10, 14, 3, 0)
    with pytest.raises(ValueError, match="time zone"):
        backup_due(last_success=no_zone, last_attempt=no_zone, now=no_zone)


def test_an_empty_dump_is_refused():
    # A dump with nothing in it means the dump failed quietly: uploading it would look like a good backup.
    with pytest.raises(ValueError, match="empty"):
        check_dump_size(0)


def test_a_dump_over_500_mb_is_refused():
    # The databases are far smaller than this: a dump this big means something ran away, and storing it costs money.
    with pytest.raises(ValueError, match="500 MB"):
        check_dump_size(500 * 1024 * 1024 + 1)


def test_a_normal_dump_passes():
    check_dump_size(8 * 1024 * 1024)


def test_each_backup_file_gets_its_own_dated_name():
    # Never the same name twice (the bucket lock refuses overwrites anyway), and sorted by date for the restore.
    assert backup_key("knowledge", NOW) == "2026/10/14/20261014T030000Z-knowledge.dump.age"


def test_the_name_is_in_utc_whatever_zone_the_time_comes_in():
    # 11 p.m. in New York (UTC-4 in October) is 3 a.m. UTC the next day: the "Z" in the name must be true.
    new_york = timezone(timedelta(hours=-4))
    assert backup_key("n8n", datetime(2026, 10, 13, 23, 0, tzinfo=new_york)) == (
        "2026/10/14/20261014T030000Z-n8n.dump.age"
    )


@pytest.mark.parametrize("name", ["", "../knowledge", "other_db"])
def test_only_the_known_backups_can_be_named(name):
    with pytest.raises(ValueError, match="unknown backup"):
        backup_key(name, NOW)


def test_a_failure_reason_is_one_short_line():
    # backup_runs keeps at most 300 characters, and the alert shows it in Slack: one line, never a whole log.
    reason = short_error(RuntimeError("pg_dump: error: connection failed\n" + "detail " * 100), secrets=[])
    assert "\n" not in reason and len(reason) <= 300
    assert reason.startswith("RuntimeError: pg_dump: error: connection failed")


def test_secrets_never_reach_the_failure_reason():
    # Some tools put the address or login they used into their error text; the reason goes to the database and Slack.
    exc = RuntimeError("upload failed for key s3cr3t-r2-key with password hunter2-db")
    reason = short_error(exc, secrets=["s3cr3t-r2-key", "hunter2-db", ""])
    assert "s3cr3t-r2-key" not in reason and "hunter2-db" not in reason
    assert reason == "RuntimeError: upload failed for key *** with password ***"


def test_restore_picks_the_newest_complete_backup():
    keys = [
        "2026/10/13/20261013T015500Z-knowledge.dump.age", "2026/10/13/20261013T015500Z-n8n.dump.age",
        "2026/10/13/20261013T015500Z-roles.dump.age",
        "2026/10/14/20261014T015547Z-knowledge.dump.age", "2026/10/14/20261014T015547Z-n8n.dump.age",
        "2026/10/14/20261014T015547Z-roles.dump.age",
    ]
    stamp, files = newest_complete_set(keys)
    assert stamp == datetime(2026, 10, 14, 1, 55, 47, tzinfo=timezone.utc)
    assert files == {name: f"2026/10/14/20261014T015547Z-{name}.dump.age" for name in ("knowledge", "n8n", "roles")}


def test_restore_skips_a_newer_set_that_is_missing_a_file():
    # The upload failed after the first file: that run can't rebuild the system, the night before can.
    keys = [
        "2026/10/13/20261013T015500Z-knowledge.dump.age", "2026/10/13/20261013T015500Z-n8n.dump.age",
        "2026/10/13/20261013T015500Z-roles.dump.age",
        "2026/10/14/20261014T015547Z-knowledge.dump.age",
    ]
    stamp, files = newest_complete_set(keys)
    assert stamp == datetime(2026, 10, 13, 1, 55, 0, tzinfo=timezone.utc)
    assert set(files) == {"knowledge", "n8n", "roles"}


@pytest.mark.parametrize("keys", [[], ["2026/10/14/20261014T015547Z-knowledge.dump.age"], ["notes.txt"]])
def test_restore_says_plainly_when_there_is_no_complete_backup(keys):
    with pytest.raises(ValueError, match="no complete backup"):
        newest_complete_set(keys)


def test_a_refresh_refuses_an_older_backup_than_the_one_it_just_took():
    # The fresh backup's upload failed halfway, so yesterday's set is the newest complete one: restoring it would give
    # the copy older workflows than live has, and a change made on top could undo a newer live one.
    keys = ["2026/10/14/20261014T020811Z-knowledge.dump.age", "2026/10/14/20261014T020811Z-n8n.dump.age",
            "2026/10/14/20261014T020811Z-roles.dump.age", "2026/10/15/20261015T030000Z-knowledge.dump.age"]
    taken = datetime(2026, 10, 15, 3, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="older than the backup just taken"):
        newest_complete_set(keys, not_before=taken)
    keys += ["2026/10/15/20261015T030000Z-n8n.dump.age", "2026/10/15/20261015T030000Z-roles.dump.age"]
    assert newest_complete_set(keys, not_before=taken)[0] == taken


SETS = ["2026/11/09/20261109T100000Z", "2026/11/09/20261109T101500Z", "2026/11/10/20261110T030000Z"]
KEYS = [f"{s}-{name}.dump.age" for s in SETS for name in ("knowledge", "n8n", "roles")]


def test_going_back_restores_the_backup_taken_before_the_change_even_when_newer_ones_exist():
    """A live change's fresh backup is the way back (apply.sh), but the images switch, a copy refresh and the nightly
    run add newer ones within minutes and R2's lock keeps them: the restore must take the one asked for by its time."""
    asked = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)
    stamp, files = complete_set_at(KEYS, asked)
    assert stamp == asked
    assert files == {name: f"2026/11/09/20261109T100000Z-{name}.dump.age" for name in ("knowledge", "n8n", "roles")}


@pytest.mark.parametrize(("keys", "message"), [
    (KEYS, "no backup was taken at 2026-11-09 10:00:01 UTC"),  # a mistyped time never falls back to another backup
    ([k for k in KEYS if not k.endswith("20261109T100000Z-roles.dump.age")], "is missing a file"),
])
def test_going_back_refuses_a_time_without_a_complete_backup(keys, message):
    asked = datetime(2026, 11, 9, 10, 0, 0 if message == "is missing a file" else 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match=message):
        complete_set_at(keys, asked)


def test_the_restore_reads_the_time_exactly_as_a_live_change_printed_it():
    """restore_job hands over RESTORE_AT and RESTORE_NOT_BEFORE as text, in the 'backup taken:' line's format."""
    assert chosen_set(KEYS, at="2026-11-09T10:00:00Z", not_before="")[0] == datetime(2026, 11, 9, 10, 0,
                                                                                    tzinfo=timezone.utc)
    assert chosen_set(KEYS, at="", not_before="")[0] == datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
    assert chosen_set(KEYS, at="", not_before="2026-11-10T03:00:00Z")[0] == datetime(2026, 11, 10, 3, 0,
                                                                                    tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="isn't a backup time"):
        chosen_set(KEYS, at="2026-11-09 10:00", not_before="")


def test_the_admin_login_gets_its_password_only_when_the_backup_has_it():
    """The admin app's login (migration 017, 1.1.0) must get its .env password after a restore, or the app can't reach
    the database; a backup taken before 017 has no such login, and setting a password on it would stop the restore."""
    before_017 = logins_to_set({"postgres", "agent_reader", "kb_sync", "n8n_support", "backup", "n8n"})
    since_017 = logins_to_set({"postgres", "agent_reader", "kb_sync", "n8n_support", "backup", "n8n", "admin_app"})

    assert "admin_app" not in before_017
    assert since_017["admin_app"] == ("ADMIN_DB_PASSWORD", "knowledge")
    assert set(since_017) - set(before_017) == {"admin_app"}  # the other logins are set either way
