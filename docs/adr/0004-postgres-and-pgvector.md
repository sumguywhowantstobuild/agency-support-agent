# 0004: Postgres with pgvector for the knowledge base and all records

Status: accepted

## Context

The system needs a vector search over guides, help summaries and past tickets, and ordinary tables for tickets,
drafts, clients, limits and staff. n8n also needs a production database (not its default SQLite).

## Options

- **Supabase.** Managed Postgres with pgvector; the free plan pauses and has no backups, the paid plan adds a monthly
  fee per project.
- **A dedicated vector database** (Pinecone, Qdrant and similar) next to a relational one. Two stores to secure,
  back up and keep in step.
- **Self-hosted Postgres with pgvector.** One store for everything, already needed for n8n; backups and upgrades
  are ours to run.

## Decision

Self-hosted Postgres with pgvector, a `knowledge` database next to n8n's own.

## Consequences

- One database to back up, restore and monitor; search and records share transactions.
- Each component gets its own least-privilege login, and rules (limits, first click wins, privacy of past tickets,
  one client per channel) live in database functions every caller has to go through.
- Exact-word search comes from Postgres full-text search in the same query (see [0006](0006-hybrid-search.md)).
- Nightly encrypted dumps to object storage, restore tested; upgrading the database image includes rebuilding
  indexes when its sorting rules change.
- Supabase stays the managed alternative for a client who would rather pay than run it.
