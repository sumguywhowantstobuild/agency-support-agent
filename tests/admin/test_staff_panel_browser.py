"""The staff panels in real browsers (Chromium, WebKit and Firefox through Playwright): a staff member and Add staff open
as the modal panel over the Staff list, on the same script as the client panel. The app runs on
127.0.0.1 with the fake database. Slow and failing answers come from holding or refusing the request with page.route, never
from timing. The made-up staff are Ana Reyes (signed in), Ben Cruz and Dee Park (deactivated).
Watch a run: pytest tests/admin/test_staff_panel_browser.py --headed --slowmo 500"""

import re
from dataclasses import replace
from html import escape

import psycopg
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from support_agent.admin.app import create_app
from support_agent.admin.auth import NOT_FROM_HERE, NOT_SIGNED_IN, FileKeys, get_key_source
from support_agent.admin.db import LastMember, Staff, get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.staff import CHANGE_DOWN, GONE, LAST_ERROR
from conftest import AUDIENCE
from fakes import ANA_MEMBER, FakeAdminDb, a_member
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)

BEN = a_member(2, "Ben Cruz", "ben@bluejay.example", "U0BEN")
DEE = a_member(3, "Dee Park", "dee@bluejay.example", "U0DEE", active=False)


@pytest.fixture
def db():
    db = FakeAdminDb(staff=(ANA_MEMBER, BEN, DEE))

    def whoever_the_list_says(email):  # as the real function reads the staff table: a new name, or a member taken off
        member = next((m for m in db.staff if m.email == email.lower() and m.active), None)
        return member and Staff(id=member.id, name=member.name, slack_user_id=member.slack_user_id)

    db.staff_for_email = whoever_the_list_says
    return db


@pytest.fixture
def sites():
    """What the browser said (Sec-Fetch-Site) on each request that came to an address asking for a message: (address, word)."""
    return []


@pytest.fixture
def team():
    """The Cloudflare team the app is behind: none (the test copy's own name), unless a test names one."""
    return None


@pytest.fixture
def new_page(browser, settings, keys, db, sites, team):
    """new_page(**context_options) -> a signed-in page in a context of its own (no-script ones included)."""

    def make_app(url):
        app = create_app()

        @app.middleware("http")
        async def note_where_it_came_from(request, call_next):
            if request.method == "GET" and any(word in request.url.query for word in ("done=", "added=")):
                sites.append((f"{request.url.path}?{request.url.query}", request.headers.get("sec-fetch-site")))
            return await call_next(request)

        here = settings.model_copy(update={"admin_origin": url} | ({"cf_team_domain": team} if team else {}))
        app.dependency_overrides[get_admin_settings] = lambda: here
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        return app

    private_key, kid, _ = keys
    issuer = {"issuer": f"https://{team}"} if team else {}
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE, **issuer)}
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


BEN_URL = re.compile(r"/staff/2$")
LIST_URL = re.compile(r"/staff$")
LIST_TITLE = "Staff · Bluejay support admin"


def modal(page: Page):
    return page.locator("dialog:modal")


def title_of(page: Page):
    return page.locator("dialog#panel .ds-sheet__title")


def name_link(page: Page, name: str):
    return page.get_by_role("link", name=name, exact=True)


# -- opening --

def test_a_row_opens_the_panel_over_the_list(page):
    page.goto("/staff")
    page.evaluate("window.__list = document.getElementById('results')")
    name_link(page, "Ben Cruz").click()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(BEN_URL)
    expect(title_of(page)).to_have_text("Ben Cruz")
    expect(title_of(page)).to_be_focused()
    expect(page).to_have_title(re.compile(r"^Ben Cruz"))
    assert page.evaluate("document.getElementById('results') === window.__list")  # the list was never touched


def test_add_staff_opens_the_panel(page):
    page.goto("/staff")
    page.evaluate("window.__list = document.getElementById('results')")
    page.get_by_role("link", name="Add staff").click()

    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(r"/staff/new$"))
    expect(title_of(page)).to_have_text("Add staff")
    expect(title_of(page)).to_be_focused()
    expect(page).to_have_title(re.compile(r"^Add staff"))
    assert page.evaluate("document.getElementById('results') === window.__list")  # in place: the list was never touched


# -- closing, and the address --

def open_ben(page: Page):
    page.goto("/staff")
    name_link(page, "Ben Cruz").click()
    expect(page.get_by_label("Work email")).to_be_visible()  # the panel is filled


def mark(page: Page):
    """A page load wipes the mark; opening, closing and saving in place keep it."""
    page.evaluate("window.__kept = true")


def kept(page: Page) -> bool:
    return page.evaluate("window.__kept === true")


def closed_on_the_list(page: Page, focused):
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    expect(page).to_have_title(LIST_TITLE)
    expect(focused).to_be_focused()
    assert kept(page)  # in place: no page load


def close_by(page: Page, way: str):
    if way == "X":
        page.get_by_role("link", name="Close panel").click()
    elif way == "Escape":
        page.keyboard.press("Escape")
    elif way == "Cancel":
        page.get_by_role("link", name="Cancel").click()
    else:
        page.mouse.click(40, 300)  # the dimmed list, left of the panel


@pytest.mark.parametrize("way", ["X", "Escape", "Cancel", "the dimmed area"])
@pytest.mark.parametrize("opens", ["a member", "Add staff"])
def test_each_way_of_closing_goes_back_to_the_list(page, opens, way):
    page.goto("/staff")
    opener = name_link(page, "Ben Cruz") if opens == "a member" else page.get_by_role("link", name="Add staff")
    opener.click()
    expect(modal(page)).to_have_count(1)
    expect(title_of(page)).to_be_focused()
    mark(page)
    close_by(page, way)

    closed_on_the_list(page, opener)


