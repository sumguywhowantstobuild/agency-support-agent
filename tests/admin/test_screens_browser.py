"""Screen checks: every admin screen with worst-case data at 320, 375, 768 and 1280 px, light and dark (the
design system's carried checks). Nothing pushes the page sideways, long values wrap or shorten with the full
value at hand, a 7-digit count stays in its column, axe finds nothing, every control is reached by Tab with its focus
ring showing and uncovered, the stand-in client's layer changes colours only, and reduced motion stops all movement.
The toast timer, live regions, one Escape to the innermost owner and the toast's scroll padding are checked by the
screens' own browser tests (test_clients_browser and the others), run in the same three engines. A client and Add
client open as the modal panel over Clients, and a staff member and Add staff as the modal panel over Staff, so their
entries here run on that panel; its drag on phones is tested in test_panel_browser. The app runs on 127.0.0.1 with the
fake database, agent and Slack.
Chromium by default; all three engines (every admin browser test):
pytest tests/admin -k browser --browser chromium --browser webkit --browser firefox"""

import itertools
from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
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
from fakes import ANA_MEMBER, FakeAdminDb, a_change, a_client, a_member, an_entry, drafted
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)

LONG_NAME = "Harborview Family and Cosmetic Dentistry of Springfield West"  # 60 characters
LONG_EMAIL = "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 53 + ".example"  # the longest an email can be
LONG_NOTE = ("Office move to the new building on Harbor Street; the client asked us to hold drafts until the phones "
             "and the HighLevel calendar are back. Ana will check in with the office manager on Monday at 9 a.m.")
BIG = 1_234_567  # a 7-digit count: the column's widest number
assert len(LONG_NAME) == 60 and len(LONG_EMAIL) == 254 and len(LONG_NOTE) == 200

WIDTHS = [320, 375, 768, 1280]
THEMES = ["light", "dark"]
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]  # the design system's own scan
SCREENS = {  # every page a staff member can open, with the worst case in view
    "clients-cards": "/clients?view=cards",
    "clients-list": "/clients?view=list",
    "add-client": "/clients/new",
    "client-sheet": "/clients/harborview",
    "client-sheet-paused": "/clients/brightsmile",
    "staff": "/staff",
    "staff-sheet": "/staff/2",
    "staff-new": "/staff/new",
    "history": "/history",
}
STANDIN = Path(__file__).resolve().parent / "standin.css"


@pytest.fixture
def db():
    long_member = a_member(2, LONG_NAME, LONG_EMAIL, "U0LONGNAME1")
    entries = (
        an_entry("client", LONG_NAME, "added", "2026-10-20T09:00", client_key="harborview", who=LONG_NAME),
        an_entry("limit", LONG_NAME, "limit_changed", "2026-10-23T14:05", client_key="harborview", detail="20 to 200"),
        an_entry("staff", LONG_NAME, "added", "2026-10-26T09:00"),
        an_entry("client", "BrightSmile Dental", "paused", "2026-10-27T10:00", client_key="brightsmile",
                 reason="client_asked", note=LONG_NOTE, detail=f"client_asked: {LONG_NOTE}"),
    )
    history = (a_change("harborview", "added", "2026-10-20T09:00", who=LONG_NAME),
               a_change("brightsmile", "paused", "2026-10-27T10:00", detail=f"client_asked: {LONG_NOTE}",
                        reason="client_asked", note=LONG_NOTE))
    drafts = tuple(drafted(key, minutes) for key, minutes in [("harborview", 5), ("harborview", 70), ("harborview", 130),
                                                              ("cedarline", 8), ("cedarline", 75), ("brightsmile", 200)])
    return FakeAdminDb(
        a_client("harborview", LONG_NAME, used=BIG, limit=200),
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", status="paused", reason="client_asked", note=LONG_NOTE),
        history=history, staff=(ANA_MEMBER, long_member), entries=entries, drafts=drafts,
    )


