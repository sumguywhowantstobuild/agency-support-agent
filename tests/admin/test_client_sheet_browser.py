"""The client sheet's script in a real browser (Chromium through Playwright): the Drafting switch asks before it pauses,
resumes at once, Undo in the toast, and the limit saved where it is shown. The app runs on 127.0.0.1 with the fake
database. Watch a run: pytest tests/admin/test_client_sheet_browser.py --headed --slowmo 500"""

import pytest
from playwright.sync_api import Page, expect

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, get_channel_lister
from conftest import AUDIENCE
from fakes import FakeAdminDb, a_change, a_client
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)

BOT_IS_IN = [Channel("C0CEDARLINE", "client-cedarline"), Channel("C0BRIGHTSMILE", "client-brightsmile"),
             Channel("C0NEWA", "harbor-yoga"), Channel("C0NEWB", "maple-dental")]


@pytest.fixture
def db():
    history = (a_change("brightsmile", "paused", "2026-10-25T11:00", detail="client_asked: office move",
                        reason="client_asked", note="office move"),)
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
                       a_client("brightsmile", "BrightSmile Dental", status="paused", reason="client_asked",
                                note="office move"), history=history)


@pytest.fixture
def page(browser, settings, keys, db) -> Page:
    def make_app(url):
        app = create_app()
        here = settings.model_copy(update={"admin_origin": url})
        app.dependency_overrides[get_admin_settings] = lambda: here
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        app.dependency_overrides[get_channel_lister] = lambda: lambda: BOT_IS_IN  # never Slack
        return app

    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    with serving(make_app) as url:
        context = browser.new_context(base_url=url, extra_http_headers=signed_in)
        context.set_default_timeout(5_000)
        yield context.new_page()
        context.close()


def status_of(db, client_key):
    return next(row for row in db.rows if row.client_key == client_key).status


def test_turning_drafting_off_asks_for_a_reason_then_pauses(page, db):
    page.goto("/clients")
    page.locator("main").get_by_role("link", name="Cedarline Roofing").click()  # the client's tile on the page
    expect(page).to_have_url("/clients/cedarline")
    question = page.locator("#pause-confirm")
    expect(question).to_be_hidden()

    page.get_by_role("switch", name="Drafting").click()
    expect(question).to_be_visible()
    expect(page.get_by_label("Reason")).to_be_focused()
    assert status_of(db, "cedarline") == "active"  # nothing yet: the switch only asks

    page.get_by_label("Reason").select_option(label="Drafts going wrong")
    page.get_by_role("button", name="Pause client").click()
    expect(page).to_have_url("/clients/cedarline")  # the message is said once: a reload doesn't repeat it
    expect(page.locator(".ds-toast__text")).to_have_text("Paused Cedarline Roofing.")
    assert status_of(db, "cedarline") == "paused"

    page.locator(".ds-toast").get_by_role("button", name="Undo").click()
    expect(page.locator(".ds-toast__text")).to_have_text("Resumed Cedarline Roofing.")
    assert status_of(db, "cedarline") == "active"


def test_keep_drafting_puts_the_switch_back(page, db):
    page.goto("/clients/cedarline")
    switch = page.get_by_role("switch", name="Drafting")
    switch.click()
    page.get_by_role("link", name="Keep drafting").click()

    expect(page.locator("#pause-confirm")).to_be_hidden()
    expect(switch).to_be_checked()
    expect(switch).to_be_focused()
    expect(page).to_have_url("/clients/cedarline")
    assert db.calls == []


def test_resuming_from_the_switch_and_undo_pauses_as_it_was(page, db):
    page.goto("/clients/brightsmile")
    page.get_by_role("switch", name="Drafting").click()

    expect(page.locator(".ds-toast__text")).to_have_text("Resumed BrightSmile Dental.")
    assert status_of(db, "brightsmile") == "active"
    page.locator(".ds-toast").get_by_role("button", name="Undo").click()
    expect(page.locator(".ds-toast__text")).to_have_text("Paused BrightSmile Dental.")
    assert db.calls[-1] == ("pause", 1, "brightsmile", "client_asked", "office move")


