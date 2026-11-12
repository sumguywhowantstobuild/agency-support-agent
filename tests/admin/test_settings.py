"""The admin app's settings: test keys and a real Cloudflare team can never be set together."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from support_agent.admin.app import create_app
from support_agent.admin.settings import get_admin_settings

LIVE_ENV = {
    "ADMIN_DATABASE_URL": "postgresql://admin_app:db-pass@127.0.0.1:5433/knowledge",
    "GHL_KEY_PUBLIC": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",  # 32 bytes of 0x01, base64: a test key only
    "CF_TEAM_DOMAIN": "example-team.cloudflareaccess.com",
    "CF_AUDIENCE": "aud-tag-for-the-admin-app",
    "CF_CERTS_URL": "https://example-team.cloudflareaccess.com/cdn-cgi/access/certs",
    "ADMIN_ORIGIN": "https://admin.example.com",
    "AGENT_URL": "http://agent:8000",
    "AGENT_API_KEY": "agent-test-key",
    "SLACK_BOT_TOKEN": "slack-test-bot-token",
}


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Keep tests away from the real .env file and from cached settings."""
    monkeypatch.chdir(tmp_path)  # .env is looked up in the current folder; tmp_path has none
    for name in [*LIVE_ENV, "ADMIN_TEST_KEYS", "TIME_ZONE"]:
        monkeypatch.delenv(name, raising=False)
    get_admin_settings.cache_clear()
    yield
    get_admin_settings.cache_clear()


def set_env(monkeypatch, **changes):
    for name, value in {**LIVE_ENV, **changes}.items():
        monkeypatch.setenv(name, value)


def test_test_keys_with_a_real_team_domain_are_refused(monkeypatch, tmp_path):
    """A test key file next to a real team would let anyone with the file's private half sign in to the live app."""
    keys = tmp_path / "test-keys.json"
    keys.write_text('{"keys": []}')
    set_env(monkeypatch, ADMIN_TEST_KEYS=str(keys))
    with pytest.raises(ValidationError) as error:
        get_admin_settings()
    assert "test.invalid" in str(error.value)


@pytest.mark.parametrize("certs_url", [
    "https://evil.example.com/cdn-cgi/access/certs",  # someone else's keys would sign passes we accept
    "http://example-team.cloudflareaccess.com/cdn-cgi/access/certs",  # plain http: the keys could be swapped on the way
    "https://example-team.cloudflareaccess.com/cdn-cgi/access/certs/extra",
])
def test_certs_url_must_be_the_team_domains(monkeypatch, certs_url):
    set_env(monkeypatch, CF_CERTS_URL=certs_url)
    with pytest.raises(ValidationError) as error:
        get_admin_settings()
    assert "CF_CERTS_URL" in str(error.value)


def test_test_copy_pair_is_accepted(monkeypatch, tmp_path):
    """The test copy has no Cloudflare team: its passes are checked against the key file, and the certs URL is unused."""
    keys = tmp_path / "test-keys.json"
    keys.write_text('{"keys": []}')
    set_env(monkeypatch, ADMIN_TEST_KEYS=str(keys), CF_TEAM_DOMAIN="test.invalid", CF_CERTS_URL="unused",
            ADMIN_ORIGIN="http://127.0.0.1:8081")
    settings = get_admin_settings()
    assert settings.admin_test_keys == keys
    assert settings.issuer == "https://test.invalid"


@pytest.mark.parametrize("domain", ["evil.example.com", "example-team.cloudflareaccess.com.evil.com", "Bluejay/x"])
def test_team_domain_must_be_a_cloudflare_access_team(monkeypatch, domain):
    """Matching certs URL and team domain isn't enough: both could name someone else's server."""
    set_env(monkeypatch, CF_TEAM_DOMAIN=domain, CF_CERTS_URL=f"https://{domain}/cdn-cgi/access/certs")
    with pytest.raises(ValidationError) as error:
        get_admin_settings()
    assert "cf_team_domain" in str(error.value)


@pytest.mark.parametrize("origin", ["http://admin.example.com", "https://admin.example.com/", "admin.example.com"])
def test_live_origin_is_an_https_origin(monkeypatch, origin):
    """Browsers send Origin as scheme://host with no slash; anything else would refuse every change, or allow http."""
    set_env(monkeypatch, ADMIN_ORIGIN=origin)
    with pytest.raises(ValidationError) as error:
        get_admin_settings()
    assert "ADMIN_ORIGIN" in str(error.value)


def test_the_app_refuses_to_start_with_bad_settings(monkeypatch, tmp_path):
    """Checked when the server starts, not at the first page: a bad setup must never report healthy to Docker."""
    keys = tmp_path / "test-keys.json"
    keys.write_text('{"keys": []}')
    set_env(monkeypatch, ADMIN_TEST_KEYS=str(keys))  # test keys next to a real team
    with pytest.raises(ValidationError):
        with TestClient(create_app()):
            pass


def test_the_time_zone_is_bluejays_unless_set(monkeypatch):
    """Today's chart and every time shown start from one zone: the agency's, New York until it is set."""
    set_env(monkeypatch)
    assert get_admin_settings().time_zone == "America/New_York"
    get_admin_settings.cache_clear()
    set_env(monkeypatch, TIME_ZONE="Europe/Paris")
    assert get_admin_settings().time_zone == "Europe/Paris"
    assert str(get_admin_settings().zone) == "Europe/Paris"


@pytest.mark.parametrize("bad", ["Mars/Base", "", "America", "../etc/passwd", "/etc/localtime", "EST5EDT\n"])
def test_a_bad_time_zone_stops_the_start(monkeypatch, bad):
    """A typo would otherwise show as wrong times, or as an error page on every History visit, not at the start."""
    set_env(monkeypatch, TIME_ZONE=bad)
    with pytest.raises(ValidationError) as error:
        get_admin_settings()
    assert "time_zone" in str(error.value)


@pytest.mark.parametrize("bad", ["not base64!", "c2hvcnQ="])  # junk, 5 bytes
def test_a_malformed_ghl_key_public_stops_the_start(monkeypatch, bad):
    """Found at start, not when someone saves a client's key."""
    set_env(monkeypatch, GHL_KEY_PUBLIC=bad)
    with pytest.raises(ValidationError) as error:
        get_admin_settings()
    assert "ghl_key_public" in str(error.value) and bad not in str(error.value)
