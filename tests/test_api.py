"""The agent API, with the agent, the database check and the draft slots replaced by fakes: no OpenAI, no database."""

import logging
import threading
import time
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from support_agent.agent.loop import fallback_draft
from support_agent import api
from support_agent.api import app, get_connection_checker, get_db_check, get_draft_slots, get_drafter, get_keystore
from support_agent.config import get_settings
from support_agent.keys import KeyOpenError
from support_agent.models import CheckRun, Draft, Source, Ticket

KEY = "agent-test-key"
TICKET_TEXT = "Patient Jane Roe says her reminder texts stopped."  # client details: must never reach the logs

ENV = {
    "OPENAI_API_KEY": "sk-test-openai",
    "OPENAI_MODEL": "gpt-test-model",
    "DATABASE_URL": "postgresql://agent:db-pass@127.0.0.1:5433/knowledge",
    "AGENT_API_KEY": KEY,
    "GHL_KEY_PRIVATE": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",  # a test key only; the key store is faked
}

TICKET_JSON = {
    "ticket_id": "t-1",
    "client_key": "brightsmile",
    "location_id": "loc_bright",
    "text": TICKET_TEXT,
    "other_client_names": ["Cedarline Roofing"],
}

DRAFT = Draft(
    likely_cause="The sending number isn't linked to the approved A2P campaign.",
    confidence="medium",
    evidence=["SMS guide: unlinked numbers fail with error 30034"],
    fix_steps=["Trust Center: link the number to the campaign"],
    client_reply="Thanks for letting us know. We're checking your texting setup and will update you here.",
    sources=[Source(title="SMS not sending", url="https://docs.google.com/document/d/sms-guide/edit")],
    needs_person=False,
)


class FakeAgent:
    """Stands in for the real agent: returns a set draft or raises, and records the tickets it got."""

    def __init__(self, draft=DRAFT, error=None):
        self.draft = draft
        self.error = error
        self.tickets = []
        self.tokens = []

    def __call__(self, ticket, token):
        self.tickets.append(ticket)
        self.tokens.append(token)
        if self.error:
            raise self.error
        return self.draft


