"""The client panel in real browsers (Chromium, WebKit and Firefox through Playwright). The server sends the panel over
Clients, so a limit shows in two places, and every form still works when the script does not run. The script opens and
closes the panel (a modal over the list), keeps the address and the history in step, and keeps the token out of the page
once it is closed. The forms and links inside the panel save and refuse without leaving it. On a phone the panel
follows a finger down and closes. The app runs on 127.0.0.1 with the fake database, and the connection test,
the check cap and the channel lister replaced, so no real Slack or agent call is made. Slow and failing answers come
from holding or refusing the request with page.route, never from timing. Watch a run:
pytest tests/admin/test_panel_browser.py --headed --slowmo 500"""

import re
import secrets
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect
from pydantic import SecretStr
from starlette.responses import RedirectResponse

from support_agent.admin.add_client import SLACK_DOWN, TAKEN_ERROR, WAIT
from support_agent.admin.agent_client import get_connection_checker
from support_agent.admin.app import create_app
from support_agent.admin.auth import NOT_FROM_HERE, FileKeys, get_key_source
from support_agent.admin.check_cap import CHECKS_PER_MINUTE, CheckCap, get_check_cap
from support_agent.admin.client_sheet import CHANGE_DOWN, GONE
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, SlackUnavailable, get_channel_lister
from support_agent.keys import make_key_pair
from conftest import AUDIENCE
from fakes import FakeAdminDb, a_client
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)

GHL_KEY = "pit-" + secrets.token_hex(8)  # made up at test time; the shape of a HighLevel private integration key


class Agent:
    """The agent's connection test: one verdict a test can change, and what it was asked."""

    def __init__(self):
        self.verdict = "works"
        self.calls = []

    def __call__(self, location_id: str, sealed: bytes) -> str:
        self.calls.append(location_id)
        return self.verdict


class Channels:
    """The channel lister: the channels the bot is in, which a test can change, and whether Slack is down. (Add client
    leaves out the clients' own, so a test lists only channels no client has.)"""

    def __init__(self):
        self.free = [Channel("C0NEWA", "harbor-yoga")]
        self.down = False

    def __call__(self) -> list[Channel]:
        if self.down:
            raise SlackUnavailable("test")
        return list(self.free)


@pytest.fixture
def db():
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
                       a_client("brightsmile", "BrightSmile Dental", status="paused", reason="client_asked",
                                note="office move"))


@pytest.fixture
def agent():
    return Agent()


@pytest.fixture
def channels():
    return Channels()


@pytest.fixture
def cap():
    return CheckCap()  # this test's own, not the app's shared one


@pytest.fixture
def pause_goes_to():
    """Test only: where the redirect after a pause points instead of the client's panel (its own address when empty). The
    client panel has no save that leaves the Replace token form open, so a test sends the answer to the panel with it."""
    return []


@pytest.fixture
def new_page(browser, settings, keys, db, agent, channels, cap, pause_goes_to):
    """new_page(**context_options) -> a signed-in page in a context of its own (no-script ones included)."""
    public, _ = make_key_pair()

    def make_app(url):
        app = create_app()

        @app.middleware("http")
        async def send_the_pause_elsewhere(request, call_next):
            response = await call_next(request)
            if pause_goes_to and request.method == "POST" and request.url.path.endswith("/pause") and "location" in response.headers:
                response.headers["location"] = pause_goes_to[0]
            return response

        here = settings.model_copy(update={"admin_origin": url, "ghl_key_public": SecretStr(public)})
        app.dependency_overrides[get_admin_settings] = lambda: here
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        app.dependency_overrides[get_connection_checker] = lambda: agent
        app.dependency_overrides[get_check_cap] = lambda: cap
        app.dependency_overrides[get_channel_lister] = lambda: channels
        # Test only: the expired pass's answer. Playwright's WebKit refuses to fulfil a redirect, so a test sends the fetch here.
        app.add_api_route(REDIRECT_PATH, lambda: RedirectResponse(OTHER_ORIGIN, status_code=302), methods=["GET", "POST"])
        return app

    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    contexts = []
    with serving(make_app) as url:
        def make(**context_options) -> Page:
            context = browser.new_context(base_url=url, extra_http_headers=signed_in, **context_options)
            context.set_default_timeout(5_000)
            contexts.append(context)
            return context.new_page()

        yield make
        for context in contexts:
            context.close()


@pytest.fixture
def page(new_page) -> Page:
    return new_page()


REDIRECT_PATH = "/__redirects-elsewhere"
OTHER_ORIGIN = "http://other-origin.invalid/login"


def status_of(db, client_key):
    return next(row for row in db.rows if row.client_key == client_key).status


def test_a_limit_saved_in_the_panel_moves_the_tile_too(page, db):
    page.goto("/clients/cedarline")
    limit = page.locator("dialog#panel").get_by_role("textbox", name="Draft limit")
    limit.fill("35")
    limit.press("Enter")

    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(page.get_by_role("textbox", name="Limit Cedarline Roofing")).to_have_value("35")
    tile = page.locator("article.ds-card--tile", has_text="Cedarline Roofing")
    expect(tile.locator(".ds-card__count-note")).to_have_text("of 35 this hour")
    assert db.calls == [(1, "cedarline", 35)]


def test_pausing_the_last_active_client_refreshes_every_region(page, db):
    """With no active client left the totals are 0 of 0 and the chart has nothing: every part
    of the page behind still follows the change, in place (a missing part would load the page instead)."""
    page.goto("/clients/cedarline")
    mark(page)
    page.get_by_role("switch", name="Drafting").click()
    page.get_by_label("Reason").select_option(label="Drafts going wrong")
    page.get_by_role("button", name="Pause client").click()

    expect(page.locator(".ds-toast__text")).to_have_text("Paused Cedarline Roofing.")
    expect(page.locator("#total-used")).to_have_text("0")  # the summary
    expect(page.locator("#total-limits")).to_have_text("0")
    expect(page.locator(".ds-band__fact b")).to_have_text("2 clients")
    expect(page.locator("#needs-list [data-needs]")).to_have_text(
        [re.compile("BrightSmile Dental is paused"), re.compile("Cedarline Roofing is paused")])  # by name
    expect(page.locator("#clients-count")).to_have_text("2")
    expect(page.locator("#results article", has_text="Cedarline Roofing").locator(".ds-status__label")).to_have_text(
        "Paused: Drafts going wrong")  # the list behind
    expect(page.locator("#change-line")).to_contain_text("Ana Reyes paused Cedarline Roofing: Drafts going wrong")
    assert kept(page)  # all in place: no page load


def test_a_refused_save_resets_the_sender_and_keeps_what_was_typed_in_the_other_field(page, db):
    """Typing in the tile while the panel's save is on its way must not leave the
    panel's field showing a limit that was never saved. The save is held and refused by the browser, not by timing."""
    held = []
    page.route("**/clients/cedarline/limit", lambda route: held.append(route))
    page.goto("/clients/cedarline")
    sender = page.locator("dialog#panel").get_by_role("textbox", name="Draft limit")
    other = page.get_by_role("textbox", name="Limit Cedarline Roofing")

    sender.fill("35")
    with page.expect_request("**/clients/cedarline/limit"):
        sender.press("Enter")
    # Typing in the other field while the save is held. The tile sits behind the modal panel now, so no person can type
    # there; the typing is made in the page, which is the state this test is about.
    other.evaluate("field => { field.value = '40'; field.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.evaluate("0")  # a round trip, so the held request has reached the handler
    assert len(held) == 1
    held[0].fulfill(status=503, content_type="application/json",
                    body='{"error": "Couldn\'t reach the database, so the limit may not have changed."}')

    expect(page.locator(".ds-toast__text")).to_have_text(
        "Couldn't reach the database, so the limit may not have changed.")
    expect(sender).to_have_value("30")  # the saved value is back where the save was sent from
    expect(other).to_have_value("40")  # what was typed stays
    assert db.calls == []  # the held request never reached the server


def use_pause(page: Page, db, agent):
    page.goto("/clients/cedarline")
    page.get_by_role("link", name="Pause this client").click()
    page.get_by_label("Reason").select_option(label="Drafts going wrong")
    page.get_by_role("button", name="Pause client").click()
    expect(page.locator("dialog#panel .ds-status__label")).to_have_text("Paused")
    assert status_of(db, "cedarline") == "paused"


def use_resume(page: Page, db, agent):
    page.goto("/clients/brightsmile")
    page.get_by_role("button", name="Resume drafting").click()
    expect(page.locator("dialog#panel .ds-status__label")).to_have_text("Drafting")
    assert status_of(db, "brightsmile") == "active"


def use_key(page: Page, db, agent):
    page.goto("/clients/cedarline")
    page.get_by_role("link", name="Replace token").click()
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    page.get_by_role("button", name="Replace token").click()
    expect(page).to_have_url(re.compile(r"/clients/cedarline\?done=key_replaced$"))
    expect(page.locator("#toast")).to_have_attribute("data-toast", "New HighLevel token saved for Cedarline Roofing.")
    expect(page.locator("#add-key")).to_have_count(0)  # the form is closed again, and the token isn't in the page
    assert agent.calls == ["loc-cedarline"] and [call[0] for call in db.calls] == ["replace_key"]


def use_remove(page: Page, db, agent):
    page.goto("/clients/cedarline")
    page.get_by_role("link", name="Remove…").click()
    page.get_by_label("Type the client's name to confirm").fill("Cedarline Roofing")
    page.get_by_role("button", name="Remove client").click()
    expect(page).to_have_url(re.compile(r"/clients\?removed=cedarline$"))
    expect(page.locator("#toast")).to_have_attribute(
        "data-toast", "Removed Cedarline Roofing. Remove the support bot from its channel in Slack.")
    expect(page.get_by_role("link", name="Cedarline Roofing")).to_have_count(0)
    assert status_of(db, "cedarline") == "removed"


def use_channel(page: Page, db, agent):
    page.goto("/clients/cedarline")
    page.get_by_role("link", name="Change channel").click()
    page.get_by_label("New channel").select_option("C0NEWA")
    page.get_by_role("button", name="Move client").click()
    expect(page).to_have_url(re.compile(r"/clients/cedarline\?done=channel_changed$"))
    expect(page.locator("#toast")).to_have_attribute("data-toast", "Moved Cedarline Roofing to #harbor-yoga.")
    expect(page.locator("dialog#panel .ds-sheet__sub")).to_have_text("#harbor-yoga")
    expect(page.get_by_label("New channel")).to_have_count(0)  # the question is closed again
    assert [call[0] for call in db.calls] == ["change_channel"]


def use_add(page: Page, db, agent):
    page.goto("/clients/new")
    page.get_by_label("Slack channel").select_option("C0NEWA")
    page.get_by_label("Client name").fill("Harbor Yoga")
    page.get_by_label("HighLevel location ID").fill("loc123")
    page.get_by_label("HighLevel token").fill(GHL_KEY)
    page.get_by_role("button", name="Add client").click()
    expect(page).to_have_url(re.compile(r"/clients\?added=harbor-yoga$"))
    expect(page.locator("main").get_by_role("link", name="Harbor Yoga")).to_be_visible()  # in the page's list
    assert [call[0] for call in db.calls] == ["add_client"]


@pytest.fixture(params=["no-javascript", "script-refused"])
def bare_page(request, new_page) -> Page:
    """A page where admin.js doesn't run: scripts off in the browser, or the file refused as if it failed to load."""
    if request.param == "no-javascript":
        return new_page(java_script_enabled=False)
    page = new_page()
    page.context.route("**/static/**/admin.js", lambda route: route.abort())
    return page


@pytest.mark.parametrize("flow", [use_pause, use_resume, use_key, use_remove, use_channel, use_add],
                         ids=["pause", "resume", "key", "remove", "channel", "add"])
def test_every_form_works_without_the_script(bare_page, db, agent, flow):
    flow(bare_page, db, agent)


@pytest.mark.parametrize("path, hidden", [("/clients/cedarline", "Pause this client"),
                                          ("/clients/brightsmile", "Resume drafting")])
def test_the_script_hides_what_is_for_no_script(page, path, hidden):
    page.goto(path)

    marked = page.locator("[data-no-script]")
    expect(marked).to_have_count(1)
    expect(marked).to_be_hidden()
    expect(page.get_by_text(hidden, exact=True)).to_be_hidden()


# ---- Opening and closing the panel ---------------------------------------------------------------------------------

CEDARLINE = re.compile(r"/clients/cedarline$")
LIST_URL = re.compile(r"/clients$")
LIST_TITLE = "Clients · Bluejay support admin"


def modal(page: Page):
    return page.locator("dialog:modal")


def title_of(page: Page):
    return page.locator("dialog#panel .ds-sheet__title")


def limit_of(page: Page):
    return page.locator("dialog#panel").get_by_role("textbox", name="Draft limit")


def name_link(page: Page, name: str):
    return page.get_by_role("link", name=name, exact=True)


def click_at_centre(page: Page, part):
    """A real mouse press at the middle of a part, whatever sits on top there: a row or card is one link stretched over
    it, so Playwright's own click would wait for the part itself to be what the press lands on."""
    part.scroll_into_view_if_needed()
    box = part.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def open_cedarline(page: Page):
    page.goto("/clients")
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()  # the panel is filled


def mark(page: Page):
    """A page load wipes the mark; opening, closing and sorting in place keep it."""
    page.evaluate("window.__kept = true")


def kept(page: Page) -> bool:
    return page.evaluate("window.__kept === true")


def hold_fetches(page: Page, pattern):
    """Holds each fetch that matches (the page loads go through); the held routes are released by the test."""
    held = []

    def handler(route):
        if route.request.resource_type == "fetch":
            held.append(route)
        else:
            route.continue_()

    page.route(pattern, handler)
    return held


def wait_until(page: Page, expression: str):
    """Polls an expression in the page. (The security header forbids the eval Playwright's wait_for_function needs.)"""
    for _ in range(500):
        try:
            if page.evaluate(expression):
                return
        except PlaywrightError:  # a page load is under way
            pass
        page.wait_for_timeout(10)
    raise AssertionError(f"never true in the page: {expression}")


def wait_held(page: Page, held: list, count: int = 1):
    """A route handler runs when the page is given a turn; this gives it turns until the request has been held."""
    for _ in range(2_000):
        if len(held) >= count:
            return
        page.evaluate("0")
    raise AssertionError(f"{len(held)} of {count} requests were held")


def beat(page: Page):
    """For a check that nothing happened: what is late, slow or failing is made by the route, and this only lets the page
    have had its turn to do the wrong thing."""
    page.evaluate("new Promise(done => setTimeout(done, 150))")


def settled(page: Page):
    """The panel's own transitions are over (an open or a close)."""
    page.evaluate("""Promise.allSettled(document.getElementById('panel').getAnimations({subtree: true})
                     .filter(a => a instanceof CSSTransition).map(a => a.finished))""")


# -- opening --

def test_a_client_name_opens_the_panel_over_the_list(page):
    page.goto("/clients")
    page.evaluate("window.__list = document.getElementById('results')")
    name_link(page, "Cedarline Roofing").click()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)
    expect(title_of(page)).to_have_text("Cedarline Roofing")
    expect(title_of(page)).to_be_focused()
    expect(page).to_have_title(re.compile(r"^Cedarline Roofing"))
    assert page.evaluate("document.getElementById('results') === window.__list")  # the list was never touched


