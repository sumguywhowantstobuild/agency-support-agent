"""The chart's data: draft times per client from the view admin_drafts_today (migration 022), nothing else. The
view's rules are proved on a database by db/checks/022_drafts_today.sql; here the app's side: the query it sends and
what it hands on."""

from datetime import datetime, timezone

from support_agent.admin.db import DraftTime
from fakes import ScriptedConn, postgres_db_on

MIDNIGHT = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)  # 00:00 in New York on 3 Nov


def test_postgres_drafts_today_reads_only_client_and_time_from_the_view(monkeypatch):
    conn = ScriptedConn(None)
    first, second = datetime(2026, 11, 3, 14, 5, tzinfo=timezone.utc), datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    conn.fetchall = lambda: [{"client_key": "cedarline", "created_at": first, "extra": "never read"},
                             {"client_key": "brightsmile", "created_at": second}]

    found = postgres_db_on(conn, monkeypatch).drafts_today(MIDNIGHT)

    assert found == [DraftTime("cedarline", first), DraftTime("brightsmile", second)]
    sql, params = conn.statements[0]
    assert " ".join(sql.split()) == ("select client_key, created_at from admin_drafts_today "
                                     "where created_at >= %s order by created_at")
    assert params == (MIDNIGHT,)
