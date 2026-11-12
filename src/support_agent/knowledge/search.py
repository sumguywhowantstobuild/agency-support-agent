"""Search the knowledge base: agency guides, GHL help summaries and past tickets.

Each search turns the question into an embedding, runs hybrid_search in Postgres (meaning + exact words), and keeps
at most a few passages per document so one long guide can't crowd out the others.
"""

from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol

from openai import OpenAI
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from support_agent.config import get_settings
from support_agent.models import Passage

# hybrid_search returns at most 20 rows; we ask for all of them so the per-document cap still leaves enough.
CANDIDATES = 20


class KnowledgeDb(Protocol):
    def hybrid_search(
        self, query_text: str, query_embedding: list[float], match_count: int, source_filter: str
    ) -> list[dict[str, Any]]: ...


Embed = Callable[[str], list[float]]


class PostgresKnowledgeDb:
    """The real database, through a small pool of connections (logged in as the read-only agent_reader)."""

    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def hybrid_search(self, query_text, query_embedding, match_count, source_filter):
        vector = "[" + ",".join(str(x) for x in query_embedding) + "]"
        with self._pool.connection() as conn:
            return conn.execute(
                "select content, metadata, score from hybrid_search(%s, %s::vector, %s, %s)",
                (query_text, vector, match_count, source_filter),
            ).fetchall()
    def sealed_key_for_location(self, location_id: str) -> bytes | None:
        """A client's locked GHL key (migration 018): only for an active client, else None. The agent opens it."""
        with self._pool.connection() as conn:
            row = conn.execute("select sealed_key_for_location(%s) as sealed", (location_id,)).fetchone()
        return row["sealed"] if row else None

    def ping(self) -> None:
        """Raises if the database can't be reached. /health uses it."""
        with self._pool.connection() as conn:
            conn.execute("select 1")    


@lru_cache
def get_db() -> PostgresKnowledgeDb:
    pool = ConnectionPool(
        get_settings().database_url.get_secret_value(),
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row, "connect_timeout": 5},  # a wrong address fails in 5 s, not hangs
        timeout=10,  # wait at most 10 s for a free connection; n8n gives the whole draft only 90 s
        open=True,
    )
    return PostgresKnowledgeDb(pool)


@lru_cache
def _openai() -> OpenAI:
    return OpenAI(api_key=get_settings().openai_api_key.get_secret_value(), timeout=20, max_retries=2)


def embed_text(text: str) -> list[float]:
    """The question as numbers, made with the same model n8n used for the passages."""
    response = _openai().embeddings.create(model=get_settings().embedding_model, input=text)
    return response.data[0].embedding


def _search(
    query: str,
    source: str,
    *,
    db: KnowledgeDb | None,
    embed: Embed | None,
    top_k: int | None,
    max_per_doc: int | None,
) -> list[Passage]:
    query = query.strip()
    if not query:
        return []  # nothing to search for: don't spend an OpenAI call or a database query
    if top_k is None or max_per_doc is None:  # tests pass both; the running agent takes them from settings
        settings = get_settings()
        top_k = settings.search_top_k if top_k is None else top_k
        max_per_doc = settings.search_max_per_doc if max_per_doc is None else max_per_doc
    db = db or get_db()
    embed = embed or embed_text

    rows = db.hybrid_search(query, embed(query), CANDIDATES, source)

    passages: list[Passage] = []
    per_doc: Counter[str] = Counter()
    for r in rows:  # already best first
        meta = r["metadata"]
        doc = meta.get("doc_id") or meta["title"]
        if per_doc[doc] >= max_per_doc:
            continue
        per_doc[doc] += 1
        passages.append(
            Passage(
                content=r["content"],
                title=meta["title"],
                heading=meta["heading"],
                url=meta["url"],
                source=meta["source"],
                score=r["score"],
            )
        )
        if len(passages) == top_k:
            break
    return passages


def search_guides(query: str, *, db=None, embed=None, top_k=None, max_per_doc=None) -> list[Passage]:
    """The agency's own how-to guides."""
    return _search(query, "guide", db=db, embed=embed, top_k=top_k, max_per_doc=max_per_doc)


def search_ghl_help(query: str, *, db=None, embed=None, top_k=None, max_per_doc=None) -> list[Passage]:
    """Our summaries of HighLevel help articles, each linking to the original."""
    return _search(query, "ghl_help", db=db, embed=embed, top_k=top_k, max_per_doc=max_per_doc)


def find_similar_tickets(query: str, *, db=None, embed=None, top_k=None, max_per_doc=None) -> list[Passage]:
    """Past tickets with their cause and fix (no customer contact details)."""
    return _search(query, "past_ticket", db=db, embed=embed, top_k=top_k, max_per_doc=max_per_doc)