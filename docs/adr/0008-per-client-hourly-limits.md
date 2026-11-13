# 0008: Per-client hourly limits, enforced in the database

Status: accepted

## Context

Every ticket costs an AI call. A noisy client, a bot posting in a channel or a flood of messages could run up the
bill and crowd out other clients. The OpenAI account's monthly cap stops the worst case but stops every client at
once.

## Options

- **Only the account's spending cap.** No code; one client can use up everyone's budget.
- **A limit in n8n.** Easy to add; two runs at once can both pass a count check.
- **A limit in the database.** A function claims a slot atomically per client per hour.

## Decision

Each client has an hourly limit of drafts. Intake claims a slot through a database function before calling the
agent. Over the limit, the ticket goes to a person as a "limit reached" card, with one alert per busy stretch. Staff
change a client's limit from Slack or the admin app, and every change is recorded.

## Consequences

- A flood from one client is capped at its own hourly allowance. Clients still share the monthly budget and the
  agent's draft slots, so the limits are sized to leave room for the others.
- The count is right even when tickets arrive at the same moment.
- The agent also caps tool calls per ticket and runs a fixed number of drafts at a time, so a single ticket can't
  run long or pile up.
