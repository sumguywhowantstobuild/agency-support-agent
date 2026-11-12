"""The Staff screen: the staff list (who may sign in here and approve drafts in Slack), add, edit, deactivate and
reactivate through the admin_*_staff functions. The database is a fake; db/checks/017 covers the real one."""

import logging
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import LastMember, StaffGone, StaffTaken, get_admin_db
from support_agent.admin.settings import get_admin_settings
from conftest import AUDIENCE, FROM_HERE, ORIGIN
from fakes import ADDED, ANA_MEMBER, FakeAdminDb, ScriptedConn, a_change, a_client, a_member, an_entry, postgres_db_on
from mint_test_pass import sign_pass
from panels import heading_levels, ids, is_open, no_gap, panel_tag

BEN = a_member(2, "Ben Cruz", "ben@bluejay.example", "U0BEN")


@pytest.fixture
def db():
    return FakeAdminDb(staff=(ANA_MEMBER, BEN))


@pytest.fixture
def browser(settings, keys, db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE),
                 "Origin": ORIGIN}
    return TestClient(app, headers=signed_in, follow_redirects=False)


def words(response) -> str:
    return response.text.replace("&#39;", "'").replace("&#34;", '"')


def test_add_staff(browser, db):
    form = browser.get("/staff/new")
    assert form.status_code == 200
    assert "During the practice build, also add their email to the Cloudflare Access list." in words(form)

    added = browser.post("/staff/new", data={"name": "  Cara Lim ", "email": " Cara@Bluejay.example",
                                              "slack_user_id": " u0cara "})
    assert added.status_code == 303
    assert added.headers["location"] == "/staff?added=3"
    assert db.calls == [("add_staff", 1, "Cara Lim", "cara@bluejay.example", "U0CARA")]  # by Ana, tidied

    listed = words(browser.get(added.headers["location"], headers=FROM_HERE))
    assert "Added Cara Lim. They can sign in and approve drafts now." in listed
    assert "cara@bluejay.example" in listed and "U0CARA" in listed


@pytest.mark.parametrize("added", ["99", "<b>Mallory</b>", ""])
def test_the_added_toast_names_only_a_saved_member(browser, added):
    listed = browser.get("/staff", params={"added": added}, headers=FROM_HERE)

    assert listed.status_code == 200
    assert "data-toast" not in listed.text and "Mallory" not in listed.text


def test_the_list_shows_active_members_first_then_by_name(browser, db):
    db.staff += [a_member(3, "aaron Diaz", "aaron@bluejay.example", "U0AARON", active=False),
                 a_member(4, "Zoe Park", "zoe@bluejay.example", "U0ZOE")]

    names = re.findall(r'class="ds-table__name[^"]*" href="/staff/\d+">([^<]+)<', browser.get("/staff").text)
    assert names == ["Ana Reyes", "Ben Cruz", "Zoe Park", "aaron Diaz"]


GOOD = {"name": "Cara Lim", "email": "cara@bluejay.example", "slack_user_id": "U0CARA"}
SLACK_ID = "A Slack member ID starts with U or W, then capital letters and digits, like U04ABC123."


@pytest.mark.parametrize(("field", "typed", "message"), [
    ("slack_user_id", "@cara", SLACK_ID),
    ("slack_user_id", "C0CHANNEL", SLACK_ID),  # a channel's ID, pasted by mistake
    ("email", "cara at bluejay", "Type their work email, like ana@bluejay.example."),
    ("name", "   ", "Type their name, up to 80 characters."),
    ("name", "n" * 81, "Type their name, up to 80 characters."),
])
def test_bad_slack_id_message(browser, db, field, typed, message):
    response = browser.post("/staff/new", data=GOOD | {field: typed})

    assert response.status_code == 422
    assert message in words(response)
    assert f'value="{typed.strip().upper() if field == "slack_user_id" else typed.strip()}"' in response.text
    assert db.calls == []


@pytest.mark.parametrize(("field", "typed", "message"), [
    ("email", "BEN@bluejay.example", "Ben Cruz already has that email. Nothing was saved."),
    ("slack_user_id", "u0ben", "Ben Cruz already has that Slack member ID. Nothing was saved."),
])
def test_an_email_or_slack_id_already_on_the_list(browser, db, field, typed, message):
    response = browser.post("/staff/new", data=GOOD | {field: typed})

    assert response.status_code == 409
    assert message in words(response)
    assert len(db.staff) == 2


