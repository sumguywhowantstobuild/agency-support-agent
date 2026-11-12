"""The hourly channel-name timer: it reads the bot's channels from Slack and notes every
client's channel name, so no page ever waits on Slack and opening a page never changes anything. Slack and the database
are fakes; db/checks/023 covers admin_note_channel_names itself."""

import asyncio
import logging
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient

from support_agent.admin import app as app_module
from support_agent.admin import db as admin_db
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.channel_names import refresh_channel_names, run_timer, start_timer, stop_timer
from support_agent.admin.db import get_admin_db, name_noter
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, SlackUnavailable, bot_channels, get_channel_lister
from conftest import AUDIENCE
from fakes import FakeAdminDb, ScriptedConn, a_client
from mint_test_pass import sign_pass

log = logging.getLogger("support_agent.admin.channel_names")
TOKEN = "slack-test-bot-token"


@pytest.fixture
def db():
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing"),
                       a_client("brightsmile", "BrightSmile Dental", status="paused", reason="client_asked"))


def noter_for(db):
    """A name_noter() stand-in: a connection that offers only note_channel_names."""
    @contextmanager
    def noter():
        yield db
    return noter


def bot_is_in(*pairs):
    return lambda: [Channel(channel_id, name) for channel_id, name in pairs]


def test_the_timer_notes_every_clients_channel_name(db):
    lister = bot_is_in(("C0CEDARLINE", "cedarline-roofing"), ("C0BRIGHTSMILE", "brightsmile-support"),
                       ("C0NOCLIENT", "not-a-client"))

    refresh_channel_names(lister, noter_for(db), log)

    # One call with the bot's whole list; the database function ignores an ID that is no client's.
    assert db.noted == [{"C0CEDARLINE": "cedarline-roofing", "C0BRIGHTSMILE": "brightsmile-support",
                         "C0NOCLIENT": "not-a-client"}]
    assert {row.client_key: row.channel_name for row in db.rows} == {"cedarline": "cedarline-roofing",
                                                                     "brightsmile": "brightsmile-support"}


@pytest.fixture
def pages(settings, keys, db):
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    return TestClient(app, headers=signed_in)


def test_a_renamed_channel_shows_its_new_name_after_a_run(pages, db):
    refresh_channel_names(bot_is_in(("C0CEDARLINE", "cedarline-old")), noter_for(db), log)
    assert '<span class="ds-card__sub">#cedarline-old</span>' in pages.get("/clients").text

    refresh_channel_names(bot_is_in(("C0CEDARLINE", "cedarline-new")), noter_for(db), log)  # renamed in Slack

    page = pages.get("/clients").text
    assert '<span class="ds-card__sub">#cedarline-new</span>' in page
    assert "cedarline-old" not in page
    assert '<span class="ds-card__sub">C0BRIGHTSMILE</span>' in page  # a channel the bot isn't in keeps its code


def test_the_timer_runs_a_minute_after_start_then_hourly_even_after_a_failure(db, caplog):
    waits = []
    runs = []
    answers = iter([SlackUnavailable("ratelimited"), [Channel("C0CEDARLINE", "cedarline-roofing")],
                    SlackUnavailable("ReadTimeout"), [Channel("C0CEDARLINE", "cedarline-roofing")]])

    def lister():
        answer = next(answers)
        runs.append(answer)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def wait(seconds):  # a fake sleep: nobody waits; True stops the timer after the fourth run
        waits.append(seconds)
        return len(waits) > 4

    run_timer(lister, noter_for(db), log, wait)

    assert waits == [60, 3600, 3600, 3600, 3600]  # a minute after start, then the hour, a failed run included
    assert len(runs) == 4  # the first run failed and the timer still went on
    assert len(db.noted) == 2 and db.noted[0] == {"C0CEDARLINE": "cedarline-roofing"}


def test_one_timer_runs_at_a_time(db):
    asked = []
    release = threading.Event()

    def wait(seconds):
        asked.append(seconds)
        return release.wait(5)  # stays asleep until the test lets go

    try:
        start_timer(bot_is_in(), noter_for(db), log, wait=wait)
        start_timer(bot_is_in(), noter_for(db), log, wait=wait)  # a second start while one is running does nothing
        deadline = time.monotonic() + 5
        while not asked and time.monotonic() < deadline:
            time.sleep(0.01)
        assert asked == [60]
        assert [thread.name for thread in threading.enumerate()].count("channel-names") == 1
    finally:
        release.set()
        stop_timer()
    assert "channel-names" not in [thread.name for thread in threading.enumerate()]


def slack_answers(answer):
    return lambda: bot_channels(TOKEN, httpx2.Client(transport=httpx2.MockTransport(answer)))


def times_out(request):
    raise httpx2.ReadTimeout("slow, with https://slack.com/api/users.conversations?token=" + TOKEN, request=request)


def db_refuses(names):
    raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")


@pytest.mark.parametrize("answer, code", [
    (lambda request: httpx2.Response(200, json={"ok": False, "error": "missing_scope"}), "missing_scope"),
    (times_out, "ReadTimeout"),
    (lambda request: httpx2.Response(200, json={"ok": True}), "unexpected answer"),
], ids=["missing-scope", "timeout", "odd-shape"])
def test_a_slack_failure_in_the_timer_keeps_the_names_and_logs_only_the_error_code(db, caplog, answer, code):
    caplog.set_level("DEBUG")
    refresh_channel_names(bot_is_in(("C0CEDARLINE", "cedarline-roofing")), noter_for(db), log)  # names known
    caplog.clear()

    refresh_channel_names(slack_answers(answer), noter_for(db), log)  # must not raise

    assert code in caplog.text
    assert TOKEN not in caplog.text and "cedarline-roofing" not in caplog.text and "C0CEDARLINE" not in caplog.text
    assert len(db.noted) == 1  # nothing more was written: the names stay
    assert db.rows[0].channel_name == "cedarline-roofing"


