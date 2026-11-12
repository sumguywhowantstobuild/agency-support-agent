"""Static files are kept by the browser for a year under an address holding a hash of every static file, so a change
gets a new address; pages and every error stay `no-store`."""

import re
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import support_agent.admin
from support_agent.admin.app import SAFETY_HEADERS, create_app, templates
from support_agent.admin.db import Staff, get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.static_files import content_hash
from support_agent.admin.templating import STATIC_HASH, static_url

ADMIN = Path(support_agent.admin.__file__).parent
KEPT = "private, max-age=31536000, immutable"
OTHER_HEADERS = {name: value for name, value in SAFETY_HEADERS.items() if name != "Cache-Control"}


@pytest.fixture
def app(settings):
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_admin_db] = lambda: None

    def page(request: Request):
        staff = Staff(id=1, name="Ana Reyes", slack_user_id="U0ANA")
        return templates.TemplateResponse(request, "base.html", {"staff": staff, "current": "clients"})

    app.add_api_route("/frame", page, methods=["GET"])
    return app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def test_static_files_are_kept_for_a_year_under_the_hashed_address(client):
    for path in ["admin.css", "admin.js", "ds/ds.css", "ds/clients/bluejay.css", "ds/ds-motion.js"]:
        response = client.get(static_url(path))
        assert response.status_code == 200, path
        assert response.headers["Cache-Control"] == KEPT, path
        assert response.content == (ADMIN / "static" / path).read_bytes(), path
        for name, value in OTHER_HEADERS.items():  # the other safety headers stay on static answers
            assert response.headers[name] == value, (path, name)


def test_cache_control_is_private(client):
    """Kept by this browser only, never by a shared cache between the tunnel and a staff member."""
    for path in ["admin.css", "ds/ds-components.css"]:
        control = client.get(static_url(path)).headers["Cache-Control"]
        assert "private" in control and "public" not in control and "s-maxage" not in control, path


def test_the_hash_changes_with_a_file(tmp_path):
    (tmp_path / "ds").mkdir()
    (tmp_path / "a.css").write_bytes(b"one")
    (tmp_path / "ds" / "b.js").write_bytes(b"two")
    first = content_hash(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert content_hash(tmp_path) == first  # the same files, the same address

    (tmp_path / "a.css").write_bytes(b"one!")  # a file's contents
    changed = content_hash(tmp_path)
    assert changed != first
    (tmp_path / "ds" / "c.js").write_bytes(b"")  # a new file
    added = content_hash(tmp_path)
    assert added not in (first, changed)
    (tmp_path / "ds" / "c.js").rename(tmp_path / "ds" / "d.js")  # only a name
    assert content_hash(tmp_path) not in (first, changed, added)
    (tmp_path / "ds" / "d.js").unlink()
    (tmp_path / "a.css").write_bytes(b"one")
    assert content_hash(tmp_path) == first  # back to the first files, back to the first address


def test_the_hash_does_not_run_a_file_boundary_together(tmp_path):
    """Contents 'ab' + 'c' must not hash like 'a' + 'bc'."""
    (tmp_path / "x").write_bytes(b"ab")
    (tmp_path / "y").write_bytes(b"c")
    first = content_hash(tmp_path)
    (tmp_path / "x").write_bytes(b"a")
    (tmp_path / "y").write_bytes(b"bc")
    assert content_hash(tmp_path) != first


def test_the_address_carries_the_hash_of_the_real_static_folder():
    assert static_url("ds/ds.css") == f"/static/{content_hash(ADMIN / 'static')}/ds/ds.css"


def test_a_missing_file_under_the_hashed_address_is_no_store(client):
    response = client.get(static_url("nothing-here.css"))
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
    for name, value in OTHER_HEADERS.items():
        assert response.headers[name] == value, name


def test_a_stale_hash_is_never_kept(client):
    """A page from before a rebuild may still name the old hash: a year of an old or missing file would be the bug."""
    assert STATIC_HASH != "0" * 16
    response = client.get(f"/static/{'0' * 16}/admin.css")
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"
    escaped = client.get(static_url("%2e%2e/admin.css"))  # climbing out of the hashed folder
    assert "immutable" not in escaped.headers["Cache-Control"]
    assert escaped.headers["Cache-Control"] == "no-store"


def test_pages_stay_no_store(client):
    page = client.get("/frame")
    assert page.status_code == 200 and page.headers["Content-Type"].startswith("text/html")
    assert page.headers["Cache-Control"] == "no-store"
    refused = client.get("/")  # no pass: the refusal page
    assert refused.status_code == 403
    assert refused.headers["Cache-Control"] == "no-store"
    assert client.get("/health").headers["Cache-Control"] == "no-store"


def test_an_uncaught_error_answers_500_with_every_safety_header_and_no_error_text(app):
    """An exception that escapes a route is answered outside the middleware, so it needs its own safety headers."""
    def broken(request: Request):
        raise RuntimeError("secret-detail-4411")

    app.add_api_route("/broken", broken, methods=["GET"])
    response = TestClient(app, raise_server_exceptions=False).get("/broken")
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "no-store"
    for name, value in SAFETY_HEADERS.items():
        assert response.headers[name] == value, name
    assert "secret-detail-4411" not in response.text and "RuntimeError" not in response.text
    assert "Traceback" not in response.text


def test_the_served_style_folder_holds_no_release_record_and_only_bluejays_sheet():
    """Moved from the design-system copy check: these files are served with no sign-in and kept for a year, so
    releases.json (it names every client) and another client's sheet must never arrive in the served folder."""
    assert not (ADMIN / "static" / "ds" / "releases.json").exists()
    assert sorted(path.name for path in (ADMIN / "static" / "ds" / "clients").iterdir()) == ["bluejay.css"]


def test_old_static_address_still_answers(client):
    for path in ["/static/admin.js", "/static/admin.css", "/static/ds/ds.css"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["Cache-Control"] == "no-store", path
        for name, value in OTHER_HEADERS.items():
            assert response.headers[name] == value, (path, name)


def test_the_page_names_every_static_file_by_its_hashed_address(client):
    html = client.get("/frame").text
    named = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert len(named) == 7  # the favicon, the two scripts and four stylesheets
    assert all(address.startswith(static_url("")) for address in named), named
    for address in named:
        assert client.get(address).headers["Cache-Control"] == KEPT, address
    for template in (ADMIN / "templates").glob("*.html"):  # no template spells a static address by hand
        assert "/static/" not in template.read_text(encoding="utf-8"), template.name