def test_add_client_opens_the_panel(page):
    page.goto("/clients")
    page.get_by_role("link", name="Add client").click()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(r"/clients/new$"))
    expect(title_of(page)).to_have_text("Add client")
    expect(title_of(page)).to_be_focused()
    expect(page).to_have_title(re.compile(r"^Add client"))


def test_add_client_from_the_empty_state_opens_the_panel(page, db):
    db.rows[:] = []
    page.goto("/clients")
    page.locator("#results").get_by_role("link", name="Add client").click()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(r"/clients/new$"))
    expect(title_of(page)).to_be_focused()


@pytest.mark.parametrize("width", [1280, 375])
def test_a_list_row_opens_the_panel(new_page, width):
    page = new_page(viewport={"width": width, "height": 800})
    page.goto("/clients?view=list")
    click_at_centre(page, page.locator("tr", has_text="Cedarline Roofing").locator(".ds-table__status"))  # not its name

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)
    expect(title_of(page)).to_have_text("Cedarline Roofing")


@pytest.mark.parametrize("width", [1280, 375])
def test_card_opens_the_panel_from_anywhere_but_the_limit_field(new_page, width):
    page = new_page(viewport={"width": width, "height": 800})
    page.goto("/clients")
    card = page.locator("article", has_text="Cedarline Roofing")
    for part in (card.locator(".ds-card__head"), card.locator(".ds-card__count-number"), card.locator(".ds-card__meter"),
                 card.locator(".ds-card__go")):
        click_at_centre(page, part)
        expect(modal(page)).to_have_count(1)
        expect(page).to_have_url(CEDARLINE)
        page.keyboard.press("Escape")
        expect(modal(page)).to_have_count(0)
    card.get_by_role("textbox", name="Limit Cedarline Roofing").click()  # the field is a field

    beat(page)
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/clients$"))


@pytest.mark.parametrize("width", [1280, 375])
def test_list_row_opens_the_panel_from_anywhere(new_page, width):
    page = new_page(viewport={"width": width, "height": 800})
    page.goto("/clients?view=list")
    row = page.locator("tr", has_text="Cedarline Roofing")
    for part in (row.locator(".ds-table__sub"), row.locator(".ds-card__meter"), row.locator(".ds-table__status"),
                 row.locator(".ds-table__go")):
        click_at_centre(page, part)
        expect(modal(page)).to_have_count(1)
        expect(page).to_have_url(CEDARLINE)
        page.keyboard.press("Escape")
        expect(modal(page)).to_have_count(0)
        expect(page).to_have_url(re.compile(r"/clients\?view=list$"))


def test_a_list_row_opens_the_clients_page_without_the_script(new_page):
    page = new_page(java_script_enabled=False)
    page.goto("/clients?view=list")
    click_at_centre(page, page.locator("tr", has_text="Cedarline Roofing").locator(".ds-table__status"))

    expect(page).to_have_url(CEDARLINE)
    expect(title_of(page)).to_have_text("Cedarline Roofing")


def test_a_panel_from_the_list_is_wired(page, db):
    page.goto("/clients")
    name_link(page, "Cedarline Roofing").click()

    panel = page.locator("dialog#panel")
    expect(panel.locator(".ds-card__fill")).to_have_attribute("style", re.compile(r"scaleX\(0\.4\)"))
    expect(panel.locator("[data-no-script]")).to_be_hidden()
    limit_of(page).fill("35")
    limit_of(page).press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    assert db.calls == [(1, "cedarline", 35)]


def test_the_pause_question_works_after_a_fill(page):
    page.goto("/clients")
    name_link(page, "Cedarline Roofing").click()

    page.get_by_role("switch", name="Drafting").click()
    expect(page.locator("#pause-confirm")).to_be_visible()
    expect(page.get_by_label("Reason")).to_be_focused()


def test_the_test_button_works_after_a_fill(page, agent):
    page.goto("/clients")
    page.get_by_role("link", name="Add client").click()
    page.get_by_label("HighLevel location ID").fill("loc123")
    page.get_by_label("HighLevel token").fill(GHL_KEY)
    page.get_by_role("button", name="Test").click()

    expect(page.locator("#add-test-result")).to_have_text("Connected to this HighLevel account.")
    assert agent.calls == ["loc123"]


def test_review_focuses_the_drafting_switch(page):
    page.goto("/clients")
    page.get_by_role("link", name="Review: BrightSmile Dental").click()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(r"/clients/brightsmile$"))
    expect(page.get_by_role("switch", name="Drafting")).to_be_focused()


def test_raise_limit_focuses_and_selects_the_limit(page, db):
    db.rows.append(a_client("dunes", "Dunes Surf School", used=15, limit=15))
    page.goto("/clients")
    page.get_by_role("link", name="Raise limit: Dunes Surf School").click()

    expect(limit_of(page)).to_be_focused()
    assert page.evaluate("[document.activeElement.selectionStart, document.activeElement.selectionEnd]") == [0, 2]


def test_ctrl_click_stays_the_browsers(page):
    # Open: this test fails now and then in a whole run, in Firefox (no new tab shows, even after 15 s), and passes alone.
    # The cause is not found; a longer wait didn't help, so the wait is the default.
    page.goto("/clients")
    with page.context.expect_page() as opened:
        name_link(page, "Cedarline Roofing").click(modifiers=["Control"])

    other = opened.value
    other.reload()  # WebKit leaves the context's sign-in header off a ctrl-click tab's first request (the real pass comes from Cloudflare)
    expect(other.locator("dialog:modal")).to_have_count(1)  # a new tab shows the panel over its own list
    expect(other).to_have_url(CEDARLINE)
    expect(modal(page)).to_have_count(0)  # this page has none
    expect(page).to_have_url(LIST_URL)


def test_a_slow_answer_shows_the_title_then_a_skeleton(page):
    page.goto("/clients")
    held = hold_fetches(page, CEDARLINE)
    name_link(page, "Cedarline Roofing").click()

    expect(title_of(page)).to_have_text("Cedarline Roofing")  # at once, from the link
    expect(page.locator("dialog#panel .ds-skeleton").first).to_be_visible()  # after 200 ms
    wait_held(page, held)
    held[0].continue_()
    expect(limit_of(page)).to_be_visible()
    expect(page.locator("dialog#panel .ds-skeleton")).to_have_count(0)


def test_a_fast_answer_shows_no_skeleton(page):
    page.goto("/clients")
    page.evaluate("""() => {
      window.__skeleton = false;
      new MutationObserver(records => {
        for (const record of records) for (const node of record.addedNodes) {
          if (node.nodeType === 1 && (node.matches('.ds-skeleton') || node.querySelector('.ds-skeleton'))) window.__skeleton = true;
        }
      }).observe(document.body, {childList: true, subtree: true});
    }""")
    page.clock.install()
    page.clock.pause_at(datetime.now() + timedelta(seconds=10))  # 200 ms of the page's time can't pass by itself
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()  # the answer came and filled the panel
    page.clock.run_for(1_000)  # a timer that was not cancelled would fire now

    assert page.evaluate("window.__skeleton") is False
    expect(page.locator("dialog#panel .ds-skeleton")).to_have_count(0)


# -- closing, and the address --

def closed_on_the_list(page: Page, opener):
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    expect(page).to_have_title(LIST_TITLE)
    expect(opener).to_be_focused()


def test_x_closes_and_focus_returns(page):
    open_cedarline(page)
    page.get_by_role("link", name="Close panel").click()
    closed_on_the_list(page, name_link(page, "Cedarline Roofing"))


def test_escape_closes_and_focus_returns(page):
    open_cedarline(page)
    page.keyboard.press("Escape")
    closed_on_the_list(page, name_link(page, "Cedarline Roofing"))


def test_the_dimmed_area_closes_and_focus_returns(page):
    open_cedarline(page)
    page.mouse.click(40, 300)  # the dimmed list, left of the panel
    closed_on_the_list(page, name_link(page, "Cedarline Roofing"))


def test_the_dimmed_area_closes_at_375(new_page):
    page = new_page(viewport={"width": 375, "height": 800})
    open_cedarline(page)
    page.mouse.click(187, 10)  # above the bottom sheet
    closed_on_the_list(page, name_link(page, "Cedarline Roofing"))


@pytest.mark.parametrize("case", ["422", "409"], ids=["a field marked invalid", "an alert"])
def test_the_dimmed_area_keeps_a_refused_form_whose_token_is_empty(page, case):
    """A refusal brings back the typed values (so none differs from its default) and empties the token: the panel still
    holds a person's work, and says what is wrong with it."""
    page.goto("/clients")
    page.get_by_role("link", name="Add client").click()
    fill_add(page)
    if case == "422":
        page.get_by_label("Drafts an hour").fill("abc")
    else:
        page.get_by_label("HighLevel location ID").fill("loc-cedarline")  # a HighLevel account that already has a client
    page.get_by_role("button", name="Add client").click()
    if case == "422":
        expect(page.get_by_label("Drafts an hour")).to_have_attribute("aria-invalid", "true")
    else:
        expect(page.locator("dialog#panel .ds-alert[role=alert]")).to_have_text(TAKEN_ERROR)
    expect(page.get_by_label("HighLevel token")).to_have_value("")
    page.mouse.click(40, 300)

    expect(modal(page)).to_have_count(1)
    expect(page.get_by_label("Client name")).to_have_value("Harbor Yoga")
    page.get_by_role("link", name="Close panel").click()  # X still closes
    expect(modal(page)).to_have_count(0)