class FakeKeyStore:
    """Stands in for the database's locked keys: active clients only, as sealed_key_for_location() answers."""

    def __init__(self, tokens=None, error=None):
        self.tokens = {"loc_bright": "pit-bright", "loc_cedar": "pit-cedar"} if tokens is None else tokens
        self.error = error

    def token_for(self, location_id):
        if self.error:
            raise self.error
        return self.tokens.get(location_id)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Test settings instead of the real .env, and no fakes left behind for the next test."""
    monkeypatch.chdir(tmp_path)  # .env is looked up in the current folder; tmp_path has none
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    app.dependency_overrides.clear()


def make_client(agent=None, *, slots=None, db_error=None, keys=None):
    agent = agent or FakeAgent()
    keys = keys or FakeKeyStore()
    app.dependency_overrides[get_keystore] = lambda: keys
    slots = slots if slots is not None else threading.BoundedSemaphore(1)

    def db_check():
        if db_error:
            raise db_error

    app.dependency_overrides[get_drafter] = lambda: agent
    app.dependency_overrides[get_draft_slots] = lambda: slots
    app.dependency_overrides[get_db_check] = lambda: db_check
    return TestClient(app), agent, slots


def post_ticket(client, key=KEY, body=None):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post("/draft", json=body or TICKET_JSON, headers=headers)


def test_draft_returns_the_agents_draft():
    client, agent, _ = make_client()
    r = post_ticket(client)
    assert r.status_code == 200
    assert Draft.model_validate(r.json()) == DRAFT
    assert agent.tickets[0].location_id == "loc_bright"


def test_needs_person_draft_is_a_normal_answer():
    client, _, _ = make_client(FakeAgent(fallback_draft("The agent ran out of time (75 s) before finishing the draft.")))
    r = post_ticket(client)
    assert r.status_code == 200 and r.json()["needs_person"] is True


@pytest.mark.parametrize("key", [None, "", "wrong-key"])
def test_draft_requires_the_right_key(key):
    client, agent, _ = make_client()
    assert post_ticket(client, key=key).status_code == 401
    assert agent.tickets == []  # the agent never ran, so nothing was spent


def test_bad_body_is_rejected():
    client, agent, _ = make_client()
    body = {k: v for k, v in TICKET_JSON.items() if k != "text"}
    assert post_ticket(client, body=body).status_code == 422
    assert agent.tickets == []


def test_unknown_client_is_rejected():
    client, agent, _ = make_client()
    r = post_ticket(client, body=TICKET_JSON | {"location_id": "loc_unknown"})
    assert r.status_code == 422
    assert agent.tickets == []  # a wrong client mapping never reaches GHL


def test_the_agent_gets_its_clients_own_key():
    client, agent, _ = make_client()
    assert post_ticket(client).status_code == 200
    assert agent.tokens == ["pit-bright"]


def test_a_paused_or_unknown_client_is_refused_before_any_ai_call(caplog):
    # The database hands out no key for a paused, removed or unknown client (migration 018).
    client, agent, slots = make_client(keys=FakeKeyStore(tokens={}))
    with caplog.at_level(logging.INFO):
        r = post_ticket(client)
    assert r.status_code == 422
    assert r.json() == {"detail": "unknown client: no active GHL key for this location_id"}
    assert agent.tickets == []  # no AI call, nothing spent
    assert slots.acquire(blocking=False)  # no draft slot was used
    assert "t-1" in caplog.text and TICKET_TEXT not in caplog.text


def test_a_key_that_wont_open_is_an_agent_error_in_the_log(caplog):
    # The key pair was changed without re-entering the keys: a setup fault, so 500 and a log line saying which.
    client, agent, _ = make_client(keys=FakeKeyStore(error=KeyOpenError("the locked key would not open")))
    with caplog.at_level(logging.INFO):
        r = post_ticket(client)
    assert r.status_code == 500 and r.json() == {"detail": "agent error"}
    assert agent.tickets == []
    assert "t-1" in caplog.text and "would not open" in caplog.text


def test_too_long_ticket_is_refused_before_any_ai_call(caplog):
    client, agent, slots = make_client()
    long_text = TICKET_TEXT + " " + "x" * 8_000  # over the 8,000-character default
    with caplog.at_level(logging.INFO):
        r = post_ticket(client, body=TICKET_JSON | {"text": long_text})
    assert r.status_code == 422
    # intake words its "needs a person" card from this
    assert r.json() == {"detail": f"ticket too long: {len(long_text)} characters, limit 8000"}
    assert agent.tickets == []  # no AI call, nothing spent
    assert slots.acquire(blocking=False)  # no draft slot was used
    assert "t-1" in caplog.text and TICKET_TEXT not in caplog.text


def test_ticket_at_the_limit_is_drafted():
    client, agent, _ = make_client()
    assert post_ticket(client, body=TICKET_JSON | {"text": "x" * 8_000}).status_code == 200
    assert len(agent.tickets) == 1


def test_ticket_limit_is_a_setting(monkeypatch):
    monkeypatch.setenv("MAX_TICKET_CHARS", "100")
    client, agent, _ = make_client()
    assert post_ticket(client, body=TICKET_JSON | {"text": "x" * 101}).status_code == 422
    assert agent.tickets == []


def test_busy_when_all_slots_are_taken(caplog):
    client, agent, _ = make_client(slots=threading.Semaphore(0))
    with caplog.at_level(logging.INFO):
        r = post_ticket(client)
    assert r.status_code == 503
    assert int(r.headers["Retry-After"]) > 0
    assert agent.tickets == []
    assert "t-1" in caplog.text  # every refusal is logged: the count shows when to move to a job queue


def test_slot_is_given_back_after_a_draft():
    client, _, _ = make_client()  # one slot only
    assert post_ticket(client).status_code == 200
    assert post_ticket(client).status_code == 200  # the first draft gave its slot back


def test_crash_returns_500_without_details_and_frees_the_slot(caplog):
    client, _, slots = make_client(FakeAgent(error=RuntimeError("boom")))
    with caplog.at_level(logging.INFO):
        r = post_ticket(client)
    assert r.status_code == 500
    assert r.json() == {"detail": "agent error"}  # the cause goes to the log, not to the caller
    assert "boom" in caplog.text and "t-1" in caplog.text
    assert TICKET_TEXT not in caplog.text
    assert slots.acquire(blocking=False)  # the crashed draft gave its slot back


def test_logs_never_hold_the_ticket_text(caplog):
    client, _, _ = make_client()
    with caplog.at_level(logging.INFO):
        post_ticket(client)
    assert "t-1" in caplog.text
    assert TICKET_TEXT not in caplog.text


def test_health_ok_when_database_answers():
    client, _, _ = make_client()
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "version": version("support-agent")}


def test_health_fails_when_database_is_down():
    client, _, _ = make_client(db_error=OSError("connection refused"))
    r = client.get("/health")
    assert r.status_code == 503 and r.json() == {"ok": False, "version": version("support-agent")}  # an alert says which release


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_description_pages_are_off(path):
    client, _, _ = make_client()
    assert client.get(path).status_code == 404


def test_old_click_check_is_gone():
    """Clicks are checked in the database since 2026-10-21 (migration 016); the agent's own check went in v1.0.0."""
    client, _, _ = make_client()
    assert client.post("/slack/verify", content=b"payload=x", headers={"X-API-Key": KEY}).status_code == 404


