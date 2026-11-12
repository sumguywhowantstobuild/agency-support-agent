"""The Clients page's script in a real browser (Chromium through Playwright): limits changed in place, the toast and
its Undo, the bars. The app runs on 127.0.0.1 with the fake database; the browser sends a signed test pass on every
request. Watch a run: pytest tests/admin/test_clients_browser.py --headed --slowmo 500"""

import re
import time

import psycopg
import pytest
from playwright.sync_api import Page, expect

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from conftest import AUDIENCE
from fakes import FakeAdminDb, a_client, drafted
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)


@pytest.fixture
def db():
    return FakeAdminDb(
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", status="paused", reason="key_broken"),
    )


def test_a_client_whose_key_is_an_object_name_saves(clients_page, db):
    """Keys come from the name ("Constructor" -> "constructor"): saves are kept by key with no inherited names."""
    db.rows.append(a_client("constructor", "Constructor", used=1, limit=10))
    clients_page.reload()
    field = limit_field(clients_page, "Constructor")
    field.fill("12")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Constructor: 12 drafts an hour.")
    assert db.calls == [(1, "constructor", 12)]


@pytest.fixture
def live_url(settings, keys, db):
    def make_app(url):
        app = create_app()
        app.dependency_overrides[get_admin_settings] = lambda: settings.model_copy(update={"admin_origin": url})
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        return app

    with serving(make_app) as url:
        yield url


def signed_in(keys) -> dict[str, str]:
    private_key, kid, _ = keys
    return {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}


@pytest.fixture
def clients_page(browser, keys, live_url) -> Page:
    context = browser.new_context(base_url=live_url, extra_http_headers=signed_in(keys))
    context.set_default_timeout(5_000)  # a local page answers in milliseconds; a miss fails fast, not after 30 s
    page = context.new_page()
    page.goto("/clients")
    yield page
    context.close()


def limit_field(page: Page, name: str):  # its spoken name: "Limit" and the client's name (the list has no field)
    return page.get_by_role("textbox", name=re.compile(rf"^Limit {re.escape(name)}$"))


def test_enter_saves_the_limit_and_says_so(clients_page, db):
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    toast = clients_page.locator(".ds-toast")
    expect(toast).to_be_visible()
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    assert db.calls == [(1, "cedarline", 35)]  # once, as Ana
    expect(field).to_have_value("35")
    tile = clients_page.locator("article", has=field)
    expect(tile.locator(".ds-card__count-note")).to_have_text("of 35 this hour")


def test_undo_puts_the_old_limit_back(clients_page, db):
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    toast = clients_page.locator(".ds-toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    toast.get_by_role("button", name="Undo").click()
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")
    expect(toast.get_by_role("button", name="Undo")).to_be_hidden()  # an undo is not undone
    assert db.calls == [(1, "cedarline", 35), (1, "cedarline", 30)]
    expect(field).to_have_value("30")


def test_icons_stay_drawn_through_a_save_and_undo_with_no_icon_file(browser, keys, live_url):
    """Live 1.2.0: a save set the icon's address again, the file wasn't kept, and the icon was blank until it came back.
    Refusing the file makes that wait endless, so no timing is involved; 12 of 12 used changes the icon as well."""
    context = browser.new_context(base_url=live_url, extra_http_headers=signed_in(keys))
    context.set_default_timeout(5_000)
    asked = []

    def refuse(route):
        asked.append(route.request.url)
        route.abort()

    context.route("**/ds-icons.svg*", refuse)
    page = context.new_page()
    page.goto("/clients")
    page.evaluate("""() => {
      window.widths = [];
      window.sampling = true;
      (function frame() {
        document.querySelectorAll(".ds-card--tile .ds-status use").forEach((use) => window.widths.push(use.getBBox().width));
        if (window.sampling) requestAnimationFrame(frame);
      })();
    }""")
    field = limit_field(page, "Cedarline Roofing")
    field.fill("12")
    field.press("Enter")
    toast = page.locator(".ds-toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 12 drafts an hour.")
    expect(page.locator("article", has=field).locator(".ds-status__label")).to_have_text("At limit")
    toast.get_by_role("button", name="Undo").click()
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")
    page.wait_for_timeout(600)  # the regions' swap and its transition are over
    widths = page.evaluate("() => { window.sampling = false; return window.widths; }")
    context.close()
    assert widths and min(widths) > 0
    assert asked == []


def test_escape_puts_the_old_value_back_and_saves_nothing(clients_page, db):
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Escape")
    expect(field).to_have_value("30")
    field.blur()  # leaving the field afterwards saves nothing either: the value is the saved one again
    clients_page.wait_for_timeout(300)
    assert db.calls == []
    expect(clients_page.locator(".ds-toast")).to_be_hidden()


def test_a_limit_out_of_range_is_marked_then_refused_before_sending(clients_page, db):
    field = limit_field(clients_page, "Cedarline Roofing")
    unit = clients_page.locator("#" + field.get_attribute("aria-describedby"))
    field.fill("250")
    expect(field).to_have_attribute("aria-invalid", "true")  # marked while typed, before any save
    expect(unit).to_have_text("1 to 200")
    field.fill("25")
    expect(field).not_to_have_attribute("aria-invalid", "true")
    expect(unit).to_have_text("an hour")
    field.fill("0")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("The limit must be a whole number from 1 to 200.")
    expect(field).to_have_value("30")  # the refused value is put back: the old one is valid again
    expect(field).not_to_have_attribute("aria-invalid", "true")
    expect(unit).to_have_text("an hour")
    assert db.calls == []


@pytest.mark.parametrize("failure, message", [
    ("database", "Couldn't reach the database, so the limit may not have changed. Reload the page to check."),
    ("network", "Couldn't reach the admin app, so the limit may not have changed. Reload the page to check."),
])
def test_a_failed_save_puts_the_value_back_and_says_what_happened(clients_page, db, failure, message):
    if failure == "database":
        def down(*args):
            raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")
        db.set_draft_limit = down
    else:
        clients_page.route("**/limit", lambda route: route.abort())  # the request never arrives
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text(message)
    expect(clients_page.locator(".ds-toast").get_by_role("button", name="Undo")).to_be_hidden()
    expect(field).to_have_value("30")


def bar_of(tile):
    return tile.locator(".ds-card__fill").evaluate("fill => fill.style.transform")


def test_bars_fill_and_the_state_follows_a_new_limit(clients_page):
    field = limit_field(clients_page, "Cedarline Roofing")
    tile = clients_page.locator("article", has=field)
    assert bar_of(tile) == "scaleX(0.4)"  # 12 of 30
    field.fill("12")  # this hour's use: at the limit now
    field.press("Enter")
    expect(tile).to_have_class(re.compile(r"\bds-card--warn\b"))
    expect(tile.locator(".ds-status")).to_have_text("At limit")
    assert bar_of(tile) == "scaleX(1)"
    field.fill("40")
    field.press("Enter")
    expect(tile).not_to_have_class(re.compile(r"\bds-card--warn\b"))
    expect(tile.locator(".ds-status")).to_have_text("Drafting")
    assert bar_of(tile) == "scaleX(0.3)"


def mark_page(page: Page):
    """A page load wipes the mark; an in-place update keeps it. (Playwright reports replaceState as a navigation, so
    a navigation event can't tell the two apart.)"""
    page.evaluate("window.__kept = true")


def page_kept(page: Page) -> bool:
    return page.evaluate("window.__kept === true")


def test_a_new_sort_reorders_in_place_and_keeps_focus(clients_page):
    """WCAG 2.2 3.2.2: picking a sort changes no page; the results reorder where they are."""
    clients_page.goto("/clients?view=list")
    mark_page(clients_page)
    sort = clients_page.get_by_label("Sort")
    sort.focus()
    sort.select_option("name")
    expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))
    assert "view=list" in clients_page.url
    names = clients_page.locator(".ds-table__name")
    expect(names).to_have_text(["BrightSmile Dental", "Cedarline Roofing"])  # by name now; closest came first
    assert page_kept(clients_page)
    expect(sort).to_be_focused()
    expect(clients_page.get_by_role("status")).to_have_text("Sorted by name")
    row = clients_page.locator("tr[data-ds-key=client-cedarline]")
    assert row.locator(".ds-card__fill").evaluate("fill => fill.style.transform") == "scaleX(0.4)"  # swapped bars fill