@pytest.fixture
def app_url(settings, keys, db, fixed_clock):  # the fixed clock: the drafts above are on the chart, whatever the hour
    public, _ = make_key_pair()

    def make_app(url):
        app = create_app()
        here = settings.model_copy(update={"admin_origin": url, "ghl_key_public": SecretStr(public)})
        app.dependency_overrides[get_admin_settings] = lambda: here
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        app.dependency_overrides[get_connection_checker] = lambda: lambda location_id, sealed: "works"
        cap = CheckCap()
        app.dependency_overrides[get_check_cap] = lambda: cap
        long_channel = "x" * 80  # the longest name Slack allows
        app.dependency_overrides[get_channel_lister] = lambda: lambda: [Channel("C0LONG", long_channel),
                                                                              Channel("C0NEWA", "harbor-yoga")]
        return app

    with serving(make_app) as url:
        yield url


@pytest.fixture
def open_screen(browser, keys, app_url):
    """open_screen(path, width, theme, motion="reduce") -> a signed-in page there, fonts loaded."""
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    contexts = []

    def open_(path: str, width: int, theme: str, motion: str = "reduce") -> Page:
        # Reduced motion by default, so a transition never catches a colour or a box half way.
        context = browser.new_context(base_url=app_url, extra_http_headers=signed_in,
                                      viewport={"width": width, "height": 900}, color_scheme=theme,
                                      reduced_motion=motion)
        context.set_default_timeout(5_000)
        contexts.append(context)
        page = context.new_page()
        # The page's theme is this browser's kept choice, not its colour preference: without this every case is dark.
        page.add_init_script(f"localStorage.setItem('admin-theme', '{theme}')")
        page.goto(path)
        assert page.locator("html").get_attribute("data-theme") == theme, "the case isn't testing the theme it names"
        page.evaluate("document.fonts.ready")
        # A panel fades in on a page load, even with reduced motion (the design system's fade), and axe reads colours as
        # the screen shows them: half way through, the help text is a blend with the dimmed page and fails the contrast
        # rule (this was a flag that came and went). Settled, it passes; so wait for the fade to end.
        page.evaluate("""Promise.allSettled([...document.querySelectorAll('dialog')]
                         .flatMap(dialog => dialog.getAnimations({subtree: true}))
                         .filter(animation => animation instanceof CSSTransition)
                         .map(animation => animation.finished))""")
        return page

    yield open_
    for context in contexts:
        context.close()


def every_screen(*dimensions, known: dict | None = None):
    """Parameters for every screen times the given dimensions, named like clients-list-320-dark. known maps a
    (screen, width) to a design-system fault: that case must fail until the fix arrives, then the mark comes off."""
    cases = [(name, *rest) for name in SCREENS for rest in itertools.product(*dimensions)]
    known = known or {}
    return pytest.mark.parametrize("case", [
        pytest.param(case, id="-".join(map(str, case)),
                     marks=[pytest.mark.xfail(strict=True, reason=known[case[:2]])] if case[:2] in known else [])
        for case in cases])


LONG_VALUES = """([values]) => {
  // Each element whose own text holds a long value: it fits (wraps), or it shortens with an ellipsis and the full
  // value is in its title. Options are left out, and a closed picker's chosen one may shorten bare: opened, the
  // picker shows it whole.
  const short = [];
  for (const el of document.body.querySelectorAll("*:not(option):not(script):not(style)")) {
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join("");
    const value = values.find(v => own.includes(v.slice(0, 40)));
    if (!value || !el.checkVisibility()) continue;
    const titled = el.closest("[title]")?.getAttribute("title")?.includes(value);
    if (!own.includes(value) && !titled) {  // shortened before it reached the page: the full value must be in a title
      short.push(`${el.tagName.toLowerCase()}.${el.className}: shows "${own.trim().slice(0, 50)}" without the rest`);
      continue;
    }
    let box = el;  // inline text has no width of its own (Firefox still reports one): measure the line's box
    while (getComputedStyle(box).display === "inline") box = box.parentElement;
    if (box.scrollWidth <= box.clientWidth + 1) continue;
    const style = getComputedStyle(box);
    if (style.textOverflow === "ellipsis" && (titled || box.localName === "selectedcontent")) continue;
    short.push(`${box.tagName.toLowerCase()}.${box.className}: ${box.scrollWidth}px of text in ${box.clientWidth}px`);
  }
  return short;
}"""