# Each ticket gets its own client's GHL access

class FakeAccess:
    def __init__(self, location_id):
        self.location_id = location_id
        self.closed = False

    def close(self):
        self.closed = True


def wire_draft(monkeypatch, run=None):
    """Run the real _draft with GHL and the agent loop replaced; returns what they were handed."""
    seen = {}

    def for_location(location_id, token, settings):
        seen["access"] = FakeAccess(location_id)
        seen["token"] = token
        return seen["access"]

    def fake_run(ticket, toolbox, llm):
        seen["toolbox"] = toolbox
        return run() if run else DRAFT

    monkeypatch.setattr(api.GhlAccess, "for_location", staticmethod(for_location))
    monkeypatch.setattr(api, "run_agent", fake_run)
    monkeypatch.setattr(api, "_llm", lambda: None)
    return seen


def test_each_ticket_gets_its_own_clients_ghl_access_closed_afterwards(monkeypatch):
    seen = wire_draft(monkeypatch)
    api._draft(Ticket(**TICKET_JSON), "pit-bright")
    assert seen["access"].location_id == "loc_bright" and seen["toolbox"].location_id == "loc_bright"
    assert seen["token"] == "pit-bright"
    assert seen["access"].closed


def test_a_draft_past_the_time_limit_is_handed_over_and_its_ghl_access_closed_later(monkeypatch):  # one draft took 87 s
    release = threading.Event()

    def slow():
        release.wait(5)  # held until the test lets it go
        return DRAFT

    seen = wire_draft(monkeypatch, run=slow)
    monkeypatch.setattr(api, "_time_limit", lambda: 0.3)
    started = time.monotonic()
    draft = api._draft(Ticket(**TICKET_JSON), "pit-bright")
    assert time.monotonic() - started < 4 and draft.needs_person and "time" in draft.needs_person_reason
    assert not seen["access"].closed  # the late work still holds it
    release.set()
    for thread in threading.enumerate():
        if thread.name == f"draft-{TICKET_JSON['ticket_id']}":
            thread.join(5)
    assert seen["access"].closed  # and closes it when it ends


def test_ghl_access_is_closed_even_when_the_agent_crashes(monkeypatch):
    def crash():
        raise RuntimeError("model down")

    seen = wire_draft(monkeypatch, run=crash)
    with pytest.raises(RuntimeError):
        api._draft(Ticket(**TICKET_JSON), "pit-bright")
    assert seen["access"].closed