def test_cards_and_list_switch_in_place_and_the_address_follows(clients_page):
    mark_page(clients_page)
    cards, as_list = (clients_page.get_by_role("button", name=name, exact=True) for name in ("Cards", "List"))
    as_list.focus()
    as_list.press("Enter")  # a keyboard press: WebKit doesn't focus a clicked button
    expect(clients_page.locator("table")).to_be_visible()
    expect(clients_page).to_have_url(re.compile(r"[?&]view=list\b"))
    assert page_kept(clients_page)
    expect(as_list).to_be_focused()
    expect(as_list).to_have_attribute("aria-pressed", "true")
    expect(cards).to_have_attribute("aria-pressed", "false")


def test_sort_then_view_then_search_keeps_every_choice(clients_page):
    """Both forms keep up with in-place changes: a later search still carries the sort and the view."""
    mark_page(clients_page)
    clients_page.get_by_label("Sort").select_option("name")
    expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))
    clients_page.get_by_role("button", name="List", exact=True).click()
    expect(clients_page.locator("table")).to_be_visible()
    expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))  # the view switch kept the sort
    assert page_kept(clients_page)  # both changes were made in place
    search = clients_page.get_by_role("searchbox", name="Search clients")
    search.fill("cedar")
    search.press("Enter")
    expect(clients_page).to_have_url(re.compile(r"[?&]q=cedar\b"))
    assert re.search(r"[?&]sort=name\b", clients_page.url) and re.search(r"[?&]view=list\b", clients_page.url)
    expect(clients_page.locator(".ds-table__name")).to_have_text(["Cedarline Roofing"])


def test_without_script_the_sort_button_submits(browser, keys, live_url, clients_page):
    expect(clients_page.get_by_role("button", name="Sort", exact=True)).to_be_hidden()  # the script sorts at once
    context = browser.new_context(base_url=live_url, extra_http_headers=signed_in(keys), java_script_enabled=False)
    page = context.new_page()
    page.goto("/clients?view=list")
    page.get_by_label("Sort").select_option("name")
    page.get_by_role("button", name="Sort", exact=True).click()
    expect(page).to_have_url(re.compile(r"[?&]sort=name\b"))
    expect(page.locator(".ds-table__name")).to_have_text(["BrightSmile Dental", "Cedarline Roofing"])
    context.close()


def test_a_failed_refresh_loads_the_page_instead(clients_page):
    """Only the script's fetch is refused; the page load it falls back to goes through."""
    answer = {"status": 503, "body": "down"}

    def refuse_the_fetch(route):
        if route.request.resource_type == "fetch":
            route.fulfill(**answer)
        else:
            route.continue_()

    clients_page.goto("/clients?view=list")
    clients_page.route(re.compile(r"/clients\?"), refuse_the_fetch)
    for status, body, sort, first in [(503, "down", "name", "BrightSmile Dental"),
                                      (200, "<p>no results region</p>", "closest", "Cedarline Roofing")]:
        answer.update(status=status, body=body)
        mark_page(clients_page)
        clients_page.get_by_label("Sort").select_option(sort)
        expect(clients_page).to_have_url(re.compile(rf"[?&]sort={sort}\b"))
        expect(clients_page.locator(".ds-table__name").first).to_have_text(first)
        assert not page_kept(clients_page)  # a real page load


def errors_on(page: Page) -> list[str]:
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


def test_a_save_during_a_sort_lands_on_the_new_field(clients_page, db):
    """The save answers after the swap; its answer goes to the field now on the page."""
    errors = errors_on(clients_page)
    answer_now = db.set_draft_limit

    def slowly(*args):
        time.sleep(0.8)
        return answer_now(*args)

    db.set_draft_limit = slowly
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    clients_page.get_by_label("Sort").select_option("name")
    expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))  # swapped while the save is on its way
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    field = limit_field(clients_page, "Cedarline Roofing")
    expect(field).to_have_value("35")
    expect(clients_page.locator("article", has=field).locator(".ds-card__count-note")).to_have_text("of 35 this hour")
    assert errors == []


def test_a_save_answering_before_the_swap_keeps_its_value(clients_page, db):
    """The other order: the swap's page was read before the save landed but arrives after its answer; the page keeps
    the saved value."""
    answer_now = db.set_draft_limit

    def a_little_slowly(*args):
        time.sleep(0.3)
        return answer_now(*args)

    def late(route):
        if route.request.resource_type != "fetch":
            route.continue_()
            return
        response = route.fetch()  # read now, while the save is still on its way: the old limit, 30
        time.sleep(1.0)  # the save answers meanwhile
        route.fulfill(response=response)

    db.set_draft_limit = a_little_slowly
    clients_page.route(re.compile(r"/clients\?"), late)
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    clients_page.get_by_label("Sort").select_option("name")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))  # the older page has landed
    field = limit_field(clients_page, "Cedarline Roofing")
    expect(field).to_have_value("35")
    expect(clients_page.locator("article", has=field).locator(".ds-card__count-note")).to_have_text("of 35 this hour")


def test_undo_after_a_sort_changes_the_field_on_the_page(clients_page, db):
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    toast = clients_page.locator(".ds-toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    clients_page.get_by_label("Sort").select_option("name")
    expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))
    toast.get_by_role("button", name="Undo").click()
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: back to 30 drafts an hour.")
    field = limit_field(clients_page, "Cedarline Roofing")
    expect(field).to_have_value("30")
    expect(clients_page.locator("article", has=field).locator(".ds-card__count-note")).to_have_text("of 30 this hour")
    assert db.calls == [(1, "cedarline", 35), (1, "cedarline", 30)]


