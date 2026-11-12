"""Add client: the key is locked with the public half in the request that brings it, tested through the
agent, and saved only when the test says it works. Mounted with SIGNED_IN, like every page.

The key never comes back: the form is read here (no FastAPI validation that could echo it), the key is sealed and
dropped before anything else, and every re-shown form leaves the key field empty. Dropped means no
code here holds or passes on the plain key after sealing: never to the agent, the database, a log or a page. Python
can't wipe memory, and the request keeps its raw body until it ends, so the plain key lives in memory until then."""

import logging
import re
import threading
from collections.abc import Callable
from time import monotonic
from typing import TypeVar
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from support_agent.admin.agent_client import ConnectionChecker, Verdict, get_connection_checker
from support_agent.admin.auth import Page, Refused, current_staff
from support_agent.admin.check_cap import CHECKS_PER_MINUTE, CheckCap, get_check_cap
from support_agent.admin.clients import (LIMIT_ERROR, clients_context, overview, read_channel_owners, read_clients,
                                         valid_limit)
from support_agent.admin.db import AdminDb, ChannelUsedByAnother, ClientRow, ClientTaken, Staff, get_admin_db
from support_agent.admin.settings import AdminSettings, get_admin_settings, get_zone
from support_agent.admin.slack import Channel, ChannelLister, SlackUnavailable, get_channel_lister
from support_agent.admin.templating import templates
from support_agent.keys import seal

logger = logging.getLogger(__name__)
router = APIRouter()
T = TypeVar("T")

ANSWER_SECONDS = 25  # Save and Test answer within this, whatever Slack and the agent do
# Slack's share of it: Slack answers in well under a second, and the agent needs 20 of the rest (it gives up at 20).
SLACK_SECONDS = 5
FIELDS = ("channel_id", "name", "limit", "location_id")  # shown again after a refusal; the key never is
# The same rules as admin_add_client (migration 018), so a refusal comes here in words, not from the database.
FIELD_ERRORS = {
    "channel_id": "Choose the client's Slack channel.",
    "name": "Type the client's name, up to 80 characters.",
    "limit": LIMIT_ERROR,
    "location_id": "The location id is letters, digits, - and _ only (up to 64).",
    "key": "Paste the client's HighLevel token.",
}
# The connection test's verdicts as staff read them.
WORKS = "Connected to this HighLevel account."
REFUSALS = {
    "wrong_account": "This token doesn't open that HighLevel account. Check the location id and the token.",
    # Also what a refused token reads as today: the agent's verdict can't tell the two apart.
    "unreachable": "Couldn't confirm this token with HighLevel. Check the token and location ID, or try again in a "
                   "minute. Nothing was saved.",
}
WAIT = f"That's {CHECKS_PER_MINUTE} connection tests in a minute. Wait a minute, then try again."
TAKEN_ERROR = "That Slack channel or HighLevel account already belongs to a client. Nothing was saved."
NOT_FREE = "That channel isn't free any more. Choose another one."
USED_BY_ANOTHER = "That channel belonged to another client. Use a new channel."
SLACK_DOWN = "Can't check the Slack channel right now. Nothing was saved; try again in a minute."
# A failure can come after the database committed, so the page doesn't promise nothing changed.
SAVE_DOWN: Page = ("Can't add the client",
                   "Couldn't reach the database, so the client may not have been added. Open Clients to check.")


async def read_form(request: Request) -> dict[str, str]:
    """The form read with the standard library, as the limit form is (no python-multipart). A blank or missing field
    reads as "" wherever it is used. The client sheet reads its forms with this too."""
    fields = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    return {name: values[0] for name, values in fields.items()}


def _field_errors(typed: dict[str, str], has_key: bool) -> dict[str, str]:
    wrong = {
        "channel_id": not re.fullmatch(r"[CG][A-Z0-9]{2,}", typed["channel_id"]),
        "name": not 1 <= len(typed["name"]) <= 80,
        "limit": valid_limit(typed["limit"]) is None,
        "location_id": not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", typed["location_id"]),
        "key": not has_key,
    }
    return {field: FIELD_ERRORS[field] for field, is_wrong in wrong.items() if is_wrong}


