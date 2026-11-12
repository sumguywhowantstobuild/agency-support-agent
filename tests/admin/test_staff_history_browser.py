"""The Staff and History screens in a real browser (Chromium through Playwright): adding and deactivating a member with
the toast, and History's pickers filtering only once Show is pressed. The app runs on 127.0.0.1 with the fake database.
Watch a run: pytest tests/admin/test_staff_history_browser.py --headed --slowmo 500"""

import pytest
from playwright.sync_api import Page, expect

from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import get_admin_db
from support_agent.admin.settings import get_admin_settings
from conftest import AUDIENCE
from fakes import ANA_MEMBER, FakeAdminDb, a_client, a_member, an_entry
from live import serving
from mint_test_pass import sign_pass

expect.set_options(timeout=5_000)


@pytest.fixture
def db():
    entries = (
        an_entry("client", "Cedarline Roofing", "added", "2026-10-20T09:00", client_key="cedarline"),
        an_entry("limit", "Cedarline Roofing", "limit_changed", "2026-10-23T14:05", client_key="cedarline",
                 detail="20 to 30"),
        an_entry("staff", "Ben Cruz", "added", "2026-10-26T09:00"),
    )
    return FakeAdminDb(a_client("cedarline", "Cedarline Roofing"),
                       staff=(ANA_MEMBER, a_member(2, "Ben Cruz", "ben@bluejay.example", "U0BEN")), entries=entries)


@pytest.fixture
def page(browser, settings, keys, db) -> Page:
    def make_app(url):
        app = create_app()
        here = settings.model_copy(update={"admin_origin": url})
        app.dependency_overrides[get_admin_settings] = lambda: here
        app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
        app.dependency_overrides[get_admin_db] = lambda: db
        return app

    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    with serving(make_app) as url:
        context = browser.new_context(base_url=url, extra_http_headers=signed_in)
        context.set_default_timeout(5_000)
        yield context.new_page()
        context.close()


def test_add_a_member_then_deactivate_them(page, db):
    page.goto("/staff")
    page.get_by_role("link", name="Add staff").click()
    expect(page.locator("dialog:modal")).to_have_count(1)  # Add staff is a panel over the list
    page.get_by_label("Name").fill("Cara Lim")
    page.get_by_label("Work email").fill("cara@bluejay.example")
    page.get_by_label("Slack member ID").fill("u0cara")
    page.get_by_role("button", name="Add staff").click()

    expect(page.locator(".ds-toast__region[role=status]")).to_have_text(
        "Added Cara Lim. They can sign in and approve drafts now.")
    expect(page.locator("dialog:modal")).to_have_count(0)
    page.get_by_role("link", name="Cara Lim").click()
    expect(page.locator("dialog:modal")).to_have_count(1)
    page.get_by_role("button", name="Deactivate").click()
    expect(page.locator(".ds-toast__region[role=status]")).to_contain_text("Deactivated Cara Lim.")
    expect(page.locator("dialog:modal")).to_have_count(1)  # the panel stays, with the new state and an Undo
    assert db.staff[-1].active is False


@pytest.mark.parametrize("width", [1280, 375])
def test_a_staff_row_opens_from_anywhere(page, width):
    page.set_viewport_size({"width": width, "height": 800})
    for part in (".ds-table__initial", 'td[data-label="Work email"]', ".ds-table__id", ".ds-table__status",
                 ".ds-table__go"):
        page.goto("/staff")
        target = page.locator("tr", has_text="Ben Cruz").locator(part)
        # A real mouse press at the middle of the part, whatever sits on top there: the row is one link stretched over
        # it, so Playwright's own click would wait for the part itself to be what the press lands on.
        box = target.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        expect(page).to_have_url("/staff/2")
        expect(page.locator("dialog:modal")).to_have_count(1)  # the panel over the list, not a page of its own
        expect(page.get_by_role("heading", name="Ben Cruz", level=2)).to_be_visible()


def test_history_filters_only_when_show_is_pressed(page, db):
    # Picking alone never reloads the page (WCAG 2.2 3.2.2 On Input): a keyboard user can step through the options.
    page.goto("/history")
    expect(page.locator(".ds-card__history-item")).to_have_count(3)

    page.get_by_label("Kind of change").select_option("limit")
    page.get_by_label("Person").select_option("Ana Reyes")
    expect(page).to_have_url("/history")
    expect(page.locator(".ds-card__history-item")).to_have_count(3)

    page.get_by_role("button", name="Show").click()
    expect(page).to_have_url("/history?kind=limit&client=&person=Ana+Reyes")
    expect(page.get_by_label("Kind of change")).to_have_value("limit")  # the picks stay shown
    expect(page.locator(".ds-card__history-item")).to_have_count(1)
    expect(page.locator(".ds-card__history-item")).to_contain_text("changed Cedarline Roofing's limit")
