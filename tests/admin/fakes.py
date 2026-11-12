"""The fake database the Clients screen's tests share (test_clients and its browser tests)."""

import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg

from conftest import CLOCK
from support_agent.admin import db as admin_db
from support_agent.admin.db import (ChangeRow, ChannelUsedByAnother, ClientGone, ClientRow, ClientTaken,
                                    ClientUnchanged, DraftTime, HistoryRow, LastMember, LimitChange, Staff, StaffGone,
                                    StaffMember, StaffTaken)

ANA = Staff(id=1, name="Ana Reyes", slack_user_id="U0ANA")
ADDED = datetime(2026, 10, 1, tzinfo=timezone.utc)


def a_member(id, name, email, slack_user_id, active=True) -> StaffMember:
    return StaffMember(id=id, name=name, email=email, slack_user_id=slack_user_id, active=active, added_at=ADDED)


ANA_MEMBER = a_member(1, "Ana Reyes", "ana@bluejay.example", "U0ANA")


def an_entry(kind, subject, action, when, who="Ana Reyes", client_key=None, detail=None, reason=None,
             note=None) -> ChangeRow:
    """One admin_history row as the History screen reads it."""
    changed_at = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
    return ChangeRow(changed_at=changed_at, staff_name=who, kind=kind, subject=subject, action=action, detail=detail,
                     client_key=client_key, reason=reason, note=note)


def a_client(key, name, used=0, limit=30, status="active", reason=None, note=None) -> ClientRow:
    return ClientRow(client_key=key, display_name=name, channel_id=f"C0{key.upper()}", location_id=f"loc-{key}",
                     drafts_per_hour=limit, used_this_hour=used, status=status, pause_reason=reason, pause_note=note)


def drafted(client_key, minutes_ago, now=None) -> DraftTime:
    """One real draft for the chart, `minutes_ago` before `now` (the fixed clock by default)."""
    return DraftTime(client_key=client_key, created_at=(now or CLOCK) - timedelta(minutes=minutes_ago))


def a_change(client_key, action, when, who="Ana Reyes", detail=None, reason=None, note=None) -> tuple:
    """One admin_history row for FakeAdminDb.history: (client key, the row as client_history returns it)."""
    changed_at = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
    return client_key, HistoryRow(changed_at=changed_at, staff_name=who, action=action, detail=detail, reason=reason,
                                  note=note)


