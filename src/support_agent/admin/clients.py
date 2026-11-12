"""The Clients screen: every client that hasn't been removed, with this hour's use against its limit.
Mounted with SIGNED_IN, so every route here needs the Cloudflare pass and a place on the staff list."""

import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from support_agent.admin.auth import NO_DATABASE, Refused, current_staff, made_by_this_app
from support_agent.admin.chart import Chart, build_chart, today_starts
from support_agent.admin.db import AdminDb, ClientGone, ClientRow, Staff, get_admin_db
from support_agent.admin.settings import get_zone
from support_agent.admin.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()

# The six reasons migration 018 allows, as staff read them.
PAUSE_REASONS = {
    "key_broken": "Key broken",
    "drafts_wrong": "Drafts going wrong",
    "account_changing": "Account changing",
    "privacy_hold": "Privacy hold",
    "client_asked": "Client asked",
    "billing_hold": "Billing hold",
}


# Each state's status badge: tone, icon, words (design system status: a word with the colour, never colour alone).
STATES = {
    "drafting": ("ok", "i-check", "Drafting"),
    "at_limit": ("warn", "i-warn", "At limit"),
    "paused": ("idle", "i-pause", "Paused"),
}


def _state(row: ClientRow) -> str:
    if row.status == "paused":
        return "paused"
    return "at_limit" if row.used_this_hour >= row.drafts_per_hour else "drafting"  # a lowered limit can be passed


def _level(row: ClientRow) -> str:
    """How full the bar is, 0 to 1: admin.js writes it as the fill's scaleX (the page's policy blocks inline styles)."""
    return format(round(min(row.used_this_hour / row.drafts_per_hour, 1), 3), "g")


def _closest_first(row: ClientRow) -> tuple[float, str]:
    """The fullest first, the name breaking a tie."""
    return -row.used_this_hour / row.drafts_per_hour, row.display_name.casefold()  # limits are 1 to 200, never 0


def _by_name(row: ClientRow) -> str:
    return row.display_name.casefold()


SORTS = {"closest": _closest_first, "name": _by_name}  # the first is the default
VIEWS = ("cards", "list")  # the first is the default


