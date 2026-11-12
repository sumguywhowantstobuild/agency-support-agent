"""The client sheet: one client's details, pause (a reason required) and resume, a new HighLevel key
(tested first, as Add client), remove (the name typed to confirm), and the client's history. Mounted with SIGNED_IN.

Every change is a plain form that answers with a redirect back to the sheet, so a reload never repeats it; admin.js
adds the confirm row's reveal and the toast's Undo on top."""

import logging
from datetime import timezone
from time import monotonic
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from support_agent.admin import add_client
from support_agent.admin.add_client import (FIELD_ERRORS, NOT_FREE, REFUSALS, SLACK_DOWN, USED_BY_ANOTHER, WAIT,
                                            bot_channels, checked_in_time, free_for_a_move, read_form, sealed_key)
from support_agent.admin.agent_client import ConnectionChecker, get_connection_checker
from support_agent.admin.auth import NO_DATABASE, Page, Refused, current_staff, made_by_this_app
from support_agent.admin.check_cap import CheckCap, get_check_cap
from support_agent.admin.clients import (PAUSE_REASONS, STATES, _level, _state, clients_context, overview,
                                         read_channel_owners, read_clients)
from support_agent.admin.db import (AdminDb, ChangeRow, ChannelUsedByAnother, ClientGone, ClientRow, ClientTaken,
                                    ClientUnchanged, HistoryRow, Staff, get_admin_db)
from support_agent.admin.settings import AdminSettings, get_admin_settings, get_zone
from support_agent.admin.slack import Channel, ChannelLister, get_channel_lister
from support_agent.admin.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()

NOTE_LIMIT = 200  # the same as client_map.pause_note (migration 018)
REASON_ERROR = "Pick a reason for the pause."
NOTE_ERROR = f"The note can be up to {NOTE_LIMIT} characters."
PICK_ERROR = "Choose the channel to move to."
GONE: Page = ("Client not found", "This client was removed, or the address is wrong. Open Clients to see the list.")
# A failure can come after the database committed, so the page doesn't promise nothing changed.
CHANGE_DOWN: Page = ("Can't change the client",
                     "Couldn't reach the database, so the change may not have been made. Open the client to check.")


def _found(rows: list[ClientRow], client_key: str) -> ClientRow:
    row = next((row for row in rows if row.client_key == client_key and row.status != "removed"), None)
    if row is None:
        raise Refused(f"no client {client_key!r}", GONE, 404)
    return row


def _client(db: AdminDb, client_key: str) -> ClientRow:
    return _found(read_clients(db), client_key)


def _said(change: HistoryRow) -> str:
    """What the change did, after the person's name."""
    if change.action == "paused":
        reason = PAUSE_REASONS.get(change.reason, change.reason)
        return f"paused it: {reason}" + (f", “{change.note}”" if change.note else "")
    if change.action == "limit_changed":
        return f"changed the limit from {change.detail} drafts an hour"  # detail is "20 to 30"
    if change.action == "channel_changed":
        return f"moved it to another Slack channel: {change.detail}"  # detail is "#old to #new" (migration 024)
    return SAID.get(change.action, change.action)


SAID = {"added": "added the client", "resumed": "resumed drafting", "removed": "removed the client",
        "key_replaced": "replaced the HighLevel token"}


def _when(change: HistoryRow | ChangeRow, zone: ZoneInfo) -> tuple[str, str]:
    """(machine time, words): the machine time is UTC, to compare and sort; the words are in the agency's zone,
    named by its short name ("EST") so a reader can tell which side of a clock change a time is on."""
    at = change.changed_at.astimezone(zone)
    return change.changed_at.astimezone(timezone.utc).isoformat(), f"{at:%b} {at.day}, {at:%H:%M} {at.tzname()}"


