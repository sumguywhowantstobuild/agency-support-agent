"""Sign-in: every request needs Cloudflare Access's signed pass and a place on the staff list. The passes here are
made by scripts/mint_test_pass.py, the same way the test copy gets them; the staff list is a fake."""

import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import jwt
import psycopg
import pytest
from fastapi import Depends, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from support_agent.admin.app import SIGNED_IN, create_app
from support_agent.admin.auth import FileKeys, _cloudflare_keys, current_staff, get_key_source, require_same_origin
from support_agent.admin import db
from support_agent.admin.db import Staff, get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import get_channel_lister
from conftest import AUDIENCE, ORIGIN
from mint_test_pass import main, make_keys, sign_pass

ANA = Staff(id=1, name="Ana Reyes", slack_user_id="U0ANA")


class FakeStaffList:
    def __init__(self, *members: tuple[str, Staff]):
        self.active = dict(members)
        self.lookups = 0

    def staff_for_email(self, email: str) -> Staff | None:
        self.lookups += 1
        return self.active.get(email.lower())


@pytest.fixture
def staff():
    return FakeStaffList(("ana@bluejay.example", ANA))


@pytest.fixture
def client(settings, keys, staff):
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: staff

    def whoami(member: Staff = Depends(current_staff)) -> dict:
        return {"id": member.id, "name": member.name, "slack_user_id": member.slack_user_id}

    app.add_api_route("/whoami", whoami, methods=["GET"], dependencies=SIGNED_IN)
    app.add_api_route("/probe", lambda: {"changed": True}, methods=["POST"], dependencies=SIGNED_IN)
    return TestClient(app)


def a_pass(keys, email="ana@bluejay.example", **changes) -> dict:
    private_key, kid, _ = keys
    token = sign_pass(private_key, kid, email, changes.pop("audience", AUDIENCE), **changes)
    return {"Cf-Access-Jwt-Assertion": token}


def test_valid_pass_for_staff(client, keys):
    response = client.get("/whoami", headers=a_pass(keys))
    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "Ana Reyes", "slack_user_id": "U0ANA"}


def refused(response) -> bool:
    return response.status_code == 403 and "not signed in to Bluejay support admin" in response.text