def _within(seconds: float, call: Callable[[], T]) -> T:
    """call()'s answer, or TimeoutError once `seconds` pass: the HTTP time limits count each wait apart (connect, send,
    each read), so only this bounds the whole call. A call still running finishes in the background, as the agent's
    connection test does (support_agent.api)."""
    done: dict = {}

    def run():
        try:
            done["answer"] = call()
        except Exception as exc:  # handed to the request, which knows what it means
            done["error"] = exc

    worker = threading.Thread(target=run, name="add-client-call", daemon=True)
    worker.start()
    worker.join(max(seconds, 0))
    if "error" in done:
        raise done["error"]
    if "answer" not in done:
        raise TimeoutError
    return done["answer"]


def checked_in_time(check: ConnectionChecker, location_id: str, sealed: bytes, deadline: float) -> Verdict:
    try:
        return _within(deadline - monotonic(), lambda: check(location_id, sealed))
    except TimeoutError:
        logger.warning("connection test for %s: no answer in time", location_id)
        return "unreachable"


def bot_channels(list_channels: ChannelLister) -> list[Channel] | None:
    """Every channel the bot is in; None while Slack can't say (within SLACK_SECONDS). The client sheet's Change channel
    asks through this too."""
    try:
        return _within(SLACK_SECONDS, list_channels)
    except SlackUnavailable as exc:  # Slack's error code, never the token
        logger.warning("channel list unavailable: %s", exc)
    except TimeoutError:
        logger.warning("channel list unavailable: no answer in time")
    return None


def free_for_a_new_client(rows: list[ClientRow], owners: dict[str, str], channels: list[Channel]) -> list[Channel]:
    """The bot's channels a new client can have. Not an active or paused client's. A removed client's own stays: picking
    it again brings that client back (admin_add_client, migration 018). Not one that belonged to another client (owners:
    client_channels, migration 024), which the database would refuse: a removed client's current channel is its own."""
    taken = {row.channel_id for row in rows if row.status != "removed"}
    coming_back = {row.channel_id for row in rows if row.status == "removed"}
    return [channel for channel in channels
            if channel.id not in taken and (channel.id not in owners or channel.id in coming_back)]


def free_for_a_move(rows: list[ClientRow], owners: dict[str, str], client_key: str,
                    channels: list[Channel]) -> list[Channel]:
    """The bot's channels this client can move to: no client row holds it (a removed client keeps its row, and the
    channel is that row's key), and none belonged to another client. The client's own old channels stay."""
    held = {row.channel_id for row in rows}
    return [channel for channel in channels if channel.id not in held and owners.get(channel.id, client_key) == client_key]


def _free_channels(rows: list[ClientRow], owners: dict[str, str], list_channels: ChannelLister) -> list[Channel] | None:
    """The channels a new client can have, given the clients and channel owners read; None while Slack can't say."""
    channels = bot_channels(list_channels)
    return None if channels is None else free_for_a_new_client(rows, owners, channels)


def _note_channel_name(db: AdminDb, channel_id: str, channels: list[Channel]) -> None:
    """The new client's channel name, from the Slack list this POST asked for (never a form field). The client is
    already saved, so a failure here only leaves its ID showing until the hourly refresh; the log gets the error's type."""
    names = {channel.id: channel.name for channel in channels if channel.id == channel_id}
    try:
        db.note_channel_names(names)
    except psycopg.Error as exc:  # its text can name the login and address
        logger.warning("channel name not saved: %s", type(exc).__name__)


def _sheet(request: Request, staff: Staff, channels: list[Channel] | None, typed: dict[str, str],
           errors: dict[str, str], error: str | None, status_code: int = 422, rows: list[ClientRow] | None = None,
           behind: dict | None = None):
    """The Add client panel, new or shown again with a refusal. `typed` never holds the key: the key field is always
    empty. With `rows` (the clients read for it) and `behind` (the main panel's chart and latest change), the panel comes
    over the Clients page; without them, a refused form answers with the panel alone, so what was typed never waits on
    another database read."""
    page = {"staff": staff, "current": "clients", "channels": channels, "form": typed, "errors": errors,
            "error": error, "panel": "add_panel.html", "page_title": "Add client"}
    if rows is None:
        return templates.TemplateResponse(request, "panel_alone.html", page, status_code=status_code)
    return templates.TemplateResponse(request, "clients.html", clients_context(rows) | behind | page,
                                      status_code=status_code)


