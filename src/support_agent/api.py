"""The agent's web API. n8n sends each ticket to /draft and checks /health; nothing outside the server can reach it."""

import base64
import logging
import secrets
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from importlib.metadata import version
from typing import Annotated

import openai
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from support_agent.agent.loop import OpenAiLlm, draft_within, run_agent
from support_agent.agent.tools import ToolBox
from support_agent.config import get_settings
from support_agent.ghl.access import GhlAccess
from support_agent.ghl.keystore import KeyStore, get_keystore
from support_agent.keys import KeyOpenError, open_sealed
from support_agent.knowledge.search import get_db
from support_agent.models import Draft, Ticket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 30  # about one draft's time
# OpenAI's codes for "no more money": the hard spend limit (project or organization) or the prepaid credit ran out.
# Every draft fails until someone raises it, so it is reported as such, not as a plain error.
SPENDING_REFUSALS = {"project_spend_limit_exceeded", "organization_spend_limit_exceeded", "credit_balance_exhausted",
                     "insufficient_quota"}

Drafter = Callable[[Ticket, str], Draft]  # the ticket and its client's GHL key

# The API description pages are off: they would describe every endpoint to anyone who reached the agent.
app = FastAPI(title="Bluejay support agent", docs_url=None, redoc_url=None, openapi_url=None)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.exception_handler(RequestValidationError)
async def _refused_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's own answer repeats what was sent ("input"): a ticket's text or a locked key must not travel back.
    errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


@lru_cache
def _llm() -> OpenAiLlm:
    return OpenAiLlm()  # one OpenAI client for all tickets: safe across threads, and it reuses connections


def _time_limit() -> float:
    return get_settings().agent_time_budget  # n8n stops waiting at 90 s


def _draft(ticket: Ticket, token: str) -> Draft:
    # A fresh toolbox and GHL access per ticket, so one ticket's links, call count or client never carry into
    # another's draft. The GHL connection opens on the first check and is always closed when the ticket's work ends,
    # even work that ran past the time limit and was handed to a person.
    ghl = GhlAccess.for_location(ticket.location_id, token, get_settings())
    toolbox = ToolBox(ticket.location_id, ghl=ghl)

    def work() -> Draft:
        try:
            return run_agent(ticket, toolbox, _llm())
        finally:
            ghl.close()

    return draft_within(ticket, toolbox, work, seconds=_time_limit())


def get_drafter() -> Drafter:
    return _draft


@lru_cache
def get_draft_slots() -> threading.BoundedSemaphore:
    return threading.BoundedSemaphore(get_settings().max_concurrent_drafts)


def get_db_check() -> Callable[[], None]:
    return get_db().ping


def require_key(key: Annotated[str | None, Depends(api_key_header)]) -> None:
    expected = get_settings().agent_api_key.get_secret_value()
    # compare_digest takes the same time however much of the key is right, so timing can't reveal it.
    if not key or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "wrong or missing API key")


