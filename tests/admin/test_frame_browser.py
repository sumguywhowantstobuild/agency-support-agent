"""The theme and the menu choice in a real browser: dark and open on a first visit, kept per browser in
localStorage, applied by admin.js before the first paint, and a page that still works when storage fails, holds a value
nobody wrote, or has no script at all. Run with: pytest tests/admin/test_frame_browser.py --browser chromium"""

import re

import pytest
from playwright.sync_api import Page, expect

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from conftest import AUDIENCE
from fakes import FakeAdminDb, a_client
from live import serving
from look_contrast import contrast, role, values
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)

THEME, MENU = "admin-theme", "admin-side-nav"  # the two words localStorage holds, under these names


@pytest.fixture
def db():
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing", used=12, limit=30))


@pytest.fixture
def team():
    """The Cloudflare team the app is behind: none (the test copy's own name), unless a test names one."""
    return None


@pytest.fixture
def new_page(browser, settings, keys, db, team):
    """new_page(**context_options) -> a signed-in page in a context of its own."""

    def make_app(url):
        app = create_app()
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
        def make(on=None, **context_options) -> Page:
            context = (on or browser).new_context(base_url=url, extra_http_headers=signed_in, **context_options)
            context.set_default_timeout(5_000)
            contexts.append(context)
            return context.new_page()

        yield make
        for context in contexts:
            context.close()


@pytest.fixture
def page(new_page) -> Page:
    return new_page()


def root(page: Page):
    return page.locator("html")


def keep(page: Page, key: str, value: str):
    """Put a value in this browser's storage, as if an earlier visit (or anyone) had."""
    page.evaluate("([key, value]) => localStorage.setItem(key, value)", [key, value])


def stored(page: Page, key: str):
    return page.evaluate("key => localStorage.getItem(key)", key)


# What the page's root held when the first stylesheet was added to the page: if admin.js has set the look by then, it
# is set before anything is painted. Kept for the test to read.
WATCH_FIRST_PAINT = """
window.__atFirstStyle = null;
new MutationObserver((changes, watcher) => {
  const link = document.querySelector('link[rel=stylesheet]');
  if (link) {
    const root = document.documentElement;
    window.__atFirstStyle = {theme: root.getAttribute('data-theme'), menu: root.getAttribute('data-side-nav')};
    watcher.disconnect();
  }
}).observe(document, {childList: true, subtree: true});
"""


def test_first_visit_is_dark_and_open(page):
    page.goto("/staff")

    expect(root(page)).to_have_attribute("data-theme", "dark")
    expect(root(page)).to_have_attribute("data-side-nav", "open")
    expect(page.get_by_role("button", name="Collapse the menu")).to_be_visible()
    expect(page.get_by_role("button", name="Switch to light")).to_be_visible()


def test_theme_choice_is_kept(page):
    page.goto("/staff")
    page.get_by_role("button", name="Switch to light").click()

    expect(root(page)).to_have_attribute("data-theme", "light")
    expect(page.get_by_role("button", name="Switch to dark")).to_be_visible()  # the button now offers the other
    assert stored(page, THEME) == "light"

    page.add_init_script(WATCH_FIRST_PAINT)
    page.goto("/history")  # another page, and then a reload: the choice is the browser's, not the page's
    expect(root(page)).to_have_attribute("data-theme", "light")
    expect(page.get_by_role("button", name="Switch to dark")).to_be_visible()
    assert page.evaluate("window.__atFirstStyle")["theme"] == "light"  # set before the first stylesheet, so before paint

    page.get_by_role("button", name="Switch to dark").click()
    page.reload()
    expect(root(page)).to_have_attribute("data-theme", "dark")
    assert stored(page, THEME) == "dark"