def read_clients(db: AdminDb) -> list[ClientRow]:
    """Every client row, as the database holds them. The one read behind the Clients page and the panels over it."""
    try:
        return db.clients()
    except psycopg.Error as exc:  # the same plain page as sign-in's; the error text can name the login
        raise Refused(f"clients unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None


def read_channel_owners(db: AdminDb) -> dict[str, str]:
    """Every channel a client has had, to the client's key (migration 024), for the lists of free channels."""
    try:
        return db.channel_owners()
    except psycopg.Error as exc:  # the same plain page as read_clients'
        raise Refused(f"channels unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None


def clients_context(rows: list[ClientRow], q: str = "", sort: str = "closest", view: str = "cards", added: str = "",
                    removed: str = "") -> dict:
    """Everything clients.html needs apart from `staff`, which each route adds. GET /clients and the two panel routes
    (a client, Add client) use it, so the list behind a panel is the page's own."""
    sort = sort if sort in SORTS else "closest"  # a hand-typed address gets the default, not an error page
    view = view if view in VIEWS else "cards"
    wanted = q.strip().casefold()
    kept = [row for row in rows if row.status != "removed"]
    shown = [row for row in kept if wanted in row.display_name.casefold()]
    shown.sort(key=SORTS[sort])
    # After Add client or Remove: the saved name of the client the address names, never words from the address itself.
    new = next((row for row in kept if row.client_key == added), None)
    gone = next((row for row in rows if row.client_key == removed and row.status == "removed"), None)
    toast = (f"Added {new.display_name}. It drafts from its next message." if new else
             f"Removed {gone.display_name}. Remove the support bot from its channel in Slack." if gone else None)
    # Needs you: clients at their limit (furthest past it first), then paused ones. Search never narrows it.
    needs = sorted((row for row in kept if _state(row) == "at_limit"), key=_closest_first)
    needs += sorted((row for row in kept if _state(row) == "paused"), key=_by_name)
    # The two numbers: this hour's drafts across active clients against their limits added up, and how many paused.
    active = [row for row in kept if row.status == "active"]
    used, limits = sum(row.used_this_hour for row in active), sum(row.drafts_per_hour for row in active)
    level = format(round(min(used / limits, 1), 3), "g") if limits else "0"
    totals = {"used": used, "limits": limits, "level": level, "paused": len(kept) - len(active)}
    return {"current": "clients", "clients": [(row, _state(row), _level(row)) for row in shown],
            "states": STATES, "reasons": PAUSE_REASONS, "q": q, "sort": sort, "view": view,
            "any_clients": bool(kept), "toast": toast, "needs": [(row, _state(row)) for row in needs],
            "totals": totals}


CHART_DOWN = "The chart can’t be shown right now."


def _now() -> datetime:
    """The moment a page is made at. A test sets it, so the chart's hours don't depend on when the test runs."""
    return datetime.now(timezone.utc)


def _latest_change(db: AdminDb, zone: ZoneInfo, now: datetime) -> dict | None:
    """The newest change of any kind, as the one line under the main panel says it; None when there is none or the
    database can't say."""
    # Here and not at the top: client_sheet and history import this module, and these two come from them.
    from support_agent.admin.client_sheet import _when
    from support_agent.admin.history import _said

    try:
        found = db.changes(None, None, None, now, 0, 1)
    except psycopg.Error as exc:  # its text can name the login and address, so only its type goes in the log
        logger.warning("the latest change is unavailable: %s", type(exc).__name__)
        return None
    if not found:
        return None
    stamp, when = _when(found[0], zone)
    return {"who": found[0].staff_name, "said": _said(found[0]), "stamp": stamp, "when": when}


def overview(db: AdminDb, rows: list[ClientRow], zone: ZoneInfo) -> dict:
    """What the Clients page shows beyond the client list: today's chart and the latest change. Each reads the database
    on its own and, failing, leaves only itself out: the page still works. Every page that has the list behind it (the
    list itself and the two panels over it) takes these."""
    now = _now()
    try:
        chart = build_chart(db.drafts_today(today_starts(now, zone)), [row for row in rows if row.status != "removed"],
                            now, zone)
    except psycopg.Error as exc:
        logger.warning("today's chart is unavailable: %s", type(exc).__name__)
        chart = Chart(lines=[], hours=[], axis=[], legend=False, words=CHART_DOWN)
    return {"chart": chart, "latest": _latest_change(db, zone, now)}


@router.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request, q: str = "", sort: str = "closest", view: str = "cards", added: str = "",
                 removed: str = "", staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
                 zone: ZoneInfo = Depends(get_zone)):
    rows = read_clients(db)
    if not made_by_this_app(request):  # the message an address asks for, only for this app's own requests
        added = removed = ""
    page = clients_context(rows, q, sort, view, added, removed) | overview(db, rows, zone)
    return templates.TemplateResponse(request, "clients.html", {"staff": staff} | page)


async def _limit_field(request: Request) -> str:
    """The form's one field, read with the standard library: FastAPI's Form() would need python-multipart, which is
    only installed as a dependency of mcp, so the app doesn't rely on it."""
    fields = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    return fields.get("limit", [""])[0]


LIMIT_ERROR = "The limit must be a whole number from 1 to 200."  # the same range as set_draft_limit() and Slack
GONE_ERROR = "This client was removed. Reload the page to see the current list."
# A failure can come after the database committed, so the message doesn't promise nothing changed.
DOWN_ERROR = "Couldn't reach the database, so the limit may not have changed. Reload the page to check."


def valid_limit(typed: str) -> int | None:
    typed = typed.strip()
    # Plain digits only: int() would also take "+35", "1_0" and other scripts' digits.
    if not re.fullmatch(r"[0-9]{1,3}", typed) or not 1 <= int(typed) <= 200:
        return None
    return int(typed)


@router.post("/clients/{client_key}/limit")
def change_limit(client_key: str, limit: str = Depends(_limit_field), staff: Staff = Depends(current_staff),
                 db: AdminDb = Depends(get_admin_db)):
    new_limit = valid_limit(limit)
    if new_limit is None:
        return JSONResponse({"error": LIMIT_ERROR}, status_code=422)
    try:
        return asdict(db.set_draft_limit(staff.id, client_key, new_limit))
    except ClientGone:
        return JSONResponse({"error": GONE_ERROR}, status_code=404)
    except psycopg.Error as exc:  # its text can name the login and address, so only its type goes in the log
        logger.warning("limit change for %s failed: %s", client_key, type(exc).__name__)
        return JSONResponse({"error": DOWN_ERROR}, status_code=503)
