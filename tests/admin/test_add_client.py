"""Add client: the key is locked in the request that brings it, tested through the agent, and saved only when the test
says it works. The database, the agent and Slack are fakes; db/checks/018 covers admin_add_client itself."""

import re
import threading
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from support_agent.admin import add_client
from support_agent.admin.add_client import REFUSALS
from support_agent.admin.agent_client import get_connection_checker
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.check_cap import CheckCap, get_check_cap
from support_agent.admin.db import ClientTaken, get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, SlackUnavailable, get_channel_lister
from support_agent.keys import make_key_pair, open_sealed
from conftest import AUDIENCE, FROM_HERE, ORIGIN
from fakes import FakeAdminDb, ScriptedConn, a_client, postgres_db_on
from mint_test_pass import sign_pass
from panels import heading_levels, is_open, no_gap, panel_tag

GHL_KEY = "pit-00000000-harbor-yoga-test-key"  # the shape of a HighLevel private integration key, made up
FORM = {"channel_id": "C0NEWA", "name": "Harbor Yoga", "location_id": "loc123", "limit": "25", "key": GHL_KEY}


class FakeChecker:
    """The agent's connection test: answers one verdict and records what it was given."""

    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls = []

    def __call__(self, location_id: str, sealed: bytes) -> str:
        self.calls.append((location_id, sealed))
        return self.verdict


@pytest.fixture
def key_pair():
    return make_key_pair()  # (public, private): the private half stands in for the agent's


@pytest.fixture
def db():
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing"))


def one_free_channel():
    return [Channel("C0NEWA", "harbor-yoga")]


def signed_in_client(settings, keys, key_pair, db, checker, lister=one_free_channel, cap=None) -> TestClient:
    app = create_app()
    cap = cap or CheckCap()  # each test its own: the app's one cap would count every test's presses together
    app.dependency_overrides[get_check_cap] = lambda: cap
    settings = settings.model_copy(update={"ghl_key_public": SecretStr(key_pair[0])})
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_connection_checker] = lambda: checker
    app.dependency_overrides[get_channel_lister] = lambda: lister
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE),
                 "Origin": ORIGIN}
    return TestClient(app, headers=signed_in, follow_redirects=False)


def test_save_only_after_a_working_test(settings, keys, key_pair, db):
    checker = FakeChecker("works")
    response = signed_in_client(settings, keys, key_pair, db, checker).post("/clients/new", data=FORM)

    assert response.status_code == 303
    assert response.headers["location"] == "/clients?added=harbor-yoga"
    [(location_id, tested)] = checker.calls
    assert location_id == "loc123"
    assert open_sealed(key_pair[1], tested) == GHL_KEY  # locked with the admin app's public half
    # Saved by Ana (staff id 1), with the very value that was tested: the saved key is the tested key.
    assert db.calls == [("add_client", 1, "C0NEWA", "Harbor Yoga", "loc123", 25, tested)]


def test_add_client_saves_the_channel_name_and_the_list_shows_it(settings, keys, key_pair, db):
    """The name comes from the Slack list the POST itself asked for (never a form field), and every place that showed
    the channel's code now shows `#name`: the list's row, the card and the client panel under its title."""
    client = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"))
    assert client.post("/clients/new", data=FORM | {"channel_name": "#forged"}).status_code == 303

    assert db.noted == [{"C0NEWA": "harbor-yoga"}]  # Slack's name, not the forged field
    cards = client.get("/clients").text
    rows = client.get("/clients?view=list").text
    panel = client.get("/clients/harbor-yoga").text
    assert '<span class="ds-card__sub">#harbor-yoga</span>' in cards
    assert '<span class="ds-table__sub">#harbor-yoga</span>' in rows
    assert '<span class="ds-sheet__sub">#harbor-yoga</span>' in panel
    for page in (cards, rows, panel):
        assert "C0NEWA" not in page and "#forged" not in page  # no code where the name is known