@pytest.mark.parametrize("way", ["X", "Escape", "Cancel", "the dimmed area"])
@pytest.mark.parametrize("path, focused", [("/staff/2", "Ben's row"), ("/staff/new", "the heading")])
def test_each_way_of_closing_after_a_direct_load_lands_on_staff(page, path, focused, way):
    """Nothing opened it, so focus goes to the same member's row, and, with no row (Add staff), to the page's heading."""
    page.goto(path)
    expect(modal(page)).to_have_count(1)
    mark(page)
    close_by(page, way)

    landing = name_link(page, "Ben Cruz") if focused == "Ben's row" else page.get_by_role("heading", name="Staff", level=1)
    closed_on_the_list(page, landing)


@pytest.mark.parametrize("opens", ["a member", "Add staff"])
def test_a_click_on_the_dimmed_area_keeps_what_was_typed(page, opens):
    page.goto("/staff")
    if opens == "a member":
        name_link(page, "Ben Cruz").click()
    else:
        page.get_by_role("link", name="Add staff").click()
    page.get_by_label("Name").fill("Benjamin Cruz")
    page.mouse.click(40, 300)

    expect(modal(page)).to_have_count(1)
    expect(page.get_by_label("Name")).to_have_value("Benjamin Cruz")
    page.get_by_role("link", name="Close panel").click()  # X still closes
    expect(modal(page)).to_have_count(0)


def test_back_closes_and_forward_opens(page):
    open_ben(page)
    page.go_back()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)

    page.go_forward()
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(BEN_URL)
    expect(page.get_by_label("Work email")).to_have_value("ben@bluejay.example")
    expect(title_of(page)).to_have_text("Ben Cruz")


@pytest.mark.parametrize("path, title", [("/staff/2", "Ben Cruz"), ("/staff/new", "Add staff")])
def test_a_direct_link_and_a_reload_show_the_panel_and_closing_lands_on_staff(page, path, title):
    page.goto(path)
    expect(modal(page)).to_have_count(1)
    expect(title_of(page)).to_have_text(title)
    sliding = page.evaluate("""document.getElementById('panel').getAnimations()
                               .filter(a => a.transitionProperty === 'transform').length""")
    assert sliding == 0  # a direct load doesn't slide in

    page.reload()
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(re.escape(path) + "$"))
    expect(title_of(page)).to_have_text(title)
    expect(page.locator("#results")).to_have_count(1)  # the list is behind it

    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    expect(page).to_have_title(LIST_TITLE)


def test_ctrl_click_stays_the_browsers(page):
    # Open: this test fails now and then in a whole run, in Firefox (no new tab shows, even after 15 s), and passes alone.
    # The cause is not found; a longer wait didn't help, so the wait is the default.
    page.goto("/staff")
    with page.context.expect_page() as opened:
        name_link(page, "Ben Cruz").click(modifiers=["Control"])

    other = opened.value
    other.reload()  # WebKit leaves the context's sign-in header off a ctrl-click tab's first request (the real pass comes from Cloudflare)
    expect(other.locator("dialog:modal")).to_have_count(1)  # a new tab shows the panel over its own list
    expect(other).to_have_url(BEN_URL)
    expect(modal(page)).to_have_count(0)  # this page has none
    expect(page).to_have_url(LIST_URL)


# -- a message an address asks for --

@pytest.mark.parametrize("scripts", [True, False], ids=["the script's fetch", "a form post, no script"])
def test_the_browser_says_same_origin_on_the_redirect_after_a_save_and_a_typed_address_gets_no_message(
        new_page, sites, scripts):
    """The server draws a message and its Undo only for Sec-Fetch-Site: same-origin (auth.made_by_this_app). The real flows
    must carry it, in every browser, or no message would ever show: the redirect after a form post, and the script's fetch.
    An address typed in the bar (or a link from elsewhere) is "none" or "cross-site", and shows the panel without them."""
    page = new_page(java_script_enabled=scripts)
    page.goto("/staff/2")
    page.get_by_role("button", name="Deactivate").click()

    expect(page).to_have_url(BEN_URL if scripts else re.compile(r"/staff/2\?done=deactivated$"))
    expect(page.locator("#toast")).to_have_attribute("data-toast", re.compile("^Deactivated Ben Cruz\\.")) if not scripts \
        else expect(toast_text(page)).to_contain_text("Deactivated Ben Cruz.")
    expect(page.locator("form#undo")).to_have_count(1)  # the message came with its Undo
    assert sites == [("/staff/2?done=deactivated", "same-origin")]

    sites.clear()
    page.goto("/staff/3?done=deactivated")  # typed: Dee is deactivated, and this is the address that would offer to bring her back
    expect(page.locator("dialog#panel[open]")).to_have_count(1)  # the panel itself still shows (modal only with the script)
    expect(page.locator("form#undo")).to_have_count(0)
    expect(page.locator("#toast")).not_to_have_attribute("data-toast", re.compile(".+"))
    assert sites == [("/staff/3?done=deactivated", "none")]


# -- saving inside the panel --

EDIT = re.compile(r"/staff/2/edit$")
ACTIVE = re.compile(r"/staff/2/active$")
NEW = re.compile(r"/staff/new$")
# The limit's own words, for a change that may not have been made.
NO_ANSWER = "Couldn't reach the admin app, so the change may not have been made. Reload the page to check."


def toast_text(page: Page):
    return page.locator(".ds-toast__text")


def entries(page: Page) -> int:
    return page.evaluate("history.length")


