"""Pictures of every admin screen for a person to judge, from the test server with made-up sample data, with scroll bars
showing. Not a test: nothing is compared, and it never touches the copy or live. Run it by name:
  PICTURES_DIR=<folder> pytest tests/admin/pictures.py -n 4 --browser chromium --browser webkit --browser firefox
then `python tests/admin/pictures.py <folder>` writes an index.html to page through them."""

import itertools
import os
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from support_agent.admin import history as history_page
from support_agent.admin.agent_client import get_connection_checker
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.check_cap import CheckCap, get_check_cap
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import Channel, get_channel_lister
from support_agent.keys import make_key_pair
from conftest import AUDIENCE, CLOCK
from fakes import ANA_MEMBER, FakeAdminDb, a_change, a_client, a_member, an_entry, drafted
from live import serving
from mint_test_pass import sign_pass

WIDTHS = [320, 360, 412, 1280]
THEMES = ["dark", "light"]
SCREENS = {
    "clients-cards": "/clients?view=cards",
    "clients-list": "/clients?view=list",
    "client-details": "/clients/cedarline",
    "client-paused": "/clients/mabini",
    "add-client": "/clients/new",
    "staff": "/staff",
    "staff-details": "/staff/1",
    "add-staff": "/staff/new",
    "history": "/history",
}