def test_deactivate_and_reactivate(browser, db):
    page = words(browser.get("/staff/2"))
    sheet = page[page.index("<dialog"):page.index("</dialog>")]  # the list behind holds these words too: the panel's own
    assert "Ben Cruz" in sheet and "ben@bluejay.example" in sheet and "U0BEN" in sheet
    assert 'action="/staff/2/active"' in sheet

    off = browser.post("/staff/2/active", data={"active": "false"})
    assert off.status_code == 303
    assert off.headers["location"] == "/staff/2?done=deactivated"
    assert db.staff[1].active is False
    assert "Deactivated Ben Cruz. They can't sign in or approve drafts from their next click." in words(
        browser.get(off.headers["location"], headers=FROM_HERE))

    on = browser.post("/staff/2/active", data={"active": "true"})
    assert on.headers["location"] == "/staff/2?done=reactivated"
    assert db.calls == [("set_staff_active", 1, 2, False), ("set_staff_active", 1, 2, True)]
    assert "Reactivated Ben Cruz." in words(browser.get(on.headers["location"], headers=FROM_HERE))


@pytest.mark.parametrize("data", [{}, {"active": ""}, {"active": "False"}, {"active": "yes"}])
def test_an_unclear_active_value_changes_nothing(browser, db, data):
    response = browser.post("/staff/2/active", data=data)  # only "true" or "false", as the sheet's form sends

    assert response.status_code == 422
    assert db.calls == [] and db.staff[1].active


def test_cannot_deactivate_yourself(browser, db):
    response = browser.post("/staff/1/active", data={"active": "false"})  # Ana is signed in

    assert response.status_code == 409
    assert "You can't deactivate yourself. Ask another member to do it." in words(response)
    assert db.calls == [] and db.staff[0].active


def test_cannot_deactivate_the_last_member_message(browser, db):
    # Only Ana can be the last active member while she is signed in, and she can't deactivate herself; the database
    # still refuses on its own (admin_set_staff_active), so a refusal that gets there reads the same way.
    def last(by, staff_id, active):
        raise LastMember(staff_id)

    db.set_staff_active = last
    response = browser.post("/staff/2/active", data={"active": "false"})

    assert response.status_code == 409
    assert "Ben Cruz is the last active member, so nobody could sign in. Add someone else first." in words(response)


def test_edit_staff(browser, db):
    saved = browser.post("/staff/2/edit", data={"name": " Benjamin Cruz", "email": "Benjamin@Bluejay.example",
                                                "slack_user_id": "u0ben"})
    assert saved.status_code == 303
    assert saved.headers["location"] == "/staff/2?done=saved"
    assert db.calls == [("edit_staff", 1, 2, "Benjamin Cruz", "benjamin@bluejay.example", "U0BEN")]
    assert "Saved Benjamin Cruz." in words(browser.get(saved.headers["location"], headers=FROM_HERE))


def test_edit_staff_refusals_keep_what_was_typed(browser, db):
    bad = browser.post("/staff/2/edit", data={"name": "Ben Cruz", "email": "ben@bluejay.example",
                                              "slack_user_id": "ben"})
    assert bad.status_code == 422
    assert SLACK_ID in words(bad) and 'value="BEN"' in bad.text

    taken = browser.post("/staff/2/edit", data={"name": "Ben Cruz", "email": "ana@bluejay.example",
                                                "slack_user_id": "U0BEN"})
    assert taken.status_code == 409
    assert "Ana Reyes already has that email. Nothing was saved." in words(taken)
    assert db.staff[1] == BEN


@pytest.mark.parametrize(("method", "path", "data"), [
    ("get", "/staff/99", None),
    ("post", "/staff/99/edit", GOOD),
    ("post", "/staff/99/active", {"active": "false"}),
    ("post", "/staff/99/active", {"active": "true"}),
])
def test_an_unknown_member_is_not_found(browser, db, method, path, data):
    response = getattr(browser, method)(path, **({"data": data} if data else {}))

    assert response.status_code == 404
    assert "Staff member not found" in words(response)


@pytest.mark.parametrize(("change", "path", "data"), [
    ("add_staff", "/staff/new", GOOD),
    ("edit_staff", "/staff/2/edit", GOOD),
    ("set_staff_active", "/staff/2/active", {"active": "false"}),
])
def test_database_down_during_a_change_is_a_plain_503(browser, db, caplog, change, path, data):
    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    setattr(db, change, down)
    caplog.set_level(logging.INFO)  # the refusal is logged at info
    response = browser.post(path, data=data)

    assert response.status_code == 503
    assert "Couldn't reach the database, so the change may not have been made. Open Staff to check." in words(response)
    assert "failed: OperationalError" in caplog.text  # the type only
    assert "admin_app" not in response.text and "admin_app" not in caplog.text