def row_of(page: Page, name: str):
    return page.locator("#results tr", has_text=name)


@pytest.mark.parametrize("who, address, new_name", [("Ben", "/staff/2", "Benjamin Cruz"), ("Ana", "/staff/1", "Ana R. Reyes")],
                         ids=["a colleague", "yourself"])
def test_saving_stays_in_the_panel_and_updates_the_row(page, db, who, address, new_name):
    page.goto("/staff")
    mark(page)
    before = entries(page)
    page.locator("#results").get_by_role("link", name=f"{who} ", exact=False).click()
    expect(page.get_by_label("Work email")).to_be_visible()
    page.get_by_label("Name").fill(new_name)
    page.get_by_role("button", name="Save").click()

    expect(toast_text(page)).to_have_text(f"Saved {new_name}.")
    expect(title_of(page)).to_have_text(new_name)  # a name can change, which a client's can't
    expect(page).to_have_title(re.compile(f"^{new_name}"))
    expect(page.get_by_label("Name")).to_have_value(new_name)  # filled again from what was saved
    expect(row_of(page, new_name)).to_have_count(1)  # the row behind
    expect(page.locator(".ds-side-nav__who")).to_contain_text(new_name if who == "Ana" else "Ana Reyes")
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(re.compile(re.escape(address) + "$"))  # ?done is not in the address
    assert kept(page) and entries(page) == before + 1  # no page load; the panel's own entry only
    assert [call[0] for call in db.calls] == ["edit_staff"]


@pytest.mark.parametrize("team", ["example-team.cloudflareaccess.com"])
def test_saving_your_own_name_keeps_the_person_menu_working(new_page, team):
    """The person area is refreshed in place after a save of one's own name: the new
    region still opens its menu (name and Sign out), and the phone's avatar shows the new initial."""
    page = new_page(viewport={"width": 1280, "height": 800})
    page.goto("/staff")
    menu = page.locator("#person-menu")
    page.locator("#results").get_by_role("link", name="Ana ", exact=False).click()
    page.get_by_label("Name").fill("Bea Reyes")
    page.get_by_role("button", name="Save").click()
    expect(toast_text(page)).to_have_text("Saved Bea Reyes.")
    expect(page.locator(".ds-side-nav__person .ds-side-nav__avatar")).to_have_text("B")  # the refreshed region
    close_by(page, "X")
    expect(modal(page)).to_have_count(0)
    # The "Saved" message has no Undo, so it goes by itself after 6 s. An Escape here closes it if it is still showing and
    # does nothing if it went already (the panel is closed), so the Escape below can't depend on how fast the run is (a
    # toast taking the first Escape is test_a_toast_takes_the_first_escape's).
    page.keyboard.press("Escape")
    expect(toast_text(page)).to_be_hidden()

    person = page.locator(".ds-side-nav__person")
    expect(person).to_have_accessible_name("Bea Reyes, Support team, account menu")
    person.click()
    expect(menu).to_be_visible()
    expect(menu.locator(".ds-side-nav__menu-name")).to_have_text("Bea Reyes")
    expect(menu.get_by_role("link", name="Sign out")).to_have_attribute("href", "/cdn-cgi/access/logout")
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()

    page.set_viewport_size({"width": 375, "height": 800})  # the phone shows the new initial, as the avatar alone
    expect(person).to_be_visible()
    expect(person.locator(".ds-side-nav__avatar")).to_have_text("B")
    expect(person.locator(".ds-side-nav__label")).to_be_hidden()
    person.click()
    expect(menu).to_be_visible()
    expect(menu.get_by_role("link", name="Sign out")).to_be_visible()


def fill_add(page: Page, name: str = "Cara Lim", email: str = "cara@bluejay.example", slack: str = "u0cara"):
    page.get_by_label("Name").fill(name)
    page.get_by_label("Work email").fill(email)
    page.get_by_label("Slack member ID").fill(slack)


def add_button(page: Page):
    return page.locator("dialog#panel").get_by_role("button", name="Add staff")


def test_adding_closes_the_panel_and_shows_the_new_row(page, db):
    page.goto("/staff")
    mark(page)
    before = entries(page)
    page.get_by_role("link", name="Add staff").click()
    expect(page.get_by_label("Name")).to_be_visible()
    fill_add(page)
    add_button(page).click()

    expect(modal(page)).to_have_count(0)
    expect(toast_text(page)).to_have_text("Added Cara Lim. They can sign in and approve drafts now.")
    expect(row_of(page, "Cara Lim")).to_have_count(1)  # the new row, behind
    expect(page.locator("#staff-count")).to_have_text("4")
    expect(page).to_have_url(LIST_URL)
    expect(page.get_by_role("link", name="Add staff")).to_be_focused()
    assert kept(page) and entries(page) == before + 1  # the panel's entry now shows the list: nothing pushed
    assert [call[0] for call in db.calls] == ["add_staff"]


def panel_status(page: Page):
    return page.locator("dialog#panel .ds-sheet__head .ds-status__label")


def row_status(page: Page, name: str):
    return row_of(page, name).locator(".ds-status__label")


def in_panel_toast(page: Page):
    return page.locator("dialog#panel #toast")


