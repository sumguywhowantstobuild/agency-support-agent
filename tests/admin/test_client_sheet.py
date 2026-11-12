"""The client sheet: one client's details, pause and resume, a new key (tested first, as Add client), remove, and the
client's history. The database, the agent and Slack are fakes; db/checks/018 covers the admin_* functions themselves."""

import re
import threading
import time
from dataclasses import replace

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from support_agent.admin import add_client
from support_agent.admin.add_client import NOT_FREE, REFUSALS, SLACK_DOWN, USED_BY_ANOTHER
from support_agent.admin.agent_client import get_connection_checker
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.check_cap import CheckCap, get_check_cap
from support_agent.admin.client_sheet import PICK_ERROR
from support_agent.admin.db import (ChannelUsedByAnother, ClientGone, ClientTaken, ClientUnchanged, get_admin_db)
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, SlackUnavailable, get_channel_lister
from support_agent.keys import make_key_pair, open_sealed
from conftest import AUDIENCE, FROM_HERE, ORIGIN
from fakes import FakeAdminDb, ScriptedConn, a_change, a_client, postgres_db_on
from mint_test_pass import sign_pass
from panels import dialog_tags, heading_levels, ids, is_open, no_gap, panel_tag


class FakeChecker:
    """The agent's connection test: answers one verdict and records what it was given."""

    def __init__(self, verdict: str = "works"):
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
    history = (
        a_change("cedarline", "added", "2026-10-20T09:00"),
        a_change("cedarline", "paused", "2026-10-21T10:30", who="Ben Cruz", detail="key_broken: new key coming",
                 reason="key_broken", note="new key coming"),
        a_change("cedarline", "resumed", "2026-10-22T08:15"),
        a_change("cedarline", "limit_changed", "2026-10-23T14:05", detail="20 to 30"),
        a_change("cedarline", "key_replaced", "2026-10-24T16:45"),
        a_change("brightsmile", "paused", "2026-10-25T11:00", detail="client_asked", reason="client_asked"),
    )
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
                       a_client("brightsmile", "BrightSmile Dental", status="paused", reason="client_asked",
                                note="Back after the office move"), history=history)


def signed_in_client(settings, keys, key_pair, db, checker=None) -> TestClient:
    app = create_app()
    cap = CheckCap()  # each test its own
    app.dependency_overrides[get_check_cap] = lambda: cap
    settings = settings.model_copy(update={"ghl_key_public": SecretStr(key_pair[0])})
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_connection_checker] = lambda: checker or FakeChecker()
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE),
                 "Origin": ORIGIN}
    return TestClient(app, headers=signed_in, follow_redirects=False)


def words(response) -> str:
    return response.text.replace("&#39;", "'").replace("&#34;", '"')


@pytest.mark.parametrize("reason", ["", "vacation"])
def test_pause_needs_a_reason(settings, keys, key_pair, db, reason):
    response = signed_in_client(settings, keys, key_pair, db).post(
        "/clients/cedarline/pause", data={"reason": reason, "note": ""})

    assert response.status_code == 422
    assert "Pick a reason for the pause." in words(response)
    assert db.calls == []


def test_pause_note_over_200_is_refused(settings, keys, key_pair, db):
    browser = signed_in_client(settings, keys, key_pair, db)

    refused = browser.post("/clients/cedarline/pause", data={"reason": "client_asked", "note": "n" * 201})
    assert refused.status_code == 422
    assert "The note can be up to 200 characters." in words(refused)
    assert "n" * 201 in refused.text  # typed again only to cut it down: the note comes back
    assert db.calls == []

    paused = browser.post("/clients/cedarline/pause", data={"reason": "client_asked", "note": " " + "n" * 200 + " "})
    assert paused.status_code == 303
    assert paused.headers["location"] == "/clients/cedarline?done=paused"
    assert db.calls == [("pause", 1, "cedarline", "client_asked", "n" * 200)]  # by Ana, trimmed


def test_resume(settings, keys, key_pair, db):
    response = signed_in_client(settings, keys, key_pair, db).post("/clients/brightsmile/resume")

    assert response.status_code == 303
    assert response.headers["location"] == "/clients/brightsmile?done=resumed"
    assert db.calls == [("resume", 1, "brightsmile")]


GHL_KEY = "pit-8c2e5d90-new-cedarline-test-key"  # made up; not hex after the first part, so no "real key" look


def test_replace_key_saves_only_after_a_working_test(settings, keys, key_pair, db):
    checker = FakeChecker("works")
    response = signed_in_client(settings, keys, key_pair, db, checker).post(
        "/clients/cedarline/key", data={"key": GHL_KEY})

    assert response.status_code == 303
    assert response.headers["location"] == "/clients/cedarline?done=key_replaced"
    [(location_id, tested)] = checker.calls
    assert location_id == "loc-cedarline"  # the client's own location: only the key is new
    assert open_sealed(key_pair[1], tested) == GHL_KEY
    assert db.calls == [("replace_key", 1, "cedarline", tested)]  # the very value that was tested


@pytest.mark.parametrize("verdict", ["wrong_account", "unreachable"])
def test_a_new_key_that_fails_its_test_saves_nothing(settings, keys, key_pair, db, verdict):
    response = signed_in_client(settings, keys, key_pair, db, FakeChecker(verdict)).post(
        "/clients/cedarline/key", data={"key": GHL_KEY})

    assert response.status_code == 422
    assert REFUSALS[verdict] in words(response)
    assert GHL_KEY not in response.text  # typed again, never shown back
    assert db.calls == []