def test_a_failed_noting_after_add_still_adds_the_client(settings, keys, key_pair, db, caplog):
    """The client is saved before its name is noted, so a refusal of the noting leaves it added and showing its ID until
    the hourly refresh; the log names the error's type only."""
    def refuse(names):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.note_channel_names = refuse
    client = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"))
    response = client.post("/clients/new", data=FORM)

    assert response.status_code == 303 and response.headers["location"] == "/clients?added=harbor-yoga"
    assert [call[0] for call in db.calls] == ["add_client"]
    assert '<span class="ds-card__sub">C0NEWA</span>' in client.get("/clients").text
    assert "OperationalError" in caplog.text
    assert "password" not in caplog.text and "127.0.0.1" not in caplog.text and "harbor-yoga" not in caplog.text


@pytest.mark.parametrize("verdict, message", [
    ("wrong_account", "This token doesn't open that HighLevel account. Check the location id and the token."),
    ("unreachable", "Couldn't confirm this token with HighLevel. Check the token and location ID, or try again in a "
                    "minute. Nothing was saved."),
])
def test_a_failed_test_saves_nothing(settings, keys, key_pair, db, verdict, message):
    checker = FakeChecker(verdict)
    response = signed_in_client(settings, keys, key_pair, db, checker).post("/clients/new", data=FORM)

    assert response.status_code == 422
    assert message in response.text.replace("&#39;", "'")
    assert len(checker.calls) == 1
    assert db.calls == []
    # The form comes back filled in, so only the key is typed again.
    for typed in ("C0NEWA", "Harbor Yoga", "loc123"):
        assert typed in response.text


@pytest.mark.parametrize("change, message", [
    ({"channel_id": ""}, "Choose the client's Slack channel."),
    ({"channel_id": "general"}, "Choose the client's Slack channel."),  # not a channel id
    ({"name": "  "}, "Type the client's name, up to 80 characters."),
    ({"name": "N" * 81}, "Type the client's name, up to 80 characters."),
    ({"limit": "0"}, "The limit must be a whole number from 1 to 200."),
    ({"limit": "twenty"}, "The limit must be a whole number from 1 to 200."),
    ({"location_id": "loc 123!"}, "The location id is letters, digits, - and _ only (up to 64)."),
    ({"key": "  "}, "Paste the client's HighLevel token."),
], ids=["no-channel", "not-a-channel", "blank-name", "long-name", "limit-0", "limit-words", "bad-location", "no-key"])
def test_a_failed_validation_never_echoes_the_key(settings, keys, key_pair, db, change, message):
    checker = FakeChecker("works")
    response = signed_in_client(settings, keys, key_pair, db, checker).post("/clients/new", data=FORM | change)

    assert response.status_code == 422
    assert message in response.text.replace("&#39;", "'")
    assert GHL_KEY not in response.text
    assert checker.calls == [] and db.calls == []  # nothing tested, nothing saved


def test_save_refuses_a_channel_that_isnt_free_any_more(settings, keys, key_pair, db):
    # The bot left C0NEWA, or it was archived, after the panel opened; or the form was changed by hand.
    checker = FakeChecker("works")
    browser = signed_in_client(settings, keys, key_pair, db, checker, lister=lambda: [Channel("C0NEWB", "maple")])
    response = browser.post("/clients/new", data=FORM)

    assert response.status_code == 422
    assert "That channel isn't free any more. Choose another one." in response.text.replace("&#39;", "'")
    assert GHL_KEY not in response.text
    assert checker.calls == [] and db.calls == []  # nothing tested, nothing saved


def test_slack_down_during_save_saves_nothing(settings, keys, key_pair, db, caplog):
    def lister():
        raise SlackUnavailable("ratelimited")

    checker = FakeChecker("works")
    response = signed_in_client(settings, keys, key_pair, db, checker, lister).post("/clients/new", data=FORM)

    assert response.status_code == 503
    assert ("Can't check the Slack channel right now. Nothing was saved; try again in a minute."
            in response.text.replace("&#39;", "'"))
    assert GHL_KEY not in response.text
    assert checker.calls == [] and db.calls == []
    assert "channel list unavailable: ratelimited" in caplog.text  # Slack's reason, not just "no answer"


def test_slacks_time_comes_out_of_the_same_limit(settings, keys, key_pair, db, hanging, monkeypatch):
    # A slow Slack (answering in 0.8 s) leaves the agent only what's left of the limit (1 s here), not a fresh 1 s.
    monkeypatch.setattr(add_client, "ANSWER_SECONDS", 1.0)

    def slow_slack():
        time.sleep(0.8)
        return [Channel("C0NEWA", "harbor-yoga")]

    browser = signed_in_client(settings, keys, key_pair, db, hanging("works"), lister=slow_slack)
    started = time.monotonic()
    response = browser.post("/clients/new", data=FORM)

    assert time.monotonic() - started < 1.5
    assert REFUSALS["unreachable"] in response.text.replace("&#39;", "'")
    assert db.calls == []