@app.post("/draft", dependencies=[Depends(require_key)])
def draft(
    ticket: Ticket,
    drafter: Annotated[Drafter, Depends(get_drafter)],
    slots: Annotated[threading.BoundedSemaphore, Depends(get_draft_slots)],
    keys: Annotated[KeyStore, Depends(get_keystore)],
) -> Draft:
    # Logs carry the ticket id and timings only, never the ticket text: tickets hold client details.
    settings = get_settings()
    # Fetched fresh per ticket: a client added or paused in the admin app counts from its next message.
    try:
        token = keys.token_for(ticket.location_id)
    except KeyOpenError:
        logger.error("ticket %s: the client's locked GHL key would not open (key pair changed?)", ticket.ticket_id)
        raise HTTPException(500, "agent error") from None
    if token is None:
        logger.warning("ticket %s refused: no active GHL key for this location (unknown, paused or removed)",
                       ticket.ticket_id)
        raise HTTPException(422, "unknown client: no active GHL key for this location_id")
    # Checked before any AI call: Slack allows 40,000 characters per message, and every one of them is paid for.
    if len(ticket.text) > settings.max_ticket_chars:
        size, limit = len(ticket.text), settings.max_ticket_chars
        logger.warning("ticket %s refused: %d characters, limit %d", ticket.ticket_id, size, limit)
        raise HTTPException(422, f"ticket too long: {size} characters, limit {limit}")
    if not slots.acquire(blocking=False):
        logger.warning("ticket %s refused: all draft slots busy", ticket.ticket_id)  # count these to know when to queue
        raise HTTPException(503, "busy: all draft slots in use", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    started = time.monotonic()
    try:
        result = drafter(ticket, token)
    except openai.APIStatusError as exc:
        if exc.code not in SPENDING_REFUSALS:
            logger.exception("ticket %s: agent error after %.1f s", ticket.ticket_id, time.monotonic() - started)
            raise HTTPException(500, "agent error") from None
        logger.error("ticket %s: OpenAI refused, spending limit reached (%s)", ticket.ticket_id, exc.code)
        raise HTTPException(429, f"ai spending limit reached ({exc.code})") from None
    except Exception:
        logger.exception("ticket %s: agent error after %.1f s", ticket.ticket_id, time.monotonic() - started)
        raise HTTPException(500, "agent error") from None  # the cause stays in the log
    finally:
        slots.release()
    logger.info(
        "ticket %s drafted in %.1f s, needs person: %s, lookups: %s", ticket.ticket_id, time.monotonic() - started,
        result.needs_person, ", ".join(f"{r.name} {r.result}" for r in result.checks_ran) or "none",
    )
    return result


ConnectionChecker = Callable[[str, str], str]  # (location id, token) -> "works" | "wrong_account" | "unreachable"
CONNECTION_TEST_SECONDS = 20  # the admin app waits 25


class ConnectionTest(BaseModel):
    location_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")  # same rule as the database (migration 018)
    sealed_key: str  # base64 of the key the admin app locked with the public half


def _check_connection(location_id: str, token: str) -> str:
    ghl = GhlAccess.for_location(location_id, token, get_settings())
    try:
        return ghl.connection_check()
    finally:
        ghl.close()


def get_connection_checker() -> ConnectionChecker:
    return _check_connection


@app.post("/clients/test-connection", dependencies=[Depends(require_key)])
def connection_test(body: ConnectionTest,
                    checker: Annotated[ConnectionChecker, Depends(get_connection_checker)]) -> dict[str, str]:
    """The admin app's Test connection: does this new key open this client's sub-account? The admin
    app can lock a key but never open one, so the agent opens it here, makes one read and answers the verdict only:
    never the key, never the account's data."""
    try:
        token = open_sealed(get_settings().ghl_key_private.get_secret_value(),
                            base64.b64decode(body.sealed_key, validate=True))
    except (KeyOpenError, ValueError):  # ValueError: not base64, or not ASCII at all
        result = "wrong_account"  # locked for another key pair, or not a locked key at all
    else:
        result = _within_deadline(checker, body.location_id, token)
    logger.info("connection test for %s: %s", body.location_id, result)
    return {"result": result}


def _within_deadline(checker: ConnectionChecker, location_id: str, token: str) -> str:
    """The verdict, or "unreachable" once CONNECTION_TEST_SECONDS pass: a cold catalog check plus a slow GHL could
    otherwise run far past the admin app's wait. A check still running finishes in the background and closes its
    connection."""
    done: dict[str, str] = {}
    worker = threading.Thread(target=lambda: done.setdefault("result", checker(location_id, token)),
                              name=f"connection-test-{location_id}", daemon=True)
    worker.start()
    worker.join(CONNECTION_TEST_SECONDS)
    return done.get("result", "unreachable")


VERSION = version("support-agent")  # from pyproject.toml: an alert can say which release was running


@app.get("/health")
def health(check: Annotated[Callable[[], None], Depends(get_db_check)]) -> JSONResponse:
    try:
        check()
    except Exception:
        logger.warning("health check: database not reachable", exc_info=True)
        return JSONResponse({"ok": False, "version": VERSION}, status_code=503)
    return JSONResponse({"ok": True, "version": VERSION})