def test_replace_key_answers_within_the_limit_even_with_a_slow_history(settings, keys, key_pair, db, monkeypatch):
    # The limit made short: a 0.8 s history read and a test that never answers still fit the one 1 s limit
    # (the history was once read after the test had used the time up).
    monkeypatch.setattr(add_client, "ANSWER_SECONDS", 1.0)
    let_go = threading.Event()
    slow_history = db.client_history
    db.client_history = lambda client_key: time.sleep(0.8) or slow_history(client_key)
    hanging = lambda location_id, sealed: "works" if let_go.wait(10) else "unreachable"  # noqa: E731
    browser = signed_in_client(settings, keys, key_pair, db, hanging)
    try:
        started = time.monotonic()
        response = browser.post("/clients/cedarline/key", data={"key": GHL_KEY})
        assert time.monotonic() - started < 1.4
    finally:
        let_go.set()
    assert REFUSALS["unreachable"] in words(response)
    assert db.calls == []


@pytest.mark.parametrize("typed", ["", "Cedarline", "cedarline roofing", "Cedarline Roofing Inc"])
def test_remove_needs_the_typed_name(settings, keys, key_pair, db, typed):
    response = signed_in_client(settings, keys, key_pair, db).post(
        "/clients/cedarline/remove", data={"confirm": typed})

    assert response.status_code == 422
    assert "Type Cedarline Roofing exactly to remove it." in words(response)
    assert db.calls == []


def test_removed_client_leaves_the_list(settings, keys, key_pair, db):
    browser = signed_in_client(settings, keys, key_pair, db)

    removed = browser.post("/clients/cedarline/remove", data={"confirm": " Cedarline Roofing "})
    assert removed.status_code == 303
    assert removed.headers["location"] == "/clients?removed=cedarline"
    assert db.calls == [("remove", 1, "cedarline")]

    clients = browser.get("/clients?removed=cedarline", headers=FROM_HERE)
    assert "Cedarline Roofing" in clients.text  # only in the message, said once by its saved name
    assert ('data-toast="Removed Cedarline Roofing. Remove the support bot from its channel in Slack."'
            in words(clients))
    after = browser.get("/clients").text
    line = after.split('id="change-line"')[1].split('<form id="find"')[0]
    assert "removed Cedarline Roofing" in line  # the latest change says so
    assert "Cedarline Roofing" not in after.replace(line, "")  # and nothing else on the page names it
    assert browser.get("/clients/cedarline").status_code == 404


@pytest.mark.parametrize("path", ["/clients/new", "/clients/cedarline?confirm=key"])
def test_the_key_field_is_a_password_field_password_managers_skip(settings, keys, key_pair, db, path, monkeypatch):
    # A password field: screen readers don't read it out as typed. The manager tags ask 1Password,
    # LastPass and Bitwarden not to offer to save a client's key; it is never given a value.
    from support_agent.admin.slack import get_channel_lister
    browser = signed_in_client(settings, keys, key_pair, db)
    browser.app.dependency_overrides[get_channel_lister] = lambda: lambda: []
    field = re.search(r'<input[^>]* name="key"[^>]*>', browser.get(path).text).group(0)

    assert 'type="password"' in field and 'autocomplete="off"' in field
    assert all(tag in field for tag in ("data-1p-ignore", 'data-lpignore="true"', "data-bwignore"))
    assert "value=" not in field


@pytest.mark.parametrize("removed", ["cedarline", "nobody", "<b>Free money</b>"])
def test_only_a_removed_client_is_announced_as_removed(settings, keys, key_pair, db, removed):
    # Cedarline is still active: an address saying otherwise announces nothing.
    page = signed_in_client(settings, keys, key_pair, db).get(
        "/clients", params={"removed": removed}, headers=FROM_HERE).text

    assert "data-toast=" not in page


def test_the_remove_screen_says_what_happens(settings, keys, key_pair, db):
    page = signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline?confirm=remove")

    assert ("Removing Cedarline Roofing deletes their HighLevel token and stops reading their channel. Their past tickets "
            "stay under the 12-month rule. Remove the support bot from the channel in Slack yourself." in words(page))


def test_sheet_shows_the_clients_history(settings, keys, key_pair, db):
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline"))

    # Newest first, in plain words, each with who and when (Bluejay's time zone; the machine time stays UTC).
    entries = [
        "Ana Reyes replaced the HighLevel token",
        "Ana Reyes changed the limit from 20 to 30 drafts an hour",
        "Ana Reyes resumed drafting",
        "Ben Cruz paused it: Key broken, “new key coming”",
        "Ana Reyes added the client",
    ]
    text = re.sub(r"<[^>]+>", "", page)  # the name is in <b>
    places = [text.index(entry) for entry in entries]
    assert places == sorted(places)
    assert '<time datetime="2026-10-24T16:45:00+00:00">Oct 24, 12:45 EDT</time>' in page
    assert "Client asked" not in page.split("Changes to this client")[1]  # BrightSmile's pause isn't here


def test_sheet_shows_this_hours_drafts_and_the_limit(settings, keys, key_pair, db):
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline"))

    assert "of 30 drafts this hour" in page
    assert 'value="30" data-client="cedarline" data-name="Cedarline Roofing"' in page  # the Clients screen's limit save


def test_a_client_with_no_changes_says_so(settings, keys, key_pair):
    db = FakeAdminDb(a_client("tala", "Tala Coffee"))
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/tala"))

    assert "No changes since it was added." in page


def test_after_a_pause_the_sheet_offers_undo(settings, keys, key_pair, db):
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/brightsmile?done=paused",
                                                                    headers=FROM_HERE))

    assert 'data-toast="Paused BrightSmile Dental." data-undo="undo"' in page
    assert '<form id="undo" method="post" action="/clients/brightsmile/resume" hidden>' in page


def test_undo_after_resume_pauses_again_as_it_was(settings, keys, key_pair, db):
    # Cedarline's last pause (history): Key broken, "new key coming".
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline?done=resumed",
                                                                    headers=FROM_HERE))

    assert 'data-toast="Resumed Cedarline Roofing." data-undo="undo"' in page
    undo = page.split('<form id="undo"', 1)[1].split("</form>", 1)[0]
    assert 'action="/clients/cedarline/pause"' in undo
    assert '<input type="hidden" name="reason" value="key_broken">' in undo
    assert '<input type="hidden" name="note" value="new key coming">' in undo