class Hanging:
    """A call that doesn't answer until the test lets it go (then answers `verdict`): an agent or Slack stuck mid-call."""

    def __init__(self, verdict=None):
        self.verdict = verdict
        self.let_go = threading.Event()

    def __call__(self, *args):
        self.let_go.wait(10)
        return self.verdict


@pytest.fixture
def hanging():
    stuck = []
    yield lambda verdict=None: stuck.append(Hanging(verdict)) or stuck[-1]
    for call in stuck:
        call.let_go.set()


def test_save_answers_within_the_time_limit_when_the_agent_hangs(settings, keys, key_pair, db, hanging, monkeypatch):
    # Measured in real time, the 25 s made short: a late "works" must not save either.
    monkeypatch.setattr(add_client, "ANSWER_SECONDS", 0.5)
    browser = signed_in_client(settings, keys, key_pair, db, hanging("works"))

    started = time.monotonic()
    response = browser.post("/clients/new", data=FORM)

    assert time.monotonic() - started < 1.5
    assert response.status_code == 422
    assert REFUSALS["unreachable"] in response.text.replace("&#39;", "'")
    assert db.calls == []


def test_save_gives_up_on_a_hanging_slack_in_its_share_of_the_time(settings, keys, key_pair, db, hanging, monkeypatch):
    # Slack gets a few seconds of the 25; the rest is the agent's (made short here).
    monkeypatch.setattr(add_client, "SLACK_SECONDS", 0.3)
    checker = FakeChecker("works")
    browser = signed_in_client(settings, keys, key_pair, db, checker, lister=hanging())

    started = time.monotonic()
    response = browser.post("/clients/new", data=FORM)

    assert time.monotonic() - started < 1.3
    assert response.status_code == 503
    assert "Can't check the Slack channel right now." in response.text.replace("&#39;", "'")
    assert checker.calls == [] and db.calls == []


def test_the_test_button_answers_within_the_time_limit_when_the_agent_hangs(settings, keys, key_pair, db, hanging,
                                                                            monkeypatch):
    monkeypatch.setattr(add_client, "ANSWER_SECONDS", 0.5)
    browser = signed_in_client(settings, keys, key_pair, db, hanging("works"))

    started = time.monotonic()
    answer = browser.post("/clients/new/test", data={"location_id": "loc123", "key": GHL_KEY})

    assert time.monotonic() - started < 1.5
    assert answer.json() == {"result": "unreachable", "message": REFUSALS["unreachable"]}


@pytest.mark.parametrize("missing", ["channel_id", "name", "limit", "location_id", "key"])
def test_a_missing_field_is_a_message_not_a_crash(settings, keys, key_pair, db, missing):
    form = {name: value for name, value in FORM.items() if name != missing}
    response = signed_in_client(settings, keys, key_pair, db, FakeChecker("works")).post("/clients/new", data=form)

    assert response.status_code == 422
    assert GHL_KEY not in response.text
    assert db.calls == []


@pytest.mark.parametrize("verdict", ["works", "wrong_account"])
def test_the_key_is_sealed_before_any_database_or_agent_call(settings, keys, key_pair, db, verdict, caplog):
    caplog.set_level("DEBUG")
    checker = FakeChecker(verdict)
    signed_in_client(settings, keys, key_pair, db, checker).post("/clients/new", data=FORM)

    # Everything the agent and the database were given, and every log line: the locked bytes, never the key.
    handed_over = repr(checker.calls) + repr(db.calls)
    assert checker.calls and all(isinstance(sealed, bytes) for _, sealed in checker.calls)
    assert GHL_KEY not in handed_over and GHL_KEY.encode() not in b"".join(sealed for _, sealed in checker.calls)
    assert GHL_KEY not in caplog.text