def test_screen_readers_hear_saves_politely_and_refusals_at_once(clients_page):
    polite, urgent = clients_page.get_by_role("status"), clients_page.get_by_role("alert")
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(polite).to_have_text("Cedarline Roofing: 35 drafts an hour, Undo available")
    expect(urgent).to_have_text("")
    field.fill("0")
    field.press("Enter")
    expect(urgent).to_have_text("The limit must be a whole number from 1 to 200.")
    expect(polite).to_have_text("")  # one message at a time: the old one isn't left to be read later


def is_open(toast) -> bool:
    """Open as a popover. Not to_be_visible: a toast that has just closed is still fading out, so it looks visible."""
    return toast.evaluate("toast => toast.matches(':popover-open')")


def test_a_toast_with_undo_stays_and_one_without_goes_unless_pointed_at(clients_page):
    clients_page.clock.install()  # the page's timers run on a clock the test moves
    clients_page.reload()
    toast = clients_page.locator(".ds-toast")
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(toast).to_be_visible()
    clients_page.clock.run_for(60_000)
    assert is_open(toast)  # Undo is offered: it waits for the person (WCAG 2.2.1)
    field.fill("0")
    field.press("Enter")  # refused: a message with nothing to act on
    toast.hover()
    clients_page.clock.run_for(60_000)
    assert is_open(toast)  # paused while pointed at
    clients_page.mouse.move(0, 0)
    clients_page.clock.run_for(6_000)
    assert not is_open(toast)


def room_at_the_foot(page: Page) -> str:
    return page.evaluate("document.documentElement.style.scrollPaddingBottom")


def test_escape_or_close_hides_the_toast_and_gives_back_its_room(clients_page):
    toast = clients_page.locator(".ds-toast")
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(toast).to_be_visible()
    assert room_at_the_foot(clients_page).endswith("px")  # Tab never lands a field under the toast (WCAG 2.4.11)
    field.press("Escape")  # nothing being edited: the Escape is the toast's
    expect(toast).to_be_hidden()
    assert room_at_the_foot(clients_page) == ""
    field.fill("36")
    field.press("Enter")
    expect(toast).to_be_visible()
    field.fill("37")
    field.press("Escape")  # being edited: this Escape puts the value back and leaves the toast alone
    expect(field).to_have_value("36")
    assert is_open(toast)
    toast.get_by_role("button", name="Close message").click()
    expect(toast).to_be_hidden()
    assert room_at_the_foot(clients_page) == ""


def test_leaving_the_field_saves_once(clients_page, db):
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Tab")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    answer_now = db.set_draft_limit

    def answer_slowly(*args):  # holds the answer back, so the Tab surely comes while the save is on its way
        time.sleep(0.5)
        return answer_now(*args)

    db.set_draft_limit = answer_slowly
    field.fill("40")
    field.press("Enter")
    field.press("Tab")  # straight after Enter, before the answer is back
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 40 drafts an hour.")
    clients_page.wait_for_timeout(300)
    assert db.calls == [(1, "cedarline", 35), (1, "cedarline", 40)]


def test_two_quick_saves_land_in_order(clients_page, db):
    """35 then 40 before 35's answer is back: the page must end where the database ends, at 40."""
    answer_now = db.set_draft_limit

    def slowly(by, key, limit):
        # 35's answer would come back last if 40 weren't made to wait; 40's wait is long enough for the test to look
        # at the box between the two answers (expect checks again at most a second apart)
        time.sleep(0.5 if limit == 35 else 1.5)
        return answer_now(by, key, limit)

    db.set_draft_limit = slowly
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    field.fill("40")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    assert field.input_value() == "40"  # 35's answer is in while 40's is on its way: the box keeps the newer value
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 40 drafts an hour.")
    clients_page.wait_for_timeout(800)  # past the slow answer
    assert [call[2] for call in db.calls] == [35, 40]
    expect(field).to_have_value("40")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 40 drafts an hour.")


def test_an_answer_never_overwrites_what_is_being_typed(clients_page, db):
    answer_now = db.set_draft_limit

    def slowly(*args):
        time.sleep(0.5)
        return answer_now(*args)

    db.set_draft_limit = slowly
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    field.fill("40")  # typed while 35 is on its way, not yet saved
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(field).to_have_value("40")  # 35's answer arrived and left the typing alone
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 40 drafts an hour.")
    assert [call[2] for call in db.calls] == [35, 40]


def slow_first_save(db, then=None):
    """The first save's answer takes half a second (and fails with `then`, an error); later saves answer at once."""
    answer_now = db.set_draft_limit
    calls = []

    def answer(*args):
        calls.append(args)
        if len(calls) == 1:
            time.sleep(0.5)
            if then:
                raise then
        return answer_now(*args)

    db.set_draft_limit = answer


def test_going_back_to_the_saved_value_mid_save_still_counts(clients_page, db):
    """30 saved; 35 sent; 30 typed again before 35's answer: the last thing sent is 30, so 30 must win."""
    slow_first_save(db)
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    field.fill("30")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 30 drafts an hour.")
    assert [call[2] for call in db.calls] == [35, 30]
    expect(field).to_have_value("30")


def test_a_failed_save_leaves_newer_typing_alone(clients_page, db):
    slow_first_save(db, then=psycopg.OperationalError("connection to server failed"))
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    field.fill("40")  # typed while 35 is on its way
    expect(clients_page.locator(".ds-toast__text")).to_contain_text("Couldn't reach the database")
    expect(field).to_have_value("40")


def test_the_toast_never_covers_the_field_just_saved(clients_page):
    """A field right at the bottom edge: after Enter it scrolls clear of the toast (WCAG 2.4.11)."""
    clients_page.set_viewport_size({"width": 400, "height": 480})
    field = limit_field(clients_page, "BrightSmile Dental")  # the last tile
    field.scroll_into_view_if_needed()
    clients_page.evaluate("f => window.scrollBy(0, f.getBoundingClientRect().bottom - innerHeight + 1)",
                          field.element_handle())
    field.fill("16")
    field.press("Enter")
    toast = clients_page.locator(".ds-toast")
    expect(toast).to_be_visible()
    clients_page.wait_for_timeout(400)  # a smooth scroll, if any, has landed
    field_bottom = field.evaluate("f => f.getBoundingClientRect().bottom")
    toast_top = toast.evaluate("t => t.getBoundingClientRect().top")
    assert field_bottom <= toast_top


def test_a_save_refreshes_this_hours_number(clients_page, db):
    """Drafts kept coming since the page loaded: the number, the bar and the state all show the answer's count."""
    db.rows[0] = a_client("cedarline", "Cedarline Roofing", used=18, limit=30)  # six more drafts since loading
    field = limit_field(clients_page, "Cedarline Roofing")
    tile = clients_page.locator("article", has=field)
    field.fill("20")
    field.press("Enter")
    expect(tile.locator(".ds-card__count-number")).to_have_text("18")


