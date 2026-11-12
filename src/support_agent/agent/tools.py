"""The tools the model may call for one ticket: knowledge searches, and read-only checks in the client's GHL account."""

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from support_agent.knowledge.search import find_similar_tickets, search_ghl_help, search_guides
from support_agent.models import CheckRun, Passage, ToolResult

logger = logging.getLogger(__name__)

Search = Callable[[str], list[Passage]]

# name -> (real function, what the model is told it does)
SEARCHES: dict[str, tuple[Search, str]] = {
    "search_guides": (
        search_guides,
        "Search Bluejay's own support guides: symptoms, what to check, the fix and what to tell the client. "
        "Start here.",
    ),
    "search_ghl_help": (
        search_ghl_help,
        "Search our summaries of HighLevel's help articles. Each result links to the original article.",
    ),
    "find_similar_tickets": (
        find_similar_tickets,
        "Find solved past tickets like this one, with their cause and fix.",
    ),
}

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What to look for, in plain words. Include exact error codes and feature names.",
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}


# Checks whose answers hold customers' (or patients') details, and the fields that are personal. The guard flags a
# client reply that repeats one of them (Bluejay policy: no personal details in Slack).
CUSTOMER_CHECKS = {"find_contact", "contact_details", "contact_tasks", "recent_messages", "opportunities",
                   "appointments", "form_submissions"}
PERSONAL_FIELDS = {"email", "phone", "contactname", "contact_name", "fullname", "full_name", "name"}
NAME_PART_FIELDS = ("firstName", "lastName", "first_name", "last_name")
EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_IN_TEXT = re.compile(r"\+?\(?\d[\d\s().-]{8,}\d")  # 10+ characters of digits, spaces, brackets, dots, dashes

MAX_GHL_CHARS = 12_000  # per GHL answer; bigger ones are trimmed so one long history can't blow up a ticket's cost

# GHL checks: name -> (arguments, what the model is told). Each name is a read-only GhlAccess method. No check takes
# a location: the client's sub-account comes from the ticket (n8n picks it from the Slack channel).
GHL_ARGUMENTS = {
    "query": "A name, email or phone number to look for.",
    "contact_id": "A contact id from find_contact.",
    "calendar_id": "A calendar id from calendars.",
    "form_id": "A form id from forms.",
}
GHL_CHECKS: dict[str, tuple[tuple[str, ...], str]] = {
    "find_contact": (("query",), "Find contacts in the client's GHL account. Gives the ids the contact checks need."),
    "contact_details": (("contact_id",), "One contact's details and tags."),
    "contact_tasks": (("contact_id",), "One contact's tasks."),
    "recent_messages": (("contact_id",), "The contact's latest conversation: last 20 messages with delivery status."),
    "opportunities": (("contact_id",), "The contact's opportunities: pipeline, stage and status."),
    "appointments": (("calendar_id",), "Appointments on a calendar from 7 days ago to 7 days ahead."),
    "free_slots": (("calendar_id",), "Open booking slots on a calendar for the next 7 days."),
    "calendars": ((), "Every calendar's setup: staff, open hours, slot length and booking rules."),
    "workflows": ((), "Workflows: name, status (draft or published) and last update. Not triggers or actions."),
    "forms": ((), "The forms in the account, with their ids."),
    "form_submissions": (("form_id",), "A form's latest 20 submissions."),
    "pipelines": ((), "Pipelines and their stages."),
    "account": ((), "The sub-account's details, including its time zone."),
    "users": ((), "The people with access to this sub-account and their roles."),
    "tags": ((), "The tags set up in the account."),
}


def _ghl_schema(arguments: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": {a: {"type": "string", "description": GHL_ARGUMENTS[a]} for a in arguments},
        "required": list(arguments),
        "additionalProperties": False,
    }