def test_second_save_of_the_same_client_shows_a_plain_message(settings, keys, key_pair, db):
    # Two tabs, both filled in for Harbor Yoga, both tested: the first Save adds it, the second is refused.
    browser = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"))
    before, owners_before = list(db.rows), dict(db.owners)
    assert browser.post("/clients/new", data=FORM).status_code == 303
    # The second request read the clients (and the channels clients have had, migration 024) before the first one saved
    # (the lister now gets the whole list, and the request leaves out the clients' own), so it still sees the channel
    # free and only the database refuses it.
    db.clients = lambda: before
    db.channel_owners = lambda: owners_before
    second = browser.post("/clients/new", data=FORM)

    assert second.status_code == 409
    assert ("That Slack channel or HighLevel account already belongs to a client. Nothing was saved."
            in second.text)
    assert GHL_KEY not in second.text
    assert [row.display_name for row in db.rows] == ["Cedarline Roofing", "Harbor Yoga"]  # one Harbor Yoga, whole


def test_the_databases_already_a_client_refusal_becomes_client_taken(monkeypatch):
    refusal = psycopg.errors.RaiseException("admin_add_client: that channel or HighLevel location already belongs to a "
                                            "client")
    with pytest.raises(ClientTaken):
        postgres_db_on(ScriptedConn(refusal), monkeypatch).add_client(1, "C0NEWA", "Harbor Yoga", "loc123", 20, b"x")
    other = psycopg.errors.RaiseException("admin: only an active staff member can make changes")
    with pytest.raises(psycopg.errors.RaiseException):  # anything else stays a database error (503)
        postgres_db_on(ScriptedConn(other), monkeypatch).add_client(1, "C0NEWA", "Harbor Yoga", "loc123", 20, b"x")


def test_add_client_says_token_never_key(settings, keys, key_pair, db):
    """One word throughout: "token", HighLevel's own ("Private Integration token")."""
    client = signed_in_client(settings, keys, key_pair, db, FakeChecker("wrong_account"))
    panel = client.get("/clients/new").text.replace("&#39;", "'")
    refused = client.post("/clients/new", data=FORM).text.replace("&#39;", "'")
    blank = client.post("/clients/new", data=FORM | {"key": "  "}).text.replace("&#39;", "'")

    assert ">HighLevel token</label>" in panel
    assert "This token doesn't open that HighLevel account. Check the location id and the token." in refused
    assert "Paste the client's HighLevel token." in blank
    for page in (panel, refused, blank):
        assert "HighLevel key" not in page and "the key." not in page and "This key" not in page


def test_database_down_during_the_save_is_a_plain_503(settings, keys, key_pair, db, caplog):
    caplog.set_level("DEBUG")  # every level: a login or address logged even at info level fails this

    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.add_client = down
    response = signed_in_client(settings, keys, key_pair, db, FakeChecker("works")).post("/clients/new", data=FORM)

    assert response.status_code == 503
    assert "Couldn't reach the database, so the client may not have been added." in response.text.replace("&#39;", "'")
    assert GHL_KEY not in response.text
    assert "OperationalError" in caplog.text  # the log was read: its type is there
    assert "password for admin_app" not in response.text + caplog.text and "127.0.0.1" not in caplog.text


def test_the_panel_offers_free_channels_and_starts_at_20(settings, keys, key_pair):
    db = FakeAdminDb(a_client("cedarline", "Cedarline Roofing"), a_client("held", "Held Dental", status="paused"),
                     a_client("gone", "Gone Plumbing", status="removed"))
    bot_is_in = [Channel("C0CEDARLINE", "client-cedarline"), Channel("C0HELD", "client-held"),
                 Channel("C0GONE", "client-gone-plumbing"), Channel("C0NEWA", "harbor-yoga"),
                 Channel("C0NEWB", "maple-dental")]

    page = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"), lambda: bot_is_in).get("/clients/new")

    assert page.status_code == 200
    # Active and paused clients' channels are left out; a removed client's can be picked again, which brings it back.
    # Add client works this out from the bot's whole list.
    assert 'value="C0CEDARLINE"' not in page.text and 'value="C0HELD"' not in page.text
    assert '<option value="C0GONE">#client-gone-plumbing</option>' in page.text
    assert '<option value="C0NEWA">#harbor-yoga</option>' in page.text
    assert '<option value="C0NEWB">#maple-dental</option>' in page.text
    assert 'name="limit" inputmode="numeric" autocomplete="off" value="20"' in page.text


