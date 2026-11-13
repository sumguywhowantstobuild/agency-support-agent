# 0006: Hybrid search: meaning plus exact words

Status: accepted

## Context

Tickets mix plain descriptions ("the bot booked the wrong time") with exact terms that matter: "A2P", "10DLC", error
messages, field names. Embedding search finds the first kind well and can miss the second; keyword search is the
reverse.

## Options

- **Embeddings only.** Simple; misses exact codes and rare terms.
- **Keywords only.** Finds codes; misses paraphrases.
- **Both, merged.** One query runs both and merges the rankings.

## Decision

`hybrid_search` in Postgres runs a pgvector similarity search and a full-text search and merges them by reciprocal
rank fusion, so a passage ranking high in either list comes up. The agent keeps at most a few passages per document,
so one long guide can't fill every result.

## Consequences

- One SQL function, no extra service.
- Guides are split by heading into passages of a few hundred words, each keeping its source, title, heading, link and
  date, so drafts can cite them.
- The number of results and the per-document cap are settings, tuned with the accuracy test set.