@every_screen(WIDTHS, THEMES)
def test_nothing_pushes_the_page_sideways_and_long_values_stay_readable(open_screen, case):
    name, width, theme = case
    page = open_screen(SCREENS[name], width, theme)
    wide = page.evaluate("document.documentElement.scrollWidth")
    assert wide <= width, f"the page is {wide}px wide in a {width}px window: something overflows"
    assert page.evaluate(LONG_VALUES, [[LONG_NAME, LONG_EMAIL, LONG_NOTE, "x" * 80]]) == []


@pytest.mark.parametrize("name", ["add-client", "client-sheet", "client-sheet-paused", "staff-sheet", "staff-new"])
def test_a_panel_screen_is_the_modal_panel_these_checks_run_on(open_screen, name):
    """The client and staff panels: the axe scan, the widths and Tab below see the open panel as the modal
    one, not a floating dialog."""
    page = open_screen(SCREENS[name], 1280, "light")
    expect(page.locator("dialog:modal")).to_have_count(1)


@pytest.mark.parametrize("width", [375, 1280])
def test_all_clients_sits_one_page_gap_under_the_latest_change(open_screen, width):
    """The overview's spacing: nothing invisible takes a row of the page's grid between the parts. Needs you and
    the main panel, the latest-change line, then All clients: the same gap between each."""
    page = open_screen("/clients", width, "light")
    gaps = page.evaluate("""() => {
      const parts = [document.querySelector(".ds-card--needs"), document.querySelector(".ds-band"),
                     document.querySelector(".ds-change-line"), document.querySelector(".ds-page__section")];
      const rows = parseFloat(getComputedStyle(document.querySelector(".ds-page")).rowGap);
      return parts.slice(1).map((part, i) => part.getBoundingClientRect().top - parts[i].getBoundingClientRect().bottom - rows);
    }""")
    assert all(abs(gap) < 1 for gap in gaps), gaps  # Firefox measures fractions of a pixel: 31.99995 for 32


@pytest.mark.parametrize("theme", THEMES)
def test_long_values_fit_the_summary_row(open_screen, theme):
    """A 60-character name in Needs you and a 7-digit total, at the narrowest width."""
    page = open_screen("/clients", 320, theme)
    assert page.evaluate("document.documentElement.scrollWidth") <= 320
    summary = page.locator("#summary")
    expect(summary.get_by_text(f"{LONG_NAME} is at its limit")).to_be_visible()
    expect(summary.locator("#total-used")).to_have_text(str(BIG + 12))
    assert page.evaluate(LONG_VALUES, [[LONG_NAME]]) == []
    for card in summary.locator(".ds-card, .ds-band").all():
        assert card.evaluate("c => c.scrollWidth <= c.clientWidth + 1")


@pytest.mark.parametrize("width", WIDTHS)
def test_a_seven_digit_count_stays_in_its_column(open_screen, width):
    for view in ("cards", "list"):
        page = open_screen(f"/clients?view={view}", width, "light")
        number = page.locator("#results").get_by_text(f"{BIG}", exact=True)  # the chart shows it too
        box = number.evaluate("""n => {
          const text = document.createRange(); text.selectNodeContents(n.firstChild);
          const t = text.getBoundingClientRect(), own = n.getBoundingClientRect();
          return {inside: t.left >= own.left - 1 && t.right <= own.right + 1, wraps: text.getClientRects().length}
        }""")
        assert box == {"inside": True, "wraps": 1}, f"{view} at {width}px: the count spills or breaks"
        if view == "list" and page.locator("#results thead").is_visible():  # a table, not the stacked phone rows
            rights = page.locator("td[data-label='This hour']").evaluate_all("""cells => cells.map(c => {
              const r = document.createRange(); r.selectNodeContents(c.firstChild);
              return Math.round(r.getBoundingClientRect().right) })""")
            assert max(rights) - min(rights) <= 1, f"the counts don't line up on the right: {rights}"