def test_menu_choice_is_kept(page):
    page.goto("/clients")
    page.get_by_role("button", name="Collapse the menu").click()

    expect(root(page)).to_have_attribute("data-side-nav", "closed")
    expect(page.get_by_role("button", name="Open the menu")).to_have_attribute("aria-expanded", "false")
    assert stored(page, MENU) == "closed"

    page.add_init_script(WATCH_FIRST_PAINT)
    page.goto("/staff")
    expect(root(page)).to_have_attribute("data-side-nav", "closed")
    assert page.evaluate("window.__atFirstStyle")["menu"] == "closed"

    page.get_by_role("button", name="Open the menu").click()
    expect(root(page)).to_have_attribute("data-side-nav", "open")
    page.reload()
    expect(root(page)).to_have_attribute("data-side-nav", "open")
    expect(page.get_by_role("button", name="Collapse the menu")).to_have_attribute("aria-expanded", "true")


# localStorage that throws on every use, as a browser with storage blocked does (a SecurityError on the property too).
BLOCKED_STORAGE = """
Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('blocked', 'SecurityError'); }});
"""
# Storage that can be read but whose writes are refused, as one that is full or read-only.
READ_ONLY_STORAGE = """
Storage.prototype.setItem = function () { throw new DOMException('full', 'QuotaExceededError'); };
"""


@pytest.mark.parametrize("script", [BLOCKED_STORAGE, READ_ONLY_STORAGE], ids=["blocked", "write-refused"])
def test_a_failed_storage_read_leaves_the_defaults(new_page, script):
    page = new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(script)
    page.goto("/staff")

    expect(root(page)).to_have_attribute("data-theme", "dark")
    expect(root(page)).to_have_attribute("data-side-nav", "open")
    # A choice the browser can't keep still changes the page, and nothing breaks.
    page.get_by_role("button", name="Switch to light").click()
    expect(root(page)).to_have_attribute("data-theme", "light")
    page.get_by_role("button", name="Collapse the menu").click()
    expect(root(page)).to_have_attribute("data-side-nav", "closed")
    page.get_by_role("link", name="History").click()  # and the page still works
    expect(page).to_have_url(re.compile(r"/history$"))
    assert errors == []


@pytest.mark.parametrize("value", ["LIGHT", "light ", " light", "Dark", "<img src=x onerror=alert(1)>", "",
                                   "__proto__", "undefined", "null", "light\n", "Closed", "closed "])
def test_a_strange_stored_value_is_ignored(page, value):
    page.goto("/history")
    keep(page, THEME, value)
    keep(page, MENU, value)
    page.reload()

    expect(root(page)).to_have_attribute("data-theme", "dark")
    expect(root(page)).to_have_attribute("data-side-nav", "open")
    expect(page.get_by_role("button", name="Switch to light")).to_be_visible()


def test_a_stored_word_of_the_other_kind_is_ignored(page):
    page.goto("/history")
    keep(page, THEME, "open")
    keep(page, MENU, "dark")
    page.reload()

    expect(root(page)).to_have_attribute("data-theme", "dark")
    expect(root(page)).to_have_attribute("data-side-nav", "open")


@pytest.fixture
def showing_scroll_bars(browser_type, browser_name, browser_type_launch_args):
    """A browser that draws scroll bars: Chromium's headless default hides them, which is how 1.3.0's phone bar got
    through with one."""
    options = dict(browser_type_launch_args)
    if browser_name == "chromium":
        options["ignore_default_args"] = ["--hide-scrollbars"]
    browser = browser_type.launch(**options)
    yield browser
    browser.close()


PHONE_BAR = """() => {
  const nav = document.querySelector(".ds-side-nav");
  const box = (el) => el.getBoundingClientRect();
  const links = [...nav.querySelectorAll(".ds-side-nav__link")];
  const strip = nav.querySelector(".ds-side-nav__links");
  const logo = nav.querySelector(".ds-side-nav__brand");
  const page = document.scrollingElement;
  return {
    scrollsSideways: page.scrollWidth > page.clientWidth,
    scrollsDown: page.scrollHeight > page.clientHeight,
    viewport: document.documentElement.clientWidth,
    window: window.innerWidth,
    stripOverflows: strip.scrollWidth > strip.clientWidth,
    stripBar: strip.offsetHeight - strip.clientHeight,
    linkRights: links.map((l) => Math.round(box(l).right)),
    linkLefts: links.map((l) => Math.round(box(l).left)),
    linkHeights: links.map((l) => Math.round(box(l).height)),
    barHeight: Math.round(box(nav).height),
    centres: [logo, strip].map((el) => Math.round(box(el).top + box(el).height / 2)),
    who: getComputedStyle(nav.querySelector(".ds-side-nav__who")).display,
    toggle: getComputedStyle(nav.querySelector(".ds-side-nav__toggle")).display,
    background: getComputedStyle(nav).backgroundColor,
    frost: getComputedStyle(nav).backdropFilter,
  };
}"""