def test_add_clients_cancel_closes_the_panel(page):
    page.goto("/clients?q=o&sort=name&view=list")
    page.get_by_role("link", name="Add client").click()
    expect(modal(page)).to_have_count(1)
    page.get_by_role("link", name="Cancel").click()

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/clients\?q=o&sort=name&view=list$"))
    expect(page.get_by_role("searchbox", name="Search clients")).to_have_value("o")
    expect(page.locator("table")).to_be_visible()


def test_a_click_on_the_panel_itself_doesnt_close(page):
    """A rectangle check: a click on the panel's padding or its inert body targets the dialog too."""
    open_cedarline(page)
    page.evaluate("""() => {
      const dialog = document.getElementById('panel'), box = dialog.getBoundingClientRect();
      dialog.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: box.left + 4, clientY: box.top + 4}));
    }""")
    title_of(page).click()

    expect(modal(page)).to_have_count(1)


@pytest.mark.parametrize("label, value", [("Client name", "Harbor Yoga"), ("HighLevel token", GHL_KEY)],
                         ids=["a name", "a token"])
def test_the_dimmed_area_keeps_a_panel_with_something_typed(page, label, value):
    page.goto("/clients")
    page.get_by_role("link", name="Add client").click()
    page.get_by_label(label).fill(value)
    page.mouse.click(40, 300)

    expect(modal(page)).to_have_count(1)
    page.get_by_role("link", name="Close panel").click()  # X still closes
    expect(modal(page)).to_have_count(0)


def test_focus_returns_to_the_row_that_opened_it(page):
    page.goto("/clients?view=list")
    row = page.locator("tr", has_text="Cedarline Roofing")
    click_at_centre(page, row.locator(".ds-table__status"))
    expect(modal(page)).to_have_count(1)
    page.keyboard.press("Escape")

    expect(modal(page)).to_have_count(0)
    expect(row.get_by_role("link", name="Cedarline Roofing")).to_be_focused()