def describe(violation: dict) -> str:
    where = "\n".join(f"    {' '.join(node['target'])}\n      {node['html'][:160]}" for node in violation["nodes"][:3])
    return f"{violation['id']} ({violation['impact']}): {violation['help']}\n{where}"


@every_screen(WIDTHS, THEMES)
def test_the_accessibility_scan_finds_nothing(open_screen, browser_name, case):
    name, width, theme = case
    page = open_screen(SCREENS[name], width, theme)
    # As tall as the whole page: axe reads colours from what is on screen and misreads anything below the fold.
    page.set_viewport_size({"width": width, "height": page.evaluate("document.documentElement.scrollHeight")})
    # Firefox has no customizable select yet: the picker's <button><selectedcontent> stays an empty element that its
    # accessibility tree leaves out (the picker is a plain combobox there), but axe reads the page and calls it unnamed.
    context = {"exclude": [["select > button"]]} if browser_name == "firefox" else None
    results = Axe().run(page, context=context, options={"runOnly": {"type": "tag", "values": TAGS}}).response
    assert len(results["passes"]) > 10, "axe ran almost no rules: the scan itself is broken"
    assert results["violations"] == [], "\n" + "\n".join(describe(v) for v in results["violations"])


RINGS = """const rings = el => [null, "::before", "::after"].map(part => {  // the visible outlines and shadows, "" for none
  const s = getComputedStyle(el, part);
  const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0;
  return [outline ? `${s.outlineStyle} ${s.outlineWidth} ${s.outlineColor}` : "",
          s.boxShadow === "none" ? "" : s.boxShadow].join(" ");
}).join("").trim();"""

CONTROLS = """(noLinks) => {""" + RINGS + """
  // What Tab should reach: every visible, enabled control (the toast's buttons are hidden until it opens). Every
  // control gets a stop number, so a link Tab reaches anyway is still named, and its look at rest. With a modal panel
  // open, Tab can't reach what is behind it: only the panel's own controls count.
  const scope = document.querySelector("dialog:modal") || document;
  const all = [...scope.querySelectorAll(
    'a[href], button, input:not([type="hidden"]), select, textarea, summary, [tabindex]:not([tabindex="-1"])')]
    .filter(el => !el.matches("select *") && !el.disabled && el.checkVisibility({visibilityProperty: true}));
  all.forEach((el, i) => { el.dataset.stop = String(i); el.dataset.rest = rings(el); });
  return all.filter(el => !(noLinks && el.matches("a[href]"))).map(el => el.dataset.stop);
}"""

FOCUSED = """async () => {""" + RINGS + """
  await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));  // WebKit scrolls a frame late
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  // A ring: a visible outline or shadow the control didn't have at rest (a resting shadow alone is no focus mark).
  const now = rings(el);
  // The centre of each line box: a link that wraps has a gap between its lines where its outer box's centre may fall.
  const onTop = r => {
    const top = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1),
                                          Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
    return !!top && el.contains(top);  // the control itself (or its own part) is what is on top
  };
  return {
    stop: el.dataset.stop ?? null,
    what: `${el.tagName.toLowerCase()} "${(el.getAttribute("aria-label") || el.textContent || el.value || "").trim().slice(0, 30)}"`,
    ring: now !== "" && now !== el.dataset.rest,
    seen: [...el.getClientRects()].some(onTop),
  };
}"""


