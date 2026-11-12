"""The Clients screen: every client that hasn't been removed, this hour's use against its limit, search and sort, and
the limit changed in place through admin_set_draft_limit. The database is a fake; db/checks/018 covers the real one."""

import re
from contextlib import contextmanager
from dataclasses import replace

import psycopg
import pytest
from fastapi.testclient import TestClient

from support_agent.admin import db as admin_db
from support_agent.admin.app import create_app
from support_agent.admin.auth import FileKeys, get_key_source
from support_agent.admin.db import ClientGone, LimitChange, get_admin_db
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import get_channel_lister
from conftest import AUDIENCE, ORIGIN
from fakes import FakeAdminDb, ScriptedConn, a_client, an_entry, drafted, postgres_db_on
from mint_test_pass import sign_pass
from panels import dialog_tags, ids, is_open, panel_tag, words


@pytest.fixture
def db():
    return FakeAdminDb(
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", status="paused", reason="key_broken", note="token expired"),
        a_client("gone", "Gone Plumbing", status="removed"),
    )


@pytest.fixture
def client(settings, keys, db):
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    app.dependency_overrides[get_admin_db] = lambda: db
    private_key, kid, _ = keys
    signed_in = {"Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)}
    return TestClient(app, headers=signed_in)


def test_clients_page_lists_active_and_paused_not_removed(client):
    page = client.get("/clients")
    assert page.status_code == 200
    assert "Cedarline Roofing" in page.text and "12" in page.text and "30" in page.text
    assert "BrightSmile Dental" in page.text and "Paused" in page.text and "Key broken" in page.text
    assert "Gone Plumbing" not in page.text


def test_a_client_with_no_stored_name_shows_its_channel_id(client, db):
    """Until the first refresh has read a name from Slack (or after a failed noting), the code shows as before; a client
    that has a name shows `#name` beside it, each on the card, the list's row and the panel."""
    db.rows[1] = replace(db.rows[1], channel_name="brightsmile-support")  # Cedarline has none stored

    cards = client.get("/clients").text
    rows = client.get("/clients?view=list").text
    cedar_panel = client.get("/clients/cedarline").text

    assert '<span class="ds-card__sub">C0CEDARLINE</span>' in cards
    assert '<span class="ds-table__sub">C0CEDARLINE</span>' in rows
    assert '<span class="ds-sheet__sub">C0CEDARLINE</span>' in cedar_panel
    assert '<span class="ds-card__sub">#brightsmile-support</span>' in cards
    assert "C0BRIGHTSMILE" not in cards and "C0BRIGHTSMILE" not in rows


def test_a_channel_name_is_text_never_markup(client, db):
    """A name comes from Slack, so it is shown as text everywhere, whatever characters it holds."""
    db.rows[0] = replace(db.rows[0], channel_name='<b>bold</b> "q" \'s\' <script>x</script>')

    for address in ("/clients", "/clients?view=list", "/clients/cedarline"):
        page = client.get(address).text
        assert "<b>bold</b>" not in page and "<script>x" not in page, address
        assert "#&lt;b&gt;bold&lt;/b&gt; &#34;q&#34; &#39;s&#39; &lt;script&gt;x&lt;/script&gt;" in page, address


@pytest.mark.parametrize("address", ["/clients", "/clients?view=list", "/clients?q=zzz"])
def test_the_clients_page_holds_a_closed_empty_panel(client, address):
    page = client.get(address).text

    [tag] = dialog_tags(page)
    assert 'id="panel"' in tag and 'class="ds-sheet"' in tag and not is_open(tag)
    assert re.search(r'<dialog[^>]*id="panel"[^>]*></dialog>', page)  # nothing inside, for the script to fill
    assert page.index('id="results"') < page.index("<dialog")
    assert ids(page).count("panel") == 1 and ids(page).count("toast") == 1


def tile(page: str, name: str) -> str:
    """The tile that holds this client's name."""
    tiles = re.findall(r'<article class="ds-card ds-card--tile.*?</article>', page, re.S)
    found = [one for one in tiles if f">{name}<" in one]
    assert len(found) == 1, f"{len(found)} tiles for {name}"
    return found[0]


def test_each_tile_shows_its_state(client, db):
    db.rows = [
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", used=20, limit=20),
        a_client("lowered", "Lowered Plumbing", used=25, limit=20),  # limit lowered below this hour's use
        a_client("paused", "Paused Bakery", used=3, limit=20, status="paused", reason="client_asked"),
    ]
    page = client.get("/clients").text
    assert "Drafting" in tile(page, "Cedarline Roofing") and "ds-card--warn" not in tile(page, "Cedarline Roofing")
    for name in ("BrightSmile Dental", "Lowered Plumbing"):
        assert "At limit" in tile(page, name) and "ds-card--warn" in tile(page, name)
    paused = tile(page, "Paused Bakery")
    assert "Paused" in paused and "Client asked" in paused and "ds-card--warn" not in paused


def test_each_bar_is_measured_against_its_own_limit(client, db):
    db.rows = [
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("lowered", "Lowered Plumbing", used=25, limit=20),
        a_client("fresh", "Fresh Dental", used=0, limit=7),
        a_client("paused", "Paused Bakery", used=3, limit=20, status="paused", reason="client_asked"),
    ]
    page = client.get("/clients").text
    level = r'class="ds-card__fill" data-level="([^"]+)"'
    assert re.findall(level, tile(page, "Cedarline Roofing")) == ["0.4"]
    assert re.findall(level, tile(page, "Lowered Plumbing")) == ["1"]  # the bar never runs past its track
    assert re.findall(level, tile(page, "Fresh Dental")) == ["0"]
    assert "ds-card__meter" not in tile(page, "Paused Bakery")  # no drafts, so no bar


def pressed(page: str) -> list[str]:
    return re.findall(r'name="view" value="(\w+)" aria-pressed="true"', page)


def test_the_view_switch_shows_cards_or_a_table(client, db):
    cards = client.get("/clients").text
    assert "ds-card--tile" in cards and "<table" not in cards and pressed(cards) == ["cards"]
    listed = client.get("/clients?view=list").text
    assert "<table" in listed and "ds-card--tile" not in listed and pressed(listed) == ["list"]
    rows = re.findall(r'<tr role="row"[^>]*>.*?</tr>', listed.split("<tbody", 1)[1], re.S)
    assert [("Cedarline Roofing" in row, "Key broken" in row) for row in rows] == [(True, False), (False, True)]
    # The list has no limit field and no HighLevel location: they are on the card and in the panel.
    assert "ds-inline-edit" not in listed and "loc-cedarline" not in listed and "Location" not in listed
    odd = client.get("/clients?view=grid").text  # a hand-typed address gets the default
    assert "ds-card--tile" in odd and pressed(odd) == ["cards"]


@pytest.mark.parametrize("view", ["cards", "list"])
def test_no_clients_and_no_match_say_so(client, db, view):
    db.rows = []
    empty = client.get(f"/clients?view={view}").text
    assert "No clients yet" in empty and "ds-card--tile" not in empty and "<table" not in empty
    db.rows = [a_client("cedarline", "Cedarline Roofing")]
    none = client.get(f"/clients?view={view}&q=zzz&sort=name").text
    assert "No client matches" in none and "No clients yet" not in none and "<table" not in none
    assert f'href="/clients?sort=name&amp;view={view}">Show all clients</a>' in none  # keeps the sort and view


@pytest.mark.parametrize("view", ["cards", "list"])
def test_names_and_searches_are_text_never_markup(client, db, view):
    db.rows = [a_client("odd", 'Ana\'s "Best" <b>Bakery</b>')]
    page = client.get(f"/clients?view={view}&q=" + '"><b>').text  # matches nothing: the no-match line shows
    assert '"><b>' not in page and "&#34;&gt;&lt;b&gt;" in page  # (the page has <b> of its own: the paused count)
    db.rows = [a_client("odd", 'Ana\'s "Best" <b>Bakery</b>')]
    page = client.get(f"/clients?view={view}").text
    assert "<b>Bakery</b>" not in page and "&lt;b&gt;Bakery&lt;/b&gt;" in page
    if view == "cards":  # (the list has no limit field to carry the name)
        assert 'data-name="Ana&#39;s &#34;Best&#34; &lt;b&gt;Bakery&lt;/b&gt;"' in page  # the attribute stays closed


def needs_you(page: str) -> str:
    return re.search(r'<section class="ds-card ds-card--needs".*?</section>', page, re.S).group(0)


def test_needs_you_lists_clients_at_their_limit_first_then_paused_ones(client, db):
    db.rows = [
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", used=20, limit=20),
        a_client("lowered", "Lowered Plumbing", used=25, limit=20),  # furthest past its limit: first
        a_client("paused", "Paused Bakery", status="paused", reason="client_asked"),
        a_client("gone", "Gone Plumbing", used=40, limit=20, status="removed"),
    ]
    card = needs_you(client.get("/clients?q=cedar").text)  # search narrows the tiles, never this card
    assert re.findall(r'class="ds-card__title">([^<]+)<', card)[:-1] == [  # the last: hidden, until all are dismissed
        "Lowered Plumbing is at its limit", "BrightSmile Dental is at its limit", "Paused Bakery is paused"]
    assert ('<a class="ds-button ds-button--plain" href="/clients/paused" data-focus="run" '
            'aria-label="Review: Paused Bakery">') in card
    assert "20 of 20 drafts this hour. New messages wait for the next hour." in card
    assert "Client asked. New messages get a “Paused: answer by hand” card in Slack." in card
    assert ">3 items<" in card and card.count(">Raise limit<") == 2
    db.rows = [a_client("cedarline", "Cedarline Roofing", used=12, limit=30)]
    calm = needs_you(client.get("/clients").text)
    assert "Every client is drafting within its limit" in calm and "ds-card__item--calm" in calm


def test_a_name_on_the_needs_you_card_is_text_never_markup(client, db):
    odd = 'Ana\'s "Best" <b>Bakery</b>'
    db.rows = [a_client("odd", odd, used=30, limit=30), a_client("gone-odd", odd, status="paused", reason="client_asked")]
    card = needs_you(client.get("/clients").text)
    assert "<b>" not in card and "&lt;b&gt;Bakery&lt;/b&gt; is at its limit" in card
    assert 'aria-label="Raise limit: Ana&#39;s &#34;Best&#34; &lt;b&gt;Bakery&lt;/b&gt;"' in card  # the attributes stay closed
    assert 'aria-label="Review: Ana&#39;s &#34;Best&#34; &lt;b&gt;Bakery&lt;/b&gt;"' in card
    assert 'aria-label="Dismiss: Ana&#39;s &#34;Best&#34; &lt;b&gt;Bakery&lt;/b&gt; is paused"' in card
    assert 'href="/clients/odd" data-focus="limit"' in card  # the link names the client's panel, not a search


def test_raise_limit_links_to_the_client(client, db):
    """Raise limit opens the client's panel on its limit field: the address is the client's, the control
    to focus is a data attribute, and no search or script hook is left on the link."""
    db.rows = [a_client("brightsmile", "BrightSmile Dental", used=20, limit=20)]
    card = needs_you(client.get("/clients?q=zzz&sort=name&view=list").text)
    assert ('<a class="ds-button" href="/clients/brightsmile" data-focus="limit" '
            'aria-label="Raise limit: BrightSmile Dental">Raise limit</a>') in card
    assert "q=" not in card and "data-raise" not in card


def band(page: str) -> str:
    """The main panel: the hour's number on the left, the day's chart on the right."""
    return re.search(r'<section class="ds-band".*?</section>', page, re.S).group(0)


def test_the_two_numbers_add_up_active_clients(client, db):
    db.rows = [
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", used=20, limit=20),
        a_client("paused", "Paused Bakery", used=3, limit=15, status="paused", reason="client_asked"),
        a_client("gone", "Gone Plumbing", used=40, limit=20, status="removed"),
    ]
    page = client.get("/clients?q=cedar").text  # the whole agency, whatever the search
    main = band(page)
    assert re.search(r'>32</span> <span class="ds-card__unit">of <span id="total-limits"[^>]*>50<', main)  # active only
    assert 'id="total-bar" data-level="0.64"' in main
    assert "<b>1 client</b>" in main
    db.rows[3] = a_client("more", "More Paused", status="paused", reason="billing_hold")
    assert "<b>2 clients</b>" in band(client.get("/clients").text)


@pytest.mark.usefixtures("fixed_clock")
def test_main_panel_has_the_number_and_chart(client, db):
    db.rows = [
        a_client("cedarline", "Cedarline Roofing", used=12, limit=30),
        a_client("brightsmile", "BrightSmile Dental", used=3, limit=20),
        a_client("paused", "Paused Bakery", status="paused", reason="client_asked"),
    ]
    db.drafts = [drafted("cedarline", 5), drafted("cedarline", 70), drafted("brightsmile", 20)]
    page = client.get("/clients").text

    main = band(page)
    assert "Drafts this hour" in main and "Drafts today" in main
    assert re.search(r'>15</span> <span class="ds-card__unit">of <span id="total-limits"[^>]*>50<', main)
    assert 'id="total-bar" data-level="0.3"' in main
    assert "Paused" in main and "<b>1 client</b>" in main
    chart = main[main.index('<div class="ds-chart'):]
    assert re.findall(r'<th scope="col">([^<]+)</th>', chart) == ["Hour ending", "Cedarline Roofing",
                                                                   "BrightSmile Dental"]  # a line each; Paused has none
    assert "ds-chart--still" not in chart  # drawn in once, on a page load
    assert page.index('id="summary"') < page.index('class="ds-band"') < page.index('id="all-clients"')


@pytest.mark.parametrize("address", ["/clients/cedarline", "/clients/new"])
@pytest.mark.usefixtures("fixed_clock")
def test_a_direct_address_shows_the_chart_behind_the_panel(client, db, address):
    client.app.dependency_overrides[get_channel_lister] = lambda: lambda: []
    db.drafts = [drafted("cedarline", 5), drafted("cedarline", 70)]
    page = client.get(address).text

    assert is_open(panel_tag(page))
    assert page.index('class="ds-band"') < page.index("<dialog")  # the list and its main panel stay behind the panel
    assert re.findall(r'<th scope="col">([^<]+)</th>', band(page)) == ["Hour ending", "Cedarline Roofing"]


def test_a_failed_chart_query_leaves_the_page_working(client, db, caplog):
    caplog.set_level("DEBUG")

    def down(since):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.drafts_today = down
    page = client.get("/clients")

    assert page.status_code == 200 and "Cedarline Roofing" in page.text and 'id="total-used"' in page.text
    main = band(page.text)
    assert "The chart can’t be shown right now." in main and "ds-chart__plot" not in main
    assert "No drafts yet today" not in main  # not a false "nothing happened"
    assert "OperationalError" in caplog.text  # the log was read: its type is there
    assert "admin_app" not in page.text + caplog.text and "127.0.0.1" not in caplog.text


def change_line(page: str) -> str:
    """The latest-change region, up to the search form that follows it."""
    return page.split('id="change-line"', 1)[1].split('<form id="find"', 1)[0]


def test_one_line_shows_the_latest_change(client, db):
    db.entries = [
        an_entry("client", "BrightSmile Dental", "resumed", "2026-11-02T15:04", client_key="brightsmile"),
        an_entry("limit", "Cedarline Roofing", "limit_changed", "2026-11-03T09:15", client_key="cedarline",
                 detail="20 to 25", who="Dana Reyes"),
    ]
    page = client.get("/clients").text

    line = change_line(page)
    assert not line.startswith(" hidden") and "ds-change-line" in line
    assert "<b>Dana Reyes</b>" in line and "changed Cedarline Roofing's limit from 20 to 25 drafts an hour" in words(line)
    assert '<time datetime="2026-11-03T09:15:00+00:00">Nov 3, 04:15 EST</time>' in line  # in Bluejay's time zone
    assert re.search(r'<a class="ds-change-line__link" href="/history">All changes', line)
    assert "BrightSmile" not in line  # the latest one only


def test_one_line_hides_with_no_changes(client, db):
    db.entries = []
    page = client.get("/clients").text

    assert 'id="change-line" hidden>' in page and "ds-change-line" not in page  # the wrapper stays, for the script
    db.entries = [an_entry("client", "Cedarline Roofing", "added", "2026-11-02T15:04", client_key="cedarline")]
    assert 'id="change-line" hidden' not in client.get("/clients").text


def test_a_failed_change_query_leaves_the_page_working(client, db, caplog):
    caplog.set_level("DEBUG")

    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.changes = down
    page = client.get("/clients")

    assert page.status_code == 200 and "Cedarline Roofing" in page.text
    assert 'id="change-line" hidden>' in page.text
    assert "OperationalError" in caplog.text and "admin_app" not in page.text + caplog.text


def test_a_paused_item_has_no_amber(client, db):
    db.rows = [a_client("cedarline", "Cedarline Roofing", used=30, limit=30),
               a_client("paused", "Paused Bakery", status="paused", reason="client_asked")]
    card = needs_you(client.get("/clients").text)

    items = dict((state, classes) for classes, state in re.findall(
        r'<li class="([^"]*)" data-client="[^"]*" data-needs="[^"]*:(\w+)"', card))
    assert "ds-card__item--warn" in items["at_limit"]  # only the limit is amber: edge and words
    assert "ds-card__item--warn" not in items["paused"]


def test_paused_card(client, db):
    db.rows = [a_client("paused", "Paused Bakery", used=3, limit=20, status="paused", reason="client_asked")]
    card = tile(client.get("/clients").text, "Paused Bakery")

    assert '<span class="ds-status__label">Paused: Client asked</span>' in card
    assert '<span class="ds-card__count-number">–</span>' in card  # a dash for the count
    assert "no drafts while paused" in card
    assert "ds-card__meter" not in card and "ds-card--warn" not in card  # no bar, no amber
    assert 'class="ds-card__go"' in card  # Details is still there


@pytest.mark.usefixtures("fixed_clock")
def test_a_cards_line_matches_its_chart_line(client, db):
    names = ["Alpha Dental", "Bravo Roofing", "Charlie Bakery", "Delta Yoga", "Echo Plumbing", "Foxtrot Vets",
             "Golf Bikes"]
    db.rows = [a_client(name.split()[0].lower(), name, used=1) for name in names]
    # Six, five, four, three, two and one drafts today; Golf has none.
    db.drafts = [drafted(name.split()[0].lower(), 5 + n) for place, name in enumerate(names[:6])
                 for n in range(6 - place)]
    page = client.get("/clients").text

    legend = dict((name, series) for series, name in re.findall(
        r'ds-chart__line--(\w+)" d="M0 1H18"/></svg>([^<]+)</span>', band(page)))
    assert legend == {"Alpha Dental": "1", "Bravo Roofing": "2", "Charlie Bakery": "3", "Delta Yoga": "4",
                      "Others": "other"}

    def line_on(name):
        return re.findall(r'<svg class="ds-chart__key"[^>]*><path class="ds-chart__line ds-chart__line--(\w+)"',
                          tile(page, name))

    for name in names[:4]:
        assert line_on(name) == [legend[name]]
    assert line_on("Echo Plumbing") == line_on("Foxtrot Vets") == [legend["Others"]]  # in Others: the Others line
    assert line_on("Golf Bikes") == []  # no draft today, no line


def order_of(page: str, *names: str) -> list[str]:
    page = page[page.index("<main"):]  # the order inside the page's main content, past the frame
    return sorted(names, key=page.index)


def test_search_and_sort(client, db):
    db.rows = [
        a_client("alpha", "Alpha Dental", used=5, limit=10),  # half used
        a_client("bravo", "bravo Roofing", used=9, limit=10),  # closest to its limit; small b sorts by name anyway
        a_client("charlie", "Charlie Bakery", used=3, limit=30),  # a tenth
    ]
    names = ("Alpha Dental", "bravo Roofing", "Charlie Bakery")
    assert order_of(client.get("/clients").text, *names) == ["bravo Roofing", "Alpha Dental", "Charlie Bakery"]
    assert order_of(client.get("/clients?sort=closest").text, *names) == ["bravo Roofing", "Alpha Dental",
                                                                          "Charlie Bakery"]
    assert order_of(client.get("/clients?sort=name").text, *names) == ["Alpha Dental", "bravo Roofing",
                                                                       "Charlie Bakery"]
    found = client.get("/clients?q=roofing").text  # the name has "Roofing": found in any case
    found = found[found.index("<main"):]  # only the main content: the frame around it is not what a search narrows
    assert "bravo Roofing" in found and "Alpha Dental" not in found and "Charlie Bakery" not in found


def test_limit_change_calls_the_function_with_the_signed_in_member(client, db):
    answer = client.post("/clients/cedarline/limit", data={"limit": "35"}, headers={"Origin": ORIGIN})
    assert answer.status_code == 200
    assert answer.json() == {"limit_before": 30, "limit_after": 35, "used_this_hour": 12}
    assert db.calls == [(1, "cedarline", 35)]  # Ana's staff id: the change is hers in the history


@pytest.mark.parametrize("typed", ["0", "201", "", "ten", "2.5", "-3", " "])
def test_limit_out_of_range_is_422(client, db, typed):
    answer = client.post("/clients/cedarline/limit", data={"limit": typed}, headers={"Origin": ORIGIN})
    assert answer.status_code == 422
    assert answer.json() == {"error": "The limit must be a whole number from 1 to 200."}
    assert db.calls == []  # refused before the database


def test_database_down_while_loading_the_page_is_a_plain_503(client, db, caplog):
    caplog.set_level("DEBUG")  # every level: a login or address logged even at info level fails this

    def down():
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.clients = down
    page = client.get("/clients")
    assert page.status_code == 503 and "reach its database right now" in page.text
    assert "OperationalError" in caplog.text  # the log was read: its type is there
    assert "admin_app" not in page.text + caplog.text and "127.0.0.1" not in caplog.text


def test_a_removed_client_gets_a_plain_message(client, db):
    db.rows = []  # removed in another tab since this page was opened
    answer = client.post("/clients/cedarline/limit", data={"limit": "35"}, headers={"Origin": ORIGIN})
    assert answer.status_code == 404
    assert answer.json() == {"error": "This client was removed. Reload the page to see the current list."}


def test_database_down_during_a_change_is_503(client, db, caplog):
    caplog.set_level("DEBUG")

    def down(*args):
        raise psycopg.OperationalError("connection to server at 127.0.0.1 failed: password for admin_app")

    db.set_draft_limit = down
    answer = client.post("/clients/cedarline/limit", data={"limit": "35"}, headers={"Origin": ORIGIN})
    assert answer.status_code == 503
    assert answer.json() == {"error": "Couldn't reach the database, so the limit may not have changed. Reload the "
                                      "page to check."}
    assert "OperationalError" in caplog.text
    assert "admin_app" not in answer.text + caplog.text and "127.0.0.1" not in answer.text + caplog.text


def test_page_has_no_key_and_no_sealed_bytes(settings, keys, monkeypatch):
    """admin_clients has no key column (db/checks/018). If a later change joined one in, the page must still show
    only the fields it names. Runs PostgresAdminDb itself on a fake connection that does return key columns."""

    class Conn:
        def execute(self, sql, params=None):
            self.sql = sql
            return self

        def fetchone(self):
            return {"id": 1, "name": "Ana Reyes", "slack_user_id": "U0ANA"}  # admin_staff_for_email

        def fetchall(self):
            if "admin_clients" not in self.sql:  # the page's other reads: the chart's view and the latest change
                assert "admin_drafts_today" in self.sql or "admin_history" in self.sql
                return []
            return [{"client_key": "cedarline", "display_name": "Cedarline Roofing", "channel_id": "C0CEDAR",
                     "location_id": "loc-cedar", "drafts_per_hour": 30, "used_this_hour": 12, "status": "active",
                     "pause_reason": None, "pause_note": None, "key_set_at": None, "channel_name": None,
                     "sealed": b"SEALED-BYTES", "ghl_token": "pit-live-secret"}]

    class Pool:
        @contextmanager
        def connection(self):
            yield Conn()

    monkeypatch.setattr(admin_db, "_pool", lambda: Pool())
    app = create_app()
    app.dependency_overrides[get_admin_settings] = lambda: settings
    app.dependency_overrides[get_key_source] = lambda: FileKeys(keys[2])
    private_key, kid, _ = keys
    page = TestClient(app).get("/clients", headers={
        "Cf-Access-Jwt-Assertion": sign_pass(private_key, kid, "ana@bluejay.example", AUDIENCE)})
    assert page.status_code == 200 and "Cedarline Roofing" in page.text
    assert "pit-live-secret" not in page.text and "SEALED" not in page.text


def test_the_change_and_this_hours_use_come_from_one_connection(monkeypatch):
    conn = ScriptedConn({"limit_before": 30, "limit_after": 35}, {"used_this_hour": 12})
    change = postgres_db_on(conn, monkeypatch).set_draft_limit(1, "cedarline", 35)
    assert change == LimitChange(limit_before=30, limit_after=35, used_this_hour=12)
    assert "admin_set_draft_limit" in conn.statements[0][0] and conn.statements[0][1] == (1, "cedarline", 35)
    assert "admin_clients" in conn.statements[1][0] and conn.statements[1][1] == ("cedarline",)


def test_the_databases_removed_client_refusal_becomes_client_gone(monkeypatch):
    refusal = psycopg.errors.RaiseException("admin: unknown or removed client")
    with pytest.raises(ClientGone):
        postgres_db_on(ScriptedConn(refusal), monkeypatch).set_draft_limit(1, "gone", 35)
    other = psycopg.errors.RaiseException("admin: only an active staff member can make changes")
    with pytest.raises(psycopg.errors.RaiseException):  # anything else stays a database error (503)
        postgres_db_on(ScriptedConn(other), monkeypatch).set_draft_limit(1, "cedarline", 35)