@router.get("/clients/new", response_class=HTMLResponse)
def add_client_sheet(request: Request, staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
                     list_channels: ChannelLister = Depends(get_channel_lister), zone: ZoneInfo = Depends(get_zone)):
    rows = read_clients(db)  # the same rows give the free channels and the list behind the panel
    blank = {field: "" for field in FIELDS} | {"limit": "20"}
    return _sheet(request, staff, _free_channels(rows, read_channel_owners(db), list_channels), blank, {}, None, 200,
                  rows, overview(db, rows, zone))


def sealed_key(form: dict[str, str], settings: AdminSettings) -> bytes | None:
    """The key field, locked with the public half and dropped; None when it was left blank."""
    key = form.pop("key", "").strip()
    return seal(settings.ghl_key_public.get_secret_value(), key) if key else None


@router.post("/clients/new/test")
def connection_test_button(form: dict[str, str] = Depends(read_form), staff: Staff = Depends(current_staff),
                           settings: AdminSettings = Depends(get_admin_settings),
                           check: ConnectionChecker = Depends(get_connection_checker),
                           cap: CheckCap = Depends(get_check_cap)):
    """The panel's Test button: the verdict in words, nothing saved. Save tests the key again anyway."""
    deadline = monotonic() + ANSWER_SECONDS
    sealed = sealed_key(form, settings)
    typed = {field: form.get(field, "").strip() for field in FIELDS}
    errors = {field: message for field, message in _field_errors(typed, has_key=sealed is not None).items()
              if field in ("location_id", "key")}
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    if not cap.allow(staff.id):
        return JSONResponse({"result": "wait", "message": WAIT}, status_code=429)
    verdict = checked_in_time(check, typed["location_id"], sealed, deadline)
    return {"result": verdict, "message": WORKS if verdict == "works" else REFUSALS[verdict]}


@router.post("/clients/new")
def add_client(request: Request, form: dict[str, str] = Depends(read_form), staff: Staff = Depends(current_staff),
               db: AdminDb = Depends(get_admin_db), settings: AdminSettings = Depends(get_admin_settings),
               check: ConnectionChecker = Depends(get_connection_checker),
               list_channels: ChannelLister = Depends(get_channel_lister), cap: CheckCap = Depends(get_check_cap)):
    deadline = monotonic() + ANSWER_SECONDS
    sealed = sealed_key(form, settings)  # from here on only the sealed value exists in this request
    typed = {field: form.get(field, "").strip() for field in FIELDS}
    # Asked once: the chosen channel must still be free (the bot can leave it, or it can be archived, after the panel
    # opened), and a refused form is shown again with this same list.
    channels = _free_channels(read_clients(db), read_channel_owners(db), list_channels)

    errors = _field_errors(typed, has_key=sealed is not None)
    if "channel_id" not in errors and channels is not None and typed["channel_id"] not in {c.id for c in channels}:
        errors["channel_id"] = NOT_FREE
    if errors:
        return _sheet(request, staff, channels, typed, errors, None)
    if channels is None:
        return _sheet(request, staff, channels, typed, {}, SLACK_DOWN, 503)
    if not cap.allow(staff.id):  # Save asks the agent too, so it counts
        return _sheet(request, staff, channels, typed, {}, WAIT, 429)
    verdict = checked_in_time(check, typed["location_id"], sealed, deadline)
    if verdict != "works":
        return _sheet(request, staff, channels, typed, {}, REFUSALS[verdict])
    try:
        client_key = db.add_client(staff.id, typed["channel_id"], typed["name"], typed["location_id"],
                                   valid_limit(typed["limit"]), sealed)
    except ChannelUsedByAnother:  # a channel another client had: the lists leave it out, so this is a stale page
        return _sheet(request, staff, channels, typed, {}, USED_BY_ANOTHER, 409)
    except ClientTaken:  # another tab, or another person, saved it first
        return _sheet(request, staff, channels, typed, {}, TAKEN_ERROR, 409)
    except psycopg.Error as exc:  # its text can name the login and address, so only its type goes in the log
        raise Refused(f"add client failed: {type(exc).__name__}", SAVE_DOWN, 503) from None
    _note_channel_name(db, typed["channel_id"], channels)
    return RedirectResponse(f"/clients?added={client_key}", status_code=303)
