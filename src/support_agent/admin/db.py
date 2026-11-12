"""The admin app's database access, logged in as admin_app: it may read two views and call the admin_* functions,
nothing else (migrations 017 and 018). Opening a page (GET) gets a read-only transaction, so Postgres itself refuses a
write there, whatever code path tries one (no GET changes anything).

One function is not a logged change: admin_note_channel_names (migration 023) sets a client's Slack channel name and
nothing else. Add client's POST calls it through PostgresAdminDb, and the hourly timer through name_noter(), a writable
connection that offers only that function. It is the one unlogged write: it changes only a cached display name."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime
from functools import lru_cache
from typing import Protocol

import psycopg
from fastapi import Request
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from support_agent.admin.settings import get_admin_settings

READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}  # opening a page; anything else is a change


@dataclass(frozen=True)
class Staff:
    id: int
    name: str
    slack_user_id: str


@dataclass(frozen=True)
class StaffMember:
    """One row of admin_staff_list (migration 017), active or not."""

    id: int
    name: str
    email: str
    slack_user_id: str
    active: bool
    added_at: datetime


class StaffTaken(Exception):
    """Another member already has this email or Slack ID (the staff table's unique columns). args[0] names which."""


class StaffGone(Exception):
    """No staff member with this id: admin_edit_staff or admin_set_staff_active refused."""


class LastMember(Exception):
    """The last active member can't be deactivated: admin_set_staff_active refused (nobody could sign in after)."""


@dataclass(frozen=True)
class ClientRow:
    """One client as the Clients screen shows it: no key, nothing sealed (admin_clients has neither)."""

    client_key: str
    display_name: str
    channel_id: str
    location_id: str
    drafts_per_hour: int
    used_this_hour: int
    status: str  # active, paused or removed
    pause_reason: str | None
    pause_note: str | None
    channel_name: str | None = None  # from Slack (migration 023); None until the first refresh has read it

    @property
    def channel_label(self) -> str:
        """What a page shows for the channel: `#name`, or the ID while no name is stored. Templates escape it."""
        return f"#{self.channel_name}" if self.channel_name else self.channel_id


@dataclass(frozen=True)
class HistoryRow:
    """One change to a client from admin_history (migration 020 adds the key, reason and note)."""

    changed_at: datetime
    staff_name: str
    action: str  # added, paused, resumed, removed, key_replaced, channel_changed, limit_changed
    detail: str | None  # a limit change's "20 to 30", a channel change's "#old to #new"
    reason: str | None  # a pause's
    note: str | None


@dataclass(frozen=True)
class ChangeRow:
    """One row of admin_history as the History screen shows it: any kind of change (migration 020 for the key)."""

    changed_at: datetime
    staff_name: str  # a Slack user ID for a Slack limit change by someone not on the staff list
    kind: str  # client, limit or staff
    subject: str  # the client's or staff member's name
    action: str
    detail: str | None
    client_key: str | None  # None for a staff change
    reason: str | None
    note: str | None


@dataclass(frozen=True)
class DraftTime:
    """One real draft for the chart: whose it was and when (admin_drafts_today, migration 022). Nothing of the ticket
    or the draft."""

    client_key: str
    created_at: datetime


class ClientGone(Exception):
    """The client was removed (or never existed) when the change reached the database: admin_client_status refused."""


class ClientTaken(Exception):
    """The channel or HighLevel location already belongs to a client: admin_add_client refused (migration 018), or
    admin_change_channel did, or two moves raced for one channel (migration 024)."""


class ChannelUsedByAnother(ClientTaken):
    """The channel belonged to another client once (client_channels, migration 024), so it can never be this client's:
    admin_change_channel or admin_add_client refused. Anything that handles ClientTaken handles it too."""


class ClientUnchanged(Exception):
    """Already as asked (paused twice, or resumed while active): another tab or person got there first."""


@dataclass(frozen=True)
class LimitChange:
    limit_before: int
    limit_after: int
    used_this_hour: int


class AdminDb(Protocol):
    def staff_for_email(self, email: str) -> Staff | None: ...

    def staff_list(self) -> list[StaffMember]: ...

    def add_staff(self, by: int, name: str, email: str, slack_user_id: str) -> int: ...

    def edit_staff(self, by: int, staff_id: int, name: str, email: str, slack_user_id: str) -> None: ...

    def set_staff_active(self, by: int, staff_id: int, active: bool) -> None: ...

    def history_people(self) -> list[str]: ...

    def changes(self, kind: str | None, client_key: str | None, person: str | None, as_of: datetime, offset: int,
                limit: int) -> list[ChangeRow]: ...

    def clients(self) -> list[ClientRow]: ...

    def drafts_today(self, since: datetime) -> list[DraftTime]: ...

    def add_client(self, by: int, channel_id: str, name: str, location_id: str, limit: int, sealed: bytes) -> str: ...

    def set_draft_limit(self, by: int, client_key: str, new_limit: int) -> LimitChange: ...

    def pause_client(self, by: int, client_key: str, reason: str, note: str | None) -> None: ...

    def resume_client(self, by: int, client_key: str) -> None: ...

    def replace_client_key(self, by: int, client_key: str, sealed: bytes) -> None: ...

    def remove_client(self, by: int, client_key: str) -> None: ...

    def change_channel(self, by: int, client_key: str, channel_id: str, channel_name: str | None) -> None: ...

    def channel_owners(self) -> dict[str, str]: ...

    def client_history(self, client_key: str) -> list[HistoryRow]: ...

    def last_change(self, client_key: str, actions: tuple[str, ...]) -> HistoryRow | None: ...

    def note_channel_names(self, names: dict[str, str]) -> int: ...


class PostgresAdminDb:
    def __init__(self, pool: ConnectionPool, read_only: bool):
        self._pool = pool
        self._read_only = read_only

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        with self._pool.connection() as conn:
            if self._read_only:
                conn.execute("set transaction read only")  # first statement, so it covers the whole transaction
            yield conn

    def staff_for_email(self, email: str) -> Staff | None:
        """The active staff member with this email (any case), or None. Asked on every request, so someone
        deactivated is refused at their next click, though their Cloudflare pass is valid for hours."""
        with self._connection() as conn:
            row = conn.execute("select id, name, slack_user_id from admin_staff_for_email(%s)", (email,)).fetchone()
        return Staff(**row) if row else None

    def staff_list(self) -> list[StaffMember]:
        with self._connection() as conn:
            rows = conn.execute(f"select {_STAFF_COLUMNS} from admin_staff_list").fetchall()
        return [StaffMember(**{name: row[name] for name in _STAFF_FIELDS}) for row in rows]

    def _staff_change(self, sql: str, params: tuple):
        """One admin_*_staff function; its refusals as the exceptions the pages answer."""
        with self._connection() as conn:
            try:
                return conn.execute(sql, params).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                # The staff table's two unique columns, by their constraint names (Postgres's defaults, migration 017).
                raise StaffTaken("email" if '"staff_email_key"' in str(exc) else "slack_user_id") from None
            except psycopg.errors.RaiseException as exc:
                if "unknown staff member" in str(exc):
                    raise StaffGone(params[1]) from None
                if "the last active member" in str(exc):
                    raise LastMember(params[1]) from None
                raise

    def add_staff(self, by: int, name: str, email: str, slack_user_id: str) -> int:
        row = self._staff_change("select admin_add_staff(%s, %s, %s, %s) as id", (by, name, email, slack_user_id))
        return row["id"]

    def edit_staff(self, by: int, staff_id: int, name: str, email: str, slack_user_id: str) -> None:
        self._staff_change("select admin_edit_staff(%s, %s, %s, %s, %s)", (by, staff_id, name, email, slack_user_id))

    def set_staff_active(self, by: int, staff_id: int, active: bool) -> None:
        self._staff_change("select admin_set_staff_active(%s, %s, %s)", (by, staff_id, active))

    def history_people(self) -> list[str]:
        """Everyone who made a change, as admin_history names them: staff names, and the Slack ID of anyone not on
        the staff list who changed a limit from Slack."""
        with self._connection() as conn:
            rows = conn.execute("select distinct staff_name from admin_history").fetchall()
        return [row["staff_name"] for row in rows]

    def changes(self, kind: str | None, client_key: str | None, person: str | None, as_of: datetime, offset: int,
                limit: int) -> list[ChangeRow]:
        """The History screen's page of admin_history, newest first, as of a moment (a change made after it would
        shift the pages); a None filter is no filter. The person is a name (the view has no staff id): two members
        with the same name show together."""
        with self._connection() as conn:
            rows = conn.execute(
                f"select {_CHANGE_COLUMNS} from admin_history "
                "where changed_at <= %(as_of)s "
                "and (%(kind)s::text is null or kind = %(kind)s) "
                "and (%(client_key)s::text is null or client_key = %(client_key)s) "
                "and (%(person)s::text is null or staff_name = %(person)s) "
                # No unique id in the view: every column after the time, so changes made at the same moment keep one
                # order from page to page and none repeats or drops at a page's edge.
                "order by changed_at desc, kind, client_key, subject, action, staff_name, detail, reason, note "
                "limit %(limit)s offset %(offset)s",
                {"kind": kind, "client_key": client_key, "person": person, "as_of": as_of, "offset": offset,
                 "limit": limit},
            ).fetchall()
        return [ChangeRow(**{name: row[name] for name in _CHANGE_FIELDS}) for row in rows]

    def clients(self) -> list[ClientRow]:
        with self._connection() as conn:
            rows = conn.execute(f"select {_CLIENT_COLUMNS} from admin_clients").fetchall()
        return [_client_row(row) for row in rows]

    def drafts_today(self, since: datetime) -> list[DraftTime]:
        """The time of every real draft since this moment, per client, oldest first. The view holds only the last
        48 hours, so a `since` further back gets no more than that; the caller passes the zone's midnight."""
        with self._connection() as conn:
            rows = conn.execute("select client_key, created_at from admin_drafts_today where created_at >= %s "
                                "order by created_at", (since,)).fetchall()
        return [DraftTime(row["client_key"], row["created_at"]) for row in rows]

    def add_client(self, by: int, channel_id: str, name: str, location_id: str, limit: int, sealed: bytes) -> str:
        """The new client's key (or a removed client's old one, brought back: migration 018)."""
        with self._connection() as conn:
            try:
                row = conn.execute("select admin_add_client(%s, %s, %s, %s, %s, %s) as client_key",
                                   (by, channel_id, name, location_id, limit, sealed)).fetchone()
            except psycopg.errors.RaiseException as exc:
                if "belonged to another client" in str(exc):  # migration 024: a channel another client once had
                    raise ChannelUsedByAnother(channel_id) from None
                if "already belongs to a client" in str(exc):
                    raise ClientTaken(channel_id) from None
                raise
        return row["client_key"]

    def set_draft_limit(self, by: int, client_key: str, new_limit: int) -> LimitChange:
        """The change and this hour's use read back in one transaction: the toast shows both."""
        with self._connection() as conn:
            try:
                changed = conn.execute("select limit_before, limit_after from admin_set_draft_limit(%s, %s, %s)",
                                       (by, client_key, new_limit)).fetchone()
            except psycopg.errors.RaiseException as exc:
                if "unknown or removed client" in str(exc):
                    raise ClientGone(client_key) from None
                raise
            used = conn.execute("select used_this_hour from admin_clients where client_key = %s",
                                (client_key,)).fetchone()
        return LimitChange(changed["limit_before"], changed["limit_after"], used["used_this_hour"])

    def _client_change(self, client_key: str, sql: str, params: tuple) -> None:
        """One admin_* client function; its refusals as the exceptions the pages answer."""
        with self._connection() as conn:
            try:
                conn.execute(sql, params)
            except psycopg.errors.RaiseException as exc:
                if "unknown or removed client" in str(exc):
                    raise ClientGone(client_key) from None
                if "already paused" in str(exc) or "not paused" in str(exc):
                    raise ClientUnchanged(client_key) from None
                raise

    def pause_client(self, by: int, client_key: str, reason: str, note: str | None) -> None:
        self._client_change(client_key, "select admin_pause_client(%s, %s, %s, %s)", (by, client_key, reason, note))

    def resume_client(self, by: int, client_key: str) -> None:
        self._client_change(client_key, "select admin_resume_client(%s, %s)", (by, client_key))

    def replace_client_key(self, by: int, client_key: str, sealed: bytes) -> None:
        self._client_change(client_key, "select admin_replace_client_key(%s, %s, %s)", (by, client_key, sealed))

    def remove_client(self, by: int, client_key: str) -> None:
        self._client_change(client_key, "select admin_remove_client(%s, %s)", (by, client_key))

    def change_channel(self, by: int, client_key: str, channel_id: str, channel_name: str | None) -> None:
        """Moves the client to another Slack channel (migration 024). The function's refusals become the exceptions the
        page answers; two moves racing for one channel end in client_map's primary key, which reads as taken too (any
        other database error stays a failure)."""
        with self._connection() as conn:
            try:
                conn.execute("select admin_change_channel(%s, %s, %s, %s)", (by, client_key, channel_id, channel_name))
            except psycopg.errors.UniqueViolation as exc:
                if '"client_map_pkey"' in str(exc):
                    raise ClientTaken(channel_id) from None
                raise
            except psycopg.errors.RaiseException as exc:
                if "unknown or removed client" in str(exc):
                    raise ClientGone(client_key) from None
                if "already on that channel" in str(exc):
                    raise ClientUnchanged(client_key) from None
                if "belonged to another client" in str(exc):
                    raise ChannelUsedByAnother(channel_id) from None
                if "already belongs to a client" in str(exc):
                    raise ClientTaken(channel_id) from None
                raise

    def channel_owners(self) -> dict[str, str]:
        """Every channel a client has had, to the client's key (client_channels, migration 024): the lists leave out a
        channel that belonged to another client."""
        with self._connection() as conn:
            rows = conn.execute("select channel_id, client_key from client_channels").fetchall()
        return {row["channel_id"]: row["client_key"] for row in rows}

    def client_history(self, client_key: str) -> list[HistoryRow]:
        """This client's changes, newest first: by its key, since two clients can share a name (migration 020)."""
        with self._connection() as conn:
            rows = conn.execute("select changed_at, staff_name, action, detail, reason, note from admin_history "
                                "where client_key = %s order by changed_at desc limit 100", (client_key,)).fetchall()
        return [HistoryRow(**row) for row in rows]

    def last_change(self, client_key: str, actions: tuple[str, ...]) -> HistoryRow | None:
        """The client's newest change of these kinds, however many others came after it (Undo: the pause to restore,
        and whether anyone paused or resumed since)."""
        with self._connection() as conn:
            row = conn.execute("select changed_at, staff_name, action, detail, reason, note from admin_history "
                               "where client_key = %s and action = any(%s) order by changed_at desc limit 1",
                               (client_key, list(actions))).fetchone()
        return HistoryRow(**row) if row else None

    def note_channel_names(self, names: dict[str, str]) -> int:
        """Add client's POST notes the new client's channel name (a GET's connection is read-only, so Postgres would
        refuse this there). Returns how many rows changed."""
        with self._connection() as conn:
            return _note_names(conn, names)


def _note_names(conn: Connection, names: dict[str, str]) -> int:
    """admin_note_channel_names (migration 023): the one function that sets a channel name, the only write besides the
    admin_* functions that log a change. Not in History."""
    return conn.execute("select admin_note_channel_names(%s) as changed", (Jsonb(names),)).fetchone()["changed"]


class ChannelNameNoter:
    """A writable connection that offers only note_channel_names: what the hourly timer is given, so it can reach
    nothing else in the database."""

    def __init__(self, conn: Connection):
        self._conn = conn

    def note_channel_names(self, names: dict[str, str]) -> int:
        return _note_names(self._conn, names)


@contextmanager
def name_noter() -> Iterator[ChannelNameNoter]:
    """The admin app's one writer outside a POST: the hourly timer's, a connection of its own."""
    with _pool().connection() as conn:
        yield ChannelNameNoter(conn)


_CHANGE_FIELDS = [field.name for field in fields(ChangeRow)]
_CHANGE_COLUMNS = ", ".join(_CHANGE_FIELDS)
_STAFF_FIELDS = [field.name for field in fields(StaffMember)]
_STAFF_COLUMNS = ", ".join(_STAFF_FIELDS)
_CLIENT_FIELDS = [field.name for field in fields(ClientRow)]
_CLIENT_COLUMNS = ", ".join(_CLIENT_FIELDS)


def _client_row(row: dict) -> ClientRow:
    # Only the fields the screen names, whatever else a row carries: a key column joined in later never reaches a page.
    return ClientRow(**{name: row[name] for name in _CLIENT_FIELDS})


@lru_cache
def _pool() -> ConnectionPool:
    return ConnectionPool(
        get_admin_settings().admin_database_url.get_secret_value(),
        min_size=1,
        max_size=4,  # a handful of support people
        kwargs={"row_factory": dict_row, "connect_timeout": 5},  # a wrong address fails in 5 s, not hangs
        timeout=10,
        open=True,
    )


def get_admin_db(request: Request) -> AdminDb:
    return PostgresAdminDb(_pool(), read_only=request.method in READ_ONLY_METHODS)
