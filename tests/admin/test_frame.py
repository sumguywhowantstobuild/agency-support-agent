"""The page frame of the new look: the side menu with the person signed in, the page's add button, the
logo, and what the server sends for the theme and the menu. The script's own choices (kept per browser, read before the
first paint) are in test_frame_browser.py. The database is a fake."""

import re

import pytest
from fastapi.testclient import TestClient

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.templating import static_url
from conftest import AUDIENCE, ORIGIN
from fakes import FakeAdminDb, a_client
from mint_test_pass import sign_pass


@pytest.fixture
def db():
    return FakeAdminDb(
        a_client("almost", "Almost Dental", used=29, limit=30),
        a_client("full", "Full Roofing", used=30, limit=30),
        a_client("mid", "Mid Bakery", used=12, limit=30),
        a_client("low", "Low Plumbing", used=5, limit=20),
        a_client("tiny", "Tiny Yoga", used=1, limit=50),
        a_client("idle", "Idle Florist", used=0, limit=30),
        a_client("zed", "Zed Idle", used=0, limit=30),
        a_client("paused", "Paused Co", used=40, limit=40, status="paused", reason="client_asked"),
        a_client("gone", "Gone Co", used=10, limit=10, status="removed"),
    )


@pytest.fixture
def client(settings, keys, db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE),
                 "Origin": ORIGIN}
    return TestClient(app, headers=signed_in, follow_redirects=False)


def test_first_visit_is_dark_and_open(client):
    for address in ["/clients", "/staff", "/history"]:
        root = re.search(r"<html[^>]*>", client.get(address).text).group(0)
        assert 'data-theme="dark"' in root and 'data-side-nav="open"' in root, address


def test_a_page_reads_no_clients_for_the_menu(client, db):
    """The menu holds no "This hour" client list, so no page reads the clients just to draw the menu."""
    reads = []
    db.clients = lambda: reads.append("clients") or []

    page = client.get("/staff")

    assert page.status_code == 200 and "This hour" not in page.text
    assert reads == []


@pytest.mark.parametrize("address, words, href", [("/clients", "Add client", "/clients/new"),
                                                   ("/staff", "Add staff", "/staff/new")])
def test_add_button_follows_the_page(client, address, words, href):
    page = client.get(address).text

    [action] = re.findall(r'<a class="ds-button ds-button--accent ds-page-header__add"[^>]*>.*?</a>', page, re.S)
    assert f'href="{href}"' in action and action.endswith(f"{words}</a>")
    assert "ds-side-nav__action" not in page  # in the page's title row at every width


def test_history_has_no_add_button(client):
    assert "ds-page-header__add" not in client.get("/history").text


def test_no_script_page_is_dark_with_a_working_menu(client):
    page = client.get("/staff").text

    assert 'data-theme="dark"' in re.search(r"<html[^>]*>", page).group(0)
    # The controls that need the script wait hidden until it runs; the menu itself is plain links.
    [collapse] = re.findall(r'<button[^>]*data-side-nav-toggle[^>]*>', page)
    [theme] = re.findall(r'<button[^>]*data-theme-toggle[^>]*>', page)
    assert " hidden" in collapse and " hidden" in theme
    nav = re.search(r'<nav class="ds-side-nav__links" aria-label="Main">(.*?)</nav>', page, re.S).group(1)
    assert re.findall(r'<a [^>]*href="([^"]+)"', nav) == ["/clients", "/staff", "/history"]
    assert re.findall(r'<a [^>]*aria-current="page"[^>]*href="([^"]+)"|<a [^>]*href="([^"]+)"[^>]*aria-current="page"',
                      nav) == [("", "/staff")]


@pytest.mark.parametrize("address", ["/clients", "/staff", "/history"])
def test_each_page_has_the_theme_button_on_its_title_row(client, address):
    page = client.get(address).text

    [button] = re.findall(r'<button[^>]*data-theme-toggle[^>]*>', page)
    assert 'aria-label="Switch to light"' in button and 'title="Switch to light"' in button  # dark on a first visit
    row = re.search(r'<h1 class="ds-page-header__title"[^>]*>.*?data-theme-toggle', page, re.S)
    assert row and "</header>" not in row.group(0)  # in the same header as the page's title


def test_the_menu_button_says_what_it_does(client):
    page = client.get("/staff").text

    [collapse] = re.findall(r'<button[^>]*data-side-nav-toggle[^>]*>', page)
    assert 'aria-label="Collapse the menu"' in collapse and 'aria-expanded="true"' in collapse