def test_undo_carries_the_change_it_undoes(settings, keys, key_pair, db):
    # BrightSmile's newest pause or resume is its pause of Oct 25, 11:00: Undo names it.
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/brightsmile?done=paused",
                                                                    headers=FROM_HERE))

    undo = page.split('<form id="undo"', 1)[1].split("</form>", 1)[0]
    assert '<input type="hidden" name="after" value="2026-10-25T11:00:00+00:00">' in undo


def test_undo_after_a_remove_and_re_add_changes_nothing(settings, keys, key_pair, db):
    """An Undo still open from before a remove and re-add names a change that no longer describes this client, so it
    must not pause the re-added one."""
    client = signed_in_client(settings, keys, key_pair, db)
    assert client.post("/clients/brightsmile/resume").headers["location"] == "/clients/brightsmile?done=resumed"
    undo = words(client.get("/clients/brightsmile?done=resumed", headers=FROM_HERE)).split('<form id="undo"', 1)[1].split("</form>", 1)[0]
    after = re.search(r'name="after" value="([^"]+)"', undo).group(1)
    row = next(row for row in db.rows if row.client_key == "brightsmile")
    time.sleep(0.05)  # later than the resume: Windows clocks can step 15 ms, and equal times would hide the change
    db.remove_client(1, "brightsmile")
    db.add_client(1, row.channel_id, row.display_name, row.location_id, 20, b"sealed")
    pauses = [change for key, change in db.history if key == "brightsmile" and change.action == "paused"]

    response = client.post("/clients/brightsmile/pause", data={"after": after, "reason": "client_asked", "note": ""})

    assert response.headers["location"] == "/clients/brightsmile?done=stale"
    assert next(row for row in db.rows if row.client_key == "brightsmile").status == "active"
    assert [change for key, change in db.history if key == "brightsmile" and change.action == "paused"] == pauses


def test_screens_say_token_never_key(settings, keys, key_pair, db):
    """One word throughout: "token", HighLevel's own ("Private Integration token")."""
    client = signed_in_client(settings, keys, key_pair, db)
    sheet = words(client.get("/clients/cedarline"))
    replacing = words(client.get("/clients/cedarline?confirm=key"))
    removing = words(client.get("/clients/cedarline?confirm=remove"))
    saved = words(client.get("/clients/cedarline?done=key_replaced", headers=FROM_HERE))

    assert '<span class="ds-card__row-text">Token</span>' in sheet and ">Replace token</a>" in sheet
    assert "Ana Reyes replaced the HighLevel token" in re.sub(r"<[^>]+>", "", sheet)  # its history (the name is in <b>)
    assert ">New HighLevel token</label>" in replacing and "Tested before it replaces the old token." in replacing
    assert ">Replace token</button>" in replacing
    assert "deletes their HighLevel token" in removing and "Deletes the token and stops drafting." in sheet
    assert 'data-toast="New HighLevel token saved for Cedarline Roofing."' in saved
    for page in (sheet, replacing, removing, saved):
        assert not re.search(r"HighLevel key|>Key<|Replace key|old key\b|[Tt]he key\b", page)


def test_database_down_during_a_pause_is_a_plain_503(settings, keys, key_pair, db, caplog):
    """Every log level is read: a login or address logged even at info level would fail this."""
    caplog.set_level("DEBUG")

    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.pause_client = down
    response = signed_in_client(settings, keys, key_pair, db).post(
        "/clients/cedarline/pause", data={"reason": "client_asked", "note": ""})

    assert response.status_code == 503
    assert "OperationalError" in caplog.text  # the log was read: its type is there
    assert "admin_app" not in response.text + caplog.text and "127.0.0.1" not in response.text + caplog.text


@pytest.mark.parametrize("change", ["resume", "pause"])
def test_undo_refuses_after_a_later_change(settings, keys, key_pair, db, change):
    # Ana's Undo names the Oct 21 change; Ben paused or resumed since: nothing is undone, and the sheet says so.
    client = "brightsmile" if change == "resume" else "cedarline"
    response = signed_in_client(settings, keys, key_pair, db).post(
        f"/clients/{client}/{change}",
        data={"after": "2026-10-21T10:30:00+00:00", "reason": "key_broken", "note": ""})

    assert response.status_code == 303
    assert response.headers["location"] == f"/clients/{client}?done=stale"
    assert db.calls == []


def test_undo_goes_ahead_when_nothing_changed_since(settings, keys, key_pair, db):
    response = signed_in_client(settings, keys, key_pair, db).post(
        "/clients/brightsmile/resume", data={"after": "2026-10-25T11:00:00+00:00"})

    assert response.headers["location"] == "/clients/brightsmile?done=resumed"
    assert db.calls == [("resume", 1, "brightsmile")]


def test_a_limit_change_since_doesnt_block_undo(settings, keys, key_pair, db):
    db.history.append(a_change("brightsmile", "limit_changed", "2026-10-26T09:00", detail="20 to 25"))
    response = signed_in_client(settings, keys, key_pair, db).post(
        "/clients/brightsmile/resume", data={"after": "2026-10-25T11:00:00+00:00"})

    assert response.headers["location"] == "/clients/brightsmile?done=resumed"


def test_the_last_change_is_asked_for_by_key_and_kind(monkeypatch):
    conn = ScriptedConn(None)
    postgres_db_on(conn, monkeypatch).last_change("cedarline", ("paused", "resumed"))

    [(sql, params)] = conn.statements
    assert "where client_key = %s and action = any(%s)" in sql and params == ("cedarline", ["paused", "resumed"])


def test_a_stale_undo_is_announced(settings, keys, key_pair, db):
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline?done=stale", headers=FROM_HERE))

    assert 'data-toast="Cedarline Roofing changed since, so nothing was undone."' in page
    assert "data-undo=" not in page