def test_a_pause_and_its_undo_never_load_a_page(page, db):
    """The sheet's changes are sent by the script and the panel is filled from the answer, so the page
    (a mark on window) is the same one throughout, and the address never gains ?done."""
    page.goto("/clients/cedarline")
    page.evaluate("window.__kept = true")
    page.get_by_role("switch", name="Drafting").click()
    page.get_by_label("Reason").select_option(label="Drafts going wrong")
    page.get_by_role("button", name="Pause client").click()
    expect(page.locator(".ds-toast__text")).to_have_text("Paused Cedarline Roofing.")
    expect(page.locator(".ds-sheet__head .ds-status__label")).to_have_text("Paused")

    page.locator(".ds-toast").get_by_role("button", name="Undo").click()
    expect(page.locator(".ds-toast__text")).to_have_text("Resumed Cedarline Roofing.")
    expect(page.locator(".ds-sheet__head .ds-status__label")).to_have_text("Drafting")
    expect(page).to_have_url("/clients/cedarline")
    assert page.evaluate("window.__kept === true")
    assert status_of(db, "cedarline") == "active"


def test_resuming_from_the_switch_stays_on_the_page(page, db):
    page.goto("/clients/brightsmile")
    page.evaluate("window.__kept = true")
    page.get_by_role("switch", name="Drafting").click()

    expect(page.locator(".ds-sheet__head .ds-status__label")).to_have_text("Drafting")
    expect(page.locator(".ds-toast__text")).to_have_text("Resumed BrightSmile Dental.")
    expect(page).to_have_url("/clients/brightsmile")
    assert page.evaluate("window.__kept === true")


def test_the_limit_saves_on_the_sheet(page, db):
    page.goto("/clients/cedarline")
    limit = page.get_by_role("textbox", name="Draft limit")
    limit.fill("35")
    limit.press("Enter")

    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(page.locator(".ds-card__use-of")).to_have_text("of 35 drafts this hour")
    assert db.calls == [(1, "cedarline", 35)]


def test_change_channel_from_the_panel(page, db):
    """Open the client, Change channel, pick, Move: the toast and the panel's new #name. Keyboard only, at the narrowest
    phone width. (test_panel_browser.py has the same flow's in-place checks: no page load, history, focus.)"""
    page.set_viewport_size({"width": 320, "height": 700})
    page.goto("/clients/cedarline")
    expect(page.locator(".ds-sheet__sub")).to_have_text("C0CEDARLINE")  # the ID shows until a name is known
    page.get_by_role("link", name="Change channel").focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url("/clients/cedarline?confirm=channel")

    channel = page.get_by_label("New channel")
    expect(channel).to_be_visible()
    channel.focus()
    page.keyboard.press("Enter")  # opens the list of channels
    page.keyboard.press("ArrowDown")  # from "Choose a channel" to the first one the bot is in that is free
    page.keyboard.press("Enter")
    expect(channel).to_have_value("C0NEWA")
    page.keyboard.press("Tab")
    move = page.get_by_role("button", name="Move client")
    expect(move).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")  # nothing sideways at 320 px
    box = move.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320
    page.keyboard.press("Enter")

    expect(page.locator(".ds-toast__text")).to_have_text("Moved Cedarline Roofing to #harbor-yoga.")
    expect(page.locator(".ds-sheet__sub")).to_have_text("#harbor-yoga")
    expect(page.get_by_role("link", name="Change channel")).to_be_visible()  # the row is back; the question is gone
    expect(page.get_by_label("New channel")).to_have_count(0)
    assert db.calls == [("change_channel", 1, "cedarline", "C0NEWA", "harbor-yoga")]
    assert status_of(db, "cedarline") == "active"