def test_deactivate_offers_undo_and_undo_reactivates(page, db):
    open_ben(page)
    mark(page)
    before = entries(page)
    page.get_by_role("button", name="Deactivate").click()

    expect(panel_status(page)).to_have_text("Deactivated")  # the panel stays, showing the new state
    expect(in_panel_toast(page).locator(".ds-toast__text")).to_have_text(
        "Deactivated Ben Cruz. They can't sign in or approve drafts from their next click.")
    expect(in_panel_toast(page).get_by_role("button", name="Undo")).to_be_visible()
    expect(row_status(page, "Ben Cruz")).to_have_text("Deactivated")  # the row behind
    expect(page.get_by_role("button", name="Reactivate")).to_be_focused()  # the new button
    expect(page).to_have_url(BEN_URL)
    assert kept(page) and entries(page) == before and db.staff[1].active is False

    in_panel_toast(page).get_by_role("button", name="Undo").click()
    expect(panel_status(page)).to_have_text("Active")
    expect(in_panel_toast(page).locator(".ds-toast__text")).to_have_text("Reactivated Ben Cruz.")
    expect(row_status(page, "Ben Cruz")).to_have_text("Active")
    expect(page.get_by_role("button", name="Deactivate")).to_be_focused()
    expect(in_panel_toast(page).get_by_role("button", name="Undo")).to_be_visible()  # and that can be undone too
    expect(modal(page)).to_have_count(1)
    assert kept(page) and db.staff[1].active is True
    assert db.calls == [("set_staff_active", 1, 2, False), ("set_staff_active", 1, 2, True)]


def test_a_refused_undo_says_why_and_keeps_the_panel(page, db):
    """The Undo form sits outside the panel, so its refusal is a toast with the reason; the panel stays, and nothing typed
    in it belongs to the Undo, so what is typed is kept."""
    page.goto("/staff")
    mark(page)
    name_link(page, "Dee Park").click()
    page.get_by_role("button", name="Reactivate").click()
    expect(in_panel_toast(page).locator(".ds-toast__text")).to_have_text("Reactivated Dee Park.")

    def last(by, staff_id, active):  # as test_staff.py: the database refuses the last active member
        raise LastMember(staff_id)

    db.set_staff_active = last
    page.get_by_label("Name").fill("Dee X Park")
    in_panel_toast(page).get_by_role("button", name="Undo").click()

    expect(toast_text(page)).to_have_text(LAST_ERROR.format("Dee Park"))
    expect(page.get_by_label("Name")).to_have_value("Dee X Park")  # what was typed stays
    expect(modal(page)).to_have_count(1)
    expect(panel_status(page)).to_have_text("Active")  # nothing changed
    assert page.locator("dialog#panel .ds-sheet__body").evaluate("body => body.inert") is False
    assert kept(page) and [member.active for member in db.staff] == [True, True, True]


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


def test_two_quick_saves_send_once(page, db):
    open_ben(page)
    mark(page)
    type_in_details(page)
    held = hold_posts(page, EDIT)
    page.get_by_role("button", name="Save").dblclick()
    wait_held(page, held)
    page.evaluate("""() => { const form = document.querySelector('form[action$="/edit"]'); form.requestSubmit(); form.requestSubmit(); }""")
    beat(page)
    assert len(held) == 1  # a second while one is on its way is dropped

    held[0].continue_()
    expect(toast_text(page)).to_have_text("Saved Benjamin Cruz.")
    beat(page)
    assert len(held) == 1 and kept(page)
    assert [call[0] for call in db.calls] == ["edit_staff"]


def test_closing_while_saving_updates_the_list(page, db):
    open_ben(page)
    mark(page)
    type_in_details(page)
    held = hold_posts(page, EDIT)
    page.get_by_role("button", name="Save").click()
    wait_held(page, held)
    page.get_by_role("link", name="Close panel").click()  # X still works while saving
    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    settled(page)  # the closed panel is emptied already: an answer that filled it now would stay there

    held[0].continue_()
    expect(toast_text(page)).to_have_text("Saved Benjamin Cruz.")
    expect(row_of(page, "Benjamin Cruz")).to_have_count(1)  # the list was updated
    beat(page)
    expect(modal(page)).to_have_count(0)
    expect(page.locator("dialog#panel > *")).to_have_count(0)  # no panel
    expect(page).to_have_url(LIST_URL)
    assert kept(page) and db.staff[1].name == "Benjamin Cruz"


def test_a_name_with_markup_stays_text(page, db):
    """A name is only ever text: in the row, the panel's title and the toast, whether the page was drawn by the server or
    its pieces were lifted out of a fetched answer."""
    page.goto("/staff")
    mark(page)
    page.get_by_role("link", name="Add staff").click()
    fill_add(page, name="<b>Mal</b>", email="mal@bluejay.example", slack="u0mal")
    add_button(page).click()

    expect(toast_text(page)).to_have_text("Added <b>Mal</b>. They can sign in and approve drafts now.")
    expect(row_of(page, "<b>Mal</b>").locator("a.ds-table__name")).to_have_text("<b>Mal</b>")
    name_link(page, "<b>Mal</b>").click()  # a fetched panel
    expect(title_of(page)).to_have_text("<b>Mal</b>")
    page.get_by_label("Name").fill("<i>Mal</i>")
    page.get_by_role("button", name="Save").click()
    expect(toast_text(page)).to_have_text("Saved <i>Mal</i>.")
    expect(title_of(page)).to_have_text("<i>Mal</i>")
    expect(row_of(page, "<i>Mal</i>").locator("a.ds-table__name")).to_have_text("<i>Mal</i>")
    expect(page.locator("b, i")).to_have_count(0)  # no element was made from any of them
    assert kept(page)

    page.reload()  # and the server's own page
    expect(title_of(page)).to_have_text("<i>Mal</i>")
    expect(page.locator("b, i")).to_have_count(0)


def wait_for_fill(page: Page, marker: str):
    """Waits until a fill has replaced the element the test stored as window.__marked_<marker> before it sent."""
    for _ in range(500):
        if page.evaluate(f"!document.contains(window.__marked_{marker})"):
            return
        page.wait_for_timeout(10)
    raise AssertionError(f"the panel was never filled again ({marker})")