def test_cannot_change_your_own_email(browser, db):
    # The sign-in pass carries the old email, so the next click would refuse the person who saved it.
    refused = browser.post("/staff/1/edit", data={"name": "Ana Reyes", "email": "ana.reyes@bluejay.example",
                                                  "slack_user_id": "U0ANA"})
    assert refused.status_code == 409
    assert "You can't change your own email: you'd be signed out. Ask another member to do it." in words(refused)
    assert db.calls == []

    renamed = browser.post("/staff/1/edit", data={"name": "Ana R. Reyes", "email": "ANA@bluejay.example",
                                                  "slack_user_id": "U0ANA"})
    assert renamed.status_code == 303  # the rest of her own details she may change


@pytest.mark.parametrize("path, title", [("/staff/2", "Ben Cruz"), ("/staff/new", "Add staff")])
def test_a_panel_comes_over_the_list(browser, path, title):
    page = browser.get(path).text

    assert 'id="results"' in page and 'id="staff-count"' in page  # the list stays behind the panel
    assert re.findall(r'class="ds-table__name[^"]*" href="/staff/(\d+)"', page) == ["1", "2"]
    tag = panel_tag(page)
    assert 'class="ds-sheet"' in tag and is_open(tag) and 'id="panel"' in tag
    assert "ds-sheet--static" not in page
    assert page.count('id="toast"') == 1
    assert re.search(r'<h1[^>]*>Staff</h1>', page) and page.count("<h1") == 1
    assert re.search(rf'<h2[^>]*ds-sheet__title[^>]*>\s*{title}\s*</h2>', page)
    assert '<div class="ds-sheet__grabber" aria-hidden="true"></div>' in page
    assert page.index('id="results"') < page.index("<dialog")  # the panel comes after the list
    twice = sorted({name for name in ids(page) if ids(page).count(name) > 1})
    assert twice == []
    assert f"<title>{title} · Bluejay support admin</title>" in page
    levels = heading_levels(page)  # the page's h1, then its h2 and the panel's, with no level skipped
    assert no_gap(levels, 1), f"headings {levels}"


def test_a_closed_panel_waits_on_the_list(browser):
    page = browser.get("/staff").text

    assert re.findall(r"<dialog\b[^>]*>\s*</dialog>", page) == ['<dialog class="ds-sheet" id="panel"></dialog>']
    assert 'id="results"' in page and 'id="staff-count"' in page and 'id="who"' in page
    assert page.count('id="toast"') == 1 and "ds-sheet--static" not in page
    assert re.search(r'<h1[^>]*tabindex="-1"[^>]*>Staff</h1>', page)  # takes focus when there is nothing to go back to


CASES = {  # a form refused with its panel: (method, path, data, status, what the panel says or still holds)
    "add-422": ("/staff/new", GOOD | {"slack_user_id": "@cara"}, 422, ['value="Cara Lim"', SLACK_ID]),
    "add-409": ("/staff/new", GOOD | {"email": "BEN@bluejay.example"}, 409,
                ['value="ben@bluejay.example"', "Ben Cruz already has that email. Nothing was saved."]),
    "edit-422": ("/staff/2/edit", GOOD | {"name": "  "}, 422,
                 ['value="cara@bluejay.example"', "Type their name, up to 80 characters."]),
    "edit-409": ("/staff/2/edit", GOOD | {"email": "ana@bluejay.example"}, 409,
                 ['value="ana@bluejay.example"', "Ana Reyes already has that email. Nothing was saved."]),
    "active-422": ("/staff/2/active", {"active": "yes"}, 422, ["Nothing changed. Use the button below"]),
    "active-409": ("/staff/1/active", {"active": "false"}, 409, ["You can't deactivate yourself."]),
}


@pytest.mark.parametrize("path, data, status, shown", CASES.values(), ids=CASES.keys())
def test_a_refused_form_answers_with_the_panel_alone(browser, path, data, status, shown):
    response = browser.post(path, data=data)
    page = words(response)

    assert response.status_code == status
    assert 'id="results"' not in page and "<table" not in page  # not the list: it needs the database
    assert 'id="staff-count"' not in page
    assert is_open(panel_tag(page)) and 'id="panel"' in panel_tag(page)
    assert page.count('id="toast"') == 1 and page.count('role="status"') == 1  # the toast, so the script starts
    levels = heading_levels(page)
    assert no_gap(levels, 2), f"headings {levels}"
    for text in shown:
        assert text in page