def test_focus_finds_the_clients_link_after_a_refresh(page):
    open_cedarline(page)
    page.evaluate("window.__old = document.querySelector('#results a[href=\"/clients/cedarline\"]')")
    page.evaluate("""() => {
      const sort = document.getElementById('sort');
      sort.value = 'name';
      sort.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    wait_until(page, "!document.contains(window.__old)")  # the sort replaced the list under the panel
    page.keyboard.press("Escape")

    expect(modal(page)).to_have_count(0)
    expect(name_link(page, "Cedarline Roofing")).to_be_focused()


def test_focus_lands_on_the_heading_with_nothing_to_return_to(page):
    page.goto("/clients/new")
    page.get_by_role("link", name="Cancel").click()

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    expect(page.get_by_role("heading", name="Clients", level=1)).to_be_focused()


@pytest.mark.parametrize("change", ["a new token", "a pause"])
def test_a_save_from_a_panel_alone_loads_the_client_with_its_toast(page, db, change):
    """A refused form's panel stands alone, at the form's own address; the retry that works redirects to the client's, so
    nothing could be filled: the typed token stayed in its field beside a "saved" toast."""
    if change == "a new token":
        page.goto("/clients/cedarline?confirm=key")
        page.evaluate("document.querySelector('form[action$=\"/key\"]').submit()")  # no token: refused, panel alone
        expect(page.locator("#add-key-error")).not_to_be_empty()
        page.get_by_label("New HighLevel token").fill(GHL_KEY)
        page.get_by_role("button", name="Replace token").click()
        words = "New HighLevel token saved for Cedarline Roofing."
    else:
        page.goto("/clients/cedarline?confirm=pause")
        page.evaluate("document.getElementById('pause-confirm').submit()")  # no reason: refused, panel alone
        expect(page.locator("#pause-reason-error")).not_to_be_empty()
        page.get_by_label("Reason").select_option(label="Drafts going wrong")
        page.get_by_role("button", name="Pause client").click()
        words = None

    expect(page).to_have_url(CEDARLINE)  # (not the form's own address: the script drops the ?done= it was sent with)
    expect(page.locator("#results")).to_have_count(1)  # a page load: the list is behind the panel now
    expect(modal(page)).to_have_count(1)
    expect(page.locator("#add-key")).to_have_count(0)
    expect(page.locator("#pause-confirm")).to_be_hidden()
    if words:
        expect(toast_text(page)).to_have_text(words)
    else:
        expect(panel_status(page)).to_have_text("Paused")
    found = page.evaluate("""key => [...document.querySelectorAll('input, textarea')].some(field => field.value === key)
        || document.documentElement.outerHTML.includes(key)""", GHL_KEY)
    assert found is False


def test_closing_a_panel_with_no_list_behind_loads_clients(page):
    page.goto("/clients/cedarline?confirm=pause")
    page.evaluate("document.getElementById('pause-confirm').submit()")  # no reason: refused, with the panel alone
    expect(page.locator("#pause-reason-error")).not_to_be_empty()
    expect(page.locator("#results")).to_have_count(0)
    expect(modal(page)).to_have_count(1)
    page.get_by_role("link", name="Close panel").click()

    expect(page).to_have_url(LIST_URL)
    expect(page.locator("#results")).to_have_count(1)
    expect(modal(page)).to_have_count(0)


def test_a_limit_saved_on_the_tile_reaches_a_panel_opened_meanwhile(page, db):
    page.goto("/clients")
    held = hold_fetches(page, re.compile(r"/clients/cedarline/limit$"))
    tile = page.get_by_role("textbox", name="Limit Cedarline Roofing")
    tile.fill("35")
    tile.press("Enter")
    wait_held(page, held)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_have_value("35")  # a save still on its way is newer than the page the panel was read from
    expect(page.locator("dialog#panel .ds-card__use-of")).to_have_text("of 30 drafts this hour")

    held[0].continue_()
    expect(page.locator("dialog#panel .ds-card__use-of")).to_have_text("of 35 drafts this hour")  # the answer reached it
    expect(limit_of(page)).to_have_value("35")


def test_back_closes_and_forward_opens(page):
    open_cedarline(page)
    page.go_back()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)

    page.go_forward()
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)
    expect(limit_of(page)).to_be_visible()


def test_no_history_entry_is_added_by_closing(page):
    page.goto("/clients")
    before = page.evaluate("history.length")
    name_link(page, "Cedarline Roofing").click()
    expect(modal(page)).to_have_count(1)
    opened = page.evaluate("history.length")
    assert opened == before + 1
    page.get_by_role("link", name="Close panel").click()

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    assert page.evaluate("history.length") == opened


def test_a_direct_address_is_modal_without_sliding(page):
    page.goto("/clients/cedarline")

    expect(modal(page)).to_have_count(1)
    sliding = page.evaluate("""document.getElementById('panel').getAnimations()
                               .filter(a => a.transitionProperty === 'transform').length""")
    assert sliding == 0


def test_a_reload_keeps_the_panel(page):
    open_cedarline(page)
    page.reload()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)
    expect(limit_of(page)).to_be_visible()


def test_a_direct_address_then_close_lands_on_clients(page):
    page.goto("/clients/cedarline")
    page.get_by_role("link", name="Close panel").click()

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    expect(name_link(page, "Cedarline Roofing")).to_be_focused()


def test_a_direct_address_then_back_goes_to_the_previous_page(page):
    page.goto("/history")
    page.goto("/clients/cedarline")
    expect(modal(page)).to_have_count(1)
    page.go_back()

    expect(page).to_have_url(re.compile(r"/history$"))


def test_search_sort_and_view_survive_open_and_close(page):
    address = "/clients?q=o&sort=name&view=list"
    page.goto(address)
    page.locator("tr", has_text="Cedarline Roofing").get_by_role("link").click()
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)
    page.keyboard.press("Escape")

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(re.escape(address) + "$"))
    expect(page.get_by_role("searchbox", name="Search clients")).to_have_value("o")
    expect(page.get_by_label("Sort")).to_have_value("name")
    expect(page.locator("table")).to_be_visible()


@pytest.fixture
def many(db):
    """Forty clients, so the list scrolls."""
    db.rows[:] = [a_client(f"c{i:02d}", f"Client {i:02d}", used=i % 10, limit=30) for i in range(40)]


def test_scroll_stays_where_it_was(new_page, many):
    page = new_page(viewport={"width": 1280, "height": 500})
    page.goto("/clients?view=list")
    link = name_link(page, "Client 20")
    link.scroll_into_view_if_needed()
    y = page.evaluate("scrollY")
    assert y > 0
    link.click()
    expect(modal(page)).to_have_count(1)
    assert page.evaluate("scrollY") == y
    page.keyboard.press("Escape")

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/clients\?view=list$"))
    assert page.evaluate("scrollY") == y


def test_a_sort_on_its_way_doesnt_take_the_panels_address(page):
    page.goto("/clients")
    held = hold_fetches(page, re.compile(r"/clients\?.*sort=name"))
    page.get_by_label("Sort").select_option("name")
    wait_held(page, held)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()

    held[0].continue_()
    wait_until(page, "document.querySelector('#results .ds-card__name-button').textContent.trim() === 'BrightSmile Dental'")
    expect(page).to_have_url(CEDARLINE)  # the sort has landed, and the address is still the panel's
    page.get_by_role("link", name="Close panel").click()

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/clients\?.*sort=name"))


def test_a_sort_failing_while_the_panel_is_open_leaves_it_open(page):
    page.goto("/clients")
    mark(page)
    held = hold_fetches(page, re.compile(r"/clients\?.*sort=name"))
    page.get_by_label("Sort").select_option("name")
    wait_held(page, held)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()

    held[0].fulfill(status=503, body="down")
    beat(page)
    assert kept(page)  # no page load
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)


# -- what can go wrong --

def only_fetches(page: Page, pattern, answer):
    """Answers the fetches that match with `answer(route)`; the page loads go through."""
    page.route(pattern, lambda route: answer(route) if route.request.resource_type == "fetch" else route.continue_())


def test_a_failed_open_loads_the_address_as_a_page(page):
    page.goto("/clients")
    mark(page)
    only_fetches(page, CEDARLINE, lambda route: route.abort())
    name_link(page, "Cedarline Roofing").click()

    wait_until(page, "window.__kept !== true")  # a real page load
    expect(page).to_have_url(CEDARLINE)
    expect(modal(page)).to_have_count(1)
    expect(limit_of(page)).to_be_visible()


def test_a_removed_client_loads_the_plain_page(page, db):
    page.goto("/clients")
    mark(page)
    db.rows[:] = [row for row in db.rows if row.client_key != "cedarline"]  # a colleague removed it
    name_link(page, "Cedarline Roofing").click()  # the list on screen is stale

    wait_until(page, "window.__kept !== true")
    expect(page.get_by_role("heading", name="Client not found")).to_be_visible()
    expect(modal(page)).to_have_count(0)


def redirect_elsewhere(route):
    """Sends the request to the test-only address that answers 302 to another origin (the app's own, so WebKit allows it)."""
    url = urlparse(route.request.url)
    route.continue_(url=f"{url.scheme}://{url.netloc}{REDIRECT_PATH}")


def test_a_refused_redirect_to_another_origin_loads_the_page(page):
    """The expired pass. The security header and CORS reject this fetch before any origin check could, so this proves
    the failure path, not the check."""
    page.goto("/clients")
    mark(page)
    only_fetches(page, CEDARLINE, redirect_elsewhere)
    name_link(page, "Cedarline Roofing").click()

    wait_until(page, "window.__kept !== true")
    expect(page).to_have_url(CEDARLINE)
    expect(modal(page)).to_have_count(1)


@pytest.mark.parametrize("late", ["answer", "failure"])
def test_closing_while_opening_then_a_late_answer_changes_nothing(page, late):
    page.goto("/clients")
    mark(page)
    held = hold_fetches(page, CEDARLINE)
    name_link(page, "Cedarline Roofing").click()
    wait_held(page, held)
    expect(title_of(page)).to_have_text("Cedarline Roofing")
    page.keyboard.press("Escape")
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    settled(page)  # the closed panel is emptied already: an answer that filled it now would stay there

    if late == "answer":
        held[0].continue_()
    else:
        held[0].abort()
    beat(page)

    assert kept(page)  # no page load
    expect(modal(page)).to_have_count(0)  # no panel
    expect(page).to_have_url(LIST_URL)
    expect(title_of(page)).to_have_count(0)


def test_a_late_answer_for_another_client_is_dropped(page):
    page.goto("/clients")
    held = hold_fetches(page, CEDARLINE)
    name_link(page, "Cedarline Roofing").click()
    wait_held(page, held)
    page.keyboard.press("Escape")
    expect(modal(page)).to_have_count(0)
    name_link(page, "BrightSmile Dental").click()
    expect(page.get_by_role("switch", name="Drafting")).to_be_visible()  # B is filled

    held[0].continue_()
    beat(page)

    expect(title_of(page)).to_have_text("BrightSmile Dental")
    expect(page).to_have_url(re.compile(r"/clients/brightsmile$"))
    expect(page).to_have_title(re.compile(r"^BrightSmile Dental"))


@pytest.mark.parametrize("closer", ["escape", "x", "back"])
@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_closing_empties_the_panel(new_page, motion, closer):
    """After close, after Back, no field, address or history entry holds the token. With reduced motion
    the panel fades instead of sliding, so emptying can't be waiting on one kind of transition."""
    page = new_page(reduced_motion=motion)
    page.goto("/clients")
    page.get_by_role("link", name="Add client").click()
    page.get_by_label("HighLevel token").fill(GHL_KEY)
    if closer == "escape":
        page.keyboard.press("Escape")
    elif closer == "x":
        page.get_by_role("link", name="Close panel").click()
    else:
        page.go_back()
    expect(modal(page)).to_have_count(0)

    expect(page.locator("input[type=password]")).to_have_count(0)
    expect(page.locator("dialog#panel > *")).to_have_count(0)
    found = page.evaluate("""key => [...document.querySelectorAll('input, textarea')].some(field => field.value === key)
        || document.documentElement.outerHTML.includes(key) || location.href.includes(key)
        || JSON.stringify(history.state).includes(key)""", GHL_KEY)
    assert found is False


@pytest.mark.parametrize("where", ["a direct Add client", "a refused form's panel alone"])
def test_leaving_the_page_scrubs_the_token_for_the_back_forward_cache(page, where):
    """Back from a directly loaded panel leaves the document without any close, and a page kept in the
    back/forward cache would show the typed token again on Forward. `pagehide` empties every password field, and only
    those: the other typed values stay."""
    if where == "a direct Add client":
        page.goto("/clients/new")
        page.get_by_label("Client name").fill("Harbor Yoga")
    else:
        page.goto("/clients/cedarline?confirm=key")
        page.evaluate("document.querySelector('form[action$=\"/key\"]').submit()")  # no token: refused, panel alone
        expect(page.locator("#add-key-error")).not_to_be_empty()
        expect(page.locator("#results")).to_have_count(0)
    page.get_by_label(re.compile("token", re.I)).fill(GHL_KEY)
    assert page.evaluate("document.querySelector('input[type=password]').value") == GHL_KEY

    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")

    assert page.evaluate("[...document.querySelectorAll('input[type=password]')].map(field => field.value)") == [""]
    if where == "a direct Add client":
        expect(page.get_by_label("Client name")).to_have_value("Harbor Yoga")


def test_close_then_reopen_at_once_keeps_the_new_panel(page):
    open_cedarline(page)
    page.evaluate("""() => {
      document.querySelector('dialog#panel a[aria-label="Close panel"]').click();
      document.querySelector('#results a[href="/clients/brightsmile"]').click();
    }""")
    expect(title_of(page)).to_have_text("BrightSmile Dental")
    expect(page.get_by_role("switch", name="Drafting")).to_be_visible()
    settled(page)  # the closed panel's emptying has had its turn

    expect(title_of(page)).to_have_text("BrightSmile Dental")
    expect(page.get_by_role("switch", name="Drafting")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/clients/brightsmile$"))
    expect(modal(page)).to_have_count(1)


# -- inside a modal --

def test_escape_goes_one_at_a_time(page):
    open_cedarline(page)
    limit = limit_of(page)
    limit.fill("35")
    limit.press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    limit.fill("40")  # typed, not saved

    limit.press("Escape")
    expect(limit).to_have_value("35")  # the field being edited goes first
    expect(page.locator("#toast")).to_be_visible()
    expect(modal(page)).to_have_count(1)

    limit.press("Escape")
    expect(page.locator("#toast")).to_be_hidden()  # then the toast
    expect(modal(page)).to_have_count(1)

    limit.press("Escape")
    expect(modal(page)).to_have_count(0)  # then the panel


def test_undo_can_be_clicked_inside_the_panel(page):
    open_cedarline(page)
    limit_of(page).fill("35")
    limit_of(page).press("Enter")
    toast = page.locator("dialog#panel #toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    toast.get_by_role("button", name="Undo").click()

    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")
    expect(limit_of(page)).to_have_value("30")


def test_the_toast_comes_back_out_on_close(page):
    open_cedarline(page)
    limit_of(page).fill("35")
    limit_of(page).press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    page.get_by_role("link", name="Close panel").click()

    expect(modal(page)).to_have_count(0)
    expect(page.locator("#toast")).to_be_visible()  # still showing, on the page
    assert page.evaluate("document.getElementById('toast').closest('dialog') === null")
    assert page.evaluate("document.getElementById('toast').matches(':popover-open')")
    page.locator("#toast").get_by_role("button", name="Undo").click()
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")


def test_the_toast_survives_a_fill(page):
    page.goto("/clients")
    tile = page.get_by_role("textbox", name="Limit Cedarline Roofing")
    tile.fill("35")
    tile.press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    held = hold_fetches(page, CEDARLINE)
    name_link(page, "Cedarline Roofing").click()
    wait_held(page, held)
    expect(page.locator("dialog#panel #toast")).to_be_visible()  # moved in with the panel still loading

    held[0].continue_()
    expect(limit_of(page)).to_be_visible()  # filled
    toast = page.locator("dialog#panel #toast")
    expect(toast).to_be_visible()
    toast.get_by_role("button", name="Undo").click()
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")


def test_the_live_regions_move_too(page):
    open_cedarline(page)
    for role in ("status", "alert"):
        expect(page.locator(f"dialog#panel > .ds-toast__region[role={role}]")).to_have_count(1)
    page.get_by_role("link", name="Close panel").click()

    expect(modal(page)).to_have_count(0)
    expect(page.locator("dialog#panel .ds-toast__region")).to_have_count(0)
    for role in ("status", "alert"):
        expect(page.locator(f".ds-toast__region[role={role}]")).to_have_count(1)


def test_the_toast_never_covers_a_field_in_the_panel(new_page):
    page = new_page(viewport={"width": 375, "height": 600})
    open_cedarline(page)
    body = page.locator("dialog#panel .ds-sheet__body")
    padding = "b => parseFloat(getComputedStyle(b).paddingBottom)"
    before = body.evaluate(padding)
    limit_of(page).fill("35")
    limit_of(page).press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")

    assert body.evaluate(padding) > before  # room to scroll into, on the panel's own scrolling body
    assert body.evaluate("b => parseFloat(b.style.scrollPaddingBottom)") > 0
    last = page.get_by_role("link", name="Remove…")
    last.focus()
    expect(last).to_be_focused()
    # The focus scrolls the link into view, which takes a moment: wait for the same comparison to hold, so it still fails
    # if the link stays under the toast.
    wait_until(page, """document.querySelector('a[href$="?confirm=remove"]').getBoundingClientRect().bottom
                        <= document.getElementById('toast').getBoundingClientRect().top""")

    page.keyboard.press("Escape")  # the toast goes, and the room with it
    expect(page.locator("#toast")).to_be_hidden()
    assert body.evaluate(padding) == before
    assert body.evaluate("b => b.style.scrollPaddingBottom") == ""


def test_tab_stays_inside_the_panel(page):
    open_cedarline(page)
    inside = 0
    for _ in range(40):
        page.keyboard.press("Tab")
        where = page.evaluate("""document.activeElement === document.body ? 'body'
                                 : document.activeElement.closest('dialog#panel') ? 'panel' : 'outside'""")
        assert where != "outside"
        inside += where == "panel"
    assert inside > 10


def test_the_page_behind_doesnt_scroll(new_page, many):
    page = new_page(viewport={"width": 1280, "height": 500})
    page.goto("/clients?view=list")
    name_link(page, "Client 00").click()
    expect(modal(page)).to_have_count(1)
    y = page.evaluate("scrollY")
    page.mouse.move(40, 250)  # over the dimmed list
    page.mouse.wheel(0, 600)
    beat(page)

    assert page.evaluate("scrollY") == y


def test_the_list_behind_swaps_without_motion(page):
    page.goto("/clients")
    if not page.evaluate("() => !!document.startViewTransition"):  # admin.js turns them off in WebKit
        pytest.skip("no view transitions in this browser (admin.js turns them off in WebKit): nothing to spy on")
    page.evaluate("""() => {
      window.__transitions = 0;
      const real = document.startViewTransition.bind(document);
      document.startViewTransition = (...args) => { window.__transitions += 1; return real(...args); };
    }""")
    sort = """name => {
      const sort = document.getElementById('sort');
      sort.value = name;
      sort.dispatchEvent(new Event('change', {bubbles: true}));
    }"""
    first = "document.querySelector('#results .ds-card__name-button').textContent.trim() === "
    page.evaluate(sort, "name")
    wait_until(page, first + "'BrightSmile Dental'")
    assert page.evaluate("window.__transitions") >= 1, "the spy saw no swap with the panel closed: it proves nothing"

    name_link(page, "Cedarline Roofing").click()  # open
    expect(limit_of(page)).to_be_visible()
    swaps = page.evaluate("window.__transitions")
    page.evaluate(sort, "closest")
    wait_until(page, first + "'Cedarline Roofing'")
    assert page.evaluate("window.__transitions") == swaps

    # Firefox has no `overlay`: a closed dialog goes display: none at once, so no panel is on its way out to protect.
    if not page.evaluate("() => CSS.supports('overlay', 'auto')"):
        return
    page.evaluate("""() => {  // closing: the panel is still on its way out when the sort's answer swaps the list
      document.querySelector('dialog#panel a[aria-label="Close panel"]').click();
      const sort = document.getElementById('sort');
      sort.value = 'name';
      sort.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    wait_until(page, first + "'BrightSmile Dental'")
    assert page.evaluate("window.__transitions") == swaps


# ---- Saving inside the panel ---------------------------------------------------------------------------------------

PAUSE = re.compile(r"/clients/cedarline/pause$")
RESUME = re.compile(r"/clients/brightsmile/resume$")
KEY = re.compile(r"/clients/cedarline/key$")
NEW = re.compile(r"/clients/new$")
# The limit's own words, for a change that may not have been made.
NO_ANSWER = "Couldn't reach the admin app, so the change may not have been made. Reload the page to check."


def hold_posts(page: Page, pattern):
    """Holds each fetch POST that matches (page loads, form posts and GETs go through); the test releases the held routes."""
    held = []

    def handler(route):
        if route.request.method == "POST" and route.request.resource_type == "fetch":
            held.append(route)
        else:
            route.continue_()

    page.route(pattern, handler)
    return held


def posts_sent(page: Page) -> list:
    """The address of every POST this page sends from now on (a held or aborted one counts)."""
    sent = []
    page.on("request", lambda request: sent.append(request.url) if request.method == "POST" else None)
    return sent


def entries(page: Page) -> int:
    return page.evaluate("history.length")


def panel_status(page: Page):
    return page.locator("dialog#panel .ds-sheet__head .ds-status__label")


def tile_status(page: Page, name: str):
    return page.locator("article.ds-card--tile", has_text=name).locator(".ds-status__label")


def toast_text(page: Page):
    return page.locator(".ds-toast__text")


def ask_to_pause(page: Page, note: str = ""):
    page.get_by_role("switch", name="Drafting").click()
    page.get_by_label("Reason").select_option(label="Drafts going wrong")
    if note:
        page.get_by_label("Note (optional)").fill(note)


def pause_in_panel(page: Page, note: str = ""):
    ask_to_pause(page, note)
    page.get_by_role("button", name="Pause client").click()


def fill_add(page: Page, name: str = "Harbor Yoga", location: str = "loc123"):
    page.get_by_label("Slack channel").select_option("C0NEWA")
    page.get_by_label("Client name").fill(name)
    page.get_by_label("HighLevel location ID").fill(location)
    page.get_by_label("HighLevel token").fill(GHL_KEY)


def wait_for_fill(page: Page, marker: str):
    """Waits until a fill has replaced the element the test stored as window.__marked_<marker> before it sent."""
    wait_until(page, f"!document.contains(window.__marked_{marker})")


# -- links inside the panel --

def test_replace_token_opens_in_the_panel(page):
    open_cedarline(page)
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Replace token").click()

    expect(page).to_have_url(re.compile(r"/clients/cedarline\?confirm=key$"))
    expect(page.get_by_label("New HighLevel token")).to_be_focused()
    assert kept(page) and entries(page) == before  # replaced, not pushed
    expect(modal(page)).to_have_count(1)
    page.go_back()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    page.go_forward()  # the address names the form, and the panel shows it
    expect(page).to_have_url(re.compile(r"/clients/cedarline\?confirm=key$"))
    expect(page.get_by_label("New HighLevel token")).to_be_visible()
    assert kept(page) and entries(page) == before
    page.get_by_role("link", name="Cancel").click()

    expect(page).to_have_url(CEDARLINE)
    expect(page.get_by_role("link", name="Replace token")).to_be_focused()
    expect(page.locator("#add-key")).to_have_count(0)
    assert kept(page) and entries(page) == before
    page.go_back()
    expect(modal(page)).to_have_count(0)  # one Back closes it: the confirm step added no entry
    expect(page).to_have_url(LIST_URL)


def test_remove_opens_in_the_panel(page):
    open_cedarline(page)
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Remove…").click()

    expect(page).to_have_url(re.compile(r"/clients/cedarline\?confirm=remove$"))
    expect(page.get_by_label("Type the client's name to confirm")).to_be_focused()
    assert kept(page) and entries(page) == before
    page.go_back()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    page.go_forward()  # the address names the form, and the panel shows it
    expect(page).to_have_url(re.compile(r"/clients/cedarline\?confirm=remove$"))
    expect(page.get_by_label("Type the client's name to confirm")).to_be_visible()
    assert kept(page) and entries(page) == before
    page.get_by_role("link", name="Keep client").click()

    expect(page).to_have_url(CEDARLINE)
    expect(page.get_by_role("link", name="Remove…")).to_be_focused()
    expect(page.locator("#remove-confirm")).to_have_count(0)
    assert kept(page) and entries(page) == before


def test_change_channel_opens_in_the_panel(page):
    open_cedarline(page)
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Change channel").click()

    expect(page).to_have_url(re.compile(r"/clients/cedarline\?confirm=channel$"))
    expect(page.get_by_label("New channel")).to_be_focused()
    assert kept(page) and entries(page) == before  # replaced, not pushed
    expect(modal(page)).to_have_count(1)
    page.go_back()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    page.go_forward()  # the address names the question, and the panel shows it
    expect(page).to_have_url(re.compile(r"/clients/cedarline\?confirm=channel$"))
    expect(page.get_by_label("New channel")).to_be_visible()
    assert kept(page) and entries(page) == before
    page.get_by_role("link", name="Cancel").click()

    expect(page).to_have_url(CEDARLINE)
    expect(page.get_by_role("link", name="Change channel")).to_be_focused()
    expect(page.locator("#move-channel")).to_have_count(0)
    assert kept(page) and entries(page) == before
    page.go_back()
    expect(modal(page)).to_have_count(0)  # one Back closes it: the question added no entry
    expect(page).to_have_url(LIST_URL)


def test_a_new_channel_stays_in_the_panel(page, db):
    open_cedarline(page)
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Change channel").click()
    page.get_by_label("New channel").select_option("C0NEWA")
    page.get_by_role("button", name="Move client").click()

    expect(toast_text(page)).to_have_text("Moved Cedarline Roofing to #harbor-yoga.")
    expect(page.locator("dialog#panel .ds-sheet__sub")).to_have_text("#harbor-yoga")
    expect(page.locator("#results .ds-card__sub", has_text="#harbor-yoga")).to_have_count(1)  # the list behind too
    expect(page.locator("#move-channel")).to_have_count(0)  # the question is closed again
    expect(page).to_have_url(CEDARLINE)  # ?confirm=channel went with the form
    expect(page.get_by_role("link", name="Change channel")).to_be_focused()
    expect(modal(page)).to_have_count(1)
    assert kept(page) and entries(page) == before
    assert db.calls == [("change_channel", 1, "cedarline", "C0NEWA", "harbor-yoga")]


def test_a_refused_move_stays_in_the_panel(page, db, channels):
    open_cedarline(page)
    mark(page)
    page.get_by_role("link", name="Change channel").click()
    page.get_by_label("New channel").select_option("C0NEWA")
    channels.free = []  # the bot left the channel after the question opened
    page.get_by_role("button", name="Move client").click()

    expect(page.locator("#move-channel-error")).to_have_text("That channel isn't free any more. Choose another one.")
    expect(page.get_by_label("New channel")).to_be_focused()
    expect(modal(page)).to_have_count(1)
    assert kept(page) and db.calls == []  # nothing saved, no page load
    expect(page.locator("dialog#panel .ds-sheet__sub")).to_have_text("C0CEDARLINE")


REVEAL_KEY = re.compile(r"/clients/cedarline\?confirm=key$")


def test_a_late_answer_to_a_link_inside_the_panel_is_dropped(page):
    open_cedarline(page)
    mark(page)
    held = hold_fetches(page, REVEAL_KEY)
    page.get_by_role("link", name="Replace token").click()
    wait_held(page, held)
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    settled(page)  # the closed panel is emptied already: an answer that filled it now would stay there

    held[0].continue_()
    beat(page)
    assert kept(page)  # no page load
    expect(modal(page)).to_have_count(0)
    expect(page.locator("dialog#panel > *")).to_have_count(0)
    expect(page).to_have_url(LIST_URL)

    held.clear()
    open_cedarline(page)  # the same again, but another panel is open when the answer comes
    page.get_by_role("link", name="Replace token").click()
    wait_held(page, held)
    page.get_by_role("link", name="Close panel").click()
    name_link(page, "BrightSmile Dental").click()
    expect(page.get_by_role("switch", name="Drafting")).to_be_visible()
    held[0].continue_()
    beat(page)

    expect(title_of(page)).to_have_text("BrightSmile Dental")
    expect(page.locator("#add-key")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/clients/brightsmile$"))


def test_a_failed_link_inside_the_panel_loads_the_address_as_a_page(page):
    open_cedarline(page)
    mark(page)
    only_fetches(page, REVEAL_KEY, lambda route: route.abort())
    page.get_by_role("link", name="Replace token").click()

    wait_until(page, "window.__kept !== true")  # a real page load, as a failed open does
    expect(page).to_have_url(REVEAL_KEY)
    expect(page.get_by_label("New HighLevel token")).to_be_visible()


def test_a_fill_moves_focus_as_the_design_says(page):
    open_cedarline(page)
    page.get_by_role("link", name="Remove…").click()
    expect(page.locator("#remove-confirm")).to_be_focused()  # the revealed form's first field
    page.get_by_role("link", name="Keep client").click()
    expect(page.get_by_role("link", name="Remove…")).to_be_focused()  # back to the control that revealed it

    pause_in_panel(page)
    expect(panel_status(page)).to_have_text("Paused")
    expect(page.get_by_role("switch", name="Drafting")).to_be_focused()  # after a pause, the switch that did it


# -- each row of the design's table --

def test_a_pause_stays_in_the_panel(page, db):
    open_cedarline(page)
    mark(page)
    before = entries(page)
    pause_in_panel(page)

    expect(panel_status(page)).to_have_text("Paused")
    toast = page.locator("dialog#panel #toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Paused Cedarline Roofing.")
    expect(toast.get_by_role("button", name="Undo")).to_be_visible()
    expect(tile_status(page, "Cedarline Roofing")).to_have_text("Paused: Drafts going wrong")  # the tile behind too
    assert kept(page)  # no page load
    expect(page).to_have_url(CEDARLINE)  # ?done is not in the address
    assert "done" not in page.url and entries(page) == before
    assert status_of(db, "cedarline") == "paused"


def test_undo_of_a_pause_stays_in_the_panel(page, db):
    open_cedarline(page)
    mark(page)
    pause_in_panel(page)
    toast = page.locator("dialog#panel #toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Paused Cedarline Roofing.")
    toast.get_by_role("button", name="Undo").click()

    expect(panel_status(page)).to_have_text("Drafting")
    expect(toast.locator(".ds-toast__text")).to_have_text("Resumed Cedarline Roofing.")
    expect(tile_status(page, "Cedarline Roofing")).to_have_text("Drafting")
    assert kept(page) and status_of(db, "cedarline") == "active"
    expect(page).to_have_url(CEDARLINE)


def test_a_new_token_stays_in_the_panel(page, db, agent):
    open_cedarline(page)
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Replace token").click()
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    held = hold_posts(page, KEY)
    page.get_by_role("button", name="Replace token").click()
    wait_held(page, held)

    expect(page.locator("#add-test-result")).to_have_text("Testing the connection…")  # the Test button's own words
    held[0].continue_()
    expect(toast_text(page)).to_have_text("New HighLevel token saved for Cedarline Roofing.")
    expect(page.locator("#add-key")).to_have_count(0)  # the form is closed again, and the token isn't in the page
    expect(page).to_have_url(CEDARLINE)  # ?confirm=key went with the form
    expect(page.get_by_role("link", name="Replace token")).to_be_focused()
    expect(modal(page)).to_have_count(1)
    assert kept(page) and entries(page) == before
    assert agent.calls == ["loc-cedarline"] and [call[0] for call in db.calls] == ["replace_key"]


def test_adding_a_client_closes_the_panel_and_lists_it(page, db):
    page.goto("/clients?q=o&sort=name&view=list")
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Add client").click()
    expect(page.get_by_label("Client name")).to_be_visible()
    fill_add(page)
    held = hold_posts(page, NEW)
    page.get_by_role("button", name="Add client").click()
    wait_held(page, held)

    expect(page.locator("#add-test-result")).to_have_text("Testing the connection…")  # the same busy message for Add
    held[0].continue_()
    expect(modal(page)).to_have_count(0)
    expect(toast_text(page)).to_have_text("Added Harbor Yoga. It drafts from its next message.")
    expect(page.locator("tr", has_text="Harbor Yoga")).to_have_count(1)
    expect(page.locator("#all-clients .ds-page-header__count")).to_have_text("2")  # outside #results and #summary
    expect(page).to_have_url(re.compile(r"/clients\?q=o&sort=name&view=list$"))
    expect(page.get_by_role("searchbox", name="Search clients")).to_have_value("o")
    expect(page.get_by_label("Sort")).to_have_value("name")
    expect(page.get_by_role("link", name="Add client")).to_be_focused()
    assert kept(page) and entries(page) == before + 1
    assert [call[0] for call in db.calls] == ["add_client"]


def test_removing_a_client_closes_the_panel_and_unlists_it(page, db):
    db.rows[0] = a_client("cedarline", "Cedarline Roofing", used=30, limit=30)  # at its limit: Needs you holds it
    page.goto("/clients")
    mark(page)
    expect(page.locator("#needs-title .ds-card__aside")).to_have_text("2 items")
    expect(page.locator("#all-clients .ds-page-header__count")).to_have_text("2")
    name_link(page, "Cedarline Roofing").click()
    page.get_by_role("link", name="Remove…").click()
    page.get_by_label("Type the client's name to confirm").fill("Cedarline Roofing")
    page.get_by_role("button", name="Remove client").click()

    expect(modal(page)).to_have_count(0)
    expect(toast_text(page)).to_have_text("Removed Cedarline Roofing. Remove the support bot from its channel in Slack.")
    # Not in the list, not in Needs you.
    expect(page.locator("main").get_by_role("link", name="Cedarline Roofing")).to_have_count(0)
    expect(page.locator("#needs-title .ds-card__aside")).to_have_text("1 item")
    expect(page.locator("#all-clients .ds-page-header__count")).to_have_text("1")
    expect(page.get_by_role("heading", name="Clients", level=1)).to_be_focused()
    expect(page).to_have_url(LIST_URL)
    assert kept(page) and status_of(db, "cedarline") == "removed"


def test_adding_the_first_client_loads_the_page(page, db):
    db.rows[:] = []
    page.goto("/clients")
    mark(page)
    page.locator("#results").get_by_role("link", name="Add client").click()
    fill_add(page)
    page.get_by_role("button", name="Add client").click()

    wait_until(page, "window.__kept !== true")  # a real page load: the list has no regions to fill yet
    expect(toast_text(page)).to_have_text("Added Harbor Yoga. It drafts from its next message.")
    expect(page.locator("main").get_by_role("link", name="Harbor Yoga")).to_be_visible()  # in the page's list
    expect(page).to_have_url(LIST_URL)
    expect(modal(page)).to_have_count(0)


def test_removing_the_last_client_loads_the_page(page, db):
    db.rows[:] = [db.rows[0]]
    page.goto("/clients")
    mark(page)
    name_link(page, "Cedarline Roofing").click()
    page.get_by_role("link", name="Remove…").click()
    page.get_by_label("Type the client's name to confirm").fill("Cedarline Roofing")
    page.get_by_role("button", name="Remove client").click()

    wait_until(page, "window.__kept !== true")  # the answer has no summary row: a page load, with its toast
    expect(toast_text(page)).to_have_text("Removed Cedarline Roofing. Remove the support bot from its channel in Slack.")
    expect(page.get_by_role("heading", name="No clients yet")).to_be_visible()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)


@pytest.mark.parametrize("save", ["a pause", "an add"])
def test_back_after_a_save(page, save):
    address = "/clients?q=o&sort=name&view=list"
    page.goto(address)
    mark(page)
    before = entries(page)
    if save == "a pause":
        page.locator("tr", has_text="Cedarline Roofing").get_by_role("link").click()
        expect(limit_of(page)).to_be_visible()
        pause_in_panel(page)
        expect(panel_status(page)).to_have_text("Paused")
        assert entries(page) == before + 1  # the save added none

        page.go_back()
        expect(modal(page)).to_have_count(0)
        expect(page).to_have_url(re.compile(re.escape(address) + "$"))
        expect(page.get_by_role("searchbox", name="Search clients")).to_have_value("o")
        expect(page.get_by_label("Sort")).to_have_value("name")
        expect(page.locator("table")).to_be_visible()
        assert kept(page) and entries(page) == before + 1  # the panel's entry waits ahead

        page.go_forward()
        expect(modal(page)).to_have_count(1)
        expect(page).to_have_url(CEDARLINE)
        expect(panel_status(page)).to_have_text("Paused")  # opened afresh: the client as the save left it
    else:
        page.get_by_role("link", name="Add client").click()
        expect(page.get_by_label("Client name")).to_be_visible()
        fill_add(page)
        page.get_by_role("button", name="Add client").click()
        expect(modal(page)).to_have_count(0)
        expect(page.locator("tr", has_text="Harbor Yoga")).to_have_count(1)
        assert entries(page) == before + 1  # the panel's entry now shows the list: nothing pushed

        page.go_back()  # not to the page before Clients: the entry the panel had is the list's address now
        expect(page).to_have_url(re.compile(re.escape(address) + "$"))
        expect(modal(page)).to_have_count(0)
        expect(page.locator("tr", has_text="Harbor Yoga")).to_have_count(1)
        assert kept(page) and entries(page) == before + 1

        page.go_forward()
        expect(page).to_have_url(re.compile(re.escape(address) + "$"))
        expect(modal(page)).to_have_count(0)  # Forward doesn't bring the Add client panel back
        assert kept(page)


@pytest.mark.parametrize("case", ["422", "409", "429", "503"])
def test_a_refused_form_fills_the_panel(page, channels, cap, case):
    page.goto("/clients")
    mark(page)
    page.get_by_role("link", name="Add client").click()
    expect(page.get_by_label("Client name")).to_be_visible()
    fill_add(page)
    location = "loc123"
    if case == "422":
        page.get_by_label("Drafts an hour").fill("abc")
    elif case == "409":
        location = "loc-cedarline"  # a HighLevel account that already belongs to a client
        page.get_by_label("HighLevel location ID").fill(location)
    elif case == "429":
        for _ in range(CHECKS_PER_MINUTE):
            cap.allow(1)
    else:
        channels.down = True
    page.evaluate("window.__marked_name = document.getElementById('add-name')")
    page.get_by_role("button", name="Add client").click()

    wait_for_fill(page, "name")
    expect(page.get_by_label("HighLevel token")).to_have_value("")  # never sent back
    expect(page.get_by_label("Client name")).to_have_value("Harbor Yoga")  # typed values are back
    expect(page.get_by_label("HighLevel location ID")).to_have_value(location)
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(r"/clients/new$"))
    assert kept(page)
    if case == "422":
        expect(page.get_by_label("Drafts an hour")).to_have_value("abc")
        expect(page.get_by_label("Drafts an hour")).to_have_attribute("aria-invalid", "true")
        expect(page.get_by_label("Drafts an hour")).to_be_focused()  # the first invalid field
    else:
        alert = page.locator("dialog#panel .ds-alert[role=alert]")
        expect(alert).to_have_text({"409": TAKEN_ERROR, "429": WAIT, "503": SLACK_DOWN}[case])
        expect(alert).to_be_focused()


@pytest.mark.parametrize("case", ["422", "409", "503"])
def test_a_refusal_after_the_panel_was_closed_still_says_so(page, db, channels, case):
    """A save refused with its form, whose answer comes after the person closed the panel, is not silent: an error toast
    says it wasn't saved. The list stays as it was, no panel opens, and the token is nowhere."""
    page.goto("/clients")
    mark(page)
    page.get_by_role("link", name="Add client").click()
    expect(page.get_by_label("Client name")).to_be_visible()
    fill_add(page)
    if case == "422":
        page.get_by_label("Drafts an hour").fill("abc")
    elif case == "409":
        page.get_by_label("HighLevel location ID").fill("loc-cedarline")
    else:
        channels.down = True
    held = hold_posts(page, NEW)
    page.get_by_role("button", name="Add client").click()
    wait_held(page, held)
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    settled(page)  # the closed panel is emptied already: an answer that filled it now would stay there

    held[0].continue_()
    words = {"422": "That wasn't saved. Open it again to try once more.", "409": TAKEN_ERROR, "503": SLACK_DOWN}[case]
    expect(toast_text(page)).to_have_text(words)
    expect(page.locator("#toast")).to_be_visible()
    beat(page)
    expect(modal(page)).to_have_count(0)  # no panel opens
    expect(page.locator("dialog#panel > *")).to_have_count(0)
    expect(page.locator("#all-clients .ds-page-header__count")).to_have_text("2")  # the list is as it was
    expect(page.get_by_role("link", name="Harbor Yoga")).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    assert kept(page) and all(row.display_name != "Harbor Yoga" for row in db.rows)  # (a refused add is still logged as a call)
    found = page.evaluate("""key => document.documentElement.outerHTML.includes(key) || location.href.includes(key)
        || JSON.stringify(history.state).includes(key)""", GHL_KEY)
    assert found is False


def test_a_refused_remove_empties_the_confirmation(page, db):
    open_cedarline(page)
    mark(page)
    page.get_by_role("link", name="Remove…").click()
    page.get_by_label("Type the client's name to confirm").fill("Cedarline")
    page.evaluate("window.__marked_confirm = document.getElementById('remove-confirm')")
    page.get_by_role("button", name="Remove client").click()

    wait_for_fill(page, "confirm")
    expect(page.locator("#remove-confirm")).to_have_value("")
    expect(page.locator("#remove-confirm-error")).to_have_text("Type Cedarline Roofing exactly to remove it.")
    expect(page.locator("#remove-confirm")).to_be_focused()
    expect(modal(page)).to_have_count(1)
    assert kept(page) and db.calls == []


@pytest.mark.parametrize("case", ["403", "404", "503"])
def test_a_refusal_without_a_panel_keeps_the_panel(page, db, case):
    sent = posts_sent(page)
    open_cedarline(page)
    mark(page)
    ask_to_pause(page, note="office move")
    if case == "403":
        heading, words = NOT_FROM_HERE
        page.route(PAUSE, lambda route: route.fulfill(
            status=403, content_type="text/html",
            body=f"<main><h1>{escape(heading)}</h1><p>{escape(words)}</p></main>"))
    elif case == "404":
        db.rows[:] = [row for row in db.rows if row.client_key != "cedarline"]  # a colleague removed it meanwhile
        words = GONE[1]
    else:
        def down(*args):
            raise psycopg.OperationalError("down")

        db.pause_client = down
        words = CHANGE_DOWN[1]
    page.get_by_role("button", name="Pause client").click()

    expect(toast_text(page)).to_have_text(words)  # the refusal page's own words
    expect(page.get_by_label("Reason")).to_have_value("drafts_wrong")  # typed values are kept
    expect(page.get_by_label("Note (optional)")).to_have_value("office move")
    expect(modal(page)).to_have_count(1)
    expect(page.get_by_role("button", name="Pause client")).to_be_focused()
    assert kept(page)
    assert len([url for url in sent if url.endswith("/clients/cedarline/pause")]) == 1  # not sent again


def test_no_answer_keeps_the_panel(page, db):
    sent = posts_sent(page)
    open_cedarline(page)
    mark(page)
    ask_to_pause(page, note="office move")
    page.route(PAUSE, lambda route: route.abort())
    page.get_by_role("button", name="Pause client").click()

    expect(toast_text(page)).to_have_text(NO_ANSWER)
    expect(page.get_by_label("Reason")).to_have_value("drafts_wrong")
    expect(page.get_by_label("Note (optional)")).to_have_value("office move")
    expect(modal(page)).to_have_count(1)
    expect(page.get_by_role("button", name="Pause client")).to_be_focused()
    assert kept(page) and db.calls == []
    beat(page)
    assert len([url for url in sent if url.endswith("/clients/cedarline/pause")]) == 1  # a failed POST is never retried


def test_no_answer_to_resume_puts_the_switch_back_off(page, db):
    """The switch sends at once, so a lost answer leaves it on beside a head that says Paused; turning it off to back out
    would send Resume again."""
    sent = posts_sent(page)
    page.goto("/clients")
    mark(page)
    name_link(page, "BrightSmile Dental").click()
    drafting = page.get_by_role("switch", name="Drafting")
    expect(drafting).not_to_be_checked()
    page.route(RESUME, lambda route: route.abort())
    drafting.click()

    expect(toast_text(page)).to_have_text(NO_ANSWER)
    expect(drafting).not_to_be_checked()
    expect(panel_status(page)).to_have_text("Paused")
    beat(page)
    assert len([url for url in sent if url.endswith("/clients/brightsmile/resume")]) == 1
    assert kept(page) and db.calls == []


def test_a_refused_redirect_to_another_origin_keeps_the_panel_and_says_so(page, db):
    """The expired pass, on a POST: the page never reloads, because the change may have been made."""
    open_cedarline(page)
    mark(page)
    ask_to_pause(page, note="office move")
    page.route(PAUSE, redirect_elsewhere)
    page.get_by_role("button", name="Pause client").click()

    expect(toast_text(page)).to_have_text(NO_ANSWER)
    beat(page)
    assert kept(page)
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(CEDARLINE)
    expect(page.get_by_label("Note (optional)")).to_have_value("office move")


# -- what must not happen --

def test_a_save_goes_once(page, db):
    open_cedarline(page)
    mark(page)
    ask_to_pause(page)
    held = hold_posts(page, PAUSE)
    page.get_by_role("button", name="Pause client").dblclick()
    wait_held(page, held)
    page.evaluate("() => { const form = document.getElementById('pause-confirm'); form.requestSubmit(); form.requestSubmit(); }")
    beat(page)
    assert len(held) == 1

    held[0].continue_()
    expect(panel_status(page)).to_have_text("Paused")
    beat(page)
    assert len(held) == 1 and kept(page)
    assert [call for call in db.calls if call[0] == "pause"] == [("pause", 1, "cedarline", "drafts_wrong", None)]


def test_flipping_the_drafting_switch_while_resuming_sends_once(page, db):
    page.goto("/clients")
    mark(page)
    name_link(page, "BrightSmile Dental").click()
    expect(page.get_by_role("switch", name="Drafting")).to_be_visible()
    held = hold_posts(page, RESUME)
    page.get_by_role("switch", name="Drafting").click()
    wait_held(page, held)
    page.evaluate("""() => {
      const run = document.getElementById('run');
      for (let i = 0; i < 2; i++) { run.checked = !run.checked; run.dispatchEvent(new Event('change', {bubbles: true})); }
    }""")
    beat(page)
    assert len(held) == 1

    held[0].continue_()
    expect(panel_status(page)).to_have_text("Drafting")
    beat(page)
    assert len(held) == 1 and kept(page)
    assert [call for call in db.calls if call[0] == "resume"] == [("resume", 1, "brightsmile")]


def test_the_panel_body_is_inert_while_sending(page, db):
    open_cedarline(page)
    ask_to_pause(page, note="office move")
    held = hold_posts(page, PAUSE)
    page.get_by_role("button", name="Pause client").click()
    wait_held(page, held)

    body = page.locator("dialog#panel .ds-sheet__body")
    assert body.evaluate("b => b.inert") is True
    expect(title_of(page)).to_be_focused()  # focus waits on the title meanwhile
    page.keyboard.type("zzz")  # typing elsewhere is refused
    expect(page.get_by_label("Note (optional)")).to_have_value("office move")
    assert page.get_by_role("link", name="Close panel").evaluate("link => link.closest('[inert]') === null")  # X is live

    held[0].abort()
    expect(toast_text(page)).to_have_text(NO_ANSWER)
    assert body.evaluate("b => b.inert") is False
    expect(page.get_by_role("button", name="Pause client")).to_be_focused()  # back on the sending control


def test_closing_while_sending_only_updates_the_list(page, db):
    page.goto("/clients")
    mark(page)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()
    ask_to_pause(page)
    held = hold_posts(page, PAUSE)
    page.get_by_role("button", name="Pause client").click()
    wait_held(page, held)
    page.get_by_role("link", name="Close panel").click()  # X still works while sending
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    settled(page)  # the closed panel is emptied already: an answer that filled it now would stay there

    held[0].continue_()
    expect(toast_text(page)).to_have_text("Paused Cedarline Roofing.")
    expect(tile_status(page, "Cedarline Roofing")).to_have_text("Paused: Drafts going wrong")  # the list was updated
    beat(page)
    expect(modal(page)).to_have_count(0)
    expect(page.locator("dialog#panel > *")).to_have_count(0)  # no panel
    expect(page).to_have_url(LIST_URL)
    assert kept(page) and status_of(db, "cedarline") == "paused"


def test_undo_after_close_only_updates_the_list(page, db):
    open_cedarline(page)
    mark(page)
    pause_in_panel(page)
    expect(toast_text(page)).to_have_text("Paused Cedarline Roofing.")
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    settled(page)
    page.locator("#toast").get_by_role("button", name="Undo").click()

    expect(toast_text(page)).to_have_text("Resumed Cedarline Roofing.")
    expect(tile_status(page, "Cedarline Roofing")).to_have_text("Drafting")
    beat(page)
    expect(modal(page)).to_have_count(0)  # no panel opens
    expect(page.locator("dialog#panel > *")).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    assert kept(page) and status_of(db, "cedarline") == "active"


def test_a_pause_then_a_limit_change_doesnt_throw(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    open_cedarline(page)
    mark(page)
    pause_in_panel(page)
    expect(panel_status(page)).to_have_text("Paused")  # filled in place: a page load would make everything consistent
    expect(tile_status(page, "Cedarline Roofing")).to_have_text("Paused: Drafts going wrong")
    limit_of(page).fill("35")
    limit_of(page).press("Enter")

    expect(toast_text(page)).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(page.locator("dialog#panel .ds-card__fill")).to_have_count(0)  # a paused client has no bar
    expect(page.locator("article.ds-card--tile", has_text="Cedarline Roofing").locator(".ds-card__fill")).to_have_count(0)
    assert errors == [] and kept(page)


def test_the_toast_and_undo_work_after_two_fills(page):
    open_cedarline(page)
    page.get_by_role("link", name="Replace token").click()
    expect(page.get_by_label("New HighLevel token")).to_be_focused()
    page.get_by_role("link", name="Cancel").click()
    expect(page.get_by_role("link", name="Replace token")).to_be_focused()
    limit_of(page).fill("35")
    limit_of(page).press("Enter")

    toast = page.locator("dialog#panel #toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    toast.get_by_role("button", name="Undo").click()
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")
    expect(limit_of(page)).to_have_value("30")


def test_the_token_is_only_in_the_post(page):
    """The token goes in three POST bodies and nowhere else: not an address, not history.state, not any
    answer, not the page once the panel is closed."""
    seen = []
    page.on("request", lambda request: seen.append((request.method, urlparse(request.url).path, request.post_data or "",
                                                    request.url)))
    answers = []
    page.on("response", lambda answer: answers.append(answer))
    page.goto("/clients")
    name_link(page, "Cedarline Roofing").click()
    page.get_by_role("link", name="Replace token").click()
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text("Connected to this HighLevel account.")
    page.get_by_role("button", name="Replace token").click()
    expect(toast_text(page)).to_have_text("New HighLevel token saved for Cedarline Roofing.")
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    page.get_by_role("link", name="Add client").click()
    fill_add(page)
    page.get_by_role("button", name="Test").click()
    expect(page.locator("#add-test-result")).to_have_text("Connected to this HighLevel account.")
    page.get_by_role("button", name="Add client").click()
    expect(toast_text(page)).to_have_text("Added Harbor Yoga. It drafts from its next message.")
    expect(modal(page)).to_have_count(0)
    expect(page.locator("input[type=password]")).to_have_count(0)  # emptied once the close transition has run

    carrying ={(method, path) for method, path, body, _ in seen if GHL_KEY in body}
    assert carrying == {("POST", "/clients/cedarline/key"), ("POST", "/clients/new"), ("POST", "/clients/new/test")}
    assert not any(GHL_KEY in url for *_, url in seen)
    read = 0
    for answer in answers:
        if answer.request.resource_type not in ("document", "fetch"):  # pages and the script's own requests, not fonts
            continue
        try:
            text = answer.text()
        except PlaywrightError:  # a redirect has no body to read
            continue
        read += 1
        assert GHL_KEY not in text
    assert read >= 4, "too few answers could be read: this check proves little"
    found = page.evaluate("""key => [...document.querySelectorAll('input, textarea')].some(field => field.value === key)
        || document.documentElement.outerHTML.includes(key) || location.href.includes(key)
        || JSON.stringify(history.state).includes(key)""", GHL_KEY)
    assert found is False


@pytest.mark.parametrize("path, data", [
    ("/clients/cedarline/pause", {"reason": "drafts_wrong"}),
    ("/clients/brightsmile/resume", {}),
    ("/clients/cedarline/key", {"key": "x"}),
    ("/clients/cedarline/remove", {"confirm": "Cedarline Roofing"}),
    ("/clients/new", {"name": "Harbor Yoga"}),
], ids=["pause", "resume", "key", "remove", "add"])
def test_the_same_origin_check_still_refuses(settings, keys, db, agent, channels, path, data):
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_connection_checker] = lambda: agent
    app.dependency_overrides[get_channel_lister] = lambda: channels
    private_key, kid, _ = keys
    headers = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE),
               "Origin": "http://other-origin.invalid"}
    answer = TestClient(app, headers=headers, follow_redirects=False).post(path, data=data)

    assert answer.status_code == 403
    assert "Change refused" in answer.text
    assert db.calls == []


# -- refusals and lost answers while saving inside the panel --

def refuse_posts(page: Page, pattern, how: str):
    """Answers the fetch POSTs that match with a 403 refusal page, or no answer at all; everything else goes through."""
    heading, words = NOT_FROM_HERE

    def handler(route):
        if route.request.method != "POST" or route.request.resource_type != "fetch":
            route.continue_()
        elif how == "a refusal":
            route.fulfill(status=403, content_type="text/html",
                          body=f"<main><h1>{escape(heading)}</h1><p>{escape(words)}</p></main>")
        else:
            route.abort()

    page.route(pattern, handler)
    return words if how == "a refusal" else NO_ANSWER


@pytest.mark.parametrize("how", ["a refusal", "no answer"])
@pytest.mark.parametrize("form", ["add", "replace"])
def test_a_refusal_without_a_panel_empties_the_token_field(page, form, how):
    """After a refusal or no answer the token field is empty, as after a refusal that brings the form
    back; the other typed values stay."""
    page.goto("/clients")
    mark(page)
    if form == "add":
        page.get_by_role("link", name="Add client").click()
        expect(page.get_by_label("Client name")).to_be_visible()
        fill_add(page)
        pattern, button = NEW, "Add client"
    else:
        name_link(page, "Cedarline Roofing").click()
        expect(limit_of(page)).to_be_visible()
        page.get_by_role("link", name="Replace token").click()
        page.get_by_label("New HighLevel token").fill(GHL_KEY)
        pattern, button = KEY, "Replace token"
    words = refuse_posts(page, pattern, how)
    page.get_by_role("button", name=button).click()

    expect(toast_text(page)).to_have_text(words)  # the toast's words are unchanged
    expect(page.locator("input[type=password]")).to_have_value("")
    expect(modal(page)).to_have_count(1)
    if form == "add":
        expect(page.get_by_label("Client name")).to_have_value("Harbor Yoga")  # the rest stays
        expect(page.get_by_label("HighLevel location ID")).to_have_value("loc123")
    assert kept(page)


def test_a_slow_reveal_cannot_replace_the_body_of_a_save_on_its_way(page):
    open_cedarline(page)
    reveal = hold_fetches(page, REVEAL_KEY)
    page.get_by_role("link", name="Replace token").click()
    wait_held(page, reveal)
    ask_to_pause(page)
    posts = hold_posts(page, PAUSE)
    page.get_by_role("button", name="Pause client").click()
    wait_held(page, posts)

    reveal[0].continue_()  # the reveal's answer comes while the save is on its way
    beat(page)
    assert page.locator("dialog#panel .ds-sheet__body").evaluate("b => b.inert") is True
    expect(page.locator("#add-key")).to_have_count(0)
    expect(page).to_have_url(CEDARLINE)
    posts[0].continue_()
    expect(panel_status(page)).to_have_text("Paused")
    expect(page.locator("#add-key")).to_have_count(0)


def test_saving_another_client_while_the_first_save_is_held(page, db):
    page.goto("/clients")
    mark(page)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()
    ask_to_pause(page)
    pauses = hold_posts(page, PAUSE)
    page.get_by_role("button", name="Pause client").click()
    wait_held(page, pauses)
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    name_link(page, "BrightSmile Dental").click()
    expect(page.get_by_role("switch", name="Drafting")).to_be_visible()
    resumes = hold_posts(page, RESUME)
    page.get_by_role("switch", name="Drafting").click()
    wait_held(page, resumes)  # sent, not dropped by the first client's save

    pauses[0].continue_()
    resumes[0].continue_()
    expect(panel_status(page)).to_have_text("Drafting")
    expect(tile_status(page, "Cedarline Roofing")).to_have_text("Paused: Drafts going wrong")
    assert status_of(db, "brightsmile") == "active" and status_of(db, "cedarline") == "paused"
    assert kept(page)


@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_an_undo_pressed_at_the_moment_of_closing_leaves_no_token(new_page, motion):
    """A save that makes no panel of its own (the Undo after a close) must not drop the emptying of the panel closed
    just before it."""
    page = new_page(reduced_motion=motion)
    open_cedarline(page)
    pause_in_panel(page)
    expect(toast_text(page)).to_have_text("Paused Cedarline Roofing.")
    page.get_by_role("link", name="Replace token").click()
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    page.evaluate("""() => {
      document.querySelector('dialog#panel a[aria-label="Close panel"]').click();
      document.querySelector('#toast .ds-toast__action').click();
    }""")
    expect(toast_text(page)).to_have_text("Resumed Cedarline Roofing.")

    expect(page.locator("input[type=password]")).to_have_count(0)
    expect(page.locator("dialog#panel > *")).to_have_count(0)
    assert page.evaluate("key => document.documentElement.outerHTML.includes(key)", GHL_KEY) is False


def test_the_busy_message_is_announced_outside_the_inert_body(page):
    open_cedarline(page)
    page.get_by_role("link", name="Replace token").click()
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    held = hold_posts(page, KEY)
    page.get_by_role("button", name="Replace token").click()
    wait_held(page, held)

    region = page.locator(".ds-toast__region[role=status]")
    expect(region).to_have_text("Testing the connection…")  # for a screen reader
    expect(page.locator("#add-test-result")).to_have_text("Testing the connection…")  # and the visible message
    assert region.evaluate("el => el.closest('[inert]') === null && el.closest('dialog#panel') !== null")

    held[0].abort()  # no answer: the busy words go, the error toast's own words stay
    expect(toast_text(page)).to_have_text(NO_ANSWER)
    expect(region).to_have_text("")


# -- dragging on phones --
# The moves come from page.mouse, and a cancel is dispatched. Quick moves read as a flick, so a slow release pauses before
# the button comes up, and a flick is one large step.

SLOW_RELEASE_MS = 200
PANEL_OFFSET = "new DOMMatrix(getComputedStyle(document.getElementById('panel')).transform).m42"


def phone_panel(new_page, width: int = 375) -> Page:
    page = new_page(viewport={"width": width, "height": 800})
    open_cedarline(page)
    settled(page)
    # Which pointer the page saw go down, for the events a test dispatches itself.
    page.evaluate("document.addEventListener('pointerdown', e => { window.__pointer = e.pointerId }, true)")
    return page


def grabber_of(page: Page):
    return page.locator("dialog#panel .ds-sheet__grabber")


def press(page: Page, part):
    box = part.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    return x, y


def panel_height(page: Page) -> float:
    return page.locator("dialog#panel").bounding_box()["height"]


def wait_for_offset(page: Page, want: float):
    wait_until(page, f"Math.abs({PANEL_OFFSET} - ({want})) < 1")


def slow_release(page: Page):
    page.wait_for_timeout(SLOW_RELEASE_MS)
    page.mouse.up()


def quick_move(page: Page, x: float, y: float, distance: float):
    """A move and its release a millisecond apart, dispatched in one go: page.mouse can't promise that speed, and two of
    its calls on a busy machine can be further apart than a flick lasts."""
    page.evaluate("""([x, y, distance]) => {
        const panel = document.getElementById('panel');
        const send = (type, clientY) => panel.dispatchEvent(
            new PointerEvent(type, {pointerId: window.__pointer, bubbles: true, clientX: x, clientY}));
        send('pointermove', y);
        const start = performance.now();
        while (performance.now() - start < 1) {}
        send('pointermove', y + distance);
        send('pointerup', y + distance);
    }""", [x, y, distance])


def expect_nothing_left_of_a_drag(page: Page, closed: bool = False):
    settled(page)
    assert page.evaluate("""(() => {
        const panel = document.getElementById('panel');
        return panel.style.transform === '' && !panel.classList.contains('ds-sheet--dragging');
    })()""")
    if not closed:  # a closed panel sits at the design system's own closed place
        assert page.evaluate(PANEL_OFFSET) == 0


def drag_closed(page: Page, part):
    x, y = press(page, part)
    distance = panel_height(page) * 0.6
    page.mouse.move(x, y + distance, steps=6)
    wait_for_offset(page, distance)  # it follows the finger
    assert page.evaluate("document.getElementById('panel').classList.contains('ds-sheet--dragging')")
    slow_release(page)
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)


def test_dragging_the_head_down_closes(new_page):
    page = phone_panel(new_page)
    drag_closed(page, title_of(page))


def test_dragging_the_grabber_down_closes(new_page):
    page = phone_panel(new_page)
    drag_closed(page, grabber_of(page))


def test_a_short_drag_snaps_back(new_page):
    """One quick step, then a hold: a finger that stopped before it let go is not a flick."""
    page = phone_panel(new_page)
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 40)
    wait_for_offset(page, 40)
    slow_release(page)
    expect(modal(page)).to_be_visible()
    expect(page).to_have_url(CEDARLINE)
    expect_nothing_left_of_a_drag(page)


def test_a_quick_flick_closes(new_page):
    page = phone_panel(new_page)
    distance = 100
    assert distance < panel_height(page) * 0.4  # too short to close without its speed
    x, y = press(page, title_of(page))
    quick_move(page, x, y, distance)
    page.mouse.up()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)


def test_a_drag_up_resists(new_page):
    page = phone_panel(new_page)
    x, y = press(page, grabber_of(page))
    pulled = min(60, y - 1)
    page.mouse.move(x, y - pulled, steps=4)
    wait_for_offset(page, -(pulled * 40) / (40 + pulled))
    slow_release(page)
    expect(modal(page)).to_be_visible()
    expect_nothing_left_of_a_drag(page)


def test_a_cancelled_drag_snaps_back_and_stays(new_page):
    page = phone_panel(new_page)
    x, y = press(page, title_of(page))
    distance = panel_height(page) * 0.6  # far enough to close, if a cancel counted as a release
    page.mouse.move(x, y + distance, steps=6)
    wait_for_offset(page, distance)
    page.evaluate("""([x, y]) => document.querySelector('#panel .ds-sheet__head').dispatchEvent(
        new PointerEvent('pointercancel', {pointerId: window.__pointer, bubbles: true, clientX: x, clientY: y}))""",
                  [x, y + distance])
    expect_nothing_left_of_a_drag(page)
    page.mouse.up()  # the drag is over: this is nothing
    beat(page)
    expect(modal(page)).to_be_visible()
    expect(page).to_have_url(CEDARLINE)


def test_a_tap_on_the_grabber_doesnt_close(new_page):
    page = phone_panel(new_page)
    press(page, grabber_of(page))
    page.mouse.up()
    beat(page)
    expect(modal(page)).to_be_visible()

    # A finger always wobbles a little, and fast: 2 px in a millisecond would be a flick, were it not a tap.
    x, y = press(page, grabber_of(page))
    quick_move(page, x, y, 2)
    page.mouse.up()
    beat(page)
    expect(modal(page)).to_be_visible()
    expect_nothing_left_of_a_drag(page)


def test_a_drag_doesnt_start_on_the_close_button(new_page):
    page = phone_panel(new_page)
    x, y = press(page, page.get_by_role("link", name="Close panel"))
    page.mouse.move(x, y + 200, steps=4)
    beat(page)
    assert page.evaluate(PANEL_OFFSET) == 0
    page.mouse.up()
    beat(page)
    expect(modal(page)).to_be_visible()


def test_a_drag_doesnt_start_at_desktop_width(new_page):
    page = phone_panel(new_page, width=1280)
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 200, steps=4)
    beat(page)
    assert page.evaluate(PANEL_OFFSET) == 0
    slow_release(page)
    expect(modal(page)).to_be_visible()
    expect_nothing_left_of_a_drag(page)