REFUSED = {  # a refusal with its form: the field it marks, the words, and the value the field is sent back with
    "422": ("#staff-slack", "A Slack member ID starts with U or W, then capital letters and digits, like U04ABC123.", "BAD"),
    "409": ("#staff-email", "Ana Reyes already has that email. Nothing was saved.", "ana@bluejay.example"),
}


@pytest.mark.parametrize("case", REFUSED)
def test_a_refused_save_stays_with_what_was_typed(page, db, case):
    field, words, sent_back = REFUSED[case]
    open_ben(page)
    mark(page)
    page.get_by_label("Name").fill("Benjamin Cruz")
    page.locator(field).fill("bad" if case == "422" else "ana@bluejay.example")
    page.evaluate("window.__marked_name = document.getElementById('staff-name')")
    page.get_by_role("button", name="Save").click()

    wait_for_fill(page, "name")
    expect(page.get_by_label("Name")).to_have_value("Benjamin Cruz")  # typed values are back
    expect(page.locator(field)).to_have_value(sent_back)
    expect(page.locator(field)).to_have_attribute("aria-invalid", "true")
    expect(page.locator(f"{field}-error")).to_have_text(words)
    expect(page.locator(field)).to_be_focused()  # the first invalid field
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(BEN_URL)
    assert kept(page)  # in the panel: no page load
    assert all(member.name != "Benjamin Cruz" for member in db.staff)


@pytest.mark.parametrize("case", REFUSED)
def test_a_refused_add_stays_with_what_was_typed(page, db, case):
    field, words, sent_back = REFUSED[case]
    page.goto("/staff")
    mark(page)
    page.get_by_role("link", name="Add staff").click()
    expect(page.get_by_label("Name")).to_be_visible()
    fill_add(page, slack="bad" if case == "422" else "u0cara")
    if case == "409":
        page.get_by_label("Work email").fill("ana@bluejay.example")
    page.evaluate("window.__marked_name = document.getElementById('staff-name')")
    add_button(page).click()

    wait_for_fill(page, "name")
    expect(page.get_by_label("Name")).to_have_value("Cara Lim")
    expect(page.locator(field)).to_have_value(sent_back)
    expect(page.locator(field)).to_have_attribute("aria-invalid", "true")
    expect(page.locator(f"{field}-error")).to_have_text(words)
    expect(page.locator(field)).to_be_focused()
    expect(modal(page)).to_have_count(1)
    expect(page).to_have_url(NEW)
    assert kept(page) and len(db.staff) == 3
    page.mouse.click(40, 300)  # a refused form is work that is still there: the dimmed area leaves it
    expect(modal(page)).to_have_count(1)


def test_undo_after_the_panel_closed_updates_the_list(page, db):
    open_ben(page)
    mark(page)
    page.get_by_role("button", name="Deactivate").click()
    expect(toast_text(page)).to_contain_text("Deactivated Ben Cruz.")
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    settled(page)
    expect(row_status(page, "Ben Cruz")).to_have_text("Deactivated")
    page.locator("#toast").get_by_role("button", name="Undo").click()

    expect(toast_text(page)).to_have_text("Reactivated Ben Cruz.")
    expect(row_status(page, "Ben Cruz")).to_have_text("Active")  # the list behind it follows
    beat(page)
    expect(modal(page)).to_have_count(0)  # no panel opens
    expect(page.locator("dialog#panel > *")).to_have_count(0)
    expect(page).to_have_url(LIST_URL)
    assert kept(page) and db.staff[1].active is True


def test_you_cant_deactivate_yourself_in_the_panel(page, db):
    page.goto("/staff")
    mark(page)
    name_link(page, "Ana Reyes").click()
    expect(page.get_by_label("Work email")).to_be_visible()
    page.get_by_role("button", name="Deactivate").click()

    alert = page.locator("dialog#panel .ds-alert[role=alert]")
    expect(alert).to_have_text("You can't deactivate yourself. Ask another member to do it.")
    expect(alert).to_be_focused()
    expect(modal(page)).to_have_count(1)
    expect(panel_status(page)).to_have_text("Active")
    expect(page.locator("#toast").get_by_role("button", name="Undo")).to_be_hidden()
    assert kept(page) and db.calls == [] and db.staff[0].active is True


def test_no_answer_keeps_what_was_typed(page, db):
    sent = posts_sent(page)
    open_ben(page)
    mark(page)
    type_in_details(page)
    page.route(EDIT, lambda route: route.abort())
    page.get_by_role("button", name="Save").click()

    expect(toast_text(page)).to_have_text(NO_ANSWER)
    still_typed(page)
    expect(modal(page)).to_have_count(1)
    expect(page.get_by_role("button", name="Save")).to_be_focused()
    assert kept(page) and db.calls == []
    beat(page)
    assert len([url for url in sent if url.endswith("/staff/2/edit")]) == 1  # a failed POST is never retried