def test_a_refused_form_without_a_member_reads_the_list_first(browser, db):
    """A refusal that needs the member (to draw the panel) reads the staff table first, as before: with the database down
    that is a 503 with no panel, and the script keeps what was typed."""
    def down():
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.staff_list = down
    response = browser.post("/staff/2/edit", data=GOOD | {"name": "  "})

    assert response.status_code == 503
    assert "<dialog" not in response.text and "admin_app" not in response.text


DEE_OFF = a_member(3, "Dee Park", "dee@bluejay.example", "U0DEE", active=False)
ANA_OFF = a_member(1, "Ana Reyes", "ana@bluejay.example", "U0ANA", active=False)


@pytest.mark.parametrize("path, state, undoes", [
    ("/staff/3?done=deactivated", DEE_OFF, ("/staff/3/active", "true")),   # Dee is deactivated: Undo reactivates
    ("/staff/2?done=reactivated", BEN, ("/staff/2/active", "false")),      # Ben is active: Undo deactivates
    ("/staff/2?done=saved", BEN, None),                                    # a save has nothing to undo
    ("/staff/2", BEN, None),                                               # no change at all
    ("/staff/2?done=deactivated", BEN, None),      # says deactivated, but Ben is active: someone reactivated since
    ("/staff/3?done=reactivated", DEE_OFF, None),  # says reactivated, but Dee is deactivated: someone deactivated since
    ("/staff/1?done=reactivated", ANA_MEMBER, None),  # Ana is active, as that change left her; but she is signed in
    ("/staff/1?done=deactivated", ANA_OFF, None),     # as that change left her, too; still never the signed-in person
], ids=["deactivated", "reactivated", "saved", "nothing", "reactivated-since", "deactivated-since", "yourself-on",
        "yourself-off"])
def test_undo_is_offered_only_while_it_fits(browser, db, path, state, undoes):
    db.staff[:] = [member for member in db.staff if member.id != state.id] + [state]  # the member, as the test needs
    response = browser.get(path, headers=FROM_HERE)
    page = words(response)

    # Whatever the answer, it is the member's panel, drawn: a refusal page has no Undo either, and would pass for "none".
    assert response.status_code == 200 and is_open(panel_tag(page)) and 'id="panel"' in panel_tag(page)
    assert ("data-toast=" in page) == ("done=" in path)  # a change was announced, and a visit without one was not
    if undoes is None:
        assert "data-undo=" not in page and 'id="undo"' not in page
        return
    address, value = undoes
    assert 'data-undo="undo"' in page
    undo = page.split('<form id="undo"', 1)[1].split("</form>", 1)[0]
    assert f'method="post" action="{address}" hidden>' in undo
    assert f'<input type="hidden" name="active" value="{value}">' in undo and undo.count("<input") == 1


def unmarked_addresses(page: str) -> list[str]:
    """Addresses in the page's text outside Cloudflare's marker. One in an attribute, as in a field's value, is left alone."""
    unmarked = re.sub(r"<!--email_off-->.*?<!--/email_off-->", "", page, flags=re.S)
    return re.findall(r"\S+@\S+", re.sub(r"<[^>]+>", " ", unmarked))


@pytest.mark.parametrize(("method", "path", "data"), [
    ("get", "/staff", None),
    ("get", "/staff/2", None),
    ("post", "/staff/new", GOOD | {"email": "not an address"}),
    ("post", "/staff/2/edit", GOOD | {"email": "not an address"}),
])
def test_cloudflare_leaves_every_email_on_a_page_alone(browser, method, path, data):
    """On live, Cloudflare swaps an address in a page's text for "[email protected]", and our security header blocks the script
    that would put it back. The copy has no Cloudflare, so only this catches one left unmarked."""
    page = getattr(browser, method)(path, **({"data": data} if data else {}))
    assert page.status_code == (422 if data else 200)
    assert not unmarked_addresses(page.text)
    if path != "/staff/new" or data:  # Add staff's empty form shows none, but its refusal names an example
        assert "<!--email_off-->" in page.text and "@" in page.text


