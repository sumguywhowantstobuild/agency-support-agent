# Agency support agent

An AI agent that drafts answers to client tech-support tickets for a GoHighLevel (GHL) marketing agency. A client
posts a problem in their Slack channel; the agent checks that client's GHL account (read-only), searches the agency's
own guides, GHL help summaries and past tickets, and drafts the likely cause, the fix steps and a plain-English reply.
A support person approves, edits or takes over every draft in Slack. Nothing reaches a client without a person.

> **Practice project.** The agency (Bluejay Funnel Co.), its clients (BrightSmile Dental, Cedarline Roofing) and every
> person and ticket in this repository are made up. The problem is a real one agencies hire for; the results below
> come from synthetic tickets, not real client traffic.

## How it works

```
Client's Slack channel ──> n8n "Ticket intake" ──> agent (POST /draft) ──> card in #support-queue
                                                     │                          │
                         GHL MCP server (read-only) ─┤                          ▼
                         Postgres + pgvector search ─┘          staff: Approve / Edit / Take over
                         (guides, GHL help, past tickets)                       │
                                                                                ▼
                                                     n8n "Ticket approval" ──> reply in the client's thread
```

- **Agent** (Python, FastAPI): a tool-using loop on the OpenAI Responses API with a fixed output schema. Tools search
  the knowledge base and read the client's GHL account through GHL's MCP server, using one read-only key per client,
  sealed in the database and opened only by the agent. It has no write tools.
- **n8n workflows** (`n8n/`): ticket intake, approval, clean-up, alerts, hourly knowledge sync from Google Drive,
  health and backup checks. Slack signatures are verified on every message and button click.
- **Postgres + pgvector**: knowledge passages, tickets, drafts and per-client limits, with one least-privilege login
  per component; n8n changes limits, past tickets and old data only through database functions.
- **Admin app** (FastAPI, server-rendered, behind Cloudflare Access): staff add and pause clients, move a client to
  another channel, replace keys, manage staff and read the history.
- **Operations**: Docker Compose, a Cloudflare tunnel (no open ports), encrypted nightly backups to object storage
  with tested restores, a test copy every change passes through before live (`deploy/apply.sh`), image pinning by
  digest and vulnerability scans, and an outside watcher (Healthchecks.io).

## Accuracy

64 synthetic tickets (40 support problems, 24 attacks such as prompt injection) are scored by rule checks plus a
separate judge model. Recent runs: 94–96% of drafts fully correct, typical draft in 14–21 seconds. Details, method and
limits: [`evals/README.md`](evals/README.md).

## Running it

Setup from scratch, every account it needs and how changes go live: [`docs/SETUP.md`](docs/SETUP.md). Release
history: [`CHANGELOG.md`](CHANGELOG.md).

Tests (Python 3.11, [uv](https://docs.astral.sh/uv/), Node 22+ for the motion script's tests; the browser tests use
Playwright in Chromium, Firefox and WebKit):

```bash
uv sync
uv run playwright install
uv run pytest -q -n 4
```

## Security

Read-only access to client accounts, signed Slack requests, per-client rate limits, sealed client keys, a model that
can't post anything itself, and a person on every reply. To report a problem, use GitHub's private vulnerability
reporting on this repository.

## Licence

[MIT](LICENSE).