def test_a_drag_leaves_no_transform_behind(new_page):
    page = phone_panel(new_page)
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 40)
    wait_for_offset(page, 40)
    slow_release(page)
    expect_nothing_left_of_a_drag(page)

    drag_closed(page, title_of(page))
    settled(page)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()
    expect_nothing_left_of_a_drag(page)
    box = page.locator("dialog#panel").bounding_box()
    assert box["y"] + box["height"] == pytest.approx(800, abs=1)


def test_a_drag_selects_nothing(new_page):
    page = phone_panel(new_page)
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 40)
    wait_for_offset(page, 40)
    assert page.evaluate("String(getSelection())") == ""  # while the finger is down
    slow_release(page)
    assert page.evaluate("String(getSelection())") == ""
    expect_nothing_left_of_a_drag(page)


def test_a_drag_from_selected_text_still_follows(new_page):
    """WebKit starts a native text drag from selected text and never sends the pointer's end: the drag stuck."""
    page = phone_panel(new_page)
    page.evaluate("""() => getSelection().selectAllChildren(document.querySelector('#panel .ds-sheet__title'))""")
    assert page.evaluate("String(getSelection())") != ""
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 40, steps=4)
    wait_for_offset(page, 40)  # the panel follows
    slow_release(page)
    expect_nothing_left_of_a_drag(page)
    expect(modal(page)).to_be_visible()