@pytest.mark.parametrize("action, taken", [
    ("/staff/2/edit", True), ("/staff/2/active", True), ("/staff/new", True),   # the three save addresses
    ("/clients/cedarline/pause", False), ("/staff/2/delete", False), ("/staff/abc/edit", False),
    ("/staff/2/edit/", False), ("/staff/2/edit?x=1", True), ("/staff/new/test", False), ("/history", False),
    ("http://other.invalid/staff/2/edit", False),
])
def test_only_staff_save_addresses_go_by_fetch(page, action, taken):
    """A form in the panel to any address but the three that save is left to the browser, as is a form to another origin.
    (The query is not part of the address a form is judged by: the server reads only the body.)"""
    open_ben(page)
    outcome = page.evaluate("""(action) => {
      let defaultPrevented = null;
      // Last in line, after the page's own handler on the document: records whether it took the submit, then keeps the
      // browser from leaving so the test can read it.
      window.addEventListener('submit', event => { defaultPrevented = event.defaultPrevented; event.preventDefault(); });
      const form = document.createElement('form');
      form.method = 'post';
      form.action = action;
      document.querySelector('dialog#panel .ds-sheet__body').append(form);
      form.requestSubmit();
      return defaultPrevented;
    }""", action)

    assert outcome is taken


def test_a_form_outside_the_panel_is_the_browsers_unless_it_is_the_undo(page):
    open_ben(page)
    outcome = page.evaluate("""() => {
      const seen = {};
      window.addEventListener('submit', event => { seen[event.target.id] = event.defaultPrevented; event.preventDefault(); });
      for (const id of ['loose', 'undo']) {
        const form = document.createElement('form');
        form.id = id;
        form.method = 'post';
        form.action = '/staff/2/active';
        document.body.append(form);
        form.requestSubmit();
      }
      return seen;
    }""")

    assert outcome == {"loose": False, "undo": True}


@pytest.mark.parametrize("way", ["X", "Escape", "Cancel"])
@pytest.mark.parametrize("path, form", [("/staff/2", "form[action$='/edit']"), ("/staff/new", "form[action$='/new']")],
                         ids=["a member", "Add staff"])
def test_closing_a_panel_that_stands_alone_lands_on_staff(page, path, form, way):
    page.goto(path)
    page.get_by_label("Name").fill("")
    page.evaluate("form => document.querySelector(form).submit()", form)  # refused: the panel alone, no list behind
    expect(page.locator("#staff-name-error")).not_to_be_empty()
    expect(page.locator("#results")).to_have_count(0)
    expect(modal(page)).to_have_count(1)
    close_by(page, way)

    expect(page).to_have_url(LIST_URL)
    expect(page.locator("#results")).to_have_count(1)  # Staff, as a page
    expect(modal(page)).to_have_count(0)


SLOW_RELEASE_MS = 200
PANEL_OFFSET = "new DOMMatrix(getComputedStyle(document.getElementById('panel')).transform).m42"


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


@pytest.mark.parametrize("part", [".ds-sheet__title", ".ds-sheet__grabber"], ids=["the head", "the grabber"])
@pytest.mark.parametrize("path, name", [("/staff", "Ben Cruz"), ("/staff/new", None)], ids=["a member", "Add staff"])
def test_a_drag_on_a_phone_closes_it(new_page, path, name, part):
    page = new_page(viewport={"width": 375, "height": 800})
    page.goto("/staff")
    if name:
        name_link(page, name).click()
    else:
        page.get_by_role("link", name="Add staff").click()
    expect(modal(page)).to_have_count(1)
    settled(page)
    box = page.locator(f"dialog#panel {part}").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    distance = page.locator("dialog#panel").bounding_box()["height"] * 0.6
    page.mouse.move(x, y + distance, steps=6)
    wait_until(page, f"Math.abs({PANEL_OFFSET} - ({distance})) < 1")  # it follows the finger
    page.wait_for_timeout(SLOW_RELEASE_MS)
    page.mouse.up()

    expect(modal(page)).to_have_count(0)
    expect(page).to_have_url(LIST_URL)


def use_add(page: Page, db):
    page.goto("/staff/new")
    fill_add(page)
    add_button(page).click()
    expect(page).to_have_url(re.compile(r"/staff\?added=4$"))
    expect(page.locator("#toast")).to_have_attribute("data-toast", "Added Cara Lim. They can sign in and approve drafts now.")
    expect(row_of(page, "Cara Lim")).to_have_count(1)
    assert [call[0] for call in db.calls] == ["add_staff"]


def use_save(page: Page, db):
    page.goto("/staff/2")
    page.get_by_label("Name").fill("Benjamin Cruz")
    page.get_by_role("button", name="Save").click()
    expect(page).to_have_url(re.compile(r"/staff/2\?done=saved$"))
    expect(page.locator("#toast")).to_have_attribute("data-toast", "Saved Benjamin Cruz.")
    expect(title_of(page)).to_have_text("Benjamin Cruz")
    assert db.staff[1].name == "Benjamin Cruz"


def use_deactivate(page: Page, db):
    page.goto("/staff/2")
    page.get_by_role("button", name="Deactivate").click()
    expect(page).to_have_url(re.compile(r"/staff/2\?done=deactivated$"))
    expect(page.locator("#toast")).to_have_attribute("data-toast", re.compile("^Deactivated Ben Cruz\\."))
    expect(panel_status(page)).to_have_text("Deactivated")
    expect(page.locator("form#undo[action='/staff/2/active']")).to_have_count(1)  # Undo is a form: it works as one too
    assert db.staff[1].active is False


def use_reactivate(page: Page, db):
    page.goto("/staff/3")
    page.get_by_role("button", name="Reactivate").click()
    expect(page).to_have_url(re.compile(r"/staff/3\?done=reactivated$"))
    expect(panel_status(page)).to_have_text("Active")
    assert db.staff[2].active is True


def use_undo(page: Page, db):
    page.goto("/staff/2")
    page.get_by_role("button", name="Deactivate").click()
    expect(page.locator("form#undo")).to_have_count(1)
    page.locator("form#undo").evaluate("form => form.requestSubmit()")  # what the Undo button does with the script
    expect(page).to_have_url(re.compile(r"/staff/2\?done=reactivated$"))
    assert db.staff[1].active is True