def test_undo_after_resume_finds_the_pause_behind_many_changes(settings, keys, key_pair, db):
    # 120 limit changes since Cedarline's pause push it out of the newest 100 the sheet lists.
    db.history += [a_change("cedarline", "limit_changed", f"2026-10-23T{hour:02d}:{minute:02d}", detail="30 to 31")
                   for hour in range(15, 23) for minute in range(0, 60, 4)]
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline?done=resumed",
                                                                    headers=FROM_HERE))

    undo = page.split('<form id="undo"', 1)[1].split("</form>", 1)[0]
    assert '<input type="hidden" name="reason" value="key_broken">' in undo


def test_a_new_key_is_announced_without_undo(settings, keys, key_pair, db):
    page = words(signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline?done=key_replaced",
                                                                    headers=FROM_HERE))

    assert 'data-toast="New HighLevel token saved for Cedarline Roofing."' in page
    assert 'data-undo=' not in page


@pytest.mark.parametrize("done", ["", "anything", "<b>Free money</b>"])
def test_only_known_changes_are_announced(settings, keys, key_pair, db, done):
    page = signed_in_client(settings, keys, key_pair, db).get(
        "/clients/cedarline", params={"done": done}, headers=FROM_HERE).text

    assert "data-toast=" not in page


def test_clients_link_to_each_sheet(settings, keys, key_pair, db):
    browser = signed_in_client(settings, keys, key_pair, db)
    for view in ("cards", "list"):
        page = browser.get(f"/clients?view={view}").text
        assert 'href="/clients/cedarline"' in page and 'href="/clients/brightsmile"' in page


def test_a_change_someone_else_already_made_just_shows_the_sheet(settings, keys, key_pair, db):
    # BrightSmile is already paused (another tab, another person): no error, no "Paused" message, the sheet as it is.
    response = signed_in_client(settings, keys, key_pair, db).post(
        "/clients/brightsmile/pause", data={"reason": "drafts_wrong", "note": ""})

    assert response.status_code == 303
    assert response.headers["location"] == "/clients/brightsmile"


# A message an address asks for is drawn only for a request one of the app's own pages made: a link sent by someone (a
# message, an email) or typed arrives as "cross-site" or "none", and a client's Undo behind it would pause or resume a client.
ADDRESSES = [  # (address, does it offer an Undo)
    ("/clients/brightsmile?done=paused", True), ("/clients/cedarline?done=resumed", True),
    ("/clients/cedarline?done=key_replaced", False), ("/clients/cedarline?done=stale", False),
]


@pytest.mark.parametrize("path, offers_undo", ADDRESSES)
@pytest.mark.parametrize("site", [None, "none", "cross-site", "same-site", ""],
                         ids=["no header", "typed", "another site", "same site", "empty"])
def test_a_message_in_an_address_needs_a_request_from_this_app(settings, keys, key_pair, db, path, offers_undo, site):
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    response = signed_in_client(settings, keys, key_pair, db).get(path, headers=headers)

    assert response.status_code == 200
    assert is_open(panel_tag(response.text))  # the panel itself still shows
    assert "data-toast=" not in response.text and "data-undo=" not in response.text and 'id="undo"' not in response.text


@pytest.mark.parametrize("path, offers_undo", ADDRESSES)
def test_a_message_in_an_address_is_drawn_for_a_request_from_this_app(settings, keys, key_pair, db, path, offers_undo):
    response = signed_in_client(settings, keys, key_pair, db).get(path, headers=FROM_HERE)

    assert "data-toast=" in response.text
    assert ('id="undo"' in response.text) is offers_undo and ("data-undo=" in response.text) is offers_undo


def test_no_undo_once_the_client_has_changed_again(settings, keys, key_pair, db):
    # "Paused" in the address, but Cedarline is active now: Undo would resume nothing.
    page = signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline?done=paused", headers=FROM_HERE).text

    assert 'data-undo=' not in page and 'id="undo"' not in page


@pytest.mark.parametrize("refusal, raised", [
    ("admin_client_status: unknown or removed client", ClientGone),
    ("admin_pause_client: the client is already paused", ClientUnchanged),
    ("admin_resume_client: the client is not paused", ClientUnchanged),
])
def test_the_databases_refusals_become_the_pages_answers(monkeypatch, refusal, raised):
    with pytest.raises(raised):
        postgres_db_on(ScriptedConn(psycopg.errors.RaiseException(refusal)), monkeypatch).pause_client(
            1, "cedarline", "client_asked", None)
    other = psycopg.errors.RaiseException("admin: only an active staff member can make changes")
    with pytest.raises(psycopg.errors.RaiseException):  # anything else stays a database error (503)
        postgres_db_on(ScriptedConn(other), monkeypatch).remove_client(1, "cedarline")


def test_history_is_asked_for_by_the_clients_key(monkeypatch):
    conn = ScriptedConn(None)
    conn.fetchall = lambda: []
    postgres_db_on(conn, monkeypatch).client_history("cedarline")

    [(sql, params)] = conn.statements
    assert "from admin_history where client_key = %s" in sql and params == ("cedarline",)


def with_channels(browser):
    """The browser's app asked for free channels (Add client) must never reach Slack."""
    browser.app.dependency_overrides[get_channel_lister] = lambda: lambda: [Channel("C0NEWA", "harbor-yoga")]
    return browser


def test_client_address_shows_the_panel_over_clients(settings, keys, key_pair, db):
    page = signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline").text

    assert 'id="results"' in page  # the list stays behind the panel
    tag = panel_tag(page)
    assert 'class="ds-sheet"' in tag and is_open(tag) and 'id="panel"' in tag
    assert "ds-sheet--static" not in page
    assert page.count('id="toast"') == 1 and page.count("<h1") == 1
    assert re.search(r'<h2[^>]*ds-sheet__title[^>]*>\s*Cedarline Roofing\s*</h2>', page)
    assert page.index('id="results"') < page.index("<dialog")  # the panel comes after the list