@pytest.fixture
def folder():
    assert os.environ.get("PICTURES_DIR"), "say where the pictures go: PICTURES_DIR=<folder>"
    path = Path(os.environ["PICTURES_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def db():
    drafts = [drafted("cedarline", minutes) for minutes in (5, 20, 35, 70, 80, 130, 190, 200, 250, 310, 420, 500)]
    drafts += [drafted("brightsmile", minutes) for minutes in (3, 12, 25, 40, 50, 65, 100, 125, 160, 240, 300, 360, 480, 600)]
    drafts += [drafted("harbor", minutes) for minutes in (30, 95, 210, 330, 550)]
    drafts += [drafted("dunes", minutes) for minutes in (15, 140, 280)]
    note = "Office move on Monday: hold drafts until the phones are back."
    entries = (
        an_entry("limit", "BrightSmile Dental", "limit_changed", "2026-11-04T17:40", client_key="brightsmile", detail="15 to 20"),
        an_entry("client", "Mabini Bakery", "paused", "2026-11-04T14:05", client_key="mabini", reason="client_asked", note=note,
                 detail=f"client_asked: {note}"),
        an_entry("client", "Dunes Surf School", "added", "2026-11-03T16:20", client_key="dunes", who="Ben Cruz"),
        an_entry("staff", "Ben Cruz", "added", "2026-11-03T15:00"),
        an_entry("limit", "Cedarline Roofing", "limit_changed", "2026-10-29T13:10", client_key="cedarline", detail="25 to 30"),
    )
    history = (a_change("cedarline", "limit_changed", "2026-10-29T13:10", detail="25 to 30"),
               a_change("mabini", "paused", "2026-11-04T14:05", detail=f"client_asked: {note}", reason="client_asked", note=note))
    return FakeAdminDb(
        a_client("brightsmile", "BrightSmile Dental", used=20, limit=20),
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("harbor", "Harbor Yoga Studio", used=4, limit=15),
        a_client("dunes", "Dunes Surf School", used=2, limit=10),
        a_client("mabini", "Mabini Bakery", status="paused", reason="client_asked", note=note),
        history=history, entries=entries, drafts=tuple(drafts),
        staff=(ANA_MEMBER, a_member(2, "Ben Cruz", "ben@bluejay.example", "U0BEN"),
               a_member(3, "Carla Diaz", "carla@bluejay.example", "U0CARLA", active=False)),
    )


@pytest.fixture
def app_url(settings, keys, db, fixed_clock, monkeypatch):
    monkeypatch.setattr(history_page, "_now", lambda: CLOCK)
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
        app.dependency_overrides[get_channel_lister] = lambda: lambda: [Channel("C0NEWA", "palm-bakery")]
        return app

    with serving(make_app) as url:
        yield url


@pytest.fixture
def showing_scroll_bars(browser_type, browser_name, browser_type_launch_args):
    options = dict(browser_type_launch_args)
    if browser_name == "chromium":
        options["ignore_default_args"] = ["--hide-scrollbars"]  # the headless default hides them
    browser = browser_type.launch(**options)
    yield browser
    browser.close()


def open_page(browser, url, keys, viewport, theme, menu="open"):
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    context = browser.new_context(base_url=url, extra_http_headers=signed_in, viewport=viewport, reduced_motion="reduce")
    context.add_init_script(f"localStorage.setItem('admin-theme', '{theme}'); localStorage.setItem('admin-side-nav', '{menu}')")
    return context, context.new_page()


def settle(page, theme):
    assert page.locator("html").get_attribute("data-theme") == theme, "this picture wouldn't show the theme it names"
    page.evaluate("""document.fonts.load('1em Geist').then(() => document.fonts.ready)
                     .then(() => Promise.allSettled([...document.querySelectorAll('dialog')]
                       .flatMap(dialog => dialog.getAnimations({subtree: true})).map(animation => animation.finished)))""")


@pytest.mark.parametrize("name,width,theme", itertools.product(SCREENS, WIDTHS, THEMES),
                         ids=lambda value: str(value))
def test_picture(showing_scroll_bars, browser_name, app_url, keys, folder, name, width, theme):
    context, page = open_page(showing_scroll_bars, app_url, keys, {"width": width, "height": 900}, theme)
    page.goto(SCREENS[name])
    settle(page, theme)
    page.screenshot(path=folder / f"{name}-{width}-{theme}-{browser_name}.png", full_page=True)
    context.close()


@pytest.mark.parametrize("theme", THEMES)
def test_picture_of_the_collapsed_menu(showing_scroll_bars, browser_name, app_url, keys, folder, theme):
    context, page = open_page(showing_scroll_bars, app_url, keys, {"width": 1280, "height": 900}, theme, menu="closed")
    page.goto(SCREENS["clients-cards"])
    settle(page, theme)
    page.screenshot(path=folder / f"collapsed-menu-1280-{theme}-{browser_name}.png", full_page=True)
    context.close()


@pytest.mark.parametrize("size", [16, 32])
def test_picture_of_the_favicon(showing_scroll_bars, browser_name, app_url, keys, folder, size):
    # If the wings blur at 16 px, the favicon drops them; a person decides from this picture.
    context, page = open_page(showing_scroll_bars, app_url, keys, {"width": size, "height": size}, "dark")
    page.goto("/static/favicon.svg")
    page.screenshot(path=folder / f"favicon-{size}-{browser_name}.png")
    context.close()


def write_index(folder: Path) -> None:
    """One page to go through them: Chromium first, a section per screen."""
    pictures = sorted(path.name for path in folder.glob("*.png"))
    screens = list(SCREENS) + ["collapsed-menu", "favicon"]
    lines = ["<!doctype html><meta charset='utf-8'><title>New look pictures</title>",
             "<body style='font:14px system-ui;background:#888;margin:16px'><h1>New look pictures</h1>"]
    for screen in screens:
        lines.append(f"<h2>{screen}</h2><div style='display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start'>")
        for browser in ("chromium", "webkit", "firefox"):
            for name in pictures:
                if name.startswith(screen + "-") and name.endswith(f"-{browser}.png"):
                    lines.append(f"<a href='{name}'><figure style='margin:0'><figcaption>{name[:-4]}</figcaption>"
                                 f"<img src='{name}' loading='lazy' style='max-width:320px;display:block'></figure></a>")
        lines.append("</div>")
    (folder / "index.html").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    write_index(Path(sys.argv[1]))