@pytest.mark.usefixtures("fixed_clock")
def test_summary_refresh_redraws_the_chart(clients_page, db):
    """A save refreshes the summary, the chart with it: the new one has today's newest draft and doesn't draw in again."""
    db.drafts = [drafted("cedarline", 5)]
    clients_page.reload()
    chart = clients_page.locator("#summary .ds-chart")
    last_hour = chart.locator(".ds-chart__table tbody tr").last.locator("td")
    expect(chart).not_to_have_class(re.compile(r"\bds-chart--still\b"))  # drawn in once, on the page load
    expect(last_hour).to_have_text(["1"])
    clients_page.evaluate("window.__chart = document.querySelector('#summary .ds-chart')")

    db.drafts.append(drafted("cedarline", 2))  # another draft since the page loaded
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")

    expect(chart).to_have_class(re.compile(r"\bds-chart--still\b"))
    expect(last_hour).to_have_text(["2"])
    assert clients_page.evaluate("document.querySelector('#summary .ds-chart') !== window.__chart")  # swapped in


KEYS_AND_LEGEND = """() => {
  const series = path => path ? path.getAttribute("class").split("--")[1] : null;
  const legend = {};  // the displayed chart: each line's name and series
  document.querySelectorAll("#summary .ds-chart__legend > span").forEach(
    span => { legend[span.textContent.trim()] = series(span.querySelector("path")); });
  const cards = {};  // each card's or row's own short line, by the client's name
  document.querySelectorAll("#results [data-ds-key]").forEach(item => {
    const name = item.querySelector(".ds-card__name-button, .ds-table__name");
    cards[name.textContent.trim()] = series(name.querySelector(".ds-chart__key path"));
  });
  return {legend, cards};
}"""


def ranking_page(clients_page, db):
    """Cedarline has the most drafts today, Dunes some, Elm none: lines 1 and 2 and no line."""
    db.rows.extend([a_client("dunes", "Dunes Surf School", used=1, limit=30), a_client("elm", "Elm Dental", used=1)])
    db.drafts = [drafted("cedarline", m) for m in (5, 6, 7)] + [drafted("dunes", 8)]
    clients_page.reload()
    shown = clients_page.evaluate(KEYS_AND_LEGEND)
    assert shown["cards"] == {"Cedarline Roofing": "1", "Dunes Surf School": "2", "Elm Dental": None,
                              "BrightSmile Dental": None}
    return shown


def drafts_change_the_ranking(db):
    """Since the page loaded: Elm drafts most, and Dunes passes Cedarline."""
    db.drafts += [drafted("elm", m) for m in range(5, 13)] + [drafted("dunes", m) for m in range(9, 14)]


@pytest.mark.usefixtures("fixed_clock")
def test_a_tile_save_leaves_the_cards_lines_matching_the_new_chart(clients_page, db):
    """A save refreshes the chart (not the cards): a client that moved up, down or got its first line
    shows the line the chart now draws for it."""
    ranking_page(clients_page, db)
    drafts_change_the_ranking(db)
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(clients_page.locator("#summary .ds-chart")).to_have_class(re.compile(r"\bds-chart--still\b"))

    shown = clients_page.evaluate(KEYS_AND_LEGEND)
    assert shown["legend"] == {"Elm Dental": "1", "Dunes Surf School": "2", "Cedarline Roofing": "3"}
    assert shown["cards"] == {"Elm Dental": "1", "Dunes Surf School": "2", "Cedarline Roofing": "3",
                              "BrightSmile Dental": None}
    expect(field).to_have_value("35")  # the field was never replaced


@pytest.mark.usefixtures("fixed_clock")
@pytest.mark.parametrize("change", ["sort", "view"])
def test_a_results_only_refresh_keeps_the_cards_lines_matching_the_chart_on_show(clients_page, db, change):
    """The other way round: the cards and rows are fetched new, beside the chart the page still shows."""
    before = ranking_page(clients_page, db)
    drafts_change_the_ranking(db)
    mark_page(clients_page)
    if change == "sort":
        clients_page.get_by_label("Sort").select_option("name")
        expect(clients_page).to_have_url(re.compile(r"[?&]sort=name\b"))
    else:
        clients_page.get_by_role("button", name="List", exact=True).click()
        expect(clients_page.locator("#results table")).to_be_visible()
    assert page_kept(clients_page)

    shown = clients_page.evaluate(KEYS_AND_LEGEND)
    assert shown["legend"] == before["legend"]  # the chart on show is the old one
    assert shown["cards"] == before["cards"]  # and the cards say the same


def test_change_line_refreshes_after_a_save(clients_page, db):
    line = clients_page.locator("#change-line")
    expect(line).to_be_hidden()  # no changes yet: the wrapper is there, empty
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")

    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(line).to_contain_text("Ana Reyes changed Cedarline Roofing's limit from 30 to 35 drafts an hour")
    expect(line.get_by_role("link", name="All changes")).to_have_attribute("href", "/history")


def test_a_tile_save_leaves_the_next_field_alone(clients_page, db):
    """A save from one tile refreshes the summary, never the cards: the next field, where something is being typed, is
    the same field, with its text and its focus."""
    db.rows.append(a_client("dunes", "Dunes Surf School", used=1, limit=30))
    clients_page.reload()
    answer_now = db.set_draft_limit

    def slowly(*args):
        time.sleep(0.5)
        return answer_now(*args)

    db.set_draft_limit = slowly
    clients_page.evaluate("window.__results = document.getElementById('results')")
    first, next_one = limit_field(clients_page, "Cedarline Roofing"), limit_field(clients_page, "Dunes Surf School")
    first.fill("35")
    first.press("Enter")
    next_one.focus()
    next_one.fill("17")  # typed while 35 is on its way

    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    expect(clients_page.locator("#total-limits")).to_have_text("65")  # the summary refresh landed
    expect(next_one).to_have_value("17")
    expect(next_one).to_be_focused()
    assert clients_page.evaluate("document.getElementById('results') === window.__results")


def test_list_status_updates_after_a_panel_save(clients_page, db):
    """The list has no field to carry a saved limit: with the panel open over it, the rows are refreshed; so is an Undo
    after the panel has closed."""
    clients_page.goto("/clients?view=list")
    mark_page(clients_page)
    row = clients_page.locator("tr[data-ds-key=client-cedarline]")
    expect(row.locator(".ds-status")).to_have_text("Drafting")
    row.get_by_role("link", name="Cedarline Roofing").click()
    limit = clients_page.locator("dialog#panel").get_by_role("textbox", name="Draft limit")
    limit.fill("12")  # this hour's use: at the limit now
    limit.press("Enter")

    toast = clients_page.locator(".ds-toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 12 drafts an hour.")
    expect(row.locator(".ds-status")).to_have_text("At limit")
    expect(row.locator(".ds-card__meter")).to_have_class(re.compile(r"\bds-card__meter--warn\b"))
    clients_page.get_by_role("link", name="Close panel").click()  # (Escape would put the toast, and its Undo, away)
    expect(clients_page.locator("dialog:modal")).to_have_count(0)
    toast.get_by_role("button", name="Undo").click()
    expect(row.locator(".ds-status")).to_have_text("Drafting")  # no panel open, and still not stale
    assert page_kept(clients_page)


# Needs you and the two numbers (the overview's summary row).

AT_LIMIT = a_client("brightsmile", "BrightSmile Dental", used=20, limit=20)
FULL_TOO = a_client("cedarline", "Cedarline Roofing", used=30, limit=30)
FULL_THIRD = a_client("dunes", "Dunes Surf School", used=15, limit=15)
PAUSED = a_client("mabini", "Mabini Bakery", status="paused", reason="client_asked")