@every_screen(WIDTHS, THEMES)
def test_tab_reaches_every_control_with_its_focus_ring_in_view(open_screen, browser_name, case):
    name, width, theme = case
    page = open_screen(SCREENS[name], width, theme)
    # Safari's Tab skips links unless its "Press Tab to highlight each item" is on, and Playwright's WebKit can't turn
    # that on: there Tab must reach every other control, and Chromium and Firefox check the links.
    expected = set(page.evaluate(CONTROLS, browser_name == "webkit"))
    # Enough presses for every control, links included: WebKit on Linux does stop on links.
    presses = page.evaluate("document.querySelectorAll('[data-stop]').length") + 5
    reached, problems = [], []
    for _ in range(presses):
        page.keyboard.press("Tab")
        now = page.evaluate(FOCUSED)
        if now is None or now["stop"] in reached:
            break  # round the page and back
        if now["stop"] is None:
            continue  # not a control: WebKit stops on the open sheet panel itself
        reached.append(now["stop"])
        if not now["ring"]:
            problems.append(f"{now['what']}: no focus ring")
        if not now["seen"]:
            problems.append(f"{now['what']}: covered or off screen when focused")
    assert problems == []
    missed = page.evaluate("ids => ids.map(i => document.querySelector(`[data-stop='${i}']`).outerHTML.slice(0, 120))",
                           sorted(expected - set(reached)))
    assert missed == [], "Tab never reaches these"


@every_screen([375, 1280], THEMES)
def test_the_stand_in_client_changes_colours_and_nothing_else(open_screen, case):
    name, width, theme = case
    page = open_screen(SCREENS[name], width, theme)
    boxes = """() => [...document.body.querySelectorAll("*")].map(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height), s.borderRadius, s.padding]
    })"""
    accent = "getComputedStyle(document.documentElement).getPropertyValue('--ds-color-accent-default').trim()"
    bluejay_boxes, bluejay_accent = page.evaluate(boxes), page.evaluate(accent)
    # Served as the app's own file (its CSP allows no inline style), then the page switched to that client.
    page.route("**/static/ds/clients/standin.css", lambda route: route.fulfill(path=STANDIN))
    page.evaluate("""() => new Promise(done => {
      const sheet = Object.assign(document.createElement("link"), {rel: "stylesheet", href: "/static/ds/clients/standin.css"});
      sheet.onload = done;
      document.head.append(sheet);
    })""")
    page.evaluate("document.documentElement.dataset.client = 'standin'")
    assert page.evaluate(accent) != bluejay_accent, "the stand-in layer didn't take: the check would prove nothing"
    assert page.evaluate(boxes) == bluejay_boxes, "a client's layer moved or reshaped something"


MOVING = """() => document.getAnimations().filter(a => {
  const timing = a.effect?.getComputedTiming();
  if (!timing || !(timing.duration > 0) || a.playState === "finished") return false;
  const changes = a.transitionProperty ? [a.transitionProperty] : a.effect.getKeyframes().flatMap(Object.keys);
  return changes.some(p => /transform|translate|scale|rotate|^(top|left|right|bottom|width|height|inset|margin)/.test(p));
}).map(a => a.transitionProperty || a.animationName || "script")"""


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_reduced_motion_stops_movement_and_everything_still_works(open_screen, db, motion):
    page = open_screen("/clients?view=cards", 1280, "light", motion=motion)
    field = page.get_by_role("textbox", name="Limit Cedarline Roofing")
    field.fill("35")
    field.press("Enter")
    expect(page.locator(".ds-toast__text")).to_have_text("Cedarline Roofing: 35 drafts an hour.")
    moving = page.evaluate(MOVING)
    if motion == "reduce":
        assert moving == []
        assert db.calls[-1] == (1, "cedarline", 35)  # the save itself is the same
    else:
        assert moving, "nothing moved even with motion allowed: the check would prove nothing"