def test_the_panel_has_the_design_systems_grabber_and_section_headings(settings, keys, key_pair, db):
    page = signed_in_client(settings, keys, key_pair, db).get("/clients/cedarline").text
    panel = page[page.index("<dialog"):page.index("</dialog>")]

    assert '<div class="ds-sheet__grabber" aria-hidden="true"></div>' in panel
    # No heading over the Drafting and Remove client rows: each is its own row, and says so.
    assert re.findall(r'<h3[^>]*>([^<]+)</h3>', panel) == ["This hour", "HighLevel connection",
                                                             "Changes to this client"]
    # The Slack channel row (Change channel, migration 024) is a row of its own, like these: no heading over it.
    assert re.findall(r'<section class="ds-sheet__section" aria-label="([^"]+)"', panel) == [
        "Drafting", "Slack channel", "Remove client"]
    assert "<h2" in panel and panel.count("<h2") == 1


@pytest.mark.parametrize("path", ["/clients/cedarline", "/clients/cedarline?confirm=pause",
                                  "/clients/cedarline?confirm=key", "/clients/cedarline?confirm=remove",
                                  "/clients/cedarline?confirm=channel",
                                  "/clients/brightsmile", "/clients/cedarline?done=paused", "/clients/new"])
def test_panel_ids_are_unique(settings, keys, key_pair, db, path):
    page = with_channels(signed_in_client(settings, keys, key_pair, db)).get(path, headers=FROM_HERE).text

    twice = sorted({name for name in ids(page) if ids(page).count(name) > 1})
    assert twice == []
    assert "panel" in ids(page) and "toast" in ids(page)


def sheet_pause(browser):
    return browser.post("/clients/cedarline/pause", data={"reason": "", "note": ""})


def sheet_key(browser):
    return browser.post("/clients/cedarline/key", data={"key": "  "})


def sheet_remove(browser):
    return browser.post("/clients/cedarline/remove", data={"confirm": "Cedar"})


def sheet_key_waits(browser):
    for _ in range(5):
        assert browser.post("/clients/new/test", data={"location_id": "loc123", "key": GHL_KEY}).status_code == 200
    return browser.post("/clients/cedarline/key", data={"key": GHL_KEY})


@pytest.mark.parametrize("refuse, status", [(sheet_pause, 422), (sheet_key, 422), (sheet_remove, 422),
                                            (sheet_key_waits, 429)],
                         ids=["pause-422", "key-422", "remove-422", "key-429"])
def test_a_refused_form_answers_with_the_panel_alone(settings, keys, key_pair, db, refuse, status):
    response = refuse(signed_in_client(settings, keys, key_pair, db))
    page = response.text

    assert response.status_code == status
    assert 'id="results"' not in page and 'id="summary"' not in page  # not the list: it needs the database
    assert is_open(panel_tag(page)) and 'id="panel"' in panel_tag(page)
    assert page.count('id="toast"') == 1 and page.count('role="status"') >= 1
    levels = heading_levels(page)
    assert no_gap(levels, 2), f"headings {levels}"
    assert GHL_KEY not in page


def test_a_refused_key_adds_no_database_read_after_the_test(settings, keys, key_pair, db):
    reads, at_the_test = [], []
    for name in ("clients", "client_history", "last_change"):
        original = getattr(db, name)
        setattr(db, name, lambda *args, _original=original, _name=name: reads.append(_name) or _original(*args))

    def refused(location_id, sealed):
        at_the_test.append(len(reads))
        return "wrong_account"

    response = signed_in_client(settings, keys, key_pair, db, refused).post(
        "/clients/cedarline/key", data={"key": GHL_KEY})

    assert response.status_code == 422 and REFUSALS["wrong_account"] in words(response)
    assert at_the_test == [len(reads)] and reads  # the page was read before the test, and not again after it


def test_nothing_is_inside_noscript(settings, keys, key_pair, db):
    browser = with_channels(signed_in_client(settings, keys, key_pair, db))
    for path in ("/clients/cedarline", "/clients/cedarline?confirm=pause", "/clients/brightsmile",
                 "/clients/cedarline?confirm=key", "/clients/new"):
        assert "<noscript" not in browser.get(path).text, path
    active, paused = browser.get("/clients/cedarline").text, browser.get("/clients/brightsmile").text
    assert re.search(r'<div[^>]*data-no-script[^>]*>\s*<a[^>]*>Pause this client</a>', active)  # the row goes whole
    assert re.search(r'<button[^>]*data-no-script[^>]*>Resume drafting</button>', paused)
    assert dialog_tags(active) and dialog_tags(paused)


# ---- Change channel (migration 024): a client moves to another Slack channel from its panel ----

BOT_IS_IN = [Channel("C0CEDARLINE", "client-cedarline"), Channel("C0BRIGHTSMILE", "client-brightsmile"),
             Channel("C0GONE", "client-gone-plumbing"), Channel("C0NEWA", "harbor-yoga"),
             Channel("C0NEWB", "maple-dental"), Channel("C0OLDCEDAR", "cedarline-old"),
             Channel("C0OLDBS", "brightsmile-old")]


@pytest.fixture
def moves():
    """Cedarline (active), BrightSmile (paused), Gone Plumbing (removed, still holding its channel), and two channels
    clients have moved away from: Cedarline's own old one, and BrightSmile's."""
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing"),
                       a_client("brightsmile", "BrightSmile Dental", status="paused", reason="client_asked"),
                       a_client("gone", "Gone Plumbing", status="removed"),
                       past={"C0OLDCEDAR": "cedarline", "C0OLDBS": "brightsmile"})


def mover(settings, keys, key_pair, db, lister=lambda: BOT_IS_IN) -> TestClient:
    """The panel's browser with the bot's channels from `lister` (never Slack)."""
    browser = signed_in_client(settings, keys, key_pair, db)
    browser.app.dependency_overrides[get_channel_lister] = lambda: lister
    return browser


