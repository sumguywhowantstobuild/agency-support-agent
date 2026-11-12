"""Read-only access to one client's GHL sub-account, through GHL's MCP server v2.

Three locks: the client's token only has read scopes (GHL answers 401 to a write), GHL binds the token to its own
sub-account (another client's id gets 403 or is ignored), and this module only ever runs the read actions in
ALLOWED_ACTIONS. GHL generates its action catalog and can change it, so ActionCatalog checks once a day that every
allowed action is still described as a read.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager
from importlib.metadata import version
from typing import Any, Literal, Protocol

from anyio.from_thread import start_blocking_portal
from mcp import ClientSession, types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from support_agent.config import Settings
from support_agent.models import ToolResult

logger = logging.getLogger(__name__)

# The only GHL actions the agent may run, all reads (names from GHL's catalog, probed 2026-10-13).
ALLOWED_ACTIONS = frozenset({
    "search-contacts-advanced", "get-contact", "get-all-tasks", "search-conversation", "get-messages",
    "get-calendar-events", "get-pipelines", "search-opportunity", "get-location", "get-calendars", "get-slots",
    "get-workflow", "get-forms", "get-forms-submissions", "search-users", "get-location-tags",
})

# GHL's firewall (Cloudflare) refuses Python's default User-Agent, so we name the app.
USER_AGENT = f"bluejay-support-agent/{version('support-agent')}"
CALL_TIMEOUT = 20  # seconds per GHL call; a ticket has 75 in all
DAY_MS = 86_400_000
MAX_DAYS = 31  # GHL refuses a free-slots range longer than 31 days
META_KEYS = {"traceId", "total", "count", "meta"}  # bookkeeping fields, not findings

EMPTY_NOTE = ("GHL returned nothing. That doesn't prove it's missing: the filter may be off or the token may lack "
              "permission. Say what you couldn't confirm.")
UNREACHABLE_NOTE = "Couldn't reach GHL just now. Try again later, or ask a person to check in GHL."
CHANGED_NOTE = "GHL's action list changed; GHL checks are off until a person reviews it."


class Session(Protocol):
    def call_tool(self, name: str, arguments: dict) -> dict: ...


class ActionNotAllowed(PermissionError):
    """An action outside ALLOWED_ACTIONS was asked for. A bug, never a GHL hiccup: it stops the ticket."""


class CatalogChanged(RuntimeError):
    """GHL no longer describes an allowed action as a read; no GHL action runs until a person reviews the list."""


def ghl_headers(token: str, location_id: str) -> dict[str, str]:
    # locationId tells GHL which sub-account the token is for; without it every action fails (400/403/422).
    return {"Authorization": f"Bearer {token}", "locationId": location_id, "User-Agent": USER_AGENT}


class ActionCatalog:
    """Checks, at most once a day per process, that GHL still describes every allowed action as a read."""

    def __init__(self, max_age: float = 86_400, clock: Callable[[], float] = time.monotonic):
        self._max_age = max_age
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._changed: set[str] = set()

    def changed_actions(self, session: Session) -> set[str]:
        """The allowed actions GHL no longer describes as reads; an empty set means all is well."""
        with self._lock:
            if self._checked_at is not None and self._clock() - self._checked_at < self._max_age:
                return self._changed
            changed = set()
            for action in sorted(ALLOWED_ACTIONS):
                info = session.call_tool("describe_operation", {"operationId": action}).get("operation") or {}
                if (info.get("operationId") != action or info.get("kind") != "read"
                        or info.get("readOnlyHint") is not True):
                    changed.add(action)
            if changed:
                logger.error("GHL action list changed, GHL checks off: %s", sorted(changed))
            self._changed, self._checked_at = changed, self._clock()  # a failed check isn't cached: retried next call
            return changed


CATALOG = ActionCatalog()  # one per process, shared by every ticket


class GhlAccess:
    """One client's GHL sub-account, read-only. Every method returns a ToolResult and never raises on GHL trouble."""

    def __init__(self, location_id: str, token: str, session: Session, catalog: ActionCatalog | None = None):
        self.location_id = location_id  # set from the Slack channel by n8n, never by the model
        self._token = token  # kept only to scrub it from logged errors
        self.session = session
        self._catalog = catalog or CATALOG

    @classmethod
    def for_location(cls, location_id: str, token: str, settings: Settings,
                     connect: Callable[[str, str, str], Session] | None = None) -> "GhlAccess":
        # The token comes from the key store (ghl/keystore.py); an unknown or paused client never gets here.
        return cls(location_id, token, (connect or McpSession)(settings.ghl_mcp_url, token, location_id))

    def __enter__(self) -> "GhlAccess":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if close:
            close()

    # The checks the agent can ask for (agent/tools.py hands them to the model).

    def find_contact(self, query: str) -> ToolResult:
        return self._read("search-contacts-advanced",
                          {"body": {"locationId": self.location_id, "query": query, "pageLimit": 10}})

    def contact_details(self, contact_id: str) -> ToolResult:
        return self._read("get-contact", {"contactId": contact_id})

    def contact_tasks(self, contact_id: str) -> ToolResult:
        return self._read("get-all-tasks", {"contactId": contact_id})

    def recent_messages(self, contact_id: str) -> ToolResult:
        found = self._read("search-conversation", {"contactId": contact_id, "limit": 1})
        if found.status != "ok":
            return found
        conversation_id = (found.data.get("conversations") or [{}])[0].get("id")
        if not conversation_id:
            return ToolResult(status="empty", note=EMPTY_NOTE)
        return self._read("get-messages", {"conversationId": conversation_id, "limit": 20})

    def appointments(self, calendar_id: str, days: int = 7) -> ToolResult:
        """Appointments from `days` before now to `days` after (tickets ask about both)."""
        now, span = _now_ms(), _days(days) * DAY_MS
        return self._read("get-calendar-events",
                          {"calendarId": calendar_id, "startTime": str(now - span), "endTime": str(now + span)})

    def pipelines(self) -> ToolResult:
        return self._read("get-pipelines")

    def opportunities(self, contact_id: str) -> ToolResult:
        return self._read("search-opportunity", {"contactId": contact_id, "limit": 20})

    def account(self) -> ToolResult:
        return self._read("get-location")  # includes the sub-account's time zone

    def calendars(self) -> ToolResult:
        return self._read("get-calendars")

    def free_slots(self, calendar_id: str, days: int = 7) -> ToolResult:
        now = _now_ms()
        return self._read("get-slots",
                          {"calendarId": calendar_id, "startDate": now, "endDate": now + _days(days) * DAY_MS})

    def workflows(self) -> ToolResult:
        return self._read("get-workflow")  # name, status and dates only: GHL doesn't share triggers or actions

    def forms(self) -> ToolResult:
        return self._read("get-forms")

    def form_submissions(self, form_id: str, limit: int = 20) -> ToolResult:
        return self._read("get-forms-submissions", {"formId": form_id, "limit": max(1, min(limit, 100))})

    def users(self) -> ToolResult:
        account = self.account()
        if account.status != "ok":
            return account
        company_id = account.data.get("location", {}).get("companyId")
        if not company_id:
            return ToolResult(status="error", note="GHL didn't say which agency this sub-account belongs to.")
        result = self._read("search-users", {"companyId": company_id, "limit": "100"})
        if result.status != "ok":
            return result
        # search-users covers the whole agency; keep only people assigned to this client's sub-account.
        here = [u for u in result.data.get("users", [])
                if self.location_id in ((u.get("roles") or {}).get("locationIds") or [])]
        if not here:
            return ToolResult(status="empty", note=EMPTY_NOTE)
        return ToolResult(status="ok", data={"users": here})

    def tags(self) -> ToolResult:
        return self._read("get-location-tags")

    def connection_check(self) -> Literal["works", "wrong_account", "unreachable"]:
        """Does this key open this sub-account? For the admin app's Test connection: one read, and
        only the verdict comes back, never the account's data. "works" only when GHL answers for this very location;
        a refusal, or an answer for another location, is "wrong_account"; anything else (busy, down, timeout) is
        "unreachable"."""
        try:
            reply = self.execute("get-location", {})
        except Exception as exc:
            if _refused(exc):  # the key wasn't accepted at all: the handshake itself was refused
                return "wrong_account"
            # The kind of error only: its text could carry account data, or the key in another form.
            logger.warning("GHL connection check failed: %s", type(exc).__name__)
            return "unreachable"
        if not isinstance(reply, dict) or not reply.get("success"):
            status = reply.get("status") if isinstance(reply, dict) else None
            return "wrong_account" if status in (401, 403) else "unreachable"
        data = reply.get("data")
        location = data.get("location") if isinstance(data, dict) else None
        if not isinstance(location, dict):
            return "unreachable"  # an answer we don't understand proves nothing either way
        return "works" if location.get("id") == self.location_id else "wrong_account"

    # The locks.

    def execute(self, action: str, params: dict) -> dict:
        """The one door every GHL action goes through: the allow-list first (nothing leaves the agent), then the
        daily catalog check, then the call."""
        if action not in ALLOWED_ACTIONS:
            raise ActionNotAllowed(f"GHL action not allowed: {action}")
        if self._catalog.changed_actions(self.session):
            raise CatalogChanged(CHANGED_NOTE)
        return self.session.call_tool("execute_operation", {"operationId": action, "params": params})

    def _read(self, action: str, params: dict | None = None) -> ToolResult:
        try:
            reply = self.execute(action, params or {})
        except ActionNotAllowed:
            raise
        except CatalogChanged:
            return ToolResult(status="error", note=CHANGED_NOTE)
        except Exception as exc:  # network, timeout, protocol: the details go to the log, not to the model
            logger.warning("GHL %s failed: %s", action, str(exc).replace(self._token, "<token>"))
            return ToolResult(status="error", note=UNREACHABLE_NOTE)
        return _result(reply)


