# 0005: n8n for the workflows, Python for the agent

Status: accepted

## Context

Much of the system is plumbing: receive Slack events, post and update cards, handle button clicks, sync a Drive
folder, clean up old data, send alerts. The agent itself is a tool-using loop with schema-checked output, rule
checks and careful error handling. Agencies running GoHighLevel often already use n8n or Make for their own
automations.

## Options

- **Everything in n8n**, the agent included (its AI agent node). Fast to build; the loop, guards and tests become
  hard to express and to test.
- **Everything in Python.** One language and one test suite; the integrations become code an agency's
  automation team can't read or change.
- **n8n for the workflows, Python for the agent.** Each part in the tool that suits it, joined by one HTTP call.

## Decision

n8n runs intake, approval, knowledge sync, clean-up, monitoring and alerts. A Python service (FastAPI) runs the
agent behind `POST /draft`, reachable only inside the Docker network.

## Consequences

- An agency's team can follow and adjust the workflows on n8n's canvas.
- The agent's loop, guards and GHL access are covered by ordinary pytest tests; the workflows' code steps and
  database queries are checked by tests too (they run the steps' JavaScript in Node and scan the SQL).
- The boundary is one request and one response with a fixed schema; n8n handles the agent being down, busy or slow.
