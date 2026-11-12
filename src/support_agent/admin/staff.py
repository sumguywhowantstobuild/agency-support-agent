"""The Staff screen: the one staff list that decides who may sign in here and approve drafts in Slack.
Add, edit, deactivate and reactivate through the admin_*_staff functions (migration 017), which log every change.
Mounted with SIGNED_IN.

A member and Add staff are panels over the list, on the same script as the client panel. Every change
is a plain form that answers with a redirect, so a reload never repeats it; a form refused with its panel answers with the
panel alone, so what was typed never waits on another database read."""

import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from typing import TypeVar

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from support_agent.admin.add_client import read_form
from support_agent.admin.auth import NO_DATABASE, Page, Refused, current_staff, made_by_this_app
from support_agent.admin.db import AdminDb, LastMember, Staff, StaffGone, StaffMember, StaffTaken, get_admin_db
from support_agent.admin.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()
T = TypeVar("T")

# The same rules as the staff table (migration 017), so a refusal comes here in words, not from the database.
FIELD_RULES = {
    "name": (lambda typed: 1 <= len(typed) <= 80, "Type their name, up to 80 characters."),
    "email": (lambda typed: len(typed) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+", typed),
              "Type their work email, like ana@bluejay.example."),
    "slack_user_id": (lambda typed: re.fullmatch(r"[UW][A-Z0-9]{2,}", typed),
                      "A Slack member ID starts with U or W, then capital letters and digits, like U04ABC123."),
}
TAKEN_WORDS = {"email": "that email", "slack_user_id": "that Slack member ID"}
SELF_ERROR = "You can't deactivate yourself. Ask another member to do it."  # no locking yourself out
# The sign-in pass carries the email, so a new one would refuse the person who saved it at their next click.
OWN_EMAIL_ERROR = "You can't change your own email: you'd be signed out. Ask another member to do it."
UNCLEAR_ERROR = "Nothing changed. Use the button below to deactivate or reactivate."
LAST_ERROR = "{} is the last active member, so nobody could sign in. Add someone else first."
GONE: Page = ("Staff member not found", "The address is wrong. Open Staff to see the list.")
# A failure can come after the database committed, so the page doesn't promise nothing changed.
CHANGE_DOWN: Page = ("Can't change the staff list",
                     "Couldn't reach the database, so the change may not have been made. Open Staff to check.")
# After a change: what the toast says, from the saved name (never words from the address).
TOASTS = {"saved": "Saved {}.",
          "deactivated": "Deactivated {}. They can't sign in or approve drafts from their next click.",
          "reactivated": "Reactivated {}."}


def _tidied(form: dict[str, str]) -> dict[str, str]:
    """As the database keeps them: trimmed, the email lower-case, the Slack ID upper-case (Slack shows it so)."""
    return {"name": form.get("name", "").strip(), "email": form.get("email", "").strip().lower(),
            "slack_user_id": form.get("slack_user_id", "").strip().upper()}


def _field_errors(typed: dict[str, str]) -> dict[str, str]:
    return {field: message for field, (ok, message) in FIELD_RULES.items() if not ok(typed[field])}


def _members(db: AdminDb) -> list[StaffMember]:
    try:
        return db.staff_list()
    except psycopg.Error as exc:  # its text can name the login, so only its type goes in the log
        raise Refused(f"staff list unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None


def _found(members: list[StaffMember], staff_id: int) -> StaffMember:
    member = next((member for member in members if member.id == staff_id), None)
    if member is None:
        raise Refused(f"no staff member {staff_id}", GONE, 404)
    return member


def _member(db: AdminDb, staff_id: int) -> StaffMember:
    return _found(_members(db), staff_id)


def _taken_by(db: AdminDb, field: str, value: str) -> str:
    """Who already has it, so staff can open that member instead (deactivated members keep theirs, too)."""
    holder = next((member for member in _members(db) if getattr(member, field) == value), None)
    return f"{holder.name if holder else 'Someone on the list'} already has {TAKEN_WORDS[field]}. Nothing was saved."


def _make(change: Callable[[], T], what: str) -> T:
    """Runs one admin_*_staff function; the refusals a form answers in words (taken, last member) pass through."""
    try:
        return change()
    except StaffGone as gone:
        raise Refused(f"no staff member {gone.args[0]}", GONE, 404) from None
    except psycopg.Error as exc:  # its text can name the login and address, so only its type goes in the log
        raise Refused(f"{what} failed: {type(exc).__name__}", CHANGE_DOWN, 503) from None


def _answer(request: Request, staff: Staff, *, members: list[StaffMember] | None, status_code: int = 200, **page):
    """The Staff page with a panel open over it (`members`, the list read for it), or, for a form refused with its
    panel, the panel alone: the list needs the database, and a database failure must not lose what was typed."""
    page = {"staff": staff, "current": "staff", "toast": None, "undo": None} | page
    if members is None:
        return templates.TemplateResponse(request, "panel_alone.html", page, status_code=status_code)
    listed = sorted(members, key=lambda member: (not member.active, member.name.casefold()))  # active first, by name
    return templates.TemplateResponse(request, "staff.html", page | {"members": listed}, status_code=status_code)


@router.get("/staff", response_class=HTMLResponse)
def staff_page(request: Request, added: str = "", staff: Staff = Depends(current_staff),
               db: AdminDb = Depends(get_admin_db)):
    members = _members(db)
    # After Add staff: the saved name of the member the address names, never words from the address itself, and only
    # for a request this app's own pages made (auth.made_by_this_app).
    new = next((member for member in members if str(member.id) == added and made_by_this_app(request)), None)
    toast = f"Added {new.name}. They can sign in and approve drafts now." if new else None
    return _answer(request, staff, members=members, toast=toast)


def _add_panel(request: Request, staff: Staff, *, members: list[StaffMember] | None = None, form: dict | None = None,
               errors: dict | None = None, status_code: int = 200):
    return _answer(request, staff, members=members, status_code=status_code, form=form or {}, errors=errors or {},
                   panel="staff_add_panel.html", page_title="Add staff")


@router.get("/staff/new", response_class=HTMLResponse)
def new_staff_form(request: Request, staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db)):
    return _add_panel(request, staff, members=_members(db))