def _result(reply: Any) -> ToolResult:
    """Unwrap GHL's {"success", "status", "data", "nextStep"}. Only data is kept: nextStep is text written to steer
    an AI, and the model must never take instructions from tool results."""
    if not isinstance(reply, dict) or "success" not in reply:
        return ToolResult(status="error", note="GHL sent an answer we don't understand.")
    status = reply.get("status")
    if not reply["success"]:
        if status in (401, 403):
            note = f"GHL refused (status {status}): the token lacks permission for this check."
        elif status == 429:
            note = "GHL rate limit reached; try again in a minute."
        elif status == 404:
            note = "GHL found nothing with that id (status 404). Check the id."
        elif status in (400, 422):
            note = f"GHL rejected the request (status {status}); the details asked for may be wrong."
        else:
            note = f"GHL had a problem (status {status}); try again later."
        return ToolResult(status="error", note=note)
    data = reply.get("data")
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "traceId"}
    if _is_empty(data):
        return ToolResult(status="empty", note=EMPTY_NOTE)
    return ToolResult(status="ok", data=data)


def _refused(exc: BaseException) -> bool:
    """True when an HTTP 401 or 403 is anywhere in the error: its causes, or inside an exception group (the async MCP
    client wraps connection errors in one)."""
    seen, todo = set(), [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if getattr(getattr(e, "response", None), "status_code", None) in (401, 403):
            return True
        todo += [e.__cause__, e.__context__, *getattr(e, "exceptions", ())]
    return False


def _is_empty(data: Any) -> bool:
    if isinstance(data, dict):
        return all(_is_empty(v) for k, v in data.items() if k not in META_KEYS)
    return data is None or data == [] or data == ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _days(days: int) -> int:
    return max(1, min(days, MAX_DAYS))


class McpSession:
    """The real connection: GHL's MCP server through the official (async) MCP client, made callable from our
    synchronous agent. It connects on the first call (a ticket that never needs GHL skips the handshake); a
    background event loop then lives for that one ticket, and close() ends it."""

    def __init__(self, url: str, token: str, location_id: str):
        self._url, self._token, self._location_id = url, token, location_id
        self._stack: ExitStack | None = None

    def _connect(self) -> None:
        stack = ExitStack()
        try:
            self._portal = stack.enter_context(start_blocking_portal())
            self._session = stack.enter_context(
                self._portal.wrap_async_context_manager(_open(self._url, self._token, self._location_id)))
        except BaseException:
            stack.close()
            raise
        self._stack = stack

    def call_tool(self, name: str, arguments: dict) -> dict:
        if self._stack is None:
            self._connect()
        result = self._portal.call(self._session.call_tool, name, arguments)
        if not isinstance(result, types.CallToolResult):
            raise RuntimeError(f"unexpected MCP result: {type(result).__name__}")
        text = next((c.text for c in result.content if isinstance(c, types.TextContent)), "")
        try:
            return json.loads(text)
        except ValueError:
            raise RuntimeError(f"GHL tool {name} answered with non-JSON text") from None

    def close(self) -> None:
        if self._stack is not None:
            self._stack.close()
            self._stack = None


@asynccontextmanager
async def _open(url: str, token: str, location_id: str):
    async with create_mcp_http_client(headers=ghl_headers(token, location_id)) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write, read_timeout_seconds=CALL_TIMEOUT,
                                     client_info=types.Implementation(name="bluejay-support-agent",
                                                                      version=version("support-agent"))) as session:
                await session.initialize()
                yield session