def with_clients(page: Page, db, *rows):
    db.rows[:] = rows
    page.reload()


def needs_titles(page: Page):
    return page.locator(".ds-card--needs [data-needs] .ds-card__title")


def dismiss_button(page: Page, title: str):
    return page.get_by_role("button", name=f"Dismiss: {title}")


def test_needs_you_folds_after_three_and_unfolds(clients_page, db):
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, FULL_THIRD, PAUSED)
    more = clients_page.locator(".ds-card__more")
    expect(needs_titles(clients_page)).to_have_count(3)
    expect(more).to_have_text("Show all 4")
    expect(more).to_have_attribute("aria-expanded", "false")
    more.focus()
    more.press("Enter")
    expect(needs_titles(clients_page)).to_have_count(4)
    expect(more).to_have_text("Show fewer")
    expect(more).to_have_attribute("aria-expanded", "true")
    expect(more).to_be_focused()
    more.press("Enter")
    expect(needs_titles(clients_page)).to_have_count(3)
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, PAUSED)
    expect(more).to_be_hidden()  # three fit


def test_dismiss_hides_an_item_for_this_page_view_only(clients_page, db):
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, PAUSED)
    aside = clients_page.locator(".ds-card--needs .ds-card__aside")
    expect(aside).to_have_text("3 items")
    # At their limit, furthest past first: both are full, so by name.
    dismiss_button(clients_page, "BrightSmile Dental is at its limit").press("Enter")
    expect(needs_titles(clients_page)).to_have_text(["Cedarline Roofing is at its limit", "Mabini Bakery is paused"])
    expect(aside).to_have_text("2 items")
    expect(dismiss_button(clients_page, "Cedarline Roofing is at its limit")).to_be_focused()  # to the next item
    dismiss_button(clients_page, "Mabini Bakery is paused").press("Enter")
    expect(aside).to_have_text("1 item")
    expect(dismiss_button(clients_page, "Cedarline Roofing is at its limit")).to_be_focused()  # the last one left
    clients_page.reload()
    expect(needs_titles(clients_page)).to_have_count(3)


def test_dismissing_the_last_item_says_youve_seen_everything(clients_page, db):
    with_clients(clients_page, db, a_client("cedarline", "Cedarline Roofing", used=1, limit=30), AT_LIMIT)
    dismiss_button(clients_page, "BrightSmile Dental is at its limit").press("Enter")
    card = clients_page.locator(".ds-card--needs")
    expect(card.get_by_text("You’ve seen everything")).to_be_visible()
    expect(card.get_by_text("Hidden messages come back when the page is reloaded.")).to_be_visible()
    heading = card.get_by_role("heading", name="Needs you")
    expect(heading).to_be_focused()  # nothing left to go to
    expect(heading).to_have_class(re.compile(r"ds-card__label--ok"))
    expect(card.locator(".ds-card__aside")).to_have_text("")
    expect(needs_titles(clients_page)).to_have_count(0)


def test_dismiss_offers_undo_and_says_when_it_comes_back(clients_page, db):
    with_clients(clients_page, db, AT_LIMIT, PAUSED)
    dismiss_button(clients_page, "BrightSmile Dental is at its limit").press("Enter")
    toast = clients_page.locator(".ds-toast")
    expect(toast.locator(".ds-toast__text")).to_have_text("Message hidden. It comes back when the page is reloaded.")
    toast.get_by_role("button", name="Undo").click()
    expect(needs_titles(clients_page)).to_have_text(["BrightSmile Dental is at its limit", "Mabini Bakery is paused"])
    expect(clients_page.locator(".ds-card--needs .ds-card__aside")).to_have_text("2 items")
    expect(dismiss_button(clients_page, "BrightSmile Dental is at its limit")).to_be_focused()
    expect(toast).to_be_hidden()


def test_a_dismissed_item_comes_back_when_the_clients_status_changes(clients_page, db):
    with_clients(clients_page, db, AT_LIMIT, PAUSED)
    dismiss_button(clients_page, "BrightSmile Dental is at its limit").press("Enter")
    expect(needs_titles(clients_page)).to_have_text(["Mabini Bakery is paused"])
    field = limit_field(clients_page, "BrightSmile Dental")
    field.fill("25")
    field.press("Enter")
    expect(clients_page.locator("#total-limits")).to_have_text("25")
    expect(needs_titles(clients_page)).to_have_text(["Mabini Bakery is paused"])
    field.fill("20")
    field.press("Enter")  # at its limit again: a new event, not the message dismissed
    expect(needs_titles(clients_page)).to_have_text(["BrightSmile Dental is at its limit", "Mabini Bakery is paused"])


def test_a_save_that_ends_a_limit_takes_the_client_off_the_card_and_moves_the_numbers(clients_page, db):
    """Both numbers and the bar follow, and an item dismissed before stays hidden."""
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, PAUSED)
    mark_page(clients_page)
    dismiss_button(clients_page, "Cedarline Roofing is at its limit").press("Enter")
    expect(clients_page.locator("#total-bar")).to_have_attribute("data-level", "1")
    field = limit_field(clients_page, "BrightSmile Dental")
    field.fill("30")
    field.press("Enter")
    expect(needs_titles(clients_page)).to_have_text(["Mabini Bakery is paused"])
    expect(clients_page.locator(".ds-card--needs .ds-card__aside")).to_have_text("1 item")
    expect(clients_page.locator("#total-used")).to_have_text("50")
    expect(clients_page.locator("#total-limits")).to_have_text("60")
    expect(clients_page.locator("#total-bar")).to_have_attribute("data-level", "0.833")
    bar = clients_page.locator("#total-bar").evaluate("fill => fill.style.transform")
    assert bar == "scaleX(0.833)"  # the swapped-in bar is filled
    expect(field).to_be_focused()
    assert page_kept(clients_page)


def test_a_lowered_limit_that_reaches_the_limit_adds_the_client(clients_page, db):
    with_clients(clients_page, db, a_client("cedarline", "Cedarline Roofing", used=12, limit=30))
    expect(clients_page.locator(".ds-card--needs").get_by_text("Every client is drafting within its limit")).to_be_visible()
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("12")
    field.press("Enter")
    expect(needs_titles(clients_page)).to_have_text(["Cedarline Roofing is at its limit"])
    expect(dismiss_button(clients_page, "Cedarline Roofing is at its limit")).to_be_visible()  # a swapped-in item works


