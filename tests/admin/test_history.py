"""The History screen: every change made in the admin app or with the Slack limit button, newest first, filtered by
kind, client (by key, migration 020) and person. The database is a fake; db/checks/018 and 020 cover admin_history."""

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from support_agent.admin import history
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from conftest import AUDIENCE, CLOCK
from fakes import ANA_MEMBER, FakeAdminDb, ScriptedConn, a_client, a_member, an_entry, postgres_db_on
from mint_test_pass import sign_pass

ENTRIES = (
    an_entry("client", "Cedarline Roofing", "added", "2026-10-20T09:00", client_key="cedarline"),
    an_entry("client", "Cedarline Roofing", "paused", "2026-10-21T10:30", who="Ben Cruz", client_key="cedarline",
             detail="key_broken: new key coming", reason="key_broken", note="new key coming"),
    an_entry("client", "Cedarline Roofing", "resumed", "2026-10-22T08:15", client_key="cedarline"),
    an_entry("limit", "Cedarline Roofing", "limit_changed", "2026-10-23T14:05", client_key="cedarline",
             detail="20 to 30"),
    an_entry("limit", "BrightSmile Dental", "limit_changed", "2026-10-23T15:00", who="U0NOTSTAFF",
             client_key="brightsmile", detail="10 to 12"),
    an_entry("client", "Cedarline Roofing", "key_replaced", "2026-10-24T16:45", client_key="cedarline"),
    an_entry("client", "Gone Plumbing", "removed", "2026-10-25T11:00", client_key="gone"),
    an_entry("staff", "Ben Cruz", "added", "2026-10-26T09:00"),
    an_entry("staff", "Ben Cruz", "edited", "2026-10-26T09:05"),
    an_entry("staff", "Ben Cruz", "deactivated", "2026-10-27T17:00"),
    an_entry("staff", "Ben Cruz", "reactivated", "2026-10-28T08:00"),
)


@pytest.fixture
def db():
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing"), a_client("brightsmile", "BrightSmile Dental"),
                       a_client("gone", "Gone Plumbing", status="removed"),
                       staff=(ANA_MEMBER, a_member(2, "Ben Cruz", "ben@bluejay.example", "U0BEN", active=False)),
                       entries=ENTRIES)


