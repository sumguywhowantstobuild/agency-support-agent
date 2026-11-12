"""Shared by the admin app's tests: the test copy's settings and key file, away from the real .env."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from support_agent.admin.settings import AdminSettings

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from mint_test_pass import make_keys  # noqa: E402

AUDIENCE = "aud-tag-for-the-test-copy"
ORIGIN = "http://127.0.0.1:8081"
# What a browser sends on a request one of the app's own pages made, the redirect after its form post included. A message
# an address asks for (?done=, ?added=, ?removed=) is drawn only for such a request (auth.made_by_this_app).
FROM_HERE = {"Sec-Fetch-Site": "same-origin"}


CLOCK = datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc)  # 1:30pm in New York: 14 points on the chart


@pytest.fixture
def fixed_clock(monkeypatch):
    """The moment the Clients page is made at, so the chart's hours don't depend on when the test runs. (The fake
    database stamps its own changes with the real time: a test that reads those leaves this out.)"""
    from support_agent.admin import clients

    monkeypatch.setattr(clients, "_now", lambda: CLOCK)
    return CLOCK


@pytest.fixture
def keys(tmp_path):
    private_key, kid, jwks = make_keys()
    path = tmp_path / "test-keys.json"
    path.write_text(jwks)
    return private_key, kid, path


@pytest.fixture
def settings(monkeypatch, tmp_path, keys) -> AdminSettings:
    monkeypatch.chdir(tmp_path)  # .env is looked up in the current folder; tmp_path has none
    return AdminSettings(
        admin_database_url="postgresql://admin_app:x@127.0.0.1:5433/knowledge",
        ghl_key_public="AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
        cf_team_domain="test.invalid", cf_audience=AUDIENCE, cf_certs_url="unused", admin_origin=ORIGIN,
        agent_url="http://agent:8000", agent_api_key="k", slack_bot_token="slack-test-bot-token",
        admin_test_keys=keys[2],
    )