def offered(page: str, select_id: str = "move-channel") -> list[str]:
    """The channel IDs a <select> of the page offers, in order (the blank "Choose" option left out)."""
    select = re.search(rf'<select[^>]*id="{select_id}".*?</select>', page, re.S).group(0)
    return [value for value in re.findall(r'<option value="([^"]*)"', select) if value]


def unchanged(db):
    return list(db.rows), list(db.history), list(db.entries), dict(db.owners)


def test_change_channel_offers_only_unheld_channels(settings, keys, key_pair, moves):
    page = mover(settings, keys, key_pair, moves).get("/clients/cedarline?confirm=channel")

    assert page.status_code == 200
    # Not Cedarline's own channel, not an active, paused or removed client's, not BrightSmile's old one: the bot's
    # channels that no client row holds and that never belonged to another client. Cedarline's own old one stays.
    assert offered(page.text) == ["C0NEWA", "C0NEWB", "C0OLDCEDAR"]
    assert '<option value="C0NEWA">#harbor-yoga</option>' in page.text
    assert 'action="/clients/cedarline/channel"' in page.text and ">Move client</button>" in page.text
    assert re.search(r'<a class="ds-button" href="/clients/cedarline">Cancel</a>', page.text)


def test_add_client_still_offers_a_removed_clients_channel(settings, keys, key_pair, moves):
    page = mover(settings, keys, key_pair, moves).get("/clients/new")

    assert page.status_code == 200
    # Gone Plumbing's channel is offered: picking it brings that client back. Clients that hold theirs are not.
    assert "C0GONE" in offered(page.text, "add-channel")
    assert "C0CEDARLINE" not in offered(page.text, "add-channel") and "C0BRIGHTSMILE" not in offered(page.text, "add-channel")


def test_lists_leave_out_another_clients_old_channel(settings, keys, key_pair, moves):
    browser = mover(settings, keys, key_pair, moves)
    change = offered(browser.get("/clients/cedarline?confirm=channel").text)
    add = offered(browser.get("/clients/new").text, "add-channel")

    assert "C0OLDBS" not in change and "C0OLDBS" not in add  # BrightSmile's old channel is nobody else's
    assert "C0OLDCEDAR" in change  # Cedarline can go back to its own old channel
    assert "C0OLDCEDAR" not in add  # but a new client can't take it
    assert "C0GONE" in add and "C0GONE" not in change  # a removed client's current channel: Add brings it back
    assert add == ["C0GONE", "C0NEWA", "C0NEWB"]


def test_change_channel_moves_the_client(settings, keys, key_pair, moves):
    browser = mover(settings, keys, key_pair, moves)
    response = browser.post("/clients/cedarline/channel", data={"channel_id": "C0NEWA"})

    assert response.status_code == 303
    assert response.headers["location"] == "/clients/cedarline?done=channel_changed"
    assert moves.calls == [("change_channel", 1, "cedarline", "C0NEWA", "harbor-yoga")]  # by Ana, Slack's name
    row = next(row for row in moves.rows if row.client_key == "cedarline")
    assert (row.channel_id, row.channel_name) == ("C0NEWA", "harbor-yoga")
    assert moves.last_change("cedarline", ("channel_changed",)).detail == "C0CEDARLINE to #harbor-yoga"
    latest = words(browser.get("/clients")).split('id="change-line"')[1].split('<form id="find"')[0]
    assert "moved Cedarline Roofing to another Slack channel: C0CEDARLINE to #harbor-yoga" in latest  # Clients' line
    toast = words(browser.get("/clients/cedarline?done=channel_changed", headers=FROM_HERE))
    assert 'data-toast="Moved Cedarline Roofing to #harbor-yoga."' in toast
    assert "data-undo=" not in toast and 'id="undo"' not in toast  # moving back is the same action: no Undo
    assert '<span class="ds-sheet__sub">#harbor-yoga</span>' in toast


@pytest.mark.parametrize("hold_it, lister, chosen", [
    ("brightsmile", lambda: BOT_IS_IN, "C0NEWA"),                                  # a client took it since the panel opened
    (None, lambda: [c for c in BOT_IS_IN if c.id != "C0NEWA"], "C0NEWA"),          # the bot left it
    (None, lambda: BOT_IS_IN, "C0NOTINSLACK"),                                     # an ID the form made up
    (None, lambda: BOT_IS_IN, "C0OLDBS"),                                          # another client had it
    (None, lambda: BOT_IS_IN, "C0GONE"),                                           # a removed client holds it
], ids=["taken-since", "bot-left", "forged", "another-clients-old", "removed-clients"])
def test_change_channel_refuses_a_channel_no_longer_free(settings, keys, key_pair, moves, hold_it, lister, chosen):
    if hold_it:  # the panel was opened before this: the fresh read sees BrightSmile on it
        row = next(row for row in moves.rows if row.client_key == hold_it)
        moves.rows[moves.rows.index(row)] = replace(row, channel_id="C0NEWA")
        moves.owners["C0NEWA"] = hold_it
    before = unchanged(moves)
    response = mover(settings, keys, key_pair, moves, lister).post("/clients/cedarline/channel",
                                                                   data={"channel_id": chosen})

    assert response.status_code == 409
    assert NOT_FREE in words(response)
    assert moves.calls == [] and unchanged(moves) == before  # nothing saved, and the database was never asked
    assert is_open(panel_tag(response.text)) and 'id="move-channel"' in response.text  # the question stays, to choose again


@pytest.mark.parametrize("chosen", ["", "  "])
def test_change_channel_needs_a_channel_picked(settings, keys, key_pair, moves, chosen):
    response = mover(settings, keys, key_pair, moves).post("/clients/cedarline/channel", data={"channel_id": chosen})

    assert response.status_code == 422
    assert PICK_ERROR in words(response)
    assert moves.calls == []


def test_change_channel_to_its_own_channel_just_shows_the_panel(settings, keys, key_pair, moves):
    # Another tab already moved it there: no error, no toast, the panel as it is (as a second Pause does).
    response = mover(settings, keys, key_pair, moves).post("/clients/cedarline/channel",
                                                           data={"channel_id": "C0CEDARLINE"})

    assert response.status_code == 303 and response.headers["location"] == "/clients/cedarline"
    assert moves.calls == []


