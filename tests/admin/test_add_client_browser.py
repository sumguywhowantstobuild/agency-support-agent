"""Add client's script in a real browser (Chromium through Playwright): the Test button, and the message on Clients after
a save. The app runs on 127.0.0.1 with the fake database, agent and Slack. Watch a run:
pytest tests/admin/test_add_client_browser.py --headed --slowmo 500"""

import threading

import pytest
from playwright.sync_api import Page, expect
from pydantic import SecretStr

from support_agent.admin.agent_client import get_connection_checker
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.check_cap import CheckCap, get_check_cap
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, get_channel_lister
from support_agent.keys import make_key_pair
from conftest import AUDIENCE
from fakes import FakeAdminDb, a_client
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)

GHL_KEY = "pit-00000000-harbor-yoga-test-key"  # made up


class Verdicts:
    """The agent's connection test: answers works for the one right pair, wrong_account for anything else."""

    def __init__(self):
        self.calls = []
        self.go = threading.Event()  # cleared by a test to hold the answer back
        self.go.set()

    def __call__(self, location_id: str, sealed: bytes) -> str:
        self.calls.append(location_id)
        self.go.wait(10)
        return "works" if location_id == "loc123" else "wrong_account"


@pytest.fixture
def db():
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing", used=12, limit=30))


@pytest.fixture
def agent():
    verdicts = Verdicts()
    yield verdicts
    verdicts.go.set()  # nothing left waiting after the test


@pytest.fixture
def page(browser, settings, keys, db, agent) -> Page:
    public, _ = make_key_pair()

    def make_app(url):
        app = create_app()
        here = settings.model_copy(update={"admin_origin": url, "ghl_key_public": SecretStr(public)})
        app.dependency_overrides[get_admin_settings] = lambda: here
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        app.dependency_overrides[get_connection_checker] = lambda: agent
        cap = CheckCap()  # this test's own, not the app's shared one
        app.dependency_overrides[get_check_cap] = lambda: cap
        app.dependency_overrides[get_channel_lister] = lambda: lambda: [Channel("C0NEWA", "harbor-yoga")]
        return app

    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    with serving(make_app) as url:
        context = browser.new_context(base_url=url, extra_http_headers=signed_in)
        context.set_default_timeout(5_000)
        yield context.new_page()
        context.close()


def fill_highlevel(page: Page, location_id: str):
    page.get_by_label("HighLevel location ID").fill(location_id)
    page.get_by_label("HighLevel token").fill(GHL_KEY)


def test_the_test_button_says_the_key_works(page, db, agent):
    page.goto("/clients/new")
    fill_highlevel(page, "loc123")
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text("Connected to this HighLevel account.")
    assert agent.calls == ["loc123"] and db.calls == []  # tested, nothing saved


def test_the_test_button_says_when_the_key_is_refused(page, db, agent):
    page.goto("/clients/new")
    fill_highlevel(page, "locOTHER")
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text(
        "This token doesn't open that HighLevel account. Check the location id and the token.")
    expect(page.get_by_label("HighLevel token")).to_have_value(GHL_KEY)  # still typed, to fix the location id
    assert db.calls == []


def test_an_answer_for_details_changed_while_testing_isnt_shown(page, agent):
    page.goto("/clients/new")
    fill_highlevel(page, "loc123")
    agent.go.clear()
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text("Testing the connection…")

    page.get_by_label("HighLevel token").fill("pit-another-key-typed-meanwhile")
    agent.go.set()  # "works" for the key typed before

    expect(page.get_by_role("button", name="Test")).to_be_enabled()
    expect(page.locator("#add-test-result")).to_have_text("")


def test_an_answer_goes_once_the_details_change(page, agent):
    page.goto("/clients/new")
    fill_highlevel(page, "loc123")
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text("Connected to this HighLevel account.")

    page.get_by_label("HighLevel location ID").fill("locOTHER")
    expect(page.locator("#add-test-result")).to_have_text("")


def test_the_test_button_marks_a_blank_key_without_asking_the_agent(page, agent):
    page.goto("/clients/new")
    page.get_by_label("HighLevel location ID").fill("loc123")
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text("Paste the client's HighLevel token.")
    assert agent.calls == []


def test_saving_closes_the_panel_without_loading_a_page(page, db):
    """The save is sent by the script, the panel closes over the same Clients page, and the new client is
    in the list (a mark on window survives)."""
    page.goto("/clients")
    page.evaluate("window.__kept = true")
    page.get_by_role("link", name="Add client").click()
    page.get_by_label("Client name").fill("Harbor Yoga")
    fill_highlevel(page, "loc123")
    page.get_by_role("button", name="Add client").click()

    expect(page.locator("dialog:modal")).to_have_count(0)
    expect(page.get_by_role("textbox", name="Limit Harbor Yoga")).to_have_value("20")
    expect(page).to_have_url("/clients")
    assert page.evaluate("window.__kept === true")


def test_a_key_the_agent_refuses_keeps_what_was_typed(page, db, agent):
    page.goto("/clients")
    page.evaluate("window.__kept = true")
    page.get_by_role("link", name="Add client").click()
    page.get_by_label("Client name").fill("Harbor Yoga")
    fill_highlevel(page, "locOTHER")  # the agent says wrong_account for any location but loc123
    page.get_by_role("button", name="Add client").click()

    alert = page.locator("dialog#panel .ds-alert")
    expect(alert).to_have_text("This token doesn't open that HighLevel account. Check the location id and the token.")
    expect(alert).to_be_focused()
    expect(page.get_by_label("Client name")).to_have_value("Harbor Yoga")
    expect(page.get_by_label("HighLevel location ID")).to_have_value("locOTHER")
    expect(page.get_by_label("HighLevel token")).to_have_value("")  # the key is never sent back
    assert page.evaluate("window.__kept === true")
    assert db.calls == []


def test_saving_comes_back_to_clients_with_a_message(page, db):
    page.goto("/clients")
    page.get_by_role("link", name="Add client").click()
    page.get_by_label("Client name").fill("Harbor Yoga")
    fill_highlevel(page, "loc123")
    page.get_by_role("button", name="Add client").click()

    expect(page).to_have_url("/clients")  # the message is said once: a reload doesn't repeat it
    expect(page.locator(".ds-toast__text")).to_have_text("Added Harbor Yoga. It drafts from its next message.")
    expect(page.locator(".ds-toast__region[role=status]")).to_have_text(
        "Added Harbor Yoga. It drafts from its next message.")
    expect(page.get_by_role("textbox", name="Limit Harbor Yoga")).to_have_value("20")