def test_a_database_failure_in_the_timer_logs_only_the_error_type(db, caplog):
    caplog.set_level("DEBUG")
    db.note_channel_names = db_refuses

    refresh_channel_names(bot_is_in(("C0CEDARLINE", "cedarline-roofing")), noter_for(db), log)

    assert "OperationalError" in caplog.text
    assert "password" not in caplog.text and "127.0.0.1" not in caplog.text and "cedarline-roofing" not in caplog.text


def test_a_run_logs_only_how_many_names_changed(db, caplog):
    caplog.set_level("DEBUG")
    lister = bot_is_in(("C0CEDARLINE", "cedarline-roofing"), ("C0BRIGHTSMILE", "brightsmile-support"))

    refresh_channel_names(lister, noter_for(db), log)
    first = caplog.text
    caplog.clear()
    refresh_channel_names(lister, noter_for(db), log)  # nothing differs the second time

    assert re.search(r"\b2\b", first) and re.search(r"\b0\b", caplog.text)
    for text in (first, caplog.text):
        assert "cedarline" not in text and "brightsmile" not in text and "C0" not in text


def test_opening_clients_never_calls_slack(pages, db):
    def never():
        raise AssertionError("a page called Slack")

    pages.app.dependency_overrides[get_channel_lister] = lambda: never
    for address in ("/clients", "/clients?view=list", "/clients/cedarline"):
        assert pages.get(address).status_code == 200

    assert db.noted == [] and db.calls == []  # and nothing was written


def test_the_app_starts_and_stops_the_timer(settings, monkeypatch):
    events = []
    monkeypatch.setattr(app_module, "get_admin_settings", lambda: events.append("settings") or settings)
    monkeypatch.setattr(app_module, "start_timer", lambda lister, noter: events.append(("start", lister, noter)))
    monkeypatch.setattr(app_module, "stop_timer", lambda: events.append("stop"))

    with TestClient(create_app()):
        events.append("serving")

    assert [event if isinstance(event, str) else event[0] for event in events] == ["settings", "start", "serving",
                                                                                  "stop"]
    assert callable(events[1][1]) and events[1][2] is name_noter  # the only writer the timer is given


def test_the_timer_stops_even_when_the_app_stops_with_an_error(settings, monkeypatch):
    events = []
    monkeypatch.setattr(app_module, "get_admin_settings", lambda: settings)
    monkeypatch.setattr(app_module, "start_timer", lambda lister, noter: events.append("start"))
    monkeypatch.setattr(app_module, "stop_timer", lambda: events.append("stop"))

    async def serve_and_fail():
        async with app_module._lifespan(create_app()):
            raise RuntimeError("the server failed while running")

    # Its own thread, so its own event loop: a browser test before it on the same worker leaves a loop running in the
    # main thread, where asyncio.run refuses with a RuntimeError of its own (seen in a whole-suite run).
    raised = []
    def run():
        try:
            asyncio.run(serve_and_fail())
        except RuntimeError as error:
            raised.append(str(error))
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=10)

    assert raised == ["the server failed while running"]
    assert events == ["start", "stop"]


def test_bad_settings_stop_the_start_before_any_timer(monkeypatch):
    events = []

    def bad():
        raise ValueError("a missing setting")

    monkeypatch.setattr(app_module, "get_admin_settings", bad)
    monkeypatch.setattr(app_module, "start_timer", lambda lister, noter: events.append("start"))
    monkeypatch.setattr(app_module, "stop_timer", lambda: events.append("stop"))

    with pytest.raises(ValueError):
        with TestClient(create_app()):
            pass

    assert events == []


def test_only_the_timer_and_db_use_the_name_noter():
    """The writer outside a POST is for the timer alone: name_noter appears in db.py (it lives there), the timer's
    module and app.py (which hands it to the timer), nowhere else."""
    admin = Path(admin_db.__file__).parent
    users = {path.name for path in admin.rglob("*.py") if "name_noter" in path.read_text(encoding="utf-8")}
    assert users == {"db.py", "channel_names.py", "app.py"}


def test_the_name_noter_offers_only_note_channel_names(monkeypatch):
    """A writable connection (no read-only statement) that gives the timer one method and no way to the database."""
    conn = ScriptedConn({"changed": 2})

    class Pool:
        @contextmanager
        def connection(self):
            yield conn

    monkeypatch.setattr(admin_db, "_pool", lambda: Pool())
    with name_noter() as noter:
        assert noter.note_channel_names({"C0CEDARLINE": "cedarline-roofing"}) == 2
        assert [name for name in dir(noter) if not name.startswith("_")] == ["note_channel_names"]

    [(sql, params)] = conn.statements
    assert "admin_note_channel_names" in sql and "set transaction read only" not in sql
    assert params[0].obj == {"C0CEDARLINE": "cedarline-roofing"}  # sent as one JSON object


def test_the_post_connection_notes_names_through_the_checked_function(monkeypatch):
    conn = ScriptedConn({"changed": 1})
    changed = admin_db.PostgresAdminDb(_pool_of(conn, monkeypatch), read_only=False).note_channel_names(
        {"C0NEWA": "harbor-yoga"})

    assert changed == 1
    [(sql, params)] = conn.statements
    assert "admin_note_channel_names" in sql and params[0].obj == {"C0NEWA": "harbor-yoga"}


def _pool_of(conn, monkeypatch):
    class Pool:
        @contextmanager
        def connection(self):
            yield conn

    monkeypatch.setattr(admin_db, "_pool", lambda: Pool())
    return admin_db._pool()