def use_refused_save(page: Page, db):
    page.goto("/staff/2")
    page.get_by_label("Name").fill("Benjamin Cruz")
    page.get_by_label("Slack member ID").fill("bad")
    page.get_by_role("button", name="Save").click()
    expect(page).to_have_url(re.compile(r"/staff/2/edit$"))
    expect(page.locator("#staff-slack-error")).to_have_text(
        "A Slack member ID starts with U or W, then capital letters and digits, like U04ABC123.")
    expect(page.get_by_label("Name")).to_have_value("Benjamin Cruz")  # what was typed stays
    expect(page.get_by_label("Slack member ID")).to_have_value("BAD")
    assert db.calls == []


def use_refused_add(page: Page, db):
    page.goto("/staff/new")
    fill_add(page, email="ben@bluejay.example")
    add_button(page).click()
    expect(page).to_have_url(NEW)
    expect(page.locator("#staff-email-error")).to_have_text("Ben Cruz already has that email. Nothing was saved.")
    expect(page.get_by_label("Name")).to_have_value("Cara Lim")
    expect(page.get_by_label("Slack member ID")).to_have_value("U0CARA")
    assert len(db.staff) == 3


@pytest.fixture(params=["no-javascript", "script-refused"])
def bare_page(request, new_page) -> Page:
    """A page where admin.js doesn't run: scripts off in the browser, or the file refused as if it failed to load."""
    if request.param == "no-javascript":
        return new_page(java_script_enabled=False)
    page = new_page()
    page.context.route("**/static/**/admin.js", lambda route: route.abort())
    return page


@pytest.mark.parametrize("flow", [use_add, use_save, use_deactivate, use_reactivate, use_undo, use_refused_save,
                                  use_refused_add],
                         ids=["add", "save", "deactivate", "reactivate", "undo", "refused-save", "refused-add"])
def test_every_form_works_without_the_script(bare_page, db, flow):
    flow(bare_page, db)


def test_the_history_link_leaves_for_history(page):
    open_ben(page)
    mark(page)
    page.get_by_role("link", name="Changes made by Ben Cruz").click()

    expect(page).to_have_url(re.compile(r"/history\?person=Ben%20Cruz$"))  # (the address keeps the name until History's own round)
    expect(page.get_by_role("heading", name="History", level=1)).to_be_visible()
    expect(modal(page)).to_have_count(0)
    assert not kept(page)  # a page load: the browser's own link


def posts_sent(page: Page) -> list:
    """The address of every POST this page sends from now on (a held or aborted one counts)."""
    sent = []
    page.on("request", lambda request: sent.append(request.url) if request.method == "POST" else None)
    return sent


def type_in_details(page: Page):
    page.get_by_label("Name").fill("Benjamin Cruz")
    page.get_by_label("Work email").fill("benjamin@bluejay.example")


def still_typed(page: Page):
    expect(page.get_by_label("Name")).to_have_value("Benjamin Cruz")  # typed values are kept
    expect(page.get_by_label("Work email")).to_have_value("benjamin@bluejay.example")


@pytest.mark.parametrize("case", ["403", "404", "503", "taken off the list"])
def test_a_refusal_without_a_panel_keeps_what_was_typed(page, db, case):
    sent = posts_sent(page)
    open_ben(page)
    mark(page)
    type_in_details(page)
    if case == "403":
        heading, words = NOT_FROM_HERE
        page.route(EDIT, lambda route: route.fulfill(
            status=403, content_type="text/html",
            body=f"<main><h1>{escape(heading)}</h1><p>{escape(words)}</p></main>"))
    elif case == "404":
        db.staff[:] = [member for member in db.staff if member.id != 2]  # gone meanwhile
        words = GONE[1]
    elif case == "503":
        def down(*args):
            raise psycopg.OperationalError("down")

        db.edit_staff = down
        words = CHANGE_DOWN[1]
    else:
        db.staff[0] = replace(db.staff[0], active=False)  # a colleague deactivated Ana while the panel was open
        words = NOT_SIGNED_IN[1]
    page.get_by_role("button", name="Save").click()

    expect(toast_text(page)).to_have_text(words)  # the refusal page's own words
    still_typed(page)
    expect(modal(page)).to_have_count(1)
    expect(page.get_by_role("button", name="Save")).to_be_focused()
    assert kept(page) and all(member.name != "Benjamin Cruz" for member in db.staff)  # nothing was saved
    assert len([url for url in sent if url.endswith("/staff/2/edit")]) == 1  # not sent again


@pytest.mark.parametrize("opens, dropped", [("Dee Park", True), ("Add staff", True), ("Ben Cruz", False)],
                         ids=["another member", "Add staff", "the same member"])
@pytest.mark.parametrize("late", [False, True], ids=["answered", "answer still on its way"])
def test_opening_another_member_drops_an_open_undo(page, db, opens, dropped, late):
    """An Undo acts on one member: with another's panel in front it could be pressed for someone out of view. That holds
    for an answer that arrives after the other panel opened: it brings no Undo into it."""
    open_ben(page)
    held = hold_posts(page, ACTIVE) if late else None
    page.get_by_role("button", name="Deactivate").click()
    undo = page.locator("#toast").get_by_role("button", name="Undo")
    if late:
        wait_held(page, held)
    else:
        expect(undo).to_be_visible()
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    if not late:
        expect(undo).to_be_visible()  # the toast is still there, with its Undo, for Ben

    page.get_by_role("link", name=opens, exact=True).click()
    expect(modal(page)).to_have_count(1)
    if late:
        settled(page)
        held[0].continue_()
        expect(toast_text(page)).to_contain_text("Deactivated Ben Cruz.")  # the answer came: only now is there a toast
        expect(row_status(page, "Ben Cruz")).to_have_text("Deactivated")
    if dropped:
        expect(undo).to_be_hidden()
        expect(page.locator("#undo")).to_have_count(0)
        expect(toast_text(page)).to_contain_text("Deactivated Ben Cruz.")  # the message stays, as any other, without it
    else:
        expect(undo).to_be_visible()
        expect(page.locator("#undo")).to_have_count(1)