@pytest.mark.parametrize("path", ["/clients", "/clients/cedarline", "/history"])
def test_an_address_typed_in_a_pause_note_is_left_alone_too(browser, db, path):
    note = "billing contact bob@acme.example"
    db.rows.append(a_client("cedarline", "Cedarline Roofing", status="paused", reason="client_asked", note=note))
    db.history.append(a_change("cedarline", "paused", "2026-10-25T11:00", reason="client_asked", note=note))
    db.entries.append(an_entry("client", "Cedarline Roofing", "paused", "2026-10-25T11:00", client_key="cedarline",
                               reason="client_asked", note=note))

    page = browser.get(path)

    assert page.status_code == 200 and note in page.text
    assert not unmarked_addresses(page.text)


# A message an address asks for is drawn only for a request one of the app's own pages made. A link sent by someone, or
# typed, arrives as "cross-site" or "none": "?done=deactivated" for a deactivated member would offer an Undo that gives
# them their access back, to whoever clicks it.
ADDRESSES = [  # (address, does it offer an Undo)
    ("/staff/3?done=deactivated", True), ("/staff/2?done=reactivated", True), ("/staff/2?done=saved", False),
    ("/staff?added=2", False),
]


@pytest.mark.parametrize("path, offers_undo", ADDRESSES)
@pytest.mark.parametrize("site", [None, "none", "cross-site", "same-site", ""],
                         ids=["no header", "typed", "another site", "same site", "empty"])
def test_a_message_in_an_address_needs_a_request_from_this_app(browser, db, path, offers_undo, site):
    db.staff.append(DEE_OFF)
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    response = browser.get(path, headers=headers)
    page = response.text

    assert response.status_code == 200
    if path.startswith("/staff/"):
        assert is_open(panel_tag(page))  # the panel itself still shows
    else:
        assert 'id="results"' in page and "Ben Cruz" in page
    assert "data-toast=" not in page and "data-undo=" not in page and 'id="undo"' not in page


@pytest.mark.parametrize("path, offers_undo", ADDRESSES)
def test_a_message_in_an_address_is_drawn_for_a_request_from_this_app(browser, db, path, offers_undo):
    db.staff.append(DEE_OFF)
    page = browser.get(path, headers=FROM_HERE).text

    assert "data-toast=" in page
    assert ('id="undo"' in page) is offers_undo and ("data-undo=" in page) is offers_undo


def test_postgres_staff_functions_answer_in_the_exceptions_the_pages_use(monkeypatch):
    # The database's own refusals (migration 017), as psycopg raises them: unique columns by constraint name.
    duplicate = 'duplicate key value violates unique constraint "staff_{}_key"'
    for column in ("email", "slack_user_id"):
        conn = ScriptedConn(psycopg.errors.UniqueViolation(duplicate.format(column)))
        with pytest.raises(StaffTaken) as taken:
            postgres_db_on(conn, monkeypatch).add_staff(1, "Cara Lim", "cara@bluejay.example", "U0CARA")
        assert taken.value.args == (column,)
        assert conn.statements == [("select admin_add_staff(%s, %s, %s, %s) as id",
                                    (1, "Cara Lim", "cara@bluejay.example", "U0CARA"))]

    unknown = psycopg.errors.RaiseException("admin_edit_staff: unknown staff member")
    with pytest.raises(StaffGone):
        postgres_db_on(ScriptedConn(unknown), monkeypatch).edit_staff(1, 99, "Cara Lim", "c@b.example", "U0CARA")
    last = psycopg.errors.RaiseException("admin_set_staff_active: the last active member can't be deactivated")
    with pytest.raises(LastMember):
        postgres_db_on(ScriptedConn(last), monkeypatch).set_staff_active(1, 1, False)
    other = psycopg.errors.RaiseException("admin: only an active staff member can make changes")
    with pytest.raises(psycopg.errors.RaiseException):  # anything else stays a database error (503)
        postgres_db_on(ScriptedConn(other), monkeypatch).set_staff_active(1, 2, False)

    assert postgres_db_on(ScriptedConn({"id": 3}), monkeypatch).add_staff(1, "Cara Lim", "c@b.example", "U0C") == 3
    listed = ScriptedConn(None)
    listed.fetchall = lambda: [{"id": 1, "name": "Ana Reyes", "email": "ana@bluejay.example",
                                "slack_user_id": "U0ANA", "active": True, "added_at": ADDED, "extra": "never shown"}]
    assert postgres_db_on(listed, monkeypatch).staff_list() == [ANA_MEMBER]
    assert listed.statements == [
        ("select id, name, email, slack_user_id, active, added_at from admin_staff_list", None)]