class FakeAdminDb:
    def __init__(self, *clients: ClientRow, history: tuple = (), staff: tuple = (ANA_MEMBER,), entries: tuple = (),
                 drafts: tuple = (), past: dict | None = None):
        self.rows = list(clients)
        # client_channels (migration 024): every channel a client has had, to its client key. Each client's own channel
        # now, and `past`: channels that clients moved away from (channel ID to client key).
        self.owners = {row.channel_id: row.client_key for row in clients} | dict(past or {})
        self.drafts = list(drafts)  # DraftTime, any order: the chart's view of admin_drafts_today
        self.history = list(history)  # (client key, HistoryRow), any order
        self.staff = list(staff)
        self.entries = list(entries)  # ChangeRow, any order: the History screen's view of admin_history
        self.as_of = []  # each History read's "as of" moment
        self.calls = []  # changes made, and what a refused request must not make
        self.reads = []  # what pages read on their own (History's query, the chart's): Clients reads them on every load
        self.noted = []  # each name map note_channel_names was given, apart from `calls`: not a change to a client

    def history_people(self) -> list[str]:
        return sorted({row.staff_name for row in self.entries})

    def changes(self, kind: str | None, client_key: str | None, person: str | None, as_of: datetime, offset: int,
                limit: int) -> list[ChangeRow]:
        self.reads.append(("changes", kind, client_key, person, offset, limit))
        self.as_of.append(as_of)  # apart from calls: it is a moment, not something a test can write down
        kept = [row for row in self.entries if row.changed_at <= as_of and (kind is None or row.kind == kind)
                and (client_key is None or row.client_key == client_key)
                and (person is None or row.staff_name == person)]
        return sorted(kept, key=lambda row: row.changed_at, reverse=True)[offset:offset + limit]

    def staff_list(self) -> list[StaffMember]:
        return list(self.staff)

    def _taken(self, email: str, slack_user_id: str, but: int | None = None):
        """The staff table's two unique columns (migration 017), checked as the database would."""
        others = [member for member in self.staff if member.id != but]
        if any(member.email == email for member in others):
            raise StaffTaken("email")
        if any(member.slack_user_id == slack_user_id for member in others):
            raise StaffTaken("slack_user_id")

    def add_staff(self, by: int, name: str, email: str, slack_user_id: str) -> int:
        self.calls.append(("add_staff", by, name, email, slack_user_id))
        self._taken(email, slack_user_id)
        new_id = max(member.id for member in self.staff) + 1
        self.staff.append(a_member(new_id, name, email, slack_user_id))
        return new_id

    def _member(self, staff_id: int) -> StaffMember:
        member = next((member for member in self.staff if member.id == staff_id), None)
        if member is None:
            raise StaffGone(staff_id)
        return member

    def edit_staff(self, by: int, staff_id: int, name: str, email: str, slack_user_id: str) -> None:
        self.calls.append(("edit_staff", by, staff_id, name, email, slack_user_id))
        member = self._member(staff_id)
        self._taken(email, slack_user_id, but=staff_id)
        self.staff[self.staff.index(member)] = replace(member, name=name, email=email, slack_user_id=slack_user_id)

    def set_staff_active(self, by: int, staff_id: int, active: bool) -> None:
        self.calls.append(("set_staff_active", by, staff_id, active))
        member = self._member(staff_id)
        if not active and member.active and sum(m.active for m in self.staff) == 1:
            raise LastMember(staff_id)  # admin_set_staff_active: the last active member
        self.staff[self.staff.index(member)] = replace(member, active=active)

    def client_history(self, client_key: str) -> list[HistoryRow]:
        mine = [row for key, row in self.history if key == client_key]
        return sorted(mine, key=lambda row: row.changed_at, reverse=True)[:100]  # the newest 100, as the query

    def last_change(self, client_key: str, actions: tuple[str, ...]) -> HistoryRow | None:
        mine = [row for key, row in self.history if key == client_key and row.action in actions]
        return max(mine, key=lambda row: row.changed_at, default=None)

    def _log(self, client_key: str, action: str, reason=None, note=None, detail=None, kind="client"):
        """As client_changes logs it: now, by Ana. The History screen's view (entries) gets the same change, so the
        Clients page's latest-change line finds it."""
        now = datetime.now(timezone.utc)
        self.history.append((client_key, HistoryRow(changed_at=now, staff_name=ANA.name, action=action, detail=detail,
                                                     reason=reason, note=note)))
        row = next((row for row in self.rows if row.client_key == client_key), None)
        self.entries.append(ChangeRow(changed_at=now, staff_name=ANA.name, kind=kind,
                                      subject=row.display_name if row else client_key, action=action, detail=detail,
                                      client_key=client_key, reason=reason, note=note))

    def staff_for_email(self, email: str) -> Staff | None:
        return ANA if email.lower() == "ana@bluejay.example" else None

    def clients(self) -> list[ClientRow]:
        return list(self.rows)

    def drafts_today(self, since: datetime) -> list[DraftTime]:
        self.reads.append(("drafts_today", since))
        return sorted((draft for draft in self.drafts if draft.created_at >= since), key=lambda d: d.created_at)

    def add_client(self, by: int, channel_id: str, name: str, location_id: str, limit: int, sealed: bytes) -> str:
        self.calls.append(("add_client", by, channel_id, name, location_id, limit, sealed))
        # admin_add_client's rule (migration 018): a channel or location already in use refuses the add, unless both
        # match one removed client, which comes back.
        same = [row for row in self.rows if row.channel_id == channel_id or row.location_id == location_id]
        if same and not (len(same) == 1 and same[0].status == "removed" and same[0].channel_id == channel_id
                         and same[0].location_id == location_id):
            raise ClientTaken(channel_id)
        key = same[0].client_key if same else name.lower().replace(" ", "-")
        # Migration 024: the channel is recorded for this client, or refused when another client has had it.
        if self.owners.setdefault(channel_id, key) != key:
            raise ChannelUsedByAnother(channel_id)
        self.rows = [row for row in self.rows if row not in same]
        self.rows.append(ClientRow(client_key=key, display_name=name, channel_id=channel_id, location_id=location_id,
                                   drafts_per_hour=limit, used_this_hour=0, status="active", pause_reason=None,
                                   pause_note=None))
        self._log(key, "added")  # admin_add_client logs it, for a client brought back too
        return key

    def channel_owners(self) -> dict[str, str]:
        """client_channels (migration 024): every channel a client has had, to its client's key."""
        return dict(self.owners)

    def change_channel(self, by: int, client_key: str, channel_id: str, channel_name: str | None) -> None:
        """admin_change_channel's rules (migration 024), in its order: an unknown or removed client, the client's own
        channel, a channel any client row holds (removed included), a channel another client has had; the name is kept
        only if it is 1 to 80 characters with no control character."""
        self.calls.append(("change_channel", by, client_key, channel_id, channel_name))
        row = next((row for row in self.rows if row.client_key == client_key and row.status != "removed"), None)
        if row is None:
            raise ClientGone(client_key)
        if not re.fullmatch(r"[CG][A-Z0-9]{2,}", channel_id or ""):  # the function's shape check, as Postgres raises it
            raise psycopg.errors.RaiseException("admin_change_channel: the channel must be a Slack channel ID")
        if channel_id == row.channel_id:
            raise ClientUnchanged(client_key)
        if any(held.channel_id == channel_id for held in self.rows):
            raise ClientTaken(channel_id)
        if self.owners.setdefault(channel_id, client_key) != client_key:
            raise ChannelUsedByAnother(channel_id)
        name = channel_name if channel_name and len(channel_name) <= 80 and not re.search(
            "[\x01-\x1f\x7f-\x9f]", channel_name) else None
        moved = replace(row, channel_id=channel_id, channel_name=name)
        self.rows[self.rows.index(row)] = moved
        said = f"{row.channel_label} to {moved.channel_label}"
        self._log(client_key, "channel_changed", note=said, detail=said)

    def note_channel_names(self, names: dict[str, str]) -> int:
        """admin_note_channel_names (migration 023): the name of each client's channel, unknown IDs ignored; how many
        rows changed."""
        self.noted.append(dict(names))
        changed = 0
        for row in list(self.rows):
            name = names.get(row.channel_id)
            if name is not None and name != row.channel_name:
                self.rows[self.rows.index(row)] = replace(row, channel_name=name)
                changed += 1
        return changed

    def _change(self, client_key: str, **values):
        """One client's row changed as the admin_* function changes it; ClientGone for an unknown or removed one."""
        row = next((row for row in self.rows if row.client_key == client_key and row.status != "removed"), None)
        if row is None:
            raise ClientGone(client_key)
        self.rows[self.rows.index(row)] = replace(row, **values)

    def _status(self, client_key: str) -> str | None:
        return next((row.status for row in self.rows if row.client_key == client_key), None)

    def pause_client(self, by: int, client_key: str, reason: str, note: str | None) -> None:
        self.calls.append(("pause", by, client_key, reason, note))
        if self._status(client_key) == "paused":
            raise ClientUnchanged(client_key)  # admin_pause_client: already paused
        self._change(client_key, status="paused", pause_reason=reason, pause_note=note)
        self._log(client_key, "paused", reason, note)

    def resume_client(self, by: int, client_key: str) -> None:
        self.calls.append(("resume", by, client_key))
        if self._status(client_key) == "active":
            raise ClientUnchanged(client_key)  # admin_resume_client: not paused
        self._change(client_key, status="active", pause_reason=None, pause_note=None)
        self._log(client_key, "resumed")

    def replace_client_key(self, by: int, client_key: str, sealed: bytes) -> None:
        self.calls.append(("replace_key", by, client_key, sealed))
        self._change(client_key)

    def remove_client(self, by: int, client_key: str) -> None:
        self.calls.append(("remove", by, client_key))
        self._change(client_key, status="removed", pause_reason=None, pause_note=None)
        self._log(client_key, "removed")

    def set_draft_limit(self, by: int, client_key: str, new_limit: int) -> LimitChange:
        self.calls.append((by, client_key, new_limit))
        row = next((row for row in self.rows if row.client_key == client_key), None)
        if row is None:
            raise ClientGone(client_key)  # what PostgresAdminDb raises when admin_client_status refuses
        self.rows[self.rows.index(row)] = replace(row, drafts_per_hour=new_limit)  # kept, as the database keeps it
        self.entries.append(ChangeRow(changed_at=datetime.now(timezone.utc), staff_name=ANA.name, kind="limit",
                                      subject=row.display_name, action="limit_changed",
                                      detail=f"{row.drafts_per_hour} to {new_limit}", client_key=client_key,
                                      reason=None, note=None))  # the History screen's view of it
        return LimitChange(limit_before=row.drafts_per_hour, limit_after=new_limit, used_this_hour=row.used_this_hour)


class ScriptedConn:
    """Answers each statement in turn; an exception in the script is raised by that statement."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.answer = self.answers.pop(0)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self

    def fetchone(self):
        return self.answer


def postgres_db_on(conn, monkeypatch) -> admin_db.PostgresAdminDb:
    """The real PostgresAdminDb, its pool swapped for one that hands out `conn`."""

    class Pool:
        @contextmanager
        def connection(self):
            yield conn

    monkeypatch.setattr(admin_db, "_pool", lambda: Pool())
    return admin_db.PostgresAdminDb(admin_db._pool(), read_only=False)
