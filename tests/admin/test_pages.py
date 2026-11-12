"""The page frame every admin page shares: the design system's files in the README's order, Bluejay's look, the nav."""

import re
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import support_agent.admin
from support_agent.admin.app import create_app, templates
from support_agent.admin.db import Staff, get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.templating import static_url

ADMIN = Path(support_agent.admin.__file__).parent
DS_FILES = [static_url(name) for name in ["ds/ds.css", "ds/clients/bluejay.css", "ds/ds-components.css", "admin.css"]]


@pytest.fixture
def app(settings):
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_admin_db] = lambda: None  # no page here reaches the staff list
    return app


@pytest.fixture
def frame_client(app) -> TestClient:
    def page(request: Request):  # what a signed-in page passes to the frame
        staff = Staff(id=1, name="Ana Reyes", slack_user_id="U0ANA")
        return templates.TemplateResponse(request, "base.html", {"staff": staff, "current": "clients"})

    app.add_api_route("/frame", page, methods=["GET"])
    return TestClient(app)


def test_the_frame_wears_bluejays_look_in_the_readme_order(frame_client):
    html = frame_client.get("/frame").text
    root = re.search(r"<html[^>]*>", html).group(0)
    assert 'data-mode="productive"' in root and 'data-client="bluejay"' in root and 'data-theme="dark"' in root
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)">', html)
    assert links == DS_FILES
    script = f'<script src="{static_url("admin.js")}"></script>'
    assert script in html and html.index(script) < html.index("<link rel=\"stylesheet\"")  # sets the look before paint
    assert "style=" not in html  # the page's security policy blocks inline styles


def test_the_nav_marks_the_current_page(frame_client):
    html = frame_client.get("/frame").text
    nav = re.search(r'<nav class="ds-side-nav__links" aria-label="Main">(.*?)</nav>', html, re.S).group(1)
    assert re.findall(r'<a [^>]*href="([^"]+)"', nav) == ["/clients", "/staff", "/history"]
    assert re.findall(r'aria-current="page"[^>]*>.*?<span class="ds-side-nav__label"[^>]*>([^<]+)<', nav) == ["Clients"]


def test_the_refused_page_has_no_nav(app):
    response = TestClient(app).get("/")  # no pass
    assert response.status_code == 403
    assert "ds-side-nav" not in response.text and "ds-shell" not in response.text
    assert 'data-client="bluejay"' in response.text


def test_the_page_scripts_are_served(app):  # the plain-address and the kept-address answers are in test_static.py
    client = TestClient(app)
    for path in [static_url("admin.js"), static_url("admin.css")]:
        assert client.get(path).status_code == 200, path


def test_every_icon_a_page_names_is_in_the_page(frame_client):
    """The set travels inside each page: asking for the file again after a save blanked the icon (live 1.2.0)."""
    html = frame_client.get("/frame").text
    drawn = set(re.findall(r'<symbol id="(i-[a-z-]+)"', html))
    assert "i-check" in drawn
    named = set()
    sources = [*ADMIN.glob("templates/*.html"), ADMIN / "static" / "admin.js", *ADMIN.glob("*.py")]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "ds-icons.svg" not in text or source.name == "templating.py", source.name
        named |= set(re.findall(r"""["'#](i-[a-z-]+)\b""", text))
    assert len(named) > 5 and named <= drawn, named - drawn


def test_the_icon_set_takes_no_room_and_no_new_style(frame_client):
    html = frame_client.get("/frame").text
    assert re.search(r'<svg display="none" aria-hidden="true"><symbol', html)
    assert "style=" not in html  # the page's security policy blocks inline styles
