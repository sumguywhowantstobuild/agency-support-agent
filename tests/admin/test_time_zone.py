"""One time zone for every time the admin app shows (Bluejay's, a setting): the words use it, the machine time stays
UTC so it can be compared and sorted. The History page and the client sheet both read it from the settings."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.client_sheet import _when
from support_agent.admin.db import HistoryRow, get_admin_db
from support_agent.admin.settings import get_admin_settings
from conftest import AUDIENCE, ORIGIN
from fakes import FakeAdminDb, a_change, a_client, an_entry
from mint_test_pass import sign_pass

NEW_YORK = ZoneInfo("America/New_York")


def a_row(moment: datetime) -> HistoryRow:
    return HistoryRow(changed_at=moment, staff_name="Ana Reyes", action="added", detail=None, reason=None, note=None)


def test_when_uses_the_time_zone():
    row = a_row(datetime(2026, 11, 3, 19, 5, tzinfo=timezone.utc))

    assert _when(row, NEW_YORK) == ("2026-11-03T19:05:00+00:00", "Nov 3, 14:05 EST")
    assert _when(row, ZoneInfo("Europe/Paris")) == ("2026-11-03T19:05:00+00:00", "Nov 3, 20:05 CET")


def test_when_names_the_zone_it_used_on_each_side_of_a_clock_change():
    """The same wall-clock hour happens twice on 1 Nov 2026 in New York; the short name tells them apart."""
    first = a_row(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
    second = a_row(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))

    assert _when(first, NEW_YORK)[1] == "Nov 1, 01:30 EDT"
    assert _when(second, NEW_YORK)[1] == "Nov 1, 01:30 EST"


def test_when_puts_a_late_utc_change_on_the_local_day():
    """A change at 03:30 UTC on 4 Nov is still the evening of 3 Nov in New York: the day shown is the local one."""
    row = a_row(datetime(2026, 11, 4, 3, 30, tzinfo=timezone.utc))

    assert _when(row, NEW_YORK)[1] == "Nov 3, 22:30 EST"


def test_when_accepts_a_time_that_is_not_in_utc():
    row = a_row(datetime.fromisoformat("2026-11-03T14:05:00-05:00"))

    assert _when(row, NEW_YORK) == ("2026-11-03T19:05:00+00:00", "Nov 3, 14:05 EST")


@pytest.fixture
def paris(settings, keys) -> TestClient:
    """The admin app with the zone set to Paris, and a client with one change and a History with one."""
    db = FakeAdminDb(a_client("cedarline", "Cedarline Roofing"),
                     history=(a_change("cedarline", "added", "2026-10-28T08:00"),),
                     entries=(an_entry("client", "Cedarline Roofing", "added", "2026-10-28T08:00",
                                       client_key="cedarline"),))
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings.model_copy(update={"time_zone": "Europe/Paris"})
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    return TestClient(app, headers={"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example",
                                                                        AUDIENCE), "Origin": ORIGIN})


def test_history_and_the_client_sheet_read_the_zone_from_the_settings(paris):
    history = paris.get("/history")
    sheet = paris.get("/clients/cedarline")

    assert history.status_code == 200 and sheet.status_code == 200
    # Paris was on winter time again on 28 Oct (the clocks went back on 25 Oct).
    assert re.search(r'<h3 class="ds-card__history-day">Oct 28 · Paris time</h3>.*?<time [^>]*>09:00</time>', history.text, re.S)
    assert re.search(r'<time datetime="2026-10-28T08:00:00\+00:00">Oct 28, 09:00 CET</time>', sheet.text)