@router.post("/staff/new")
def add_staff(request: Request, form: dict[str, str] = Depends(read_form), staff: Staff = Depends(current_staff),
              db: AdminDb = Depends(get_admin_db)):
    typed = _tidied(form)
    errors = _field_errors(typed)
    if errors:
        return _add_panel(request, staff, form=typed, errors=errors, status_code=422)
    try:
        new_id = _make(lambda: db.add_staff(staff.id, typed["name"], typed["email"], typed["slack_user_id"]),
                       "add staff")
    except StaffTaken as taken:
        field = taken.args[0]
        return _add_panel(request, staff, form=typed, errors={field: _taken_by(db, field, typed[field])},
                          status_code=409)
    return RedirectResponse(f"/staff?added={new_id}", status_code=303)


def _undo(staff: Staff, member: StaffMember, done: str) -> dict | None:
    """What the toast's Undo posts: the opposite of the change, so a deactivation is reactivated and the other way round.
    Offered only while the member is as the change left it, and never for the person signed in (the server refuses
    deactivating yourself even from a forged form)."""
    if member.id == staff.id or (done, member.active) not in (("deactivated", False), ("reactivated", True)):
        return None
    return {"address": f"/staff/{member.id}/active", "fields": {"active": "true" if done == "deactivated" else "false"}}


def _sheet(request: Request, staff: Staff, member: StaffMember, *, members: list[StaffMember] | None = None,
           form: dict | None = None, errors: dict | None = None, done: str = "", status_code: int = 200):
    """The member's panel; with `members` (the list read for it) it comes over the Staff page, without them it stands alone."""
    toast = TOASTS[done].format(member.name) if done in TOASTS else None
    return _answer(request, staff, members=members, status_code=status_code, member=member,
                   form=form or asdict(member), errors=errors or {}, toast=toast,
                   undo=_undo(staff, member, done) if toast else None, panel="staff_panel.html",
                   page_title=member.name)


@router.get("/staff/{staff_id}", response_class=HTMLResponse)
def staff_sheet(request: Request, staff_id: int, done: str = "", staff: Staff = Depends(current_staff),
                db: AdminDb = Depends(get_admin_db)):
    members = _members(db)  # the same read gives the member and the list behind the panel
    # The message for `done`, and its Undo, only for a request this app's own pages made: a crafted link would otherwise
    # offer an Undo that reactivates someone who was deactivated (auth.made_by_this_app).
    return _sheet(request, staff, _found(members, staff_id), members=members,
                  done=done if made_by_this_app(request) else "")


@router.post("/staff/{staff_id}/edit")
def edit_staff(request: Request, staff_id: int, form: dict[str, str] = Depends(read_form),
               staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db)):
    typed = _tidied(form)
    errors = _field_errors(typed)
    if errors:
        return _sheet(request, staff, _member(db, staff_id), form=typed, errors=errors, status_code=422)
    if staff_id == staff.id and typed["email"] != (member := _member(db, staff_id)).email:
        return _sheet(request, staff, member, form=typed, errors={"email": OWN_EMAIL_ERROR}, status_code=409)
    try:
        _make(lambda: db.edit_staff(staff.id, staff_id, typed["name"], typed["email"], typed["slack_user_id"]),
              f"edit staff {staff_id}")
    except StaffTaken as taken:
        field = taken.args[0]
        return _sheet(request, staff, _member(db, staff_id), form=typed,
                      errors={field: _taken_by(db, field, typed[field])}, status_code=409)
    return RedirectResponse(f"/staff/{staff_id}?done=saved", status_code=303)


@router.post("/staff/{staff_id}/active")
def set_active(request: Request, staff_id: int, form: dict[str, str] = Depends(read_form),
               staff: Staff = Depends(current_staff), db: AdminDb = Depends(get_admin_db)):
    if form.get("active") not in ("true", "false"):  # only what the sheet's form sends: nothing else deactivates
        return _sheet(request, staff, _member(db, staff_id), errors={"active": UNCLEAR_ERROR}, status_code=422)
    active = form["active"] == "true"
    if not active and staff_id == staff.id:
        return _sheet(request, staff, _member(db, staff_id), errors={"active": SELF_ERROR}, status_code=409)
    try:
        _make(lambda: db.set_staff_active(staff.id, staff_id, active), f"set staff {staff_id} active")
    except LastMember:
        member = _member(db, staff_id)
        return _sheet(request, staff, member, errors={"active": LAST_ERROR.format(member.name)}, status_code=409)
    return RedirectResponse(f"/staff/{staff_id}?done={'reactivated' if active else 'deactivated'}", status_code=303)