def test_a_save_that_stays_drafting_moves_the_totals(clients_page, db):
    """Most saves change no one's state: the totals still follow, "Show all" stays, and a refused refresh changes nothing."""
    drafting = a_client("elm", "Elm Dental", used=10, limit=30)
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, FULL_THIRD, PAUSED, drafting)
    mark_page(clients_page)
    more = clients_page.locator(".ds-card__more")
    more.click()
    expect(more).to_have_text("Show fewer")
    field = limit_field(clients_page, "Elm Dental")
    field.fill("35")
    field.press("Enter")
    expect(clients_page.locator("#total-limits")).to_have_text("100")
    expect(clients_page.locator("#total-bar")).to_have_attribute("data-level", "0.75")
    expect(more).to_have_text("Show fewer")
    expect(needs_titles(clients_page)).to_have_count(4)

    def refuse_the_refresh(route):
        if route.request.resource_type == "fetch" and route.request.method == "GET":
            route.fulfill(status=503, body="down")
        else:
            route.continue_()

    clients_page.route(re.compile(r"/clients(\?|$)"), refuse_the_refresh)
    field.fill("40")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Elm Dental: 40 drafts an hour.")
    clients_page.wait_for_timeout(300)
    expect(clients_page.locator("#total-limits")).to_have_text("100")
    assert page_kept(clients_page)