# -- typing that is not saved yet survives a save elsewhere in the panel --

def test_typed_details_survive_deactivate(page, db):
    open_ben(page)
    mark(page)
    type_in_details(page)
    page.get_by_role("button", name="Deactivate").click()

    expect(panel_status(page)).to_have_text("Deactivated")
    still_typed(page)  # the Access form saved; the Details form is not its business
    expect(title_of(page)).to_have_text("Ben Cruz")  # and the saved name is still the database's
    assert kept(page) and db.staff[1].active is False
    assert db.staff[1].name == "Ben Cruz" and db.staff[1].email == "ben@bluejay.example"  # typing was never saved


def test_typed_details_survive_reactivate(page, db):
    page.goto("/staff")
    mark(page)
    name_link(page, "Dee Park").click()
    expect(page.get_by_label("Work email")).to_be_visible()
    page.get_by_label("Name").fill("Deirdre Park")
    page.get_by_label("Work email").fill("deirdre@bluejay.example")
    page.get_by_role("button", name="Reactivate").click()

    expect(panel_status(page)).to_have_text("Active")
    expect(page.get_by_label("Name")).to_have_value("Deirdre Park")
    expect(page.get_by_label("Work email")).to_have_value("deirdre@bluejay.example")
    expect(title_of(page)).to_have_text("Dee Park")
    assert kept(page) and db.staff[2].active is True
    assert db.staff[2].name == "Dee Park" and db.staff[2].email == "dee@bluejay.example"


def test_typed_details_survive_a_refused_deactivate(page, db):
    """The last active member's refusal comes back as the panel again, with the reason; what was typed elsewhere stays."""
    open_ben(page)
    mark(page)

    def last(by, staff_id, active):  # as the database refuses it
        raise LastMember(staff_id)

    db.set_staff_active = last
    type_in_details(page)
    page.get_by_role("button", name="Deactivate").click()

    expect(page.locator("dialog#panel .ds-alert[role=alert]")).to_contain_text(LAST_ERROR.format("Ben Cruz"))
    still_typed(page)
    expect(panel_status(page)).to_have_text("Active")  # nothing changed
    expect(modal(page)).to_have_count(1)
    assert kept(page) and [member.active for member in db.staff] == [True, True, False]
    assert db.staff[1].name == "Ben Cruz"


def test_restored_typing_still_keeps_the_panel_open(page):
    """The typing is put back as unsaved, so a press on the dimmed area refuses to close over it, as before the save."""
    open_ben(page)
    type_in_details(page)
    page.get_by_role("button", name="Deactivate").click()
    expect(panel_status(page)).to_have_text("Deactivated")
    still_typed(page)

    page.mouse.click(40, 300)
    expect(modal(page)).to_have_count(1)
    still_typed(page)
    page.get_by_role("link", name="Close panel").click()  # X still closes
    expect(modal(page)).to_have_count(0)


def test_typing_in_the_saved_form_is_not_put_back(page, db):
    """The form that saved shows what the database holds (it keeps the email lower-case and the Slack ID upper-case),
    not what was typed."""
    open_ben(page)
    page.get_by_label("Work email").fill("Benjamin@Bluejay.example")
    page.get_by_label("Slack member ID").fill("u0benx")
    page.get_by_role("button", name="Save").click()

    expect(toast_text(page)).to_have_text("Saved Ben Cruz.")
    expect(page.get_by_label("Work email")).to_have_value("benjamin@bluejay.example")
    expect(page.get_by_label("Slack member ID")).to_have_value("U0BENX")
    assert (db.staff[1].email, db.staff[1].slack_user_id) == ("benjamin@bluejay.example", "U0BENX")
    page.mouse.click(40, 300)  # nothing is left unsaved, so the dimmed area closes the panel
    expect(modal(page)).to_have_count(0)


def test_typing_never_moves_to_another_members_panel(page, db):
    """Ben's Deactivate is still on its way when his panel is closed and Dee's is opened: the answer carries nothing
    typed in Ben's panel into Dee's, and does not fill Dee's panel at all."""
    open_ben(page)
    type_in_details(page)
    held = hold_posts(page, ACTIVE)
    page.get_by_role("button", name="Deactivate").click()
    wait_held(page, held)
    page.get_by_role("link", name="Close panel").click()
    expect(modal(page)).to_have_count(0)
    name_link(page, "Dee Park").click()
    expect(page.get_by_label("Work email")).to_have_value("dee@bluejay.example")
    settled(page)
    held[0].continue_()
    expect(row_status(page, "Ben Cruz")).to_have_text("Deactivated")  # the answer came and refreshed the list behind

    expect(title_of(page)).to_have_text("Dee Park")
    expect(page.get_by_label("Name")).to_have_value("Dee Park")
    expect(page.get_by_label("Work email")).to_have_value("dee@bluejay.example")
    expect(page.get_by_label("Slack member ID")).to_have_value("U0DEE")
    page.mouse.click(40, 300)  # nothing typed in Dee's panel, so the dimmed area closes it
    expect(modal(page)).to_have_count(0)