def who_region(page: str) -> str:
    """What is inside the person area (a region of Staff's script): up to the end of the side menu, past the person's own
    menu, which closes with it."""
    return re.search(r'<div class="ds-side-nav__who" id="who">(.*?)</div>\s*</div>\s*</div>\s*<div class="ds-shell__main">',
                     page, re.S).group(1)


def test_the_person_signed_in_is_in_the_menu(client):
    page = client.get("/history").text

    who = who_region(page)
    assert "Ana Reyes" in who and 'aria-hidden="true">A<' in who


SIGN_OUT = "/cdn-cgi/access/logout"
CLOUDFLARE_TEAM = "example-team.cloudflareaccess.com"


@pytest.fixture
def behind_cloudflare(settings, keys, db) -> TestClient:
    """The same app with a real Cloudflare team in its settings, and a pass that team signed (its issuer), as
    test_auth.py does."""
    real = settings.model_copy(update={"cf_team_domain": CLOUDFLARE_TEAM})
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: real
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE,
                                                      issuer=real.issuer),
                 "Origin": ORIGIN}
    return TestClient(app, headers=signed_in, follow_redirects=False)


def test_sign_out_shows_behind_cloudflare(behind_cloudflare, settings):
    assert settings.sign_out == ""
    assert settings.model_copy(update={"cf_team_domain": CLOUDFLARE_TEAM}).sign_out == SIGN_OUT
    for address in ["/clients", "/staff", "/history"]:
        who = who_region(behind_cloudflare.get(address).text)
        assert re.findall(r'<a [^>]*href="([^"]*)"[^>]*>Sign out</a>', who) == [SIGN_OUT], address
        assert len(re.findall(r"<a ", who)) == 1, address  # the one item, and nothing else to follow
    # The address is fixed: nothing in the request (its query, its Host) builds or changes it.
    hostile = behind_cloudflare.get("/clients?next=https://evil.example/&return=//evil.example/out",
                                    headers={"Host": "evil.example"})
    assert re.findall(r'href="([^"]*)"[^>]*>Sign out</a>', who_region(hostile.text)) == [SIGN_OUT]
    assert "evil.example" not in hostile.text


def test_no_sign_out_on_the_test_copy(client):
    for address in ["/clients", "/staff", "/history"]:
        page = client.get(address).text
        who = who_region(page)
        assert "Sign out" not in who and "logout" not in page.lower(), address
        assert re.findall(r'<h2 class="ds-side-nav__menu-name">([^<]*)</h2>', who) == ["Ana Reyes"], address  # the name shows


def test_the_person_button_names_the_person_first(client):
    who = who_region(client.get("/staff").text)

    [button] = re.findall(r"<button[^>]*>", who)
    label = re.search(r'aria-label="([^"]*)"', button).group(1)
    assert 'popovertarget="person-menu"' in button and 'type="button"' in button
    assert label == "Ana Reyes, Support team, account menu"
    visible = re.search(r'<span class="ds-side-nav__name">([^<]*)</span>', who).group(1)
    assert visible == "Ana Reyes" and label.startswith(visible)  # the spoken name starts with the words people see
    [menu] = re.findall(r'<div[^>]*id="person-menu"[^>]*>', who)
    assert re.search(r"\spopover[\s>]", menu), "the menu is a native popover"
    assert "role=" not in menu and "role=" not in who  # no role="menu": it would promise arrow keys


def test_the_logo_takes_its_colours_from_classes_and_has_a_favicon(client):
    page = client.get("/history").text

    [logo] = re.findall(r'<svg class="ds-side-nav__logo".*?</svg>', page, re.S)
    stops = re.findall(r"<stop[^>]*>", logo)
    # The system's own rule paints the two stops (brand.logo-from and logo-to), so nothing is set on them here.
    assert len(stops) == 2 and not any("style" in stop or "stop-color" in stop for stop in stops)
    gradient = re.search(r'<linearGradient id="([^"]+)"', logo).group(1)
    assert f'fill="url(#{gradient})"' in logo and "style=" not in page
    assert 'fill-rule="evenodd"' in logo and "<path" in logo
    assert f'<link rel="icon" type="image/svg+xml" href="{static_url("favicon.svg")}">' in page
    icon = client.get(static_url("favicon.svg"))
    assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg+xml")
    assert "<style" not in icon.text and "<script" not in icon.text


def test_the_logo_is_the_crested_profile_with_wings(client):
    page = client.get("/history").text
    drawing = client.get(static_url("favicon.svg")).text

    for where in [page, drawing]:
        assert where.count("M3 29C6 27 7.5 24 8 20") == 1  # the board's option 4: the crested profile
        assert where.count("M11.8 18.7") == 1 and where.count("M13.6 22.2") == 1  # and its two wing lines