def test_change_channel_takes_the_name_from_slack(settings, keys, key_pair, moves):
    response = mover(settings, keys, key_pair, moves).post(
        "/clients/cedarline/channel", data={"channel_id": "C0NEWB", "channel_name": "#forged", "name": "forged"})

    assert response.status_code == 303
    assert moves.calls == [("change_channel", 1, "cedarline", "C0NEWB", "maple-dental")]  # Slack's, not the form's


def test_change_channel_refuses_when_slack_is_down(settings, keys, key_pair, moves, caplog):
    caplog.set_level("DEBUG")

    def down():
        raise SlackUnavailable("ratelimited")

    before = unchanged(moves)
    response = mover(settings, keys, key_pair, moves, down).post("/clients/cedarline/channel",
                                                                 data={"channel_id": "C0NEWA"})

    assert response.status_code == 503
    assert SLACK_DOWN in words(response)
    assert moves.calls == [] and unchanged(moves) == before
    assert "ratelimited" in caplog.text and "slack-test-bot-token" not in caplog.text + response.text  # the code, never the token
    page = mover(settings, keys, key_pair, moves, down).get("/clients/cedarline?confirm=channel")
    assert page.status_code == 200 and "Can't load the support bot's Slack channels right now." in words(page)
    assert ">Move client</button>" not in page.text  # nothing to move to


def test_a_channel_with_another_clients_tickets_is_refused(settings, keys, key_pair, moves):
    """The lists leave C0OLDBS out, but a stale view (another tab, another person) can still send it: the database
    refuses, and the page says so in words. The move and Add client both."""
    browser = mover(settings, keys, key_pair, moves)
    moves.channel_owners = lambda: {}  # what the request read before BrightSmile moved away from it
    before = unchanged(moves)

    moved = browser.post("/clients/cedarline/channel", data={"channel_id": "C0OLDBS"})
    added = browser.post("/clients/new", data={"channel_id": "C0OLDBS", "name": "Harbor Yoga", "location_id": "loc123",
                                               "limit": "25", "key": GHL_KEY})

    for refused in (moved, added):
        assert refused.status_code == 409
        assert USED_BY_ANOTHER in words(refused)
        assert GHL_KEY not in refused.text
    assert USED_BY_ANOTHER == "That channel belonged to another client. Use a new channel."
    assert unchanged(moves) == before  # nothing saved
    assert [call[0] for call in moves.calls] == ["change_channel", "add_client"]  # the database was asked, and refused


def test_change_channel_on_a_removed_client_is_not_found(settings, keys, key_pair, moves):
    browser = mover(settings, keys, key_pair, moves)

    assert browser.post("/clients/gone/channel", data={"channel_id": "C0NEWA"}).status_code == 404
    assert browser.get("/clients/gone?confirm=channel").status_code == 404
    assert browser.post("/clients/nobody/channel", data={"channel_id": "C0NEWA"}).status_code == 404
    assert moves.calls == []


def test_a_client_taken_between_the_check_and_the_move_is_a_refusal(settings, keys, key_pair, moves):
    # Two people move two clients to one channel in the same second: the second finds it held (or the database's key
    # refuses it). Either way the page says the channel isn't free, and the client stays where it was.
    def lost(*args):
        raise ClientTaken("C0NEWA")

    moves.change_channel = lost
    response = mover(settings, keys, key_pair, moves).post("/clients/cedarline/channel", data={"channel_id": "C0NEWA"})

    assert response.status_code == 409 and NOT_FREE in words(response)


def test_database_down_during_a_move_is_a_plain_503(settings, keys, key_pair, moves, caplog):
    caplog.set_level("DEBUG")

    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    moves.change_channel = down
    response = mover(settings, keys, key_pair, moves).post("/clients/cedarline/channel", data={"channel_id": "C0NEWA"})

    assert response.status_code == 503
    assert "OperationalError" in caplog.text
    assert "admin_app" not in response.text + caplog.text and "127.0.0.1" not in response.text + caplog.text


@pytest.mark.parametrize("bad", ["general", "c0lower", "C1", "", "C0NEWA\n", " C0NEWA"])
def test_the_fake_refuses_a_malformed_channel_id_as_the_database_does(moves, bad):
    """admin_change_channel checks ^[CG][A-Z0-9]{2,}$ (after the client, before the client's own channel), and the fake
    follows it: a free value that isn't a Slack channel ID is refused, and nothing changes."""
    before = unchanged(moves)

    with pytest.raises(psycopg.errors.RaiseException, match="must be a Slack channel ID"):
        moves.change_channel(1, "cedarline", bad, "name")
    with pytest.raises(ClientGone):  # the client is checked first
        moves.change_channel(1, "gone", bad, "name")
    assert unchanged(moves) == before


def test_a_malformed_channel_id_from_slack_saves_nothing(settings, keys, key_pair, moves):
    # A made-up Slack answer with an ID of no Slack shape: the database would refuse it, so the page says it failed.
    before = unchanged(moves)
    response = mover(settings, keys, key_pair, moves, lambda: [Channel("general", "general")]).post(
        "/clients/cedarline/channel", data={"channel_id": "general"})

    assert response.status_code == 503
    assert unchanged(moves) == before