def openai_refusal(code):
    import httpx2
    import openai

    response = httpx2.Response(429, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    return openai.RateLimitError("Error code: 429", response=response,
                                 body={"message": "refused", "type": "insufficient_quota", "code": code})


@pytest.mark.parametrize("code", ["project_spend_limit_exceeded", "organization_spend_limit_exceeded",
                                  "credit_balance_exhausted", "insufficient_quota"])
def test_an_openai_spending_refusal_says_so_and_frees_the_slot(code):
    # The hard spend limit or the prepaid credit ran out: every draft fails until someone raises it, so the card
    # and the health check must say "money", not "error".
    client, _, slots = make_client(FakeAgent(error=openai_refusal(code)))
    r = post_ticket(client)
    assert r.status_code == 429
    assert r.json()["detail"] == f"ai spending limit reached ({code})"
    assert slots.acquire(blocking=False)


def test_an_ordinary_openai_rate_limit_is_not_called_a_spending_limit():
    client, _, _ = make_client(FakeAgent(error=openai_refusal("rate_limit_exceeded")))
    r = post_ticket(client)
    assert r.status_code == 500 and r.json() == {"detail": "agent error"}


def test_log_lists_the_lookups_without_client_words(caplog):  # which checks ran
    ran = [CheckRun(name="search_guides", result="ok"), CheckRun(name="workflows", result="error")]
    client, _, _ = make_client(FakeAgent(draft=DRAFT.model_copy(update={"checks_ran": ran})))
    with caplog.at_level(logging.INFO):
        post_ticket(client)
    assert "lookups: search_guides ok, workflows error" in caplog.text
    assert TICKET_TEXT not in caplog.text


# Connection test for a new client's key

def sealed_for_this_agent(token):
    """Lock a key the way the admin app does, with the public half of the test private key in ENV."""
    import base64

    from nacl.public import PrivateKey

    from support_agent.keys import seal

    public = base64.b64encode(bytes(PrivateKey(base64.b64decode(ENV["GHL_KEY_PRIVATE"])).public_key)).decode()
    return base64.b64encode(seal(public, token)).decode()


class FakeChecker:
    def __init__(self, verdict="works"):
        self.verdict = verdict
        self.calls = []

    def __call__(self, location_id, token):
        self.calls.append((location_id, token))
        return self.verdict


def post_check(checker, body, key=KEY):
    app.dependency_overrides[get_connection_checker] = lambda: checker
    headers = {"X-API-Key": key} if key is not None else {}
    return TestClient(app).post("/clients/test-connection", json=body, headers=headers)


@pytest.mark.parametrize("verdict", ["works", "wrong_account", "unreachable"])
def test_connection_test_opens_the_key_and_answers_the_verdict(verdict):
    checker = FakeChecker(verdict)
    r = post_check(checker, {"location_id": "LOCNEW1", "sealed_key": sealed_for_this_agent("pit-new")})
    assert r.status_code == 200 and r.json() == {"result": verdict}
    assert checker.calls == [("LOCNEW1", "pit-new")]


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_connection_test_needs_the_agent_key(key):
    checker = FakeChecker()
    r = post_check(checker, {"location_id": "LOCNEW1", "sealed_key": sealed_for_this_agent("pit-new")}, key=key)
    assert r.status_code == 401 and checker.calls == []


@pytest.mark.parametrize("sealed", ["bm90IGEga2V5", "not base64!", ""])  # locked for another pair, junk, empty
def test_a_key_that_wont_open_is_wrong_account_without_calling_ghl(sealed):
    checker = FakeChecker()
    r = post_check(checker, {"location_id": "LOCNEW1", "sealed_key": sealed})
    assert r.status_code == 200 and r.json() == {"result": "wrong_account"} and checker.calls == []


def test_a_location_id_that_is_not_one_is_refused():
    checker = FakeChecker()
    r = post_check(checker, {"location_id": "../x", "sealed_key": sealed_for_this_agent("pit-new")})
    assert r.status_code == 422 and checker.calls == []


def test_connection_answer_and_log_hold_no_key(caplog):
    with caplog.at_level(logging.INFO):
        r = post_check(FakeChecker(), {"location_id": "LOCNEW1", "sealed_key": sealed_for_this_agent("pit-new")})
    assert r.json() == {"result": "works"}  # the verdict only, never account data
    assert "LOCNEW1" in caplog.text and "works" in caplog.text
    assert "pit-new" not in caplog.text


def test_a_key_with_odd_characters_is_wrong_account():
    checker = FakeChecker()
    r = post_check(checker, {"location_id": "LOCNEW1", "sealed_key": "ключ"})
    assert r.status_code == 200 and r.json() == {"result": "wrong_account"} and checker.calls == []


def test_a_slow_check_is_unreachable_within_the_deadline(monkeypatch):
    release = threading.Event()

    def slow(location_id, token):
        release.wait(5)
        return "works"

    monkeypatch.setattr(api, "CONNECTION_TEST_SECONDS", 0.3)
    started = time.monotonic()
    r = post_check(slow, {"location_id": "LOCNEW1", "sealed_key": sealed_for_this_agent("pit-new")})
    release.set()
    assert r.json() == {"result": "unreachable"} and time.monotonic() - started < 3


def test_a_refused_request_never_echoes_what_was_sent():
    r = post_check(FakeChecker(), {"sealed_key": "c2VhbGVkLWtleS1ieXRlcw=="})  # location_id missing; gitleaks:allow
    assert r.status_code == 422
    assert "c2VhbGVkLWtleS1ieXRlcw==" not in r.text and "location_id" in r.text