@pytest.fixture
def browser(settings, keys, db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    return TestClient(app, headers=signed_in)


def plain(markup: str) -> str:
    """Markup as words: tags and spacing removed."""
    text = re.sub(r"<[^>]+>", " ", markup)
    return " ".join(text.replace("&#39;", "'").replace("&#34;", '"').split())


def groups(response) -> list[tuple[str, list[tuple[str, str]]]]:
    """The changes as the page groups them, in page order: (the day's heading, [(time, words), ...])."""
    found = re.findall(r'<h3 class="ds-card__history-day">(.*?)</h3>\s*<ol class="ds-card__history">(.*?)</ol>',
                       response.text, re.S)
    return [(plain(heading).split(" · ")[0], [(plain(clock), plain(words)) for clock, words in re.findall(
        r'<li class="ds-card__history-item"><time class="ds-card__history-time" datetime="[^"]*">(.*?)</time>'
        r"<span>(.*?)</span></li>", items, re.S)]) for heading, items in found]


def said(response) -> list[str]:
    """Each change as the page says it, in page order, with no time."""
    return [words for _, items in groups(response) for _, words in items]


@pytest.fixture
def now(monkeypatch) -> list:
    """The moment History is made at (1:30pm on 4 Nov in New York), kept in a list so a test can move it."""
    moment = [CLOCK]
    monkeypatch.setattr(history, "_now", lambda: moment[0])
    return moment


def test_history_says_every_change_newest_first(browser, now):
    response = browser.get("/history")

    assert response.status_code == 200
    assert groups(response) == [
        # The times are Bluejay's (New York, still on summer time until 1 Nov), four hours behind the stored UTC ones.
        ("Oct 28", [("04:00", "Ana Reyes reactivated Ben Cruz")]),
        ("Oct 27", [("13:00", "Ana Reyes deactivated Ben Cruz")]),
        ("Oct 26", [("05:05", "Ana Reyes edited Ben Cruz's details"),
                    ("05:00", "Ana Reyes added Ben Cruz to the staff list")]),
        ("Oct 25", [("07:00", "Ana Reyes removed Gone Plumbing")]),
        ("Oct 24", [("12:45", "Ana Reyes replaced Cedarline Roofing's HighLevel token")]),
        ("Oct 23", [("11:00", "U0NOTSTAFF changed BrightSmile Dental's limit from 10 to 12 drafts an hour"),
                    ("10:05", "Ana Reyes changed Cedarline Roofing's limit from 20 to 30 drafts an hour")]),
        ("Oct 22", [("04:15", "Ana Reyes resumed Cedarline Roofing")]),
        ("Oct 21", [("06:30", "Ben Cruz paused Cedarline Roofing: Key broken, “new key coming”")]),
        ("Oct 20", [("05:00", "Ana Reyes added Cedarline Roofing")]),
    ]


def test_history_filters_by_kind_client_and_person(browser, db):
    limits = said(browser.get("/history", params={"kind": "limit"}))
    assert limits == [
        "U0NOTSTAFF changed BrightSmile Dental's limit from 10 to 12 drafts an hour",
        "Ana Reyes changed Cedarline Roofing's limit from 20 to 30 drafts an hour"]

    cedarline = said(browser.get("/history", params={"client": "cedarline"}))  # its limit changes too
    assert len(cedarline) == 5 and all("Cedarline Roofing" in line for line in cedarline)

    page = browser.get("/history", params={"kind": "client", "client": "cedarline", "person": "Ben Cruz"})
    assert said(page) == ["Ben Cruz paused Cedarline Roofing: Key broken, “new key coming”"]
    assert db.reads[-1] == ("changes", "client", "cedarline", "Ben Cruz", 0, 101)
    # The pickers show what was picked; a removed client and a deactivated member can still be picked.
    assert '<option value="client" selected>' in page.text
    assert '<option value="cedarline" selected>Cedarline Roofing</option>' in page.text
    assert '<option value="gone">Gone Plumbing (removed)</option>' in page.text
    assert '<option value="Ben Cruz" selected>Ben Cruz</option>' in page.text


def test_history_ignores_a_filter_it_doesnt_know(browser, db):
    page = browser.get("/history", params={"kind": "everything", "client": "nobody", "person": "Mallory"})

    assert page.status_code == 200 and len(said(page)) == len(ENTRIES)
    assert db.reads[-1] == ("changes", None, None, None, 0, 101)


def test_history_pages_by_100_keeping_the_filters(browser, db):
    db.entries = [an_entry("limit", "Cedarline Roofing", "limit_changed", f"2026-10-{1 + n // 60:02}T{n // 60 % 24:02}:"
                           f"{n % 60:02}", client_key="cedarline", detail=f"{n} to {n + 1}") for n in range(205)]

    first = browser.get("/history", params={"client": "cedarline"})
    assert len(said(first)) == 100
    assert 'href="/history?client=cedarline&amp;page=2&amp;asof=' in first.text and "page=0" not in first.text

    second = browser.get("/history", params={"client": "cedarline", "page": "2"})
    assert db.reads[-1] == ("changes", None, "cedarline", None, 100, 101)
    assert 'href="/history?client=cedarline&amp;page=3&amp;asof=' in second.text
    assert 'href="/history?client=cedarline"' in second.text  # newer: back to the first page

    last = browser.get("/history", params={"client": "cedarline", "page": "3"})
    assert len(said(last)) == 5 and "page=4" not in last.text

    for wrong in ("0", "-1", "two", "99999999999999999999"):
        assert browser.get("/history", params={"page": wrong}).status_code == 200
        assert db.reads[-1][4] == 0  # a page number that isn't one is the first page


def test_postgres_changes_filters_in_the_query(monkeypatch):
    conn = ScriptedConn(None)
    row = {"changed_at": ENTRIES[1].changed_at, "staff_name": "Ben Cruz", "kind": "client",
           "subject": "Cedarline Roofing", "action": "paused", "detail": "key_broken: new key coming",
           "client_key": "cedarline", "reason": "key_broken", "note": "new key coming"}
    conn.fetchall = lambda: [row]

    as_of = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
    found = postgres_db_on(conn, monkeypatch).changes("client", "cedarline", "Ben Cruz", as_of, 100, 101)

    assert found == [ENTRIES[1]]
    sql, params = conn.statements[0]
    assert " ".join(sql.split()) == (
        "select changed_at, staff_name, kind, subject, action, detail, client_key, reason, note from admin_history "
        "where changed_at <= %(as_of)s "
        "and (%(kind)s::text is null or kind = %(kind)s) and (%(client_key)s::text is null or client_key = "
        "%(client_key)s) and (%(person)s::text is null or staff_name = %(person)s) "
        # The view has no unique id: every column after the time, so changes made at the same moment keep one order
        # from page to page (rows equal in every column read the same, whichever comes first).
        "order by changed_at desc, kind, client_key, subject, action, staff_name, detail, reason, note "
        "limit %(limit)s offset %(offset)s")
    assert params == {"kind": "client", "client_key": "cedarline", "person": "Ben Cruz", "as_of": as_of,
                      "offset": 100, "limit": 101}

    people = ScriptedConn(None)
    people.fetchall = lambda: [{"staff_name": "Ana Reyes"}, {"staff_name": "U0NOTSTAFF"}]
    assert postgres_db_on(people, monkeypatch).history_people() == ["Ana Reyes", "U0NOTSTAFF"]
    assert people.statements == [("select distinct staff_name from admin_history", None)]


def test_database_down_is_a_plain_503(browser, db, caplog):
    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.changes = down
    caplog.set_level(logging.INFO)  # the refusal is logged at info
    response = browser.get("/history")

    assert response.status_code == 503
    assert "history unreachable: OperationalError" in caplog.text  # the type only
    assert "admin_app" not in response.text and "admin_app" not in caplog.text


def test_history_can_pick_someone_not_on_the_staff_list(browser, db):
    # A Slack limit change by someone not on the staff list is named by their Slack ID (admin_history).
    page = browser.get("/history", params={"person": "U0NOTSTAFF"})

    assert said(page) == ["U0NOTSTAFF changed BrightSmile Dental's limit from 10 to 12 drafts an hour"]
    assert '<option value="U0NOTSTAFF" selected>U0NOTSTAFF</option>' in page.text
    assert db.reads[-1] == ("changes", None, None, "U0NOTSTAFF", 0, 101)


def test_the_link_from_a_staff_sheet_works_for_someone_with_no_changes(browser, db):
    db.staff.append(a_member(3, "Cara Lim", "cara@bluejay.example", "U0CARA", active=False))

    page = browser.get("/history", params={"person": "Cara Lim"})  # the staff sheet's "Changes made by" link

    assert said(page) == [] and "No changes match." in page.text
    assert db.reads[-1] == ("changes", None, None, "Cara Lim", 0, 101)


def test_older_pages_stay_as_of_the_first_page_while_changes_arrive(browser, db):
    db.entries = [an_entry("limit", "Cedarline Roofing", "limit_changed", f"2026-10-{1 + n // 60:02}T{n // 60 % 24:02}:"
                           f"{n % 60:02}", client_key="cedarline", detail=f"{n} to {n + 1}") for n in range(150)]
    first = browser.get("/history")
    older = re.search(r'href="(/history\?[^"]*page=2[^"]*)"', first.text).group(1).replace("&amp;", "&")
    assert "asof=" in older

    # Someone changes a limit while the first page is open: page 2 still starts right after page 1's last change.
    # Dated just after the first page's moment, not "now": Windows' clock moves in steps of about 16 ms, so two quick
    # readings can be equal and the change would count as already on page 1 (this flaked in the full suite).
    changed_at = db.as_of[-1] + timedelta(microseconds=1)
    db.entries.append(an_entry("limit", "Cedarline Roofing", "limit_changed", changed_at.replace(tzinfo=None).isoformat(),
                               client_key="cedarline", detail="300 to 301"))
    second = said(browser.get(older))
    assert second[0].startswith("Ana Reyes changed Cedarline Roofing's limit from 49 to 50 drafts")
    assert len(second) == 50 and not any("300 to 301" in line for line in second)

    while datetime.now(timezone.utc) <= changed_at:  # the clock's next step, so the change is in the past
        time.sleep(0.001)
    assert "300 to 301" in said(browser.get("/history"))[0]  # back on the first page: the new change shows
    newer = re.search(r'href="(/history[^"]*)">Newer changes', browser.get(older).text).group(1)
    assert "asof" not in newer and "page" not in newer


def test_the_first_page_is_always_now(browser, db):
    browser.get("/history", params={"asof": "2026-10-01T00:00:00+00:00"})  # an old link, bookmarked

    assert abs(db.as_of[-1] - datetime.now(timezone.utc)).total_seconds() < 60


def test_a_wrong_asof_is_now(browser, db):
    for wrong in ("yesterday", "2026-10-29T10:00:00", ""):  # the last two: no time zone, nothing
        assert browser.get("/history", params={"page": "2", "asof": wrong}).status_code == 200
        assert abs(db.as_of[-1] - datetime.now(timezone.utc)).total_seconds() < 60


def added(subject: str, when: str):
    return an_entry("client", subject, "added", when, client_key=subject.lower())


def test_the_first_day_says_which_zone_the_times_are_in(browser, db, now):
    """No line above the title names the zone, so each row's clock needs its zone said once."""
    db.entries = [added("A", "2026-11-04T05:00"), added("B", "2026-11-02T05:00")]
    page = browser.get("/history").text
    headings = [plain(h) for h in re.findall(r'<h3 class="ds-card__history-day">(.*?)</h3>', page, re.S)]
    assert len(headings) == 2
    assert headings[0].endswith(" · New York time")
    assert "time" not in headings[1]


def test_changes_group_by_day_in_the_zone(browser, db, now):
    # Every time is UTC here; New York is five hours behind from 1 Nov (EST) and was four behind before (EDT).
    db.entries = [
        added("A", "2026-11-04T05:00"),  # 00:00 on 4 Nov in New York: the first moment of today (UTC says the same day)
        added("B", "2026-11-04T04:59"),  # 23:59 on 3 Nov: yesterday, though UTC already says 4 Nov
        added("C", "2026-11-02T05:00"),  # 00:00 on 2 Nov
        # 1 Nov has 25 hours: the clocks went back at 02:00, so 01:30 happens twice and the times say which.
        added("D", "2026-11-02T04:59"),  # 23:59 EST, the last moment of 1 Nov
        added("E", "2026-11-01T06:30"),  # 01:30 EST, after the change
        added("F", "2026-11-01T05:30"),  # 01:30 EDT, before it
        added("G", "2026-11-01T04:00"),  # 00:00 EDT, the first moment of 1 Nov
        added("H", "2026-11-01T03:59"),  # 23:59 EDT on 31 Oct
        added("I", "2025-12-03T15:00"),  # another year says so
    ]

    assert groups(browser.get("/history")) == [
        ("Today", [("00:00", "Ana Reyes added A")]),
        ("Yesterday", [("23:59", "Ana Reyes added B")]),
        ("Nov 2", [("00:00", "Ana Reyes added C")]),
        ("Nov 1", [("23:59 EST", "Ana Reyes added D"), ("01:30 EST", "Ana Reyes added E"),
                   ("01:30 EDT", "Ana Reyes added F"), ("00:00 EDT", "Ana Reyes added G")]),
        ("Oct 31", [("23:59", "Ana Reyes added H")]),
        ("Dec 3, 2025", [("10:00", "Ana Reyes added I")]),
    ]

    # Later that evening (10:30pm on 4 Nov in New York, already the 5th in UTC) a change at 6:30pm is still today.
    now[0] = datetime(2026, 11, 5, 3, 30, tzinfo=timezone.utc)
    db.entries = [added("J", "2026-11-05T01:00"), added("K", "2026-11-04T23:30"), added("L", "2026-11-04T04:59")]
    assert groups(browser.get("/history")) == [
        ("Today", [("20:00", "Ana Reyes added J"), ("18:30", "Ana Reyes added K")]),
        ("Yesterday", [("23:59", "Ana Reyes added L")]),
    ]


def test_zone_is_named(browser, settings):
    page = browser.get("/history")
    assert page.text.count("New York time") == 1  # once on the page, not on each change

    browser.app.dependency_overrides[get_admin_settings] = lambda: settings.model_copy(
        update={"time_zone": "Europe/Paris"})
    page = browser.get("/history")
    assert page.text.count("Paris time") == 1 and "New York" not in page.text


def test_older_and_newer_keep_the_day_groups_right(browser, db, now):
    # 130 changes on 3 Nov (yesterday) and 20 on 2 Nov, in New York. A page ends after 100, so 3 Nov spans both pages.
    started = datetime(2026, 11, 3, 15, 0)  # 10:00 in New York
    db.entries = [added(f"E{n}", (started + timedelta(minutes=n)).isoformat()) for n in range(130)]
    db.entries += [added(f"F{n}", f"2026-11-02T15:{n:02}") for n in range(20)]

    first = browser.get("/history")
    older = re.search(r'href="(/history\?[^"]*page=2[^"]*)"', first.text).group(1).replace("&amp;", "&")
    second = browser.get(older)
    one, two = groups(first), groups(second)

    assert [(day, len(items)) for day, items in one] == [("Yesterday", 100)]
    assert [(day, len(items)) for day, items in two] == [("Yesterday", 30), ("Nov 2", 20)]  # the heading comes again
    # Nothing is lost and nothing shows twice, and the second page carries on from where the first stopped.
    everything = [words for _, items in one + two for _, words in items]
    assert len(everything) == len(set(everything)) == 150
    assert one[0][1][-1][1] == "Ana Reyes added E30" and two[0][1][0][1] == "Ana Reyes added E29"
    newer = re.search(r'href="(/history[^"]*)">Newer changes', second.text).group(1).replace("&amp;", "&")
    assert groups(browser.get(newer)) == one

    # A change made while page 1 is open doesn't move page 2, and the first page, opened again, shows it under Today.
    db.entries.append(added("NEW", "2026-11-04T18:45"))
    assert groups(browser.get(older)) == two
    now[0] = CLOCK + timedelta(hours=1)
    assert [(day, len(items)) for day, items in groups(browser.get("/history"))] == [("Today", 1), ("Yesterday", 99)]


def test_a_channel_change_reads_in_history(browser, db, now):
    """Migration 024's channel_changed: who moved which client, from which channel to which, in words. A channel's name
    is text from Slack, so it is shown as text and never as markup."""
    db.entries.append(an_entry("client", "Cedarline Roofing", "channel_changed", "2026-10-29T10:00",
                               client_key="cedarline", detail="#old-cedar to #new-cedar"))
    db.entries.append(an_entry("client", "BrightSmile Dental", "channel_changed", "2026-10-29T11:00",
                               client_key="brightsmile", detail="C0OLD to #<b>bold</b>"))
    response = browser.get("/history")

    assert said(response)[:2] == [
        "Ana Reyes moved BrightSmile Dental to another Slack channel: C0OLD to #&lt;b&gt;bold&lt;/b&gt;",
        "Ana Reyes moved Cedarline Roofing to another Slack channel: #old-cedar to #new-cedar"]
    assert "<b>bold</b>" not in response.text
    only = said(browser.get("/history", params={"client": "cedarline"}))
    assert only[0] == "Ana Reyes moved Cedarline Roofing to another Slack channel: #old-cedar to #new-cedar"