def test_a_second_finger_doesnt_end_the_drag(new_page):
    page = phone_panel(new_page)
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 100, steps=4)
    wait_for_offset(page, 100)
    page.evaluate("""document.querySelector('#panel .ds-sheet__head').dispatchEvent(
        new PointerEvent('pointerup', {pointerId: window.__pointer + 7, bubbles: true}))""")
    assert page.evaluate(PANEL_OFFSET) == pytest.approx(100, abs=1)
    distance = panel_height(page) * 0.6
    page.mouse.move(x, y + distance, steps=4)
    wait_for_offset(page, distance)  # the first finger still drives it
    slow_release(page)
    expect(modal(page)).to_have_count(0)


def test_escape_during_a_drag_leaves_nothing_behind(new_page):
    page = phone_panel(new_page)
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 100, steps=4)
    wait_for_offset(page, 100)
    page.keyboard.press("Escape")
    expect(modal(page)).to_have_count(0)
    page.mouse.move(x, y + 300, steps=2)  # the finger is still down: the closed panel doesn't follow it
    beat(page)
    expect_nothing_left_of_a_drag(page, closed=True)
    page.mouse.up()
    settled(page)
    name_link(page, "Cedarline Roofing").click()
    expect(limit_of(page)).to_be_visible()
    expect_nothing_left_of_a_drag(page)