class ToolBox:
    """One per ticket. Remembers every link its searches returned, so the draft can only cite those."""

    def __init__(self, location_id: str, search: dict[str, Search] | None = None, ghl: Any = None):
        self.location_id = location_id  # the client's GHL sub-account, set by n8n; the model never chooses it
        if ghl is not None and ghl.location_id != location_id:
            raise ValueError("GHL access is for another client's sub-account than this ticket's")
        self._search = search if search is not None else {name: fn for name, (fn, _) in SEARCHES.items()}
        self._ghl = ghl  # the client's GhlAccess (read-only); None means no GHL checks are offered
        self.calls = 0
        self.seen_urls: set[str] = set()
        # The two records below are replaced, never changed in place: at the time limit the hand-over draft reads them
        # while late work may still add to them, and a reader must never see one change mid-read.
        self.ghl_failed: dict[str, str] = {}  # GHL check -> why it failed, shown on the draft
        self.seen_personal: set[str] = set()  # customers' names, emails and phones GHL showed; the guard uses them
        self.seen_name_parts: set[str] = set()  # lone first and last names too: only the past-ticket check uses them
        self.ran: tuple[CheckRun, ...] = ()  # every lookup in order, by name and result only (shown on the card)

    def definitions(self) -> list[dict]:
        """The tool list in OpenAI's format."""
        searches = [
            {"type": "function", "name": name, "description": description, "parameters": QUERY_SCHEMA, "strict": True}
            for name, (_, description) in SEARCHES.items()
            if name in self._search
        ]
        if self._ghl is None:
            return searches
        return searches + [
            {"type": "function", "name": name, "description": description, "parameters": _ghl_schema(arguments),
             "strict": True}
            for name, (arguments, description) in GHL_CHECKS.items()
        ]

    def call(self, name: str, args: dict) -> ToolResult:
        self.calls += 1
        result = self._run(name, args)
        # Only names from the tool list are recorded: a name the model made up could carry anything.
        known = name in self._search or (name in GHL_CHECKS and self._ghl is not None)
        self.ran = (*self.ran, CheckRun(name=name if known else "unknown_tool", result=result.status))
        return result

    def _run(self, name: str, args: dict) -> ToolResult:
        if name in GHL_CHECKS and self._ghl is not None:
            return self._check_ghl(name, args)
        search = self._search.get(name)
        if search is None:
            return ToolResult(status="error", note=f"unknown tool {name!r}; use one from the tool list")
        query = args.get("query") if isinstance(args, dict) else None
        if not isinstance(query, str) or not query.strip():
            return ToolResult(status="error", note="query must be some words to search for")
        try:
            passages = search(query)
        except Exception:
            logger.exception("search %s failed", name)  # the details go to the log, not to the model
            return ToolResult(status="error", note="the search failed; try once more, or flag a person")
        if not passages:
            return ToolResult(status="empty", note="nothing matched; try other words or another search")
        self.seen_urls.update(p.url for p in passages)
        return ToolResult(
            status="ok",
            data=[{"title": p.title, "heading": p.heading, "url": p.url, "text": p.content} for p in passages],
        )

    def _check_ghl(self, name: str, args: dict) -> ToolResult:
        """Run one read-only GHL check. Only the declared arguments get through: anything else the model sends,
        such as a location, is dropped, so the check always runs on this ticket's client."""
        arguments, _ = GHL_CHECKS[name]
        given = args if isinstance(args, dict) else {}
        kwargs = {a: given.get(a) for a in arguments}
        missing = [a for a, v in kwargs.items() if not isinstance(v, str) or not v.strip()]
        if missing:
            return ToolResult(status="error", note=f"{', '.join(missing)} is needed: {GHL_ARGUMENTS[missing[0]]}")
        try:
            result = getattr(self._ghl, name)(**{a: v.strip() for a, v in kwargs.items()})
        except Exception:
            logger.exception("GHL check %s failed", name)  # the details go to the log, not to the model
            note = "the GHL check failed; say it couldn't be checked, or flag a person"
            result = ToolResult(status="error", note=note)
        if result.status == "error":
            self.ghl_failed = {**self.ghl_failed, name: result.note}
        elif name in CUSTOMER_CHECKS:
            self.seen_personal |= _personal_details(result.data)
            self.seen_name_parts |= _name_parts(result.data)
        return _trimmed(result)


def _trimmed(result: ToolResult) -> ToolResult:
    """Cap an answer at MAX_GHL_CHARS as the model receives it (the trimmed text is quoted again when sent), keeping
    the original note: an "empty is not proof" warning must survive trimming."""
    text = json.dumps(result.data, ensure_ascii=False)
    if len(text) <= MAX_GHL_CHARS:
        return result
    cut = text[:MAX_GHL_CHARS]
    while len(json.dumps(cut, ensure_ascii=False)) > MAX_GHL_CHARS:
        cut = cut[: len(cut) * 9 // 10]
    trim_note = f"Answer trimmed to fit {MAX_GHL_CHARS:,} characters; ask a narrower check if you need more."
    note = f"{result.note} {trim_note}" if result.note else trim_note
    return ToolResult(status=result.status, data=cut, note=note)


def _personal_details(data: Any) -> set[str]:
    """Full names, emails and phones anywhere in a GHL answer: in personal fields, and emails and phones inside free
    text (message bodies, form answers). Lone first or last names are left out: as words on their own ("Will",
    "May", "Young") they would raise false alarms, and names inside free text can't be picked out reliably."""
    found: set[str] = set()
    if isinstance(data, dict):
        for first_key, last_key in (("firstName", "lastName"), ("first_name", "last_name")):
            first, last = data.get(first_key), data.get(last_key)
            if isinstance(first, str) and isinstance(last, str) and first.strip() and last.strip():
                found.add(f"{first.strip()} {last.strip()}")
        for key, value in data.items():
            if key.lower() in PERSONAL_FIELDS and isinstance(value, str) and value.strip():
                found.add(value.strip())
            else:
                found |= _personal_details(value)
    elif isinstance(data, list):
        for item in data:
            found |= _personal_details(item)
    elif isinstance(data, str):
        found |= set(EMAIL_IN_TEXT.findall(data))
        found |= {m.strip() for m in PHONE_IN_TEXT.findall(data) if len(re.sub(r"\D", "", m)) >= 10}
    return found


def _name_parts(data: Any) -> set[str]:
    """Every first and last name in a GHL answer, even without the other half (a contact saved as just "Jane"). Only
    the past-ticket check uses them: there a false alarm costs one save, in the reply check it would cost a person."""
    found: set[str] = set()
    if isinstance(data, dict):
        for key in NAME_PART_FIELDS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                found.add(value.strip())
        for value in data.values():
            found |= _name_parts(value)
    elif isinstance(data, list):
        for item in data:
            found |= _name_parts(item)
    return found