def test_missing_pass_is_403(client, staff):
    assert refused(client.get("/whoami"))
    assert refused(client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": ""}))
    assert refused(client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": "not-a-pass"}))
    assert staff.lookups == 0


def test_wrong_audience_is_403(client, keys, staff):
    """A pass for another Access application of the same team must not open this one."""
    assert refused(client.get("/whoami", headers=a_pass(keys, audience="aud-tag-of-another-app")))
    assert refused(client.get("/whoami", headers=a_pass(keys, issuer="https://other-team.cloudflareaccess.com")))
    assert staff.lookups == 0


def test_expired_pass_is_403(client, keys, staff):
    nine_hours_ago = datetime.now(timezone.utc) - timedelta(hours=9)
    assert refused(client.get("/whoami", headers=a_pass(keys, issued_at=nine_hours_ago)))  # 8-hour pass, ended 1 h ago
    assert staff.lookups == 0


def test_a_pass_issued_30_seconds_ahead_is_accepted(client, keys):
    """A server clock a few seconds behind Cloudflare's sees a fresh pass as issued in the future (live 1.7.0)."""
    ahead = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert client.get("/whoami", headers=a_pass(keys, issued_at=ahead)).status_code == 200


def test_a_pass_issued_2_minutes_ahead_is_refused(client, keys, staff):
    ahead = datetime.now(timezone.utc) + timedelta(minutes=2)
    assert refused(client.get("/whoami", headers=a_pass(keys, issued_at=ahead)))
    assert staff.lookups == 0


def test_a_pass_expired_30_seconds_ago_is_accepted(client, keys):
    """The allowance works both ways: a clock a little ahead sees a pass as just ended."""
    ended_30_seconds_ago = datetime.now(timezone.utc) - timedelta(hours=8, seconds=30)
    assert client.get("/whoami", headers=a_pass(keys, issued_at=ended_30_seconds_ago)).status_code == 200


def test_a_pass_expired_2_minutes_ago_is_refused(client, keys, staff):
    ended_2_minutes_ago = datetime.now(timezone.utc) - timedelta(hours=8, minutes=2)
    assert refused(client.get("/whoami", headers=a_pass(keys, issued_at=ended_2_minutes_ago)))
    assert staff.lookups == 0


def test_signed_by_another_key_is_403(client, keys, staff):
    _, kid, _ = keys
    other_private_key, _, _ = make_keys()
    forged = sign_pass(other_private_key, kid, "ana@bluejay.example", AUDIENCE)  # claims our key id, signed by another
    assert refused(client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": forged}))
    claims = {"aud": [AUDIENCE], "iss": "https://test.invalid", "email": "ana@bluejay.example",
              "iat": datetime.now(timezone.utc), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": kid})
    shared_secret = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": kid})
    assert refused(client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": unsigned}))
    assert refused(client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": shared_secret}))
    assert staff.lookups == 0


def test_valid_pass_not_on_staff_list_is_403(client, keys, staff, caplog):
    """The gate may let in a whole email domain; only the staff list decides who works here."""
    assert refused(client.get("/whoami", headers=a_pass(keys, email="stranger@bluejay.example")))
    assert staff.lookups == 1
    assert "stranger" not in caplog.text  # who tried is Cloudflare's log, not ours


def test_deactivated_member_is_refused_on_the_next_request(client, keys, staff):
    """Ana's pass stays valid for hours; taking her off the list must count at once."""
    ana_pass = a_pass(keys)
    assert client.get("/whoami", headers=ana_pass).status_code == 200
    del staff.active["ana@bluejay.example"]  # what admin_set_staff_active(false) does to admin_staff_for_email
    assert refused(client.get("/whoami", headers=ana_pass))
    assert staff.lookups == 2


def test_database_down_is_a_plain_503_page(client, keys, staff, caplog):
    """Not "not signed in" (that would send people to fix their sign-in), and no error text on the page or in the log."""
    def down(email):
        raise psycopg.OperationalError("connection to 127.0.0.1 failed: password authentication failed for admin_app")

    staff.staff_for_email = down
    response = client.get("/whoami", headers=a_pass(keys))
    assert response.status_code == 503 and "reach its database right now" in response.text
    assert "password" not in response.text and "password" not in caplog.text


@pytest.mark.parametrize("damage", ["missing", "not json", "no keys"])
def test_a_broken_test_key_file_is_403_not_a_crash(client, keys, damage):
    ana_pass = a_pass(keys)
    if damage == "missing":
        keys[2].unlink()
    else:
        keys[2].write_text("{" if damage == "not json" else '{"keys": []}')
    assert refused(client.get("/whoami", headers=ana_pass))


def test_cloudflare_unreachable_is_a_plain_503_page(client, keys, staff):
    class Unreachable:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url, err: timed out")

    client.app.dependency_overrides[get_key_source] = Unreachable
    response = client.get("/whoami", headers=a_pass(keys))
    assert response.status_code == 503 and "reach Cloudflare to check your sign-in" in response.text
    assert staff.lookups == 0


def test_post_from_another_origin_is_403(client, keys):
    """A page elsewhere can't make a signed-in person's browser change anything here (the gate's cookie travels)."""
    ana_pass = a_pass(keys)
    assert client.post("/probe", headers=ana_pass | {"Origin": ORIGIN}).json() == {"changed": True}
    for origin in ["https://evil.example.com", "null", ORIGIN + ".evil.example.com", ORIGIN.upper()]:
        response = client.post("/probe", headers=ana_pass | {"Origin": origin})
        assert response.status_code == 403 and "changed" not in response.text, origin
    assert client.post("/probe", headers=ana_pass).status_code == 403  # no Origin at all


def test_every_answer_carries_safety_headers(client, keys):
    """No framing by another site (a click on a hidden Remove button), nothing cached on a shared computer, scripts
    and styles from the app only."""
    for response in [client.get("/whoami", headers=a_pass(keys)), client.get("/whoami"), client.get("/health")]:
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy and "form-action 'self'" in policy
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "same-origin"
        assert response.headers["Cache-Control"] == "no-store"


def test_live_checks_passes_against_cloudflare_and_the_copy_against_its_key_file(settings):
    team = "example-team.cloudflareaccess.com"
    live = settings.model_copy(update={"admin_test_keys": None, "cf_team_domain": team,
                                       "cf_certs_url": f"https://{team}/cdn-cgi/access/certs"})
    source = get_key_source(live)
    assert isinstance(source, jwt.PyJWKClient) and source.uri == live.cf_certs_url
    assert get_key_source(live) is source  # one client, so Cloudflare's keys are fetched once per 5 minutes
    assert isinstance(get_key_source(settings), FileKeys)


@pytest.fixture
def certs_server():
    """Stands in for https://<team>.cloudflareaccess.com/cdn-cgi/access/certs: serves whatever key set it holds."""
    served = {"jwks": '{"keys": []}', "fetches": 0}

    class Certs(BaseHTTPRequestHandler):
        def do_GET(self):
            served["fetches"] += 1
            body = served["jwks"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Certs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    served["url"] = f"http://127.0.0.1:{server.server_port}/cdn-cgi/access/certs"
    yield served
    server.shutdown()


def test_cloudflares_published_keys_check_the_pass_and_follow_a_key_change(client, certs_server, monkeypatch):
    """The live path: keys fetched from the certs URL, kept, and fetched again when Cloudflare signs with a new key,
    at most once per 30 s (an unknown key id is what every forged pass carries)."""
    now = [1000.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(jwt.jwks_client, "time", clock)
    monkeypatch.setattr(jwt.jwk_set_cache, "time", clock)
    old_private, old_kid, old_jwks = make_keys()
    certs_server["jwks"] = old_jwks
    client.app.dependency_overrides[get_key_source] = lambda: _cloudflare_keys(certs_server["url"])
    old_pass = {"Cf-Access-Jwt-Assertion": sign_pass(old_private, old_kid, "ana@bluejay.example", AUDIENCE)}
    assert client.get("/whoami", headers=old_pass).status_code == 200
    assert client.get("/whoami", headers=old_pass).status_code == 200
    assert certs_server["fetches"] == 1  # kept, not fetched per request

    new_private, new_kid, new_jwks = make_keys()  # Cloudflare rotates its signing key
    certs_server["jwks"] = new_jwks
    new_pass = {"Cf-Access-Jwt-Assertion": sign_pass(new_private, new_kid, "ana@bluejay.example", AUDIENCE)}
    now[0] += 10
    assert refused(client.get("/whoami", headers=new_pass))  # within 30 s of the last fetch: no new fetch
    assert certs_server["fetches"] == 1
    now[0] += 25
    assert client.get("/whoami", headers=new_pass).status_code == 200
    assert certs_server["fetches"] == 2
    now[0] += 60
    forged = {"Cf-Access-Jwt-Assertion": sign_pass(make_keys()[0], new_kid, "ana@bluejay.example", AUDIENCE)}
    assert refused(client.get("/whoami", headers=forged))


def test_home_goes_to_clients_and_health_needs_no_sign_in(client, keys):
    response = client.get("/", headers=a_pass(keys), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/clients"
    assert client.get("/health").json() == {"ok": True}


def test_mint_script_makes_a_pass_the_copy_accepts(client, keys, capsys):
    """As run for the test copy: a new key file, the old passes dead, the printed pass accepted."""
    old_pass = a_pass(keys)
    assert main(["ana@bluejay.example", str(keys[2]), AUDIENCE]) == 0
    new_pass = capsys.readouterr().out.strip()
    assert client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": new_pass}).status_code == 200
    assert refused(client.get("/whoami", headers=old_pass))


def app_routes() -> list[APIRoute]:
    return [route for route in create_app().routes if isinstance(route, APIRoute)]


def test_every_route_but_health_needs_sign_in_and_same_origin():
    """Proves it for pages added later too: a route without SIGNED_IN fails here."""
    for route in app_routes():
        checks = {dependency.call for dependency in route.dependant.dependencies}
        if route.path == "/health":
            assert checks == set(), "health answers Docker without sign-in, and nothing else"
        else:
            assert {current_staff, require_same_origin} <= checks, route.path




def test_get_never_changes_anything(monkeypatch, settings, keys):
    """Opening a page (GET, HEAD) gets a read-only database transaction, so Postgres itself refuses any write, from any
    code path; a change (POST) gets a normal one. Postgres's refusal is checked on the test copy. The hourly
    channel-name timer is the one writer outside a POST, and it has a connection of its own (db.name_noter): every
    connection a GET opens, whichever page it is, starts read-only."""
    statements = []

    class Conn:
        def execute(self, sql, params=None):
            statements.append(sql)
            return self

        def fetchone(self):
            return None

    class Pool:
        @contextmanager
        def connection(self):
            yield Conn()

    monkeypatch.setattr(db, "_pool", lambda: Pool())
    for method, read_only in [("GET", True), ("HEAD", True), ("POST", False)]:
        statements.clear()
        get_admin_db(Request({"type": "http", "method": method, "headers": []})).staff_for_email("ana@bluejay.example")
        assert (statements[0] == "set transaction read only") is read_only, method
        assert "admin_staff_for_email" in statements[-1]

    # Every page a GET opens, through the app: each connection's first statement is the read-only one.
    connections = []

    class PageConn:
        def __init__(self):
            self.statements = []
            connections.append(self.statements)

        def execute(self, sql, params=None):
            self.statements.append(sql)
            self.last = sql
            return self

        def fetchone(self):
            if "admin_note_channel_names" in self.last:
                return {"changed": 0}
            return {"id": 1, "name": "Ana Reyes", "slack_user_id": "U0ANA"} if "admin_staff_for_email" in self.last else None

        def fetchall(self):
            return []

    class PagePool:
        @contextmanager
        def connection(self):
            yield PageConn()

    monkeypatch.setattr(db, "_pool", lambda: PagePool())
    # The note function is a write, so on a GET's connection it too starts read-only and Postgres refuses it there.
    get_admin_db(Request({"type": "http", "method": "GET", "headers": []})).note_channel_names({"C0X": "x"})
    assert connections[0][0] == "set transaction read only" and "admin_note_channel_names" in connections[0][-1]
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_channel_lister] = lambda: lambda: []
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    pages = ("/clients", "/clients?view=list", "/clients/new", "/clients/nobody", "/history", "/staff", "/staff/new",
             "/staff/1")
    for address in pages:
        connections.clear()
        TestClient(app, headers=signed_in).get(address)
        assert connections, address
        assert all(opened[0] == "set transaction read only" for opened in connections), address


def test_only_the_db_module_opens_connections():
    """The read-only rule lives in get_admin_db; a page that opened its own connection would skip it."""
    admin = Path(db.__file__).parent
    for path in admin.rglob("*.py"):
        if path.name != "db.py":
            code = path.read_text(encoding="utf-8")
            assert not re.search(r"_pool\b|ConnectionPool|psycopg\.connect|PostgresAdminDb", code), path.name