def test_change_channel_confirm_row_without_the_script(settings, keys, key_pair, moves):
    browser = mover(settings, keys, key_pair, moves)
    sheet = browser.get("/clients/cedarline").text
    asked = browser.get("/clients/cedarline?confirm=channel").text

    # The panel's row: the channel, and a link that shows the question (no script needed).
    assert '<span class="ds-card__row-title">Slack channel</span><span class="ds-card__row-hint">C0CEDARLINE</span>' in sheet
    assert '<a class="ds-button" href="/clients/cedarline?confirm=channel">Change channel</a>' in sheet
    assert 'id="move-channel"' not in sheet
    # The question is a plain form, visible as sent: a blank first option picked, so a channel is chosen on purpose.
    form = re.search(r'<form[^>]*action="/clients/cedarline/channel"[^>]*>', asked).group(0)
    assert "hidden" not in form and 'method="post"' in form
    select = re.search(r'<select[^>]*id="move-channel".*?</select>', asked, re.S).group(0)
    assert re.search(r'<option value="" selected>Choose a channel</option>', select)
    assert 'name="channel_id"' in select and re.search(r'<label[^>]*for="move-channel"[^>]*>New channel</label>', asked)
    dialog = asked[asked.index("<dialog"):asked.index("</dialog>")]
    assert "<noscript" not in asked and "<script" not in dialog and "style=" not in dialog  # the page's security header
    assert 'href="/clients/cedarline?confirm=channel"' not in asked  # the link gave way to the question


def test_change_channel_gets_write_nothing(settings, keys, key_pair, moves):
    """No GET changes anything: the panel, the question and the toast's address call no writer, and leave the client and
    its history as they were (design: no GET changes anything)."""
    browser = mover(settings, keys, key_pair, moves)
    before = unchanged(moves)

    for path in ("/clients/cedarline", "/clients/cedarline?confirm=channel", "/clients/cedarline?done=channel_changed",
                 "/clients/new", "/clients"):
        assert browser.get(path, headers=FROM_HERE).status_code == 200, path

    assert moves.calls == [] and moves.noted == [] and unchanged(moves) == before


def test_a_channel_change_reads_in_history(settings, keys, key_pair, moves):
    moves.history.append(a_change("cedarline", "channel_changed", "2026-10-26T09:00", detail="#old-cedar to #<b>new</b>"))
    page = words(mover(settings, keys, key_pair, moves).get("/clients/cedarline"))

    text = re.sub(r"<[^>]+>", "", page)
    assert "Ana Reyes moved it to another Slack channel: #old-cedar to #&lt;b&gt;new&lt;/b&gt;" in text
    assert "<b>new</b>" not in page  # a name is text, never markup


def test_the_panel_has_one_row_for_the_channel(settings, keys, key_pair, moves):
    panel = signed_in_client(settings, keys, key_pair, moves).get("/clients/cedarline").text
    panel = panel[panel.index("<dialog"):panel.index("</dialog>")]

    assert re.findall(r'<section class="ds-sheet__section" aria-label="([^"]+)"', panel) == [
        "Drafting", "Slack channel", "Remove client"]
    assert panel.count("Change channel") == 1


def test_a_raced_channel_reads_as_taken(monkeypatch):
    """Should a claim on a channel still reach client_map's primary key (a backstop: the channel lock and client_channels
    stop races first, migration 024), the app reads that as taken."""
    race = psycopg.errors.UniqueViolation('duplicate key value violates unique constraint "client_map_pkey"')
    conn = ScriptedConn(race)
    with pytest.raises(ClientTaken):
        postgres_db_on(conn, monkeypatch).change_channel(1, "cedarline", "C0NEWA", "harbor-yoga")
    assert conn.statements == [("select admin_change_channel(%s, %s, %s, %s)", (1, "cedarline", "C0NEWA", "harbor-yoga"))]


def test_other_database_errors_stay_failures(monkeypatch):
    other = psycopg.errors.UniqueViolation('duplicate key value violates unique constraint "client_map_location_id_key"')
    with pytest.raises(psycopg.errors.UniqueViolation):  # not taken: a failure, so the page says 503
        postgres_db_on(ScriptedConn(other), monkeypatch).change_channel(1, "cedarline", "C0NEWA", None)
    unknown = psycopg.errors.RaiseException("admin: only an active staff member can make changes")
    with pytest.raises(psycopg.errors.RaiseException):
        postgres_db_on(ScriptedConn(unknown), monkeypatch).change_channel(1, "cedarline", "C0NEWA", None)
    shape = psycopg.errors.RaiseException("admin_change_channel: the channel must be a Slack channel ID")
    with pytest.raises(psycopg.errors.RaiseException):
        postgres_db_on(ScriptedConn(shape), monkeypatch).change_channel(1, "cedarline", "general", None)


@pytest.mark.parametrize("refusal, raised", [
    ("admin: unknown or removed client", ClientGone),
    ("admin_change_channel: the client is already on that channel", ClientUnchanged),
    ("admin_change_channel: that channel already belongs to a client", ClientTaken),
    ("admin_change_channel: that channel belonged to another client", ChannelUsedByAnother),
])
def test_the_databases_move_refusals_become_the_pages_answers(monkeypatch, refusal, raised):
    with pytest.raises(raised) as caught:
        postgres_db_on(ScriptedConn(psycopg.errors.RaiseException(refusal)), monkeypatch).change_channel(
            1, "cedarline", "C0NEWA", None)
    if raised is ChannelUsedByAnother:
        assert isinstance(caught.value, ClientTaken)  # the same family: anything that handles "taken" handles it


def test_add_clients_refusal_for_a_channel_another_client_had_is_its_own(monkeypatch):
    refusal = psycopg.errors.RaiseException("admin_add_client: that channel belonged to another client")
    with pytest.raises(ChannelUsedByAnother):
        postgres_db_on(ScriptedConn(refusal), monkeypatch).add_client(1, "C0NEWA", "Harbor Yoga", "loc123", 20, b"x")


def test_the_channels_clients_have_had_are_read_from_client_channels(monkeypatch):
    conn = ScriptedConn(None)
    conn.fetchall = lambda: [{"channel_id": "C0A", "client_key": "cedarline"}, {"channel_id": "C0B", "client_key": "gone"}]

    owners = postgres_db_on(conn, monkeypatch).channel_owners()

    assert owners == {"C0A": "cedarline", "C0B": "gone"}
    assert conn.statements == [("select channel_id, client_key from client_channels", None)]