def test_a_late_summary_refresh_keeps_the_view_chosen_since(clients_page):
    """The refresh was asked for the old address: it moves the numbers, never the sort, view or address picked since."""
    held = []

    def hold_the_refresh(route):
        if route.request.resource_type == "fetch" and route.request.method == "GET":
            held.append(route)
        else:
            route.continue_()

    clients_page.route(re.compile(r"/clients$"), hold_the_refresh)
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(clients_page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    as_list = clients_page.get_by_role("button", name="List", exact=True)
    as_list.click()
    expect(clients_page.locator("table")).to_be_visible()
    assert len(held) == 1
    held[0].continue_()
    expect(clients_page.locator("#total-limits")).to_have_text("35")
    expect(as_list).to_have_attribute("aria-pressed", "true")
    assert "view=list" in clients_page.url
    assert clients_page.locator("#find input[name=view]").input_value() == "list"


def test_a_paused_item_links_to_its_sheet(clients_page, db):
    with_clients(clients_page, db, PAUSED)
    clients_page.get_by_role("link", name="Review: Mabini Bakery").click()
    expect(clients_page).to_have_url(re.compile(r"/clients/mabini$"))


# The motion add-on. What moved is recorded while it moves and every test checks the end state too: a
# spy on the add-on would pass even for a skipped transition.

WATCH = """
window.seen = {groups: new Set(), bars: new Set(), folds: new Set(), rises: new Map(), limits: []};
new MutationObserver(() => {
  const text = document.getElementById("total-limits")?.textContent;
  if (text !== undefined && text !== seen.limits.at(-1)) seen.limits.push(text);
}).observe(document, {subtree: true, childList: true, characterData: true});
(function look() {
  document.getAnimations().forEach(a => {
    const effect = a.effect, target = effect?.target;
    if (!target || !(effect.getComputedTiming().duration > 0)) return;
    if (effect.pseudoElement?.startsWith("::view-transition-group")) seen.groups.add(effect.pseudoElement);
    if (a instanceof CSSTransition) {
      if (a.transitionProperty === "transform" && target.matches(".ds-card__fill"))
        seen.bars.add(target.id || target.closest("[data-state]").querySelector("[data-client]").dataset.client);
      return;
    }
    if (target.matches("[data-needs]")) seen.folds.add(target.dataset.needs);
    if (target.parentElement?.matches(".ds-page__grid"))
      seen.rises.set(target.querySelector("[data-client]").dataset.client, effect.getTiming().delay);
  });
  requestAnimationFrame(look);
})();
"""
NO_VIEW_TRANSITIONS = "delete Document.prototype.startViewTransition;"


def watch(page: Page):
    page.add_init_script(WATCH)
    page.reload()


def seen(page: Page, what: str):
    return page.evaluate("what => { const s = seen[what]; return s instanceof Map ? Object.fromEntries(s) : [...s]; }", what)


def group_of(key: str) -> str:
    return f"::view-transition-group(ds-{key})"


def wait_for_group(page: Page, key: str):
    page.wait_for_function("name => seen.groups.has(name)", arg=group_of(key))


NO_TRANSITIONS_IN_WEBKIT = "admin.js turns view transitions off in WebKit: its page dies when a table arrives during one"


def test_view_transitions_are_off_in_webkit_only(clients_page, browser_name):
    """The browser check in admin.js must never catch Chrome, whose agent string also says AppleWebKit."""
    assert clients_page.evaluate("() => !!document.startViewTransition") is (browser_name != "webkit")


def test_a_limit_change_rolls_the_total_and_grows_the_bars(clients_page):
    watch(clients_page)
    total = clients_page.locator("#total-limits")
    assert total.get_attribute("data-ds-ticker") == "total-limits"
    # The bars fill at load too (not in every browser): only what moves after this comes from the save.
    clients_page.wait_for_timeout(100)
    clients_page.wait_for_function(
        """() => !document.getAnimations().some(a => a.effect?.target?.matches?.(".ds-card__fill"))""")
    clients_page.evaluate("() => seen.bars.clear()")
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("60")
    field.press("Enter")
    expect(total).to_have_text("60")
    clients_page.wait_for_function("() => seen.bars.has('total-bar') && seen.bars.has('cedarline')")
    assert [text for text in seen(clients_page, "limits") if text not in ("30", "60")], "the total never rolled"
    bar = clients_page.locator("#total-bar")
    expect(bar).to_have_attribute("data-level", "0.2")
    assert bar.evaluate("fill => fill.style.transform") == "scaleX(0.2)"
    assert field.evaluate("field => field === document.activeElement")


@pytest.mark.skip_browser("webkit", reason=NO_TRANSITIONS_IN_WEBKIT)
def test_sorting_glides_the_cards(clients_page, db):
    db.rows.append(a_client("dunes", "Dunes Surf School", used=1, limit=30))
    watch(clients_page)
    keys = clients_page.locator("article.ds-card--tile").evaluate_all("tiles => tiles.map(t => t.dataset.dsKey)")
    assert sorted(keys) == ["client-brightsmile", "client-cedarline", "client-dunes"]  # every tile, each its own
    sort = clients_page.get_by_label("Sort")
    sort.focus()
    sort.select_option("name")
    wait_for_group(clients_page, "client-cedarline")
    expect(clients_page.locator("article .ds-card__name-button")).to_have_text(
        ["BrightSmile Dental", "Cedarline Roofing", "Dunes Surf School"])
    expect(sort).to_be_focused()


@pytest.mark.parametrize("address", ["/clients", "/clients?view=list"])
def test_a_table_arriving_right_after_a_save_keeps_the_page_alive(clients_page, address):
    """A view switch while a save's transition runs: a table arriving then killed WebKit's page, whichever came first."""
    clients_page.goto(address)
    mark_page(clients_page)
    errors = errors_on(clients_page)
    for limit in ("35", "40", "45"):
        if clients_page.locator("table").count():  # only the cards have a field to save from: back to them first
            clients_page.locator(".ds-segmented__item[value=cards]").evaluate("button => button.click()")
            expect(clients_page.locator("article.ds-card--tile").first).to_be_visible()
        field = limit_field(clients_page, "Cedarline Roofing")
        field.fill(limit)
        field.press("Enter")
        clients_page.wait_for_function(
            "() => !document.startViewTransition || document.getAnimations().some(a => a.effect?.pseudoElement)")
        clients_page.locator(".ds-segmented__item[value=list]").evaluate("button => button.click()")
        expect(clients_page).to_have_url(re.compile(r"view=list"))
        expect(clients_page.locator("#total-limits")).to_have_text(limit)
    expect(clients_page.locator("table")).to_be_visible()
    assert page_kept(clients_page)
    assert errors == []


@pytest.mark.skip_browser("webkit", reason=NO_TRANSITIONS_IN_WEBKIT)
def test_a_client_named_toast_or_nav_still_glides(clients_page, db):
    """Their keys can't meet the toast's or the nav bar's: a duplicate name would turn every transition off."""
    db.rows[:] = [a_client("toast", "Toast", used=1, limit=30), a_client("nav", "Nav", used=2, limit=30)]
    watch(clients_page)
    clients_page.get_by_label("Sort").select_option("name")
    wait_for_group(clients_page, "client-toast")
    wait_for_group(clients_page, "client-nav")


@pytest.mark.skip_browser("webkit", reason=NO_TRANSITIONS_IN_WEBKIT)
def test_the_toast_and_the_side_menu_move_with_the_tiles_not_under_them(clients_page):
    """An element with no name of its own is drawn beneath every named one while a transition runs."""
    watch(clients_page)
    field = limit_field(clients_page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    wait_for_group(clients_page, "toast")
    wait_for_group(clients_page, "nav")
    expect(clients_page.locator(".ds-toast")).to_be_visible()


def test_dismiss_folds_the_item_and_closes_the_gap(clients_page, db):
    watch(clients_page)
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, PAUSED)
    below = clients_page.locator("[data-needs='cedarline:at_limit']")
    place = below.bounding_box()["y"]
    dismiss_button(clients_page, "BrightSmile Dental is at its limit").press("Enter")
    clients_page.wait_for_function("() => seen.folds.has('brightsmile:at_limit')")
    expect(dismiss_button(clients_page, "Cedarline Roofing is at its limit")).to_be_focused()  # at once, not after it
    expect(clients_page.locator("[data-needs='brightsmile:at_limit']")).to_have_count(0)
    expect(needs_titles(clients_page)).to_have_text(["Cedarline Roofing is at its limit", "Mabini Bakery is paused"])
    assert below.bounding_box()["y"] < place - 1  # the item below moved up into its place


def test_a_double_click_on_dismiss_hides_one_item(clients_page, db):
    """The second click lands on the item still folding: that one item goes and the rest stay."""
    with_clients(clients_page, db, AT_LIMIT, FULL_TOO, PAUSED)
    dismiss_button(clients_page, "BrightSmile Dental is at its limit").dblclick()
    expect(clients_page.locator("[data-needs='brightsmile:at_limit']")).to_have_count(0)
    clients_page.wait_for_timeout(400)
    expect(needs_titles(clients_page)).to_have_text(["Cedarline Roofing is at its limit", "Mabini Bakery is paused"])
    expect(clients_page.locator(".ds-card--needs .ds-card__aside")).to_have_text("2 items")


def test_the_page_arrives_in_a_ripple(clients_page, db):
    db.rows.append(a_client("dunes", "Dunes Surf School", used=1, limit=30))
    watch(clients_page)
    clients_page.wait_for_function("() => seen.rises.size === 3")
    delays = sorted(seen(clients_page, "rises").values())
    assert delays[0] < delays[1] < delays[2], "the tiles rose together, not in turn"
    for tile in clients_page.locator("article.ds-card--tile").all():
        expect(tile).to_have_css("opacity", "1")


@pytest.fixture
def open_clients(browser, keys, live_url):
    """open_clients(init=..., **context options) -> a signed-in page on Clients, watching what moves, its errors in .errors."""
    contexts = []

    def open_(init=None, block_motion=False, **options) -> Page:
        context = browser.new_context(base_url=live_url, extra_http_headers=signed_in(keys), **options)
        context.set_default_timeout(5_000)
        contexts.append(context)
        page = context.new_page()
        page.errors = errors_on(page)
        page.add_init_script(WATCH)
        if init:
            page.add_init_script(init)
        if block_motion:
            page.route("**/static/**/ds/ds-motion.js", lambda route: route.fulfill(status=404, body="gone"))
        page.goto("/clients")
        return page

    yield open_
    for context in contexts:
        context.close()


def everything_works(page: Page, db):
    """Every action, and the result each one shows, with or without motion."""
    mark_page(page)
    field = limit_field(page, "Cedarline Roofing")
    field.fill("40")
    field.press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 40 drafts an hour.")
    expect(page.locator("#total-limits")).to_have_text("60")
    expect(page.locator("#total-used")).to_have_text("32")
    expect(page.locator("#total-bar")).to_have_attribute("data-level", "0.533")
    assert page.locator("#total-bar").evaluate("fill => fill.style.transform") == "scaleX(0.533)"
    assert db.calls[-1] == (1, "cedarline", 40)
    sort = page.get_by_label("Sort")
    sort.focus()
    sort.select_option("name")
    expect(page.locator("article .ds-card__name-button").first).to_have_text("BrightSmile Dental")
    expect(sort).to_be_focused()
    expect(page.locator(".ds-toast__region[role=status]")).to_have_text("Sorted by name")
    as_list = page.get_by_role("button", name="List", exact=True)
    as_list.focus()
    as_list.press("Enter")
    expect(page.locator("table")).to_be_visible()
    expect(as_list).to_be_focused()
    expect(page).to_have_url(re.compile(r"view=list"))
    dismiss_button(page, "BrightSmile Dental is at its limit").press("Enter")
    expect(needs_titles(page)).to_have_text(["Mabini Bakery is paused"])
    expect(dismiss_button(page, "Mabini Bakery is paused")).to_be_focused()
    assert page_kept(page)
    assert page.errors == []


def rolled(page: Page) -> list[str]:
    return [text for text in seen(page, "limits") if text not in ("50", "60")]


def with_three_clients(db):
    db.rows[:] = [a_client("cedarline", "Cedarline Roofing", used=12, limit=30), AT_LIMIT, PAUSED]


def test_with_reduced_motion_nothing_moves_and_everything_works(open_clients, db):
    with_three_clients(db)
    page = open_clients(reduced_motion="reduce")
    everything_works(page, db)
    assert page.evaluate("typeof window.dsMotion") == "object"  # the add-on is there and stays still
    assert rolled(page) == []
    assert seen(page, "groups") == []
    assert seen(page, "folds") == []
    assert seen(page, "rises") == {}


def test_without_view_transitions_the_swap_is_instant(open_clients, db):
    with_three_clients(db)
    page = open_clients(init=NO_VIEW_TRANSITIONS)
    assert page.evaluate("() => !document.startViewTransition")
    everything_works(page, db)
    assert seen(page, "groups") == []
    assert rolled(page), "the add-on's fallback keeps the number's roll"


def test_without_the_motion_script_everything_works(open_clients, db):
    with_three_clients(db)
    page = open_clients(block_motion=True)
    assert page.evaluate("typeof window.dsMotion") == "undefined"
    everything_works(page, db)
    assert rolled(page) == []
    assert seen(page, "groups") == []
    assert seen(page, "folds") == []
    assert seen(page, "rises") == {}


# What a real phone showed on 1.4.0. A phone is the width the slim bar starts at.
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}