def _history(db: AdminDb, client_key: str, zone: ZoneInfo) -> list[tuple[HistoryRow, str, tuple[str, str]]]:
    try:
        changes = db.client_history(client_key)
    except psycopg.Error as exc:
        raise Refused(f"history unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None
    return [(change, _said(change), _when(change, zone)) for change in changes]


# After a change: what the toast says, from the saved name (never words from the address).
TOASTS = {"paused": "Paused {}.", "resumed": "Resumed {}.", "key_replaced": "New HighLevel token saved for {}.",
          "channel_changed": "Moved {} to {}.",  # the client, and its channel as it is now: #name, or the ID
          "stale": "{} changed since, so nothing was undone."}


def _sheet(request: Request, staff: Staff, db: AdminDb, row: ClientRow, *, zone: ZoneInfo, pausing: bool = False,
           replacing: bool = False, removing: bool = False, changing: bool = False,
           move_channels: list[Channel] | None = None, channel_error: str | None = None, form: dict | None = None,
           errors: dict | None = None, done: str = "", history: list | None = None, status_code: int = 200,
           rows: list[ClientRow] | None = None):
    """The client's panel; `form` never holds a key: the key field is always empty. `history` when already read.
    `changing` shows Change channel's question, with `move_channels` to pick from (None while Slack can't say).
    With `rows` (the clients read for the panel), the panel comes over the Clients page; without them, a refused form
    answers with the panel alone, so what was typed never waits on another database read."""
    history = _history(db, row.client_key, zone) if history is None else history
    toast = TOASTS[done].format(row.display_name, row.channel_label) if done in TOASTS else None
    page = {"staff": staff, "current": "clients", "client": row, "state": _state(row), "level": _level(row),
            "states": STATES, "reasons": PAUSE_REASONS, "pausing": pausing, "replacing": replacing,
            "removing": removing, "changing": changing, "move_channels": move_channels,
            "channel_error": channel_error, "form": form or {}, "errors": errors or {}, "note_limit": NOTE_LIMIT,
            "history": history, "toast": toast, "undo": _undo(db, row, done) if toast else None,
            "panel": "client_panel.html", "page_title": row.display_name}
    if rows is None:
        return templates.TemplateResponse(request, "panel_alone.html", page, status_code=status_code)
    behind = clients_context(rows) | overview(db, rows, zone)  # the list and its main panel, as on the page itself
    return templates.TemplateResponse(request, "clients.html", behind | page, status_code=status_code)


def _changed(client_key: str, done: str | None = None) -> RedirectResponse:
    return RedirectResponse(f"/clients/{client_key}" + (f"?done={done}" if done else ""), status_code=303)


def _make(change, client_key: str, done: str) -> RedirectResponse:
    """Runs one change: back to the sheet with what was done, or as it now is when someone got there first."""
    try:
        change()
    except ClientUnchanged:
        return _changed(client_key)
    except ClientGone:
        raise Refused(f"no client {client_key!r}", GONE, 404) from None
    except psycopg.Error as exc:  # its text can name the login and address, so only its type goes in the log
        raise Refused(f"{done} {client_key} failed: {type(exc).__name__}", CHANGE_DOWN, 503) from None
    return _changed(client_key, done)


@router.get("/clients/{client_key}", response_class=HTMLResponse)
def client_sheet(request: Request, client_key: str, confirm: str = "", done: str = "",
                 staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
                 zone: ZoneInfo = Depends(get_zone), list_channels: ChannelLister = Depends(get_channel_lister)):
    rows = read_clients(db)  # the same rows give the client and the list behind its panel
    row = _found(rows, client_key)
    changing = confirm == "channel"  # only its question asks Slack for the channels
    # The message for `done`, and its Undo, only for a request this app's own pages made (auth.made_by_this_app).
    return _sheet(request, staff, db, row, zone=zone, pausing=confirm == "pause" and row.status == "active",
                  replacing=confirm == "key", removing=confirm == "remove", changing=changing,
                  move_channels=_movable(db, rows, client_key, list_channels) if changing else None,
                  done=done if made_by_this_app(request) else "", rows=rows)


def _movable(db: AdminDb, rows: list[ClientRow], client_key: str, list_channels: ChannelLister) -> list[Channel] | None:
    """The channels this client could move to, from the bot's list as it is now; None while Slack can't say."""
    owners = read_channel_owners(db)
    channels = bot_channels(list_channels)
    return None if channels is None else free_for_a_move(rows, owners, client_key, channels)


# The changes after which an open Undo no longer fits: the pause or resume it reverses, and a remove or re-add (a
# re-added client starts afresh). Limit changes don't count.
SWITCHES = ("paused", "resumed", "added", "removed")


def _last(db: AdminDb, client_key: str, actions: tuple[str, ...]) -> HistoryRow | None:
    try:
        return db.last_change(client_key, actions)
    except psycopg.Error as exc:
        raise Refused(f"history unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None


def _undo(db: AdminDb, row: ClientRow, done: str) -> dict | None:
    """What the toast's Undo posts: a pause is resumed; a resume pauses again with the last pause's reason and note
    (asked for directly, migration 020). Only while the client is still as the change left it, and naming that change
    ("after"), so an Undo pressed after someone else paused or resumed undoes nothing. The dict says its whole address
    and the fields it posts (toast.html, shared with Staff)."""
    if (done, row.status) not in (("paused", "paused"), ("resumed", "active")):
        return None
    switched = _last(db, row.client_key, SWITCHES)
    if switched is None:
        return None
    after = {"after": switched.changed_at.isoformat()}
    if done == "paused":
        return {"address": f"/clients/{row.client_key}/resume", "fields": after}
    last = _last(db, row.client_key, ("paused",))
    if last and last.reason in PAUSE_REASONS:
        return {"address": f"/clients/{row.client_key}/pause",
                "fields": after | {"reason": last.reason, "note": last.note or ""}}
    return None


def _stale(db: AdminDb, client_key: str, form: dict[str, str]) -> bool:
    """An Undo whose change is no longer the newest pause, resume, remove or re-add. (A change in the moment between
    this check and the Undo's own is not caught; staff are a handful.)"""
    if "after" not in form:
        return False
    switched = _last(db, client_key, SWITCHES)
    return switched is None or switched.changed_at.isoformat() != form["after"]


@router.post("/clients/{client_key}/pause")
def pause(request: Request, client_key: str, form: dict[str, str] = Depends(read_form),
          staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
          zone: ZoneInfo = Depends(get_zone)):
    reason = form.get("reason", "")
    note = form.get("note", "").strip()
    errors = {}
    if reason not in PAUSE_REASONS:
        errors["reason"] = REASON_ERROR
    if len(note) > NOTE_LIMIT:
        errors["note"] = NOTE_ERROR
    if errors:
        return _sheet(request, staff, db, _client(db, client_key), zone=zone, pausing=True,
                      form={"reason": reason, "note": note}, errors=errors, status_code=422)
    if _stale(db, client_key, form):  # an Undo of a resume someone else has changed since
        return _changed(client_key, "stale")
    return _make(lambda: db.pause_client(staff.id, client_key, reason, note or None), client_key, "paused")


@router.post("/clients/{client_key}/resume")
def resume(client_key: str, form: dict[str, str] = Depends(read_form), staff: Staff = Depends(current_staff),
           db: AdminDb = Depends(get_admin_db)):
    if _stale(db, client_key, form):  # an Undo of a pause someone else has changed since
        return _changed(client_key, "stale")
    return _make(lambda: db.resume_client(staff.id, client_key), client_key, "resumed")


@router.post("/clients/{client_key}/key")
def replace_key(request: Request, client_key: str, form: dict[str, str] = Depends(read_form),
                staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
                settings: AdminSettings = Depends(get_admin_settings),
                check: ConnectionChecker = Depends(get_connection_checker), cap: CheckCap = Depends(get_check_cap)):
    """A new key for the client's own HighLevel account: the same seal, test and time limit as Add client."""
    deadline = monotonic() + add_client.ANSWER_SECONDS  # read at the call, as Add client's own routes do
    sealed = sealed_key(form, settings)  # from here on only the sealed value exists in this request
    row = _client(db, client_key)
    history = _history(db, client_key, settings.zone)  # read before the test: a refusal after it needs no more reads
    if sealed is None:
        error = FIELD_ERRORS["key"]
    elif not cap.allow(staff.id):
        error = WAIT
    else:
        verdict = checked_in_time(check, row.location_id, sealed, deadline)
        if verdict == "works":
            return _make(lambda: db.replace_client_key(staff.id, client_key, sealed), client_key, "key_replaced")
        error = REFUSALS[verdict]
    return _sheet(request, staff, db, row, zone=settings.zone, replacing=True, errors={"key": error},
                  history=history, status_code=429 if error == WAIT else 422)


@router.post("/clients/{client_key}/remove")
def remove(request: Request, client_key: str, form: dict[str, str] = Depends(read_form),
           staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
           zone: ZoneInfo = Depends(get_zone)):
    """Only with the client's name typed exactly: the key is deleted and can't come back without a new one."""
    row = _client(db, client_key)
    if form.get("confirm", "").strip() != row.display_name:
        return _sheet(request, staff, db, row, zone=zone, removing=True,
                      errors={"confirm": f"Type {row.display_name} exactly to remove it."}, status_code=422)
    _make(lambda: db.remove_client(staff.id, client_key), client_key, "removed")
    return RedirectResponse(f"/clients?removed={client_key}", status_code=303)  # the sheet is gone with the client


@router.post("/clients/{client_key}/channel")
def change_channel(request: Request, client_key: str, form: dict[str, str] = Depends(read_form),
                   staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
                   zone: ZoneInfo = Depends(get_zone), list_channels: ChannelLister = Depends(get_channel_lister)):
    """Moves the client to another Slack channel. The channel and its name come from the bot's list asked for now, never
    from the form: the chosen ID must still be in it and free for this client (the bot can leave a channel, or another
    client take it, after the panel opened). Nothing is saved on a refusal."""
    rows = read_clients(db)
    row = _found(rows, client_key)
    chosen = form.get("channel_id", "").strip()
    if chosen == row.channel_id:  # another tab already moved it there: the panel as it is, no message
        return _changed(client_key)
    free = _movable(db, rows, client_key, list_channels)

    def again(status: int, errors: dict | None = None, error: str | None = None):
        return _sheet(request, staff, db, row, zone=zone, changing=True, move_channels=free, errors=errors,
                      channel_error=error, status_code=status)

    if free is None:
        return again(503, error=SLACK_DOWN)
    if not chosen:
        return again(422, {"channel_id": PICK_ERROR})
    picked = next((channel for channel in free if channel.id == chosen), None)
    if picked is None:
        return again(409, {"channel_id": NOT_FREE})
    try:
        return _make(lambda: db.change_channel(staff.id, client_key, picked.id, picked.name), client_key,
                     "channel_changed")
    except ChannelUsedByAnother:  # a stale page: the lists leave out a channel another client had
        return again(409, {"channel_id": USED_BY_ANOTHER})
    except ClientTaken:  # held since the check, or a race for it that the database's key settled
        return again(409, {"channel_id": NOT_FREE})
