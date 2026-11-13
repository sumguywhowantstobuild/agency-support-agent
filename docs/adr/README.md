# Decision records

Short records of the choices that shape the system, in the [MADR](https://adr.github.io/madr/) format: the context,
the options weighed, the decision and what follows from it. A new record replaces an old one rather than editing it.

| # | Decision | Status |
|---|---|---|
| [0001](0001-drafts-and-waits.md) | The agent drafts and a person sends | Accepted |
| [0002](0002-ghl-through-its-mcp-server-read-only.md) | GoHighLevel only through its MCP server, read-only, allow-listed | Accepted |
| [0003](0003-one-machine-behind-a-tunnel.md) | One machine behind a tunnel, ready to move to a server | Accepted |
| [0004](0004-postgres-and-pgvector.md) | Postgres with pgvector for the knowledge base and all records | Accepted |
| [0005](0005-n8n-for-workflows-python-for-the-agent.md) | n8n for the workflows, Python for the agent | Accepted |
| [0006](0006-hybrid-search.md) | Hybrid search: meaning plus exact words | Accepted |
| [0007](0007-the-test-set-picks-the-model.md) | The accuracy test set picks the model | Accepted |
| [0008](0008-per-client-hourly-limits.md) | Per-client hourly limits, enforced in the database | Accepted |
| [0009](0009-where-slack-signatures-are-checked.md) | Slack signatures: n8n's trigger for messages, the database for clicks | Accepted |
