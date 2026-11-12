"""The History screen: every change made in the admin app or with the Slack limit button, newest first,
100 to a page. Mounted with SIGNED_IN."""

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from support_agent.admin.auth import NO_DATABASE, Refused, current_staff
from support_agent.admin.client_sheet import _when
from support_agent.admin.clients import PAUSE_REASONS
from support_agent.admin.db import AdminDb, ChangeRow, Staff, get_admin_db
from support_agent.admin.settings import get_zone
from support_agent.admin.templating import templates

router = APIRouter()

PAGE_SIZE = 100

# What each change did, after the person's name; {} is the client's or staff member's name.
SAID = {
    ("client", "added"): "added {}",
    ("client", "resumed"): "resumed {}",
    ("client", "removed"): "removed {}",
    ("client", "key_replaced"): "replaced {}'s HighLevel token",
    ("staff", "added"): "added {} to the staff list",
    ("staff", "edited"): "edited {}'s details",
    ("staff", "deactivated"): "deactivated {}",
    ("staff", "reactivated"): "reactivated {}",
}


def _said(change: ChangeRow) -> str:
    if change.action == "paused":
        reason = PAUSE_REASONS.get(change.reason, change.reason)
        return f"paused {change.subject}: {reason}" + (f", “{change.note}”" if change.note else "")
    if change.action == "limit_changed":
        return f"changed {change.subject}'s limit from {change.detail} drafts an hour"  # detail is "20 to 30"
    if change.action == "channel_changed":
        return f"moved {change.subject} to another Slack channel: {change.detail}"  # detail is "#old to #new"
    return SAID.get((change.kind, change.action), f"{change.action} {{}}").format(change.subject)


KINDS = {"client": "Clients", "limit": "Limits", "staff": "Staff"}


def _page_number(typed: str) -> int:
    """1 for anything that isn't a page number (plain digits, up to six: int() would take "+2" and "2_0")."""
    return int(typed) if re.fullmatch(r"[0-9]{1,6}", typed) and int(typed) >= 1 else 1


def _now() -> datetime:
    """The moment a page is made at. A test sets it, so the day groups don't depend on when the test runs."""
    return datetime.now(timezone.utc)


def _as_of(typed: str) -> datetime:
    """The moment the first page was opened, from an older page's address; now for the first page or anything that
    isn't such a moment (a time without a time zone included)."""
    try:
        moment = datetime.fromisoformat(typed)
    except ValueError:
        return _now()
    return moment if moment.tzinfo else _now()


def _heading(day: date, today: date) -> str:
    """Today, Yesterday, or the date (with its year when it isn't this year's)."""
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return f"{day:%b} {day.day}" + (f", {day.year}" if day.year != today.year else "")


def _clocks_change(day: date, zone: ZoneInfo) -> bool:
    """Whether the zone's clocks change during this day: its times then need their short zone name (EDT, EST) to be
    told apart, since one wall-clock hour happens twice."""
    start, end = (datetime(d.year, d.month, d.day, tzinfo=zone) for d in (day, day + timedelta(days=1)))
    return start.utcoffset() != end.utcoffset()


def _by_day(changes: list[ChangeRow], zone: ZoneInfo, now: datetime) -> list[tuple[str, list[tuple]]]:
    """Newest first, as they come: (heading, [(change, words, machine time, time of day), ...]) for each day in the
    zone. The days are the zone's, not UTC's, so a change just after midnight there is under the new day. A day that
    runs over two pages has its heading on both, each over its own changes."""
    today = now.astimezone(zone).date()
    days: list[tuple[date, list[tuple]]] = []
    for change in changes:
        local = change.changed_at.astimezone(zone)
        if not days or days[-1][0] != local.date():
            days.append((local.date(), []))
        clock = f"{local:%H:%M} {local.tzname()}" if _clocks_change(local.date(), zone) else f"{local:%H:%M}"
        days[-1][1].append((change, _said(change), _when(change, zone)[0], clock))
    return [(_heading(day, today), items) for day, items in days]


def _link(picked: tuple[str | None, str | None, str | None], number: int, as_of: datetime) -> str:
    """This page's address with another page number: the same filters, no empty ones. Pages after the first stay as
    of the moment the first was opened, so a change made meanwhile doesn't shift them; page 1 is always now."""
    query = {name: value for name, value in zip(("kind", "client", "person"), picked) if value}
    if number > 1:
        query |= {"page": number, "asof": as_of.isoformat()}
    return "/history" + (f"?{urlencode(query)}" if query else "")


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, kind: str = "", client: str = "", person: str = "", page: str = "1",
                 asof: str = "", staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db),
                 zone: ZoneInfo = Depends(get_zone)):
    number = _page_number(page)
    now = _now()
    as_of = _as_of(asof) if number > 1 else now
    try:
        clients = sorted(db.clients(), key=lambda row: row.display_name.casefold())  # removed ones too: history stays
        # The staff (deactivated too) and anyone else History names, such as a Slack ID that changed a limit.
        people = sorted({member.name for member in db.staff_list()} | set(db.history_people()), key=str.casefold)
        # A filter the pickers don't offer (a hand-typed address) is no filter, not an error page.
        picked = (kind if kind in KINDS else None, client if any(row.client_key == client for row in clients) else None,
                  person if person in people else None)
        # One more than a page: is there an older page?
        changes = db.changes(*picked, as_of, (number - 1) * PAGE_SIZE, PAGE_SIZE + 1)
    except psycopg.Error as exc:  # its text can name the login, so only its type goes in the log
        raise Refused(f"history unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None
    shown = {"staff": staff, "current": "history", "kinds": KINDS, "clients": clients, "people": people,
             "kind": picked[0], "client": picked[1], "person": picked[2],
             "days": _by_day(changes[:PAGE_SIZE], zone, now), "zone_name": zone.key.split("/")[-1].replace("_", " "),
             "newer": _link(picked, number - 1, as_of) if number > 1 else None,
             "older": _link(picked, number + 1, as_of) if len(changes) > PAGE_SIZE else None}
    return templates.TemplateResponse(request, "history.html", shown)
