# Security

## Reporting a problem

Please report a vulnerability through GitHub's **private vulnerability reporting** on this repository (Security →
Report a vulnerability), not in a public issue. Include what you found, how to reproduce it and what it could lead
to. You can expect a first answer within a week. Fixes go into the newest release; older releases are not patched.

This is a practice project: the agency, its clients and every ticket in the repository are made up, and no real
customer data is involved.

## How the system is protected

- **The agent can only read and draft.** It has no write tools; every reply needs a person's click in Slack
  ([ADR 0001](docs/adr/0001-drafts-and-waits.md)).
- **Client isolation in three layers.** The Slack channel decides the client in code; each client's GoHighLevel
  key is read-only and bound by GHL to that client's sub-account; the agent allows sixteen named read actions and
  nothing else ([ADR 0002](docs/adr/0002-ghl-through-its-mcp-server-read-only.md)).
- **Signed requests.** Slack messages and button clicks are checked against Slack's signing secret; stale and
  repeated clicks are refused.
- **Sealed keys.** Client GHL keys are sealed with a public key before they reach the database; only the agent's
  container holds the private key to open them. Pages never show them.
- **Least privilege.** Each component has its own database login, and rule-bearing writes go through database
  functions.
- **No open ports.** Slack reaches the system through a Cloudflare tunnel; the admin app requires a Cloudflare Access
  sign-in, checks the signed pass on every request, allows only listed staff and refuses cross-site form posts.
- **Limits.** Per-client hourly limits, a tool-call cap and a time budget per ticket, a cap on drafts at a time, a
  maximum ticket length and a monthly spending cap on the OpenAI account.
- **Privacy.** Customer details are kept out of replies and out of saved past tickets; ticket text is deleted after
  90 days; OpenAI calls are made with `store=False`.
- **Supply chain.** Locked Python dependencies, images pinned by digest and scanned before updates, GitHub Actions
  pinned by commit, a leaked-key scan of the whole history and a vulnerable-library scan on every pull request.
- **Backups.** Nightly encrypted dumps to object storage, restore tested.

## Threat model

### Assets

Client GoHighLevel accounts and their customers' details; the client keys; Slack channels and the replies sent to
clients; ticket and draft records; the OpenAI account and its budget; the knowledge base; the admin app.

### Trust boundaries

```
client (untrusted text) ─> Slack ─> tunnel ─> n8n ─> agent ─> OpenAI          (model output is untrusted too)
                                                  │      └──> GHL MCP server  (answers are untrusted data)
                                                  └─> Postgres <── admin app <── Cloudflare Access <── staff
Google Drive guides ─> knowledge sync ─> Postgres (agency-written, still treated as reference, not orders)
```

Everything a client writes, everything GHL returns and everything the model produces is treated as untrusted data.

### Threats and defences

Mapped to the [OWASP Top 10 for LLM Applications (2025)](https://genai.owasp.org/llm-top-10/), then the threats
outside the model. Test names are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#requirements-and-the-tests-that-prove-them).

| Threat | Defence | Proof |
|---|---|---|
| **LLM01 Prompt injection:** a ticket, a GHL record or a guide tells the agent to ignore its rules, look at another client or send a link | Client text is fenced in a block it can't break out of; search results and GHL data are framed as reference; GHL's steering text is dropped; no write tools; code checks every draft; a person approves | 24 attack tickets in the test set; loop, GHL-access and guard tests |
| **LLM02 Sensitive information disclosure:** one client's or customer's data in another's reply | The client is fixed by the channel; the key is bound to one sub-account; a draft naming another client or repeating a customer's details goes to a person; past tickets are saved only without customer details | Isolation and privacy tests; database checks 014 and 015 |
| **LLM03 Supply chain:** a tampered library, image or action | Lockfile, digest-pinned images scanned before updates, commit-pinned actions, a week's wait before taking a new release, vulnerable-library scan | `tests/test_image_pins.py`; the checks workflow |
| **LLM04 Data and model poisoning:** a bad guide or past ticket steers future drafts | Guides come from the agency's own Drive folder; past tickets are saved only from drafts a person approved; sources are cited so staff can see what a draft relied on | Past-ticket privacy and saving checks |
| **LLM05 Improper output handling:** model output used unchecked | Structured output validated against a schema; links and sources must come from the searches; the reply length fits Slack; nothing the model writes is run or executed | Guard and loop tests |
| **LLM06 Excessive agency:** the agent changes an account | No write tools; a read-only key; an allow-list of read actions checked against GHL's catalog | `tests/agent/test_tools.py`, `tests/ghl/test_access.py` |
| **LLM07 System prompt leakage** | The prompt holds no secrets or keys; a leaked prompt reveals only the rules a client could read anyway | — |
| **LLM08 Vector and embedding weaknesses:** search returns another client's material | The knowledge base holds agency guides, help summaries and past tickets without customer details; client account data is fetched live per ticket, never stored as embeddings | Past-ticket privacy checks |
| **LLM09 Misinformation:** a confident wrong cause or a promise | Confidence levels and evidence on every draft; "it's fixed" and promises are flagged in code; a person reviews; the test set gates every model or prompt change | Guard tests; the test set's pass marks |
| **LLM10 Unbounded consumption:** floods run up the bill | Per-client hourly limits, tool-call cap, time budget, maximum ticket length, drafts at a time, OpenAI monthly cap | Loop and API tests; database check 008 |
| **Forged Slack requests** | Signing-secret checks on messages and clicks; stale and repeated clicks refused | Database check 016 |
| **Admin app access** | Cloudflare Access, its signed pass checked on every request, a staff list, same-origin form posts, strict security headers | `tests/admin/test_auth.py` |
| **Key theft from the database or a backup** | Client keys sealed, opened only in the agent's container; backups encrypted; the backup's private key is kept offline | Key and compose tests; database check 018 |
| **SQL injection in the workflows** | Parameterised queries; a scan refuses placeholders inside quoted text or comments | `tests/test_n8n_queries.py` |
| **Leaked secrets in the repository** | Keys only in a git-ignored `.env` and n8n's credential store; a leaked-key scan on every pull request; GitHub secret scanning and push protection | The checks workflow |

### Known limits

- Health-care clients need business associate agreements with every service that would touch patient data before
  any real data goes in; this practice setup has none.
- The system answers only while its machine is on (see [ADR 0003](docs/adr/0003-one-machine-behind-a-tunnel.md)).
- Some rules are proved by database checks run by hand rather than on each pull request; the gaps are listed in
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#known-gaps).