def test_a_removed_client_is_added_back_on_its_own_channel(settings, keys, key_pair):
    db = FakeAdminDb(a_client("cedarline", "Cedarline Roofing"), a_client("gone", "Gone Plumbing", status="removed"))
    bot_is_in = [Channel("C0CEDARLINE", "client-cedarline"), Channel("C0GONE", "client-gone-plumbing")]

    client = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"), lambda: bot_is_in)
    page = client.get("/clients/new")
    back = {**FORM, "channel_id": "C0GONE", "name": "Gone Plumbing", "location_id": "loc-gone"}
    response = client.post("/clients/new", data=back)

    assert '<option value="C0GONE">#client-gone-plumbing</option>' in page.text
    assert 'value="C0CEDARLINE"' not in page.text
    assert response.status_code == 303
    assert response.headers["location"] == "/clients?added=gone"  # the same client, not a new one
    assert [row.status for row in db.clients() if row.client_key == "gone"] == ["active"]


def test_slack_down_shows_a_plain_message(settings, keys, key_pair, db):
    def lister():
        raise SlackUnavailable("missing_scope")

    page = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"), lister).get("/clients/new")

    assert page.status_code == 200
    assert "Can't load the support bot's Slack channels right now. Reload in a minute." in page.text.replace("&#39;", "'")
    assert "missing_scope" not in page.text


def test_no_free_channel_says_what_to_do(settings, keys, key_pair, db):
    page = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"), lambda: []).get("/clients/new")

    assert ("The support bot isn't in a channel without a client yet. Add it to the client's channel in Slack, then "
            "reload." in page.text.replace("&#39;", "'"))


@pytest.mark.parametrize("verdict, message", [
    ("works", "Connected to this HighLevel account."),
    ("wrong_account", "This token doesn't open that HighLevel account. Check the location id and the token."),
    ("unreachable", "Couldn't confirm this token with HighLevel. Check the token and location ID, or try again in a "
                    "minute. Nothing was saved."),
])
def test_the_test_button_answers_the_message_and_saves_nothing(settings, keys, key_pair, db, verdict, message):
    checker = FakeChecker(verdict)
    answer = signed_in_client(settings, keys, key_pair, db, checker).post(
        "/clients/new/test", data={"location_id": "loc123", "key": GHL_KEY})

    assert answer.status_code == 200
    assert answer.json() == {"result": verdict, "message": message}
    [(location_id, sealed)] = checker.calls
    assert location_id == "loc123" and open_sealed(key_pair[1], sealed) == GHL_KEY
    assert db.calls == []


@pytest.mark.parametrize("form, field", [
    ({"location_id": "loc 123!", "key": GHL_KEY}, "location_id"),
    ({"location_id": "loc123", "key": " "}, "key"),
    ({}, "location_id"),
])
def test_the_test_button_checks_its_two_fields_first(settings, keys, key_pair, db, form, field):
    checker = FakeChecker("works")
    answer = signed_in_client(settings, keys, key_pair, db, checker).post("/clients/new/test", data=form)

    assert answer.status_code == 422
    assert answer.json()["errors"][field]
    assert GHL_KEY not in answer.text
    assert checker.calls == []


def test_clients_links_to_add_client_even_when_empty(settings, keys, key_pair, db):
    for rows in (db, FakeAdminDb()):
        page = signed_in_client(settings, keys, key_pair, rows, FakeChecker("works")).get("/clients")
        assert 'href="/clients/new"' in page.text


def test_an_empty_clients_page_has_one_add_client(settings, keys, key_pair):
    """A new agency's first screen offers one first step, the empty state's button."""
    page = signed_in_client(settings, keys, key_pair, FakeAdminDb(), FakeChecker("works")).get("/clients").text
    assert page.count('href="/clients/new"') == 1
    empty_state = page[page.index('class="ds-empty-state"'):]
    assert 'href="/clients/new"' in empty_state[:empty_state.index("</div>")]


def test_the_header_add_client_is_back_with_a_client(settings, keys, key_pair, db):
    page = signed_in_client(settings, keys, key_pair, db, FakeChecker("works")).get("/clients").text
    assert 'class="ds-button ds-button--accent ds-page-header__add" href="/clients/new"' in page
    assert "No clients yet" not in page


