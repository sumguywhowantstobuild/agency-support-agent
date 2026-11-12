-- 001: knowledge search, client mapping, tickets and drafts.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 001_schema.sql

create table schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);
insert into schema_migrations (version) values ('001');  -- a second run fails here instead of half-applying

create extension if not exists vector;

-- Guides, GHL help summaries and past tickets, split into passages. n8n's PGVector Store node writes here
-- (its column options: id, content, embedding, metadata).
create table documents (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  metadata jsonb not null default '{}',  -- doc_id, title, heading, url, source, content_hash, updated_at
  embedding vector(1536),  -- text-embedding-3-small
  fts tsvector generated always as (to_tsvector('english', content)) stored
);
create index documents_embedding_idx on documents using hnsw (embedding vector_cosine_ops);
create index documents_fts_idx on documents using gin (fts);
create index documents_source_idx on documents ((metadata->>'source'));
create index documents_doc_id_idx on documents ((metadata->>'doc_id'));  -- sync replaces a changed doc's passages

-- Meaning search and exact-word search, merged by reciprocal rank fusion: a passage that ranks high in either
-- list scores well, one that ranks high in both scores best. Exact words matter for codes like "A2P 10DLC".
create function hybrid_search(
  query_text text,
  query_embedding vector(1536),
  match_count int,
  source_filter text,
  rrf_k int default 50
)
returns table (content text, metadata jsonb, score float)
language sql stable
set hnsw.iterative_scan = strict_order  -- keep scanning the index until enough rows pass the source filter
as $$
with words as (
  select d.id, row_number() over (order by ts_rank_cd(d.fts, q) desc) as rank_ix
  from documents d, websearch_to_tsquery('english', query_text) q
  where d.fts @@ q and d.metadata->>'source' = source_filter
  order by rank_ix
  limit least(match_count, 20) * 2
),
meaning as (
  select d.id, row_number() over (order by d.embedding <=> query_embedding) as rank_ix
  from documents d
  where d.metadata->>'source' = source_filter
  order by d.embedding <=> query_embedding
  limit least(match_count, 20) * 2
)
select d.content, d.metadata,
       coalesce(1.0 / (rrf_k + words.rank_ix), 0.0) + coalesce(1.0 / (rrf_k + meaning.rank_ix), 0.0) as score
from words
full outer join meaning on words.id = meaning.id
join documents d on d.id = coalesce(words.id, meaning.id)
order by score desc
limit least(match_count, 20)
$$;

-- Which Slack channel belongs to which client and GHL sub-account. n8n picks the client from here, never the model.
create table client_map (
  channel_id text primary key,
  client_key text not null unique,
  display_name text not null,
  location_id text not null unique
);

create table tickets (
  id uuid primary key default gen_random_uuid(),
  slack_event_id text not null unique,  -- Slack can deliver an event twice; the second insert is refused
  channel_id text not null references client_map,
  thread_ts text not null,
  client_key text not null,
  location_id text not null,
  text text not null,
  created_at timestamptz not null default now()
);

create table drafts (
  id uuid primary key default gen_random_uuid(),
  ticket_id uuid not null references tickets on delete cascade,
  draft jsonb not null,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'edited', 'taken_over', 'agent_unavailable')),
  decided_by text,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  check (status not in ('approved', 'edited', 'taken_over') or (decided_by is not null and decided_at is not null))
);
create index drafts_ticket_id_idx on drafts (ticket_id);