@pytest.fixture
def open_at(browser, keys, live_url):
    contexts = []

    def make(viewport) -> Page:
        context = browser.new_context(base_url=live_url, extra_http_headers=signed_in(keys), viewport=viewport)
        context.set_default_timeout(5_000)
        contexts.append(context)
        page = context.new_page()
        page.goto("/clients")
        return page

    yield make
    for context in contexts:
        context.close()


def test_the_current_tab_on_a_phone_is_marked_by_more_than_colour(open_at):
    """No outline there (the bar would cut it off, so a faint fill instead): the word's weight keeps it clear without
    colour (WCAG 1.4.1). The side menu keeps its ring."""
    # WebKit fades the current tab in on a browser's first page, so the fill is read once that has finished.
    look = """async () => { const style = (el) => getComputedStyle(el);
      const tab = document.querySelector('.ds-side-nav__link[aria-current="page"]');
      await Promise.all(tab.getAnimations().map((animation) => animation.finished));
      const current = style(tab);
      const other = style(document.querySelector('.ds-side-nav__link:not([aria-current])'));
      return {ring: current.boxShadow, fill: current.backgroundColor, weight: Number(current.fontWeight), other: Number(other.fontWeight)}; }"""
    phone = open_at(PHONE).evaluate(look)
    assert phone["ring"] == "none"
    assert phone["fill"] != "rgba(0, 0, 0, 0)"
    assert phone["weight"] > phone["other"]
    desktop = open_at(DESKTOP).evaluate(look)
    assert desktop["ring"] != "none"


MOVES = """async () => { // after the page's own opening rise and its bars' first fill, or those would count
  await Promise.allSettled(document.getAnimations().filter((a) => a.constructor === Animation || a.effect?.target?.closest?.("#results")).map((a) => a.finished));
  window.moves = new Set();
  (function look() {
    document.getAnimations().forEach((a) => {
      const pictures = a.effect?.pseudoElement?.includes("view-transition");
      if (pictures || a.effect?.target?.closest?.("#results")) moves.add(a.animationName || a.transitionProperty || "script");
    });
    requestAnimationFrame(look);
  })(); }"""


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("name, what", [("List", "#results table"), ("Cards", "#results article")])
def test_cards_and_list_change_at_once(open_at, viewport, name, what):
    """No animation between Cards and List: a switch, pressed often, with nothing to follow."""
    page = open_at(viewport)
    if name == "Cards":
        page.get_by_role("button", name="List", exact=True).click()
        expect(page.locator("#results table")).to_be_visible()
    page.evaluate(MOVES)
    page.get_by_role("button", name=name, exact=True).click()
    expect(page.get_by_role("button", name=name, exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.locator(what).first).to_be_visible()
    page.evaluate("() => new Promise((done) => setTimeout(done, 400))")  # long enough for one to have shown
    assert page.evaluate("() => [...moves]") == []


RISES = """async () => { // after the page's own opening rise, or that one would count
  await Promise.all(document.getAnimations().filter((a) => a.constructor === Animation).map((a) => a.finished));
  window.rises = new Set();
  window.riseEnds = 0;
  (function look() {
    document.getAnimations().forEach((a) => {
      const target = a.effect?.target;
      // A script's animation (CSS's own have their own kinds), from unseen: a row's hover fade isn't a rise.
      if (a.constructor === Animation && target?.closest?.("#results") && a.effect.getKeyframes()[0]?.opacity === "0") {
        rises.add(target.tagName);
        riseEnds = Math.max(riseEnds, a.effect.getComputedTiming().endTime);
      }
    });
    requestAnimationFrame(look);
  })(); }"""


def test_on_a_phone_sorted_results_rise_in(open_at):
    """Our own motion in place of the picture animation; it moves the real page, so the bar is left
    alone."""
    page = open_at(PHONE)
    page.evaluate(RISES)
    page.locator("#sort").select_option("name")
    page.wait_for_function("() => rises.size > 0")
    page.wait_for_function("() => !document.getAnimations().some((a) => a.effect?.target?.closest?.('#results'))")
    assert page.locator("#results article").first.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert page.evaluate("() => riseEnds") <= 340, "slower than the chosen speed (280 ms, 56 apart)"


def test_on_a_desktop_sorted_results_have_no_rise_on_top_of_their_glide(open_at, browser_name):
    if browser_name == "webkit":
        pytest.skip(NO_TRANSITIONS_IN_WEBKIT)
    page = open_at({"width": 1280, "height": 800})
    page.evaluate(RISES)
    page.locator("#sort").select_option("name")
    page.evaluate("() => new Promise((done) => setTimeout(done, 400))")
    assert page.evaluate("() => rises.size") == 0


def test_the_picture_animation_follows_the_width_when_a_phone_turns(open_at, browser_name):
    if browser_name == "webkit":
        pytest.skip(NO_TRANSITIONS_IN_WEBKIT)
    page = open_at({"width": 800, "height": 400})  # a phone on its side: past the bar's width
    assert page.evaluate("!!document.startViewTransition")
    page.set_viewport_size({"width": 720, "height": 800})
    page.wait_for_function("() => !document.startViewTransition")
    page.set_viewport_size({"width": 721, "height": 400})
    page.wait_for_function("() => !!document.startViewTransition")


def test_all_changes_sits_at_the_right_on_a_phone(open_at, db):
    """The date on the left, the way to all of them on the right, as on a desktop; still a full-size target."""
    page = open_at(PHONE)
    field = limit_field(page, "Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    link = page.locator(".ds-change-line__link")
    expect(link).to_be_visible()
    seen_at = page.evaluate("""() => { const box = (s) => document.querySelector(s).getBoundingClientRect();
      const line = box('.ds-change-line'), when = box('.ds-change-line time'), all = box('.ds-change-line__link');
      return {gapAtRight: line.right - all.right, between: all.left - when.right, height: all.height,
              sameRow: Math.abs((when.top + when.bottom) - (all.top + all.bottom)) <= 2}; }""")
    assert seen_at["gapAtRight"] <= 16, seen_at
    assert seen_at["between"] > 16, seen_at
    assert seen_at["sameRow"], seen_at
    assert seen_at["height"] >= 44, seen_at


def test_no_drafts_today_takes_little_room_on_a_phone_and_is_unchanged_on_a_desktop(open_at):
    """The chart's empty block once took most of a phone's first screen; the phone alone changes."""
    words = ".ds-chart__empty"
    phone = open_at(PHONE).locator(words)
    expect(phone).to_have_text("No drafts yet today")
    assert phone.bounding_box()["height"] < 40
    assert phone.evaluate("el => getComputedStyle(el).textAlign") == "start"
    desktop = open_at(DESKTOP).locator(words)
    assert desktop.bounding_box()["height"] >= 100
    assert desktop.evaluate("el => getComputedStyle(el).textAlign") == "center"