def test_an_added_client_is_announced_by_its_saved_name(settings, keys, key_pair, db):
    browser = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"))

    page = browser.get("/clients?added=cedarline", headers=FROM_HERE)
    assert 'data-toast="Added Cedarline Roofing. It drafts from its next message."' in page.text

    # Only a client's saved name is ever shown: an unknown key, or words in the address, show nothing.
    for added in ("nobody", "<b>Free money</b>"):
        assert "data-toast=" not in browser.get("/clients", params={"added": added}, headers=FROM_HERE).text


@pytest.mark.parametrize("address", ["/clients?added=cedarline", "/clients?removed=gone"])
@pytest.mark.parametrize("site", [None, "none", "cross-site", "same-site"], ids=["no header", "typed", "another site",
                                                                               "same site"])
def test_an_added_or_removed_message_needs_a_request_from_this_app(settings, keys, key_pair, address, site):
    db = FakeAdminDb(a_client("cedarline", "Cedarline Roofing"), a_client("gone", "Gone Plumbing", status="removed"))
    browser = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"))
    response = browser.get(address, headers={"Sec-Fetch-Site": site} if site else {})

    assert response.status_code == 200 and 'id="results"' in response.text  # the list itself still shows
    assert "data-toast=" not in response.text
    drawn = browser.get(address, headers=FROM_HERE)
    assert "data-toast=" in drawn.text  # and the same address, from this app's own request, says it


WAIT = "That's 5 connection tests in a minute. Wait a minute, then try again."


def test_the_sixth_test_press_in_a_minute_waits_without_asking_the_agent(settings, keys, key_pair, db):
    checker = FakeChecker("wrong_account")
    browser = signed_in_client(settings, keys, key_pair, db, checker)
    press = {"location_id": "loc123", "key": GHL_KEY}
    for _ in range(5):
        assert browser.post("/clients/new/test", data=press).status_code == 200

    sixth = browser.post("/clients/new/test", data=press)
    assert sixth.status_code == 429
    assert sixth.json() == {"result": "wait", "message": WAIT}
    assert len(checker.calls) == 5


def test_save_counts_toward_the_cap_and_saves_nothing_when_held_back(settings, keys, key_pair, db):
    checker = FakeChecker("works")
    browser = signed_in_client(settings, keys, key_pair, db, checker)
    for _ in range(5):
        browser.post("/clients/new/test", data={"location_id": "loc123", "key": GHL_KEY})

    held = browser.post("/clients/new", data=FORM)
    assert held.status_code == 429
    assert WAIT in held.text.replace("&#39;", "'")
    assert GHL_KEY not in held.text
    assert len(checker.calls) == 5 and db.calls == []


def test_a_form_refused_for_its_fields_doesnt_count(settings, keys, key_pair, db):
    checker = FakeChecker("works")
    browser = signed_in_client(settings, keys, key_pair, db, checker)
    for _ in range(6):
        browser.post("/clients/new/test", data={"location_id": "loc 123!", "key": GHL_KEY})  # refused before the agent

    assert browser.post("/clients/new/test", data={"location_id": "loc123", "key": GHL_KEY}).status_code == 200


def test_add_client_address_shows_the_panel_over_clients(settings, keys, key_pair, db):
    page = signed_in_client(settings, keys, key_pair, db, FakeChecker("works")).get("/clients/new").text

    assert 'id="results"' in page
    tag = panel_tag(page)
    assert 'class="ds-sheet"' in tag and is_open(tag) and 'id="panel"' in tag
    assert "ds-sheet--static" not in page
    assert page.count('id="toast"') == 1 and page.count("<h1") == 1
    assert re.search(r'<h2[^>]*ds-sheet__title[^>]*>\s*Add client\s*</h2>', page)
    assert page.index('id="results"') < page.index("<dialog")
    assert 'name="limit" inputmode="numeric" autocomplete="off" value="20"' in page  # blank form, limit 20
    assert 'name="name" autocomplete="organization" maxlength="80" value=""' in page
    assert 'value="Harbor' not in page and "Choose a channel" not in page  # the first channel is the starting pick


def refuse_add(**change):
    return lambda browser, db: browser.post("/clients/new", data=FORM | change)


