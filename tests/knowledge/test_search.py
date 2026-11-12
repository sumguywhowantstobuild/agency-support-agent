"""Knowledge search: the right source, a few passages per guide at most, nothing for an empty question."""

import pytest

from support_agent.knowledge.search import find_similar_tickets, search_ghl_help, search_guides

EMBEDDING = [0.0] * 1536


def fake_embed(text):
    return EMBEDDING


class FakeDb:
    """Stands in for Postgres: records the search it was asked for and returns the rows it was given."""

    def __init__(self, rows):
        self.rows = rows
        self.last_search = None

    def hybrid_search(self, query_text, query_embedding, match_count, source_filter):
        self.last_search = (query_text, query_embedding, match_count, source_filter)
        return self.rows


def row(doc_id, heading="Fix", source="guide", score=0.03):
    return {
        "content": f"{doc_id} > {heading}\n\nSome text.",
        "metadata": {
            "doc_id": doc_id,
            "title": f"Title of {doc_id}",
            "heading": heading,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "source": source,
        },
        "score": score,
    }


def search(fn, query, db):
    return fn(query, db=db, embed=fake_embed, top_k=5, max_per_doc=2)


def test_search_guides_asks_for_guides_only():
    db = FakeDb(rows=[row("form-guide")])
    result = search(search_guides, "form not in pipeline", db)
    assert db.last_search == ("form not in pipeline", EMBEDDING, 20, "guide")
    assert result[0].title == "Title of form-guide"
    assert result[0].heading == "Fix"
    assert result[0].url == "https://docs.google.com/document/d/form-guide/edit"
    assert result[0].source == "guide"
    assert result[0].score == 0.03


@pytest.mark.parametrize(
    ("fn", "source"),
    [(search_guides, "guide"), (search_ghl_help, "ghl_help"), (find_similar_tickets, "past_ticket")],
)
def test_each_search_uses_its_own_source(fn, source):
    db = FakeDb(rows=[])
    search(fn, "texts not sending", db)
    assert db.last_search[3] == source


def test_search_with_no_rows_returns_empty_list():
    assert search(search_ghl_help, "anything", FakeDb(rows=[])) == []


def test_blank_query_does_not_search():
    db = FakeDb(rows=[row("a")])

    def embed_must_not_run(text):
        raise AssertionError("a blank question should not be sent to OpenAI")

    assert find_similar_tickets("   ", db=db, embed=embed_must_not_run, top_k=5, max_per_doc=2) == []
    assert db.last_search is None


def test_at_most_two_passages_per_doc_and_top_k_in_total():
    rows = [row("sms", h) for h in ("Symptoms", "Fix", "Check first", "Tell the client")]
    rows += [row("a2p", h) for h in ("Summary", "Fix", "Symptoms")]
    rows += [row("phone"), row("email")]
    result = search(search_guides, "texts not sending", FakeDb(rows=rows))
    assert [(p.title, p.heading) for p in result] == [
        ("Title of sms", "Symptoms"),
        ("Title of sms", "Fix"),
        ("Title of a2p", "Summary"),
        ("Title of a2p", "Fix"),
        ("Title of phone", "Fix"),
    ]