def test_a_fill_during_a_drag_keeps_following_the_finger(new_page):
    page = phone_panel(new_page)
    held = hold_fetches(page, REVEAL_KEY)
    page.get_by_role("link", name="Replace token").click()
    wait_held(page, held)
    page.evaluate("window.__marked_title = document.querySelector('#panel .ds-sheet__title')")
    x, y = press(page, title_of(page))
    page.mouse.move(x, y + 60, steps=3)
    wait_for_offset(page, 60)

    held[0].continue_()
    wait_for_fill(page, "title")

    page.mouse.move(x, y + 100, steps=3)
    wait_for_offset(page, 100)
    slow_release(page)
    expect(modal(page)).to_be_visible()
    expect_nothing_left_of_a_drag(page)


# ---- Typing that is not saved yet survives a save elsewhere in the panel ---------------------------------------------

def test_a_typed_token_never_comes_back_after_a_fill(page, db, pause_goes_to):
    """A pause is pressed with the Replace token form open (the Drafting switch stays live beside it). Its answer is
    redirected to the panel with the token form in it, as a form that survives the save would be. The token typed before
    comes back to nobody: the field is empty, and the script never read a password field to put it back."""
    pause_goes_to.append("/clients/cedarline?confirm=key")
    page.goto("/clients/cedarline?confirm=key")
    expect(page.get_by_label("New HighLevel token")).to_be_visible()
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    page.evaluate("""() => {  // counts each read of a password field's value from here on
        window.__password_reads = 0;
        const real = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        Object.defineProperty(HTMLInputElement.prototype, 'value', {
            ...real, get() { if (this.type === 'password') window.__password_reads += 1; return real.get.call(this); }});
    }""")
    ask_to_pause(page)

    page.evaluate("window.__marked_title = document.querySelector('#panel .ds-sheet__title')")
    page.get_by_role("button", name="Pause client").click()
    wait_for_fill(page, "title")

    assert status_of(db, "cedarline") == "paused"  # the pause itself went through
    assert page.evaluate("window.__password_reads") == 0
    expect(page.get_by_label("New HighLevel token")).to_have_count(1)
    expect(page.get_by_label("New HighLevel token")).to_have_value("")
    found = page.evaluate("key => document.documentElement.outerHTML.includes(key) || history.state && JSON.stringify(history.state).includes(key)", GHL_KEY)
    assert not found


def test_typing_in_a_hidden_form_is_not_put_back(page, db, agent):
    """The pause question after Keep drafting is hidden but still holds what was typed. A save elsewhere in the panel
    (the new token) must not put it back where nobody can see it: the panel would then refuse to close over it."""
    page.goto("/clients/cedarline?confirm=key")
    expect(page.get_by_label("New HighLevel token")).to_be_visible()
    ask_to_pause(page, note="office move")
    page.get_by_role("link", name="Keep drafting").click()
    expect(page.locator("#pause-confirm")).to_be_hidden()
    expect(page.locator("#pause-note")).to_have_value("office move")  # still in the hidden form
    page.get_by_label("New HighLevel token").fill(GHL_KEY)
    page.get_by_role("button", name="Replace token").click()

    expect(toast_text(page)).to_have_text("New HighLevel token saved for Cedarline Roofing.")
    expect(page.locator("#pause-confirm")).to_be_hidden()
    expect(page.locator("#pause-note")).to_have_value("")  # the fill's own, not the hidden typing
    assert [call[0] for call in db.calls] == ["replace_key"]
    page.mouse.click(40, 300)  # nothing visible is typed, so the dimmed area closes the panel
    expect(modal(page)).to_have_count(0)