def refuse_add_taken(browser, db):
    db.add_client = lambda *args: (_ for _ in ()).throw(ClientTaken("C0NEWA"))
    return browser.post("/clients/new", data=FORM)


def refuse_add_waits(browser, db):
    for _ in range(5):
        browser.post("/clients/new/test", data={"location_id": "loc123", "key": GHL_KEY})
    return browser.post("/clients/new", data=FORM)


def refuse_add_slack(browser, db):
    def lister():
        raise SlackUnavailable("ratelimited")

    browser.app.dependency_overrides[get_channel_lister] = lambda: lister
    return browser.post("/clients/new", data=FORM)


@pytest.mark.parametrize("refuse, status", [(refuse_add(name=" "), 422), (refuse_add_taken, 409),
                                            (refuse_add_waits, 429), (refuse_add_slack, 503)],
                         ids=["add-422", "add-409", "add-429", "add-503"])
def test_a_refused_form_answers_with_the_panel_alone(settings, keys, key_pair, db, refuse, status):
    response = refuse(signed_in_client(settings, keys, key_pair, db, FakeChecker("works")), db)
    page = response.text

    assert response.status_code == status
    assert 'id="results"' not in page and 'id="summary"' not in page
    assert is_open(panel_tag(page)) and 'id="panel"' in panel_tag(page)
    assert page.count('id="toast"') == 1 and 'role="status"' in page
    levels = heading_levels(page)
    assert no_gap(levels, 2), f"headings {levels}"
    assert GHL_KEY not in page


@pytest.mark.parametrize("change, status", [({}, 503), ({"name": " "}, 422)])
def test_a_refused_add_needs_no_more_database(settings, keys, key_pair, db, change, status):
    """The list behind needs the database, and a failure there must not lose what was typed: after the channels are
    read (their one read), db.clients() raises, and the refusal still comes back as the panel with the typed name."""
    reads = []
    original = db.clients

    def one_read():
        reads.append("clients")
        if len(reads) > 1:
            raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")
        return original()

    db.clients = one_read

    def slack_down():
        raise SlackUnavailable("ratelimited")

    lister = slack_down if status == 503 else one_free_channel
    response = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"), lister).post(
        "/clients/new", data=FORM | change)

    assert response.status_code == status
    assert is_open(panel_tag(response.text)) and 'id="results"' not in response.text
    if status == 503:
        assert "Can't check the Slack channel right now." in response.text.replace("&#39;", "'")
        assert 'value="Harbor Yoga"' in response.text  # what was typed comes back
    else:
        assert "Type the client's name, up to 80 characters." in response.text.replace("&#39;", "'")
    assert reads == ["clients"]


def test_add_refused_for_a_channel_no_longer_free_selects_the_blank_option(settings, keys, key_pair, db):
    # The chosen channel left the list: the browser would pick the first channel by itself, and the next Add could
    # go to the wrong channel. The blank option is selected, so a channel has to be picked again.
    browser = signed_in_client(settings, keys, key_pair, db, FakeChecker("works"),
                               lister=lambda: [Channel("C0NEWB", "maple"), Channel("C0NEWC", "oak")])
    refused = browser.post("/clients/new", data=FORM)

    assert refused.status_code == 422
    assert '<option value="" selected>Choose a channel</option>' in refused.text
    assert '<option value="C0NEWB">#maple</option>' in refused.text
    assert '<option value="C0NEWC">#oak</option>' in refused.text
    assert refused.text.count(" selected>") == 1  # only the blank one

    blank = browser.post("/clients/new", data=FORM | {"channel_id": ""})  # posting the blank is refused on the channel
    assert blank.status_code == 422
    assert "Choose the client's Slack channel." in blank.text.replace("&#39;", "'")
    assert '<option value="" selected>Choose a channel</option>' in blank.text and blank.text.count(" selected>") == 1
    assert db.calls == [] and GHL_KEY not in refused.text + blank.text


def test_a_refusal_for_another_field_keeps_the_chosen_channel(settings, keys, key_pair, db):
    refused = signed_in_client(settings, keys, key_pair, db, FakeChecker("works")).post(
        "/clients/new", data=FORM | {"name": " "})

    assert '<option value="C0NEWA" selected>#harbor-yoga</option>' in refused.text
    assert "Choose a channel" not in refused.text