# 361: the first width past the narrow step, where the tabs have their roomier padding and the least room.
@pytest.mark.parametrize("width", [320, 360, 361, 375, 412])
@pytest.mark.parametrize("tall", [False, True], ids=["short", "tall"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_phone_bar_fits_with_no_scroll_bar(new_page, showing_scroll_bars, browser_name, width, tall, theme):
    """One row, nothing scrolls, the action is icon-only and the bar is solid, on the real page: on a short page and on a
    tall one, whose own scroll bar takes room from the window."""
    page = new_page(on=showing_scroll_bars, viewport={"width": width, "height": 200 if tall else 3000})
    page.add_init_script(f"localStorage.setItem('admin-theme', '{theme}')")
    page.goto("/clients")
    # The real font, and two frames so the layout it causes has landed: the stand-in font is wider for a moment.
    page.evaluate("""document.fonts.load('1em Geist').then(() => document.fonts.ready)
                     .then(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))""")
    seen = page.evaluate(PHONE_BAR)

    assert seen["scrollsDown"] == tall, "the page isn't the height the test means it to be"  # or it checks nothing
    if tall and browser_name == "chromium":
        assert seen["viewport"] < seen["window"], "the page's scroll bar takes no room: the test checks nothing"
    assert not seen["scrollsSideways"], "the page scrolls sideways"
    assert not seen["stripOverflows"], "the tabs overflow their box: a scroll bar would show"
    assert seen["stripBar"] == 0, "the tabs' box has a scroll bar"
    assert all(right <= seen["viewport"] for right in seen["linkRights"]) and min(seen["linkLefts"]) >= 0
    assert min(seen["linkHeights"]) >= 44, seen["linkHeights"]
    assert seen["barHeight"] <= 64, "the bar wrapped"
    assert max(seen["centres"]) - min(seen["centres"]) <= 2, "logo and tabs aren't on one row"
    assert (seen["who"], seen["toggle"]) == ("flex", "none")  # the person comes back as the avatar alone
    assert seen["background"].startswith("rgb("), "the bar is translucent"
    assert seen["frost"] in ("none", ""), "the bar is frosted"


@pytest.mark.parametrize("width", [320, 412])
def test_the_tabs_stay_in_one_place_from_page_to_page_on_a_phone(new_page, width):
    """History has no Add button, and going there once moved the whole bar."""
    page = new_page(viewport={"width": width, "height": 800})
    seen = []
    for address in ("/clients", "/staff", "/history"):
        page.goto(address)
        seen.append(page.locator(".ds-side-nav__links").bounding_box())
    assert seen[0] == seen[1] == seen[2]


TAB_BOXES = """() => document.fonts.load('1em Geist').then(() => document.fonts.ready)
  .then(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
  .then(() => Array.from(document.querySelectorAll('.ds-side-nav__link'), (link) => {
    const r = link.getBoundingClientRect(); return [r.left, r.width]; }))"""


@pytest.mark.parametrize("width", [320, 375])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_phone_tabs_keep_their_place_on_every_page(new_page, width, theme):
    """The current tab's bolder word once made every tab after it move about 1.4 px when the page
    changed. Each tab keeps its left edge and width on every page, and is named once, as its label."""
    page = new_page(viewport={"width": width, "height": 800})
    page.add_init_script(f"localStorage.setItem('admin-theme', '{theme}')")
    seen = {}
    for address in ("/clients", "/staff", "/history"):
        page.goto(address)
        seen[address] = page.evaluate(TAB_BOXES)
        links = page.locator(".ds-side-nav__link")
        for index, label in enumerate(("Clients", "Staff", "History")):
            expect(links.nth(index)).to_have_accessible_name(label)
    assert seen["/clients"] == seen["/staff"] == seen["/history"], seen


@pytest.mark.parametrize("shut", [False, True], ids=["open", "closed"])
def test_the_side_menu_stays_in_one_place_from_page_to_page_on_a_desktop(new_page, shut):
    page = new_page(viewport={"width": 1280, "height": 800})
    page.add_init_script(f"localStorage.setItem('admin-side-nav', '{'closed' if shut else 'open'}')")
    seen = []
    for address in ("/clients", "/staff", "/history"):
        page.goto(address)
        seen.append(page.locator(".ds-side-nav__links").bounding_box()["y"])
    assert seen[0] == seen[1] == seen[2]
    top = page.locator(".ds-side-nav__top").bounding_box()
    gap = page.locator(".ds-side-nav").evaluate("el => parseFloat(getComputedStyle(el).rowGap)")
    assert abs(seen[2] - (top["y"] + top["height"] + gap)) <= 1, "an empty space above the links"


PLACES = """() => { const box = (s) => { const r = document.querySelector(s).getBoundingClientRect(); return [r.left, r.top, r.right]; };
  return {title: box("h1").slice(0, 2), theme: box("[data-theme-toggle]"), scrolls: document.scrollingElement.scrollHeight > innerHeight}; }"""


@pytest.mark.parametrize("width", [412, 1280])
def test_title_and_buttons_stay_in_one_place_from_page_to_page(new_page, showing_scroll_bars, width):
    """Every page keeps one frame. A line above the title once pushed Staff's down, and a page too short for a scroll
    bar pushed its buttons sideways."""
    viewport = {"width": width, "height": 500}
    # A phone's scroll bar floats over the page, as the hidden one does here; a desktop's takes room.
    page = new_page(on=showing_scroll_bars, viewport=viewport) if width > 720 else new_page(viewport=viewport)
    seen = {}
    for address in ("/clients", "/history"):
        page.goto(address)
        seen[address] = page.evaluate(PLACES)
    page.set_viewport_size({"width": width, "height": 2000})  # tall enough that Staff has no scroll bar
    page.goto("/staff")
    seen["/staff"] = page.evaluate(PLACES)
    assert not seen["/staff"].pop("scrolls") and seen["/clients"].pop("scrolls"), "the test needs one page of each kind"
    seen["/history"].pop("scrolls")
    assert seen["/clients"] == seen["/staff"] == seen["/history"]
    add = page.get_by_role("link", name="Add staff").bounding_box()
    page.set_viewport_size({"width": width, "height": 500})
    page.goto("/clients")
    other = page.get_by_role("link", name="Add client").bounding_box()
    assert abs((add["x"] + add["width"]) - (other["x"] + other["width"])) <= 1, "the Add buttons' right edges don't line up"
    assert abs(add["y"] - other["y"]) <= 1


@pytest.mark.parametrize("width, room", [(412, "auto"), (720, "auto"), (721, "stable"), (1280, "stable")])
def test_room_for_the_scroll_bar_is_kept_past_a_phones_width(new_page, width, room):
    page = new_page(viewport={"width": width, "height": 800})
    page.goto("/staff")
    assert page.evaluate("getComputedStyle(document.documentElement).scrollbarGutter") == room


def test_an_open_panel_leaves_the_page_where_it_is(new_page, showing_scroll_bars):
    """Opening a panel once moved the whole page over. Wide enough that the page is centred, so losing
    the room kept for the scroll bar would move the title."""
    page = new_page(on=showing_scroll_bars, viewport={"width": 1920, "height": 700})
    page.goto("/clients")
    title = page.get_by_role("heading", level=1).bounding_box()["x"]
    page.get_by_role("link", name="Add client").click()
    expect(page.locator("dialog:modal")).to_be_visible()
    assert page.get_by_role("heading", level=1, name="Clients").bounding_box()["x"] == title


def test_the_room_beside_an_open_panel_takes_the_dimmed_page_s_colour(new_page):
    """That room sits outside what a dialog's dimmed area covers, so it would show as a bright strip beside the panel."""
    page = new_page(viewport={"width": 1280, "height": 800})
    page.goto("/clients")
    colour = "getComputedStyle(document.documentElement).backgroundColor"
    closed = page.evaluate(colour)
    page.get_by_role("link", name="Add client").click()
    expect(page.locator("dialog:modal")).to_be_visible()
    page.evaluate("Promise.all(document.documentElement.getAnimations().map((animation) => animation.finished))")
    assert page.evaluate(colour) != closed
    assert page.evaluate(colour) == page.evaluate("""() => { const probe = document.createElement("i");
      probe.style.color = "var(--ds-color-overlay-scrim-page)"; document.body.append(probe);
      const seen = getComputedStyle(probe).color; probe.remove(); return seen; }""")


def test_the_side_menu_keeps_its_own_layer_so_its_words_dont_flicker(new_page):
    """With one client showing, Cards and List flickered the menu. The page flips between scrolling and
    fitting the window, and without its own layer the browser redraws the menu's words with different smoothing. Filmed
    frame by frame on the copy: five frames changed, none with this. A rule, because no test can see the smoothing."""
    page = new_page(viewport={"width": 1280, "height": 800})
    page.goto("/clients")
    assert page.locator(".ds-side-nav").evaluate("el => getComputedStyle(el).willChange") == "transform"


ADD_BUTTONS = [("/clients", "Add client", "/clients/new"), ("/staff", "Add staff", "/staff/new")]


@pytest.mark.parametrize("width", [320, 412, 1280])
@pytest.mark.parametrize("address, words, href", ADD_BUTTONS)
def test_the_add_button_sits_beside_the_page_title(new_page, width, address, words, href):
    page = new_page(viewport={"width": width, "height": 800})
    page.goto(address)
    page.evaluate("document.fonts.load('1em Geist').then(() => document.fonts.ready)")

    button = page.get_by_role("link", name=words)  # hidden ones aren't counted: one Add button, in the words
    expect(button).to_have_attribute("href", href)
    title = page.get_by_role("heading", level=1).bounding_box()
    box = button.bounding_box()
    theme = page.locator("[data-theme-toggle]").bounding_box()
    assert title["x"] + title["width"] <= box["x"] and box["x"] + box["width"] <= theme["x"], "not in one row, in order"
    assert abs((title["y"] + title["height"] / 2) - (box["y"] + box["height"] / 2)) <= 2, "the row wrapped"
    assert theme["x"] + theme["width"] <= width, "the row runs off the screen"
    assert abs(box["height"] - theme["height"]) <= 1, "the two buttons beside each other aren't one height"
    assert page.evaluate("document.scrollingElement.scrollWidth <= document.scrollingElement.clientWidth")


def test_no_script_page_is_dark_with_a_working_menu(new_page):
    page = new_page(java_script_enabled=False)
    page.goto("/staff")

    expect(root(page)).to_have_attribute("data-theme", "dark")
    expect(page.get_by_role("button", name="Collapse the menu")).to_be_hidden()  # it can't work, so it isn't there
    expect(page.get_by_role("button", name="Switch to light")).to_be_hidden()
    expect(page.get_by_role("navigation", name="Main").get_by_role("link", name="Staff")).to_have_attribute(
        "aria-current", "page")
    page.get_by_role("navigation", name="Main").get_by_role("link", name="History").click()  # plain links still go
    expect(page).to_have_url(re.compile(r"/history$"))
    expect(page.get_by_role("heading", name="History", level=1)).to_be_visible()
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Clients").click()
    expect(page.get_by_role("link", name="Cedarline Roofing").first).to_be_visible()


# ---- The person menu. Run behind a real Cloudflare team, so Sign out is drawn. ----

REAL_TEAM = "example-team.cloudflareaccess.com"
SIGN_OUT = "/cdn-cgi/access/logout"
ACCOUNT_NAME = "Ana Reyes, Support team, account menu"
FONTS = """document.fonts.load('1em Geist').then(() => document.fonts.ready)
  .then(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))"""


def person(page: Page):
    return page.locator(".ds-side-nav__person")


def person_menu(page: Page):
    return page.locator("#person-menu")


def open_person_menu(page: Page):
    person(page).click()
    expect(person_menu(page)).to_be_visible()


def tab_to_sign_out(page: Page):
    """Tab from the person button to Sign out. A browser that leaves links out of the Tab order (Safari's own default: only
    Option+Tab, or a setting, reaches one) can't be asked to, and is found out first by a Tab between two of the page's own
    links; there the link is focused by script instead, and what happens next is checked the same."""
    sign_out = person_menu(page).get_by_role("link", name="Sign out")
    here = page.evaluate_handle("document.activeElement").as_element()
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Clients").focus()
    page.keyboard.press("Tab")
    reaches_links = page.evaluate("document.activeElement.matches('a[href]')")
    here.focus()
    if reaches_links:
        page.keyboard.press("Tab")
    else:
        sign_out.focus()
    expect(sign_out).to_be_focused()


@pytest.mark.parametrize("team", [REAL_TEAM])
def test_the_person_menu_opens_and_closes_without_the_script(new_page, team):
    page = new_page(java_script_enabled=False, viewport={"width": 1280, "height": 800})
    page.goto("/staff")
    expect(person_menu(page)).to_be_hidden()
    sign_out = person_menu(page).get_by_role("link", name="Sign out")

    person(page).focus()
    page.keyboard.press("Enter")
    expect(person_menu(page)).to_be_visible()
    expect(person_menu(page).get_by_role("heading", name="Ana Reyes", level=2)).to_be_visible()  # the name is its heading
    expect(sign_out).to_have_attribute("href", SIGN_OUT)
    tab_to_sign_out(page)  # the next stop after the button

    page.keyboard.press("Escape")
    expect(person_menu(page)).to_be_hidden()
    expect(person(page)).to_be_focused()  # back on the button

    page.keyboard.press("Enter")
    expect(person_menu(page)).to_be_visible()
    page.keyboard.press("Escape")  # also from the button itself
    expect(person_menu(page)).to_be_hidden()

    open_person_menu(page)
    page.mouse.click(900, 600)  # outside it, on the page
    expect(page).to_have_url(re.compile(r"/staff$"))  # the click followed no link: the menu closed by light dismiss
    expect(person_menu(page)).to_be_hidden()


BOXES = """() => { const r = (s) => { const b = document.querySelector(s).getBoundingClientRect();
    return {left: b.left, top: b.top, right: b.right, bottom: b.bottom}; };
  return {menu: r("#person-menu"), person: r(".ds-side-nav__person"), width: window.innerWidth, height: window.innerHeight}; }"""
NEAR = 24  # the most room a menu may leave between itself and the person it belongs to


@pytest.mark.parametrize("team", [REAL_TEAM])
@pytest.mark.parametrize("where, width, height, side_menu",
                         [("a computer", 1280, 800, "open"), ("a computer, side menu shut", 1280, 800, "closed"),
                          ("a computer, short window", 1280, 500, "open"),
                          ("a phone", 375, 700, "open"), ("a narrow phone", 320, 600, "open")])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_menu_sits_by_the_person(new_page, team, where, width, height, side_menu, theme):
    page = new_page(viewport={"width": width, "height": height})
    page.add_init_script(f"localStorage.setItem('admin-side-nav', '{side_menu}'); localStorage.setItem('admin-theme', '{theme}')")
    page.goto("/clients")
    page.evaluate(FONTS)
    open_person_menu(page)
    seen = page.evaluate(BOXES)
    menu, who = seen["menu"], seen["person"]

    assert 0 <= menu["left"] and menu["right"] <= seen["width"] and 0 <= menu["top"] and menu["bottom"] <= seen["height"], \
        f"{where}: the menu runs off the window: {menu}"
    if width <= 720:  # below the avatar, at the bar's end
        assert who["bottom"] - 1 <= menu["top"] <= who["bottom"] + NEAR, (where, menu, who)
        assert abs(menu["right"] - who["right"]) <= 4, (where, menu, who)
    elif side_menu == "closed":  # beside the avatar
        assert who["right"] - 1 <= menu["left"] <= who["right"] + NEAR, (where, menu, who)
        assert who["top"] <= menu["bottom"] <= who["bottom"] + 1, (where, menu, who)
    else:  # above the person, over the same room as the side menu
        assert who["top"] - NEAR <= menu["bottom"] <= who["top"] + 1, (where, menu, who)
        assert who["left"] - 1 <= menu["left"] < who["right"], (where, menu, who)
        assert menu["left"] <= who["left"] + NEAR


PHONE_AVATAR = """() => { const box = (el) => { const b = el.getBoundingClientRect(); return {left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height}; };
  const nav = document.querySelector(".ds-side-nav"), strip = nav.querySelector(".ds-side-nav__links");
  const links = [...nav.querySelectorAll(".ds-side-nav__link")], who = nav.querySelector(".ds-side-nav__person");
  const logo = nav.querySelector(".ds-side-nav__brand"), page = document.scrollingElement;
  return {
    person: box(who), links: links.map(box), logo: box(logo),
    scrollsSideways: page.scrollWidth > page.clientWidth, viewport: document.documentElement.clientWidth,
    stripOverflows: strip.scrollWidth > strip.clientWidth, barHeight: Math.round(box(nav).height),
    initial: who.querySelector(".ds-side-nav__avatar").textContent.trim(),
    words: getComputedStyle(who.querySelector(".ds-side-nav__label")).display,
  }; }"""


@pytest.mark.parametrize("team", [REAL_TEAM])
@pytest.mark.parametrize("width", [375, 320])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_phone_top_bar_shows_the_avatar(new_page, showing_scroll_bars, team, width, theme):
    page = new_page(on=showing_scroll_bars, viewport={"width": width, "height": 3000})
    page.add_init_script(f"localStorage.setItem('admin-theme', '{theme}')")
    page.goto("/clients")
    page.evaluate(FONTS)
    seen = page.evaluate(PHONE_AVATAR)

    expect(person(page)).to_be_visible()
    expect(person(page)).to_have_accessible_name(ACCOUNT_NAME)
    assert seen["initial"] == "A" and seen["words"] == "none", "the avatar alone, on a phone"
    assert seen["person"]["width"] >= 44 and seen["person"]["height"] >= 44, seen["person"]
    assert seen["person"]["left"] >= max(link["right"] for link in seen["links"]), "not at the end, after the tabs"
    assert seen["person"]["right"] <= seen["viewport"]
    assert not seen["scrollsSideways"], "the page scrolls sideways"
    assert not seen["stripOverflows"], "the tabs lost their room and now scroll"
    assert all(link["right"] <= seen["person"]["left"] and link["left"] >= seen["logo"]["right"] for link in seen["links"])
    assert min(link["height"] for link in seen["links"]) >= 44
    assert seen["barHeight"] <= 64, "the bar wrapped"
    centres = [(box["top"] + box["bottom"]) / 2 for box in (seen["person"], seen["logo"], seen["links"][0])]
    assert max(centres) - min(centres) <= 2, "the avatar, logo and tabs aren't on one row"


def hexed(computed: str) -> str:
    """#rrggbb of an opaque computed colour, 'rgb(r, g, b)'; a translucent one is a failure, since it can't be measured."""
    parts = re.findall(r"[\d.]+", computed)
    assert len(parts) == 3 or float(parts[3]) == 1.0, f"{computed} is translucent: measure it over what it sits on"
    return "#{:02x}{:02x}{:02x}".format(*(int(float(part)) for part in parts[:3]))


LOOKS = """() => { const look = (el) => { const s = getComputedStyle(el); return {color: s.color, background: s.backgroundColor,
    outline: s.outlineColor, outlineWidth: s.outlineWidth, focusVisible: el.matches(":focus-visible"),
    width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height}; };
  const menu = document.getElementById("person-menu");
  return {menu: look(menu), name: look(menu.querySelector(".ds-side-nav__menu-name")),
          item: look(menu.querySelector("a")), button: look(document.querySelector(".ds-side-nav__person")),
          avatar: look(document.querySelector(".ds-side-nav__avatar"))}; }"""


@pytest.mark.parametrize("team", [REAL_TEAM])
@pytest.mark.parametrize("width, side_menu", [(1280, "open"), (1280, "closed"), (375, "open")])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_person_button_and_sign_out_are_big_enough_and_clear(new_page, team, width, side_menu, theme):
    """44 px targets (the button and Sign out), and the colours as they appear on screen: the words 4.5:1, the focus ring
    3:1, on the grounds they sit on. Each state: at rest and focused with the keyboard. With the side menu shut the
    avatar alone is the button, so it needs its 44 px across too (it once had 43)."""
    page = new_page(viewport={"width": width, "height": 800})
    page.add_init_script(f"localStorage.setItem('admin-theme', '{theme}'); localStorage.setItem('admin-side-nav', '{side_menu}')")
    page.goto("/clients")
    page.evaluate(FONTS)
    found = values(theme)
    canvas = role(found, "color.bg.canvas")

    page.keyboard.press("Shift")  # the keyboard is in use, so a focus shows its ring (focus-visible)
    person(page).focus()
    expect(person(page)).to_be_focused()
    focused_button = page.evaluate(LOOKS)["button"]
    page.keyboard.press("Enter")
    expect(person_menu(page)).to_be_visible()
    tab_to_sign_out(page)
    looks = page.evaluate(LOOKS)

    assert focused_button["focusVisible"] and focused_button["outlineWidth"] != "0px"
    assert contrast(hexed(focused_button["outline"]), canvas) >= 3.0, "the button's focus ring"
    assert looks["button"]["height"] >= 44 and looks["item"]["height"] >= 44 and looks["item"]["width"] >= 44
    if width <= 720 or side_menu == "closed":
        assert looks["button"]["width"] >= 44, looks["button"]
    surface = hexed(looks["menu"]["background"])
    assert surface == role(found, "color.bg.surface")
    assert contrast(hexed(looks["item"]["color"]), surface) >= 4.5, "Sign out"
    assert contrast(hexed(looks["name"]["color"]), surface) >= 4.5, "the name heading"
    assert looks["item"]["focusVisible"] and looks["item"]["outlineWidth"] != "0px"
    assert contrast(hexed(looks["item"]["outline"]), surface) >= 3.0, "Sign out's focus ring"
    assert contrast(hexed(looks["avatar"]["color"]), hexed(looks["avatar"]["background"])) >= 4.5, "the initial"


@pytest.mark.parametrize("team", [REAL_TEAM])
def test_a_toast_takes_the_first_escape(new_page, team):
    """One Escape goes to the innermost owner: with a message showing, it closes the message and leaves the person menu."""
    page = new_page(viewport={"width": 1280, "height": 800})
    page.goto("/clients")
    open_person_menu(page)
    page.evaluate("""() => { const toast = document.getElementById("toast");
      toast.querySelector(".ds-toast__text").textContent = "Saved."; toast.showPopover(); }""")
    toast_open = "document.getElementById('toast').matches(':popover-open')"
    assert page.evaluate(toast_open)

    page.keyboard.press("Escape")
    expect(page.locator("#toast")).to_be_hidden()
    assert not page.evaluate(toast_open)
    expect(person_menu(page)).to_be_visible()  # the first Escape was the message's

    page.keyboard.press("Escape")
    expect(person_menu(page)).to_be_hidden()
