# 0001: The agent drafts and a person sends

Status: accepted

## Context

Agency clients are small-business owners with live funnels, calendars and text campaigns. A wrong answer or a wrong
change in their GoHighLevel account costs them leads and costs the agency trust. Support staff already know the
SOPs; their time goes into investigating and writing.

## Options

- **Draft and wait.** The agent investigates and drafts; a person approves, edits or takes over every reply.
  Safe, and staff stay in control; every ticket still needs a click.
- **Answer simple tickets alone.** Faster for easy tickets; needs a reliable way to tell easy from risky, and one
  wrong automatic reply reaches a client directly.
- **Fix things too.** The agent changes the account (re-publishes a workflow, edits a calendar). Biggest time
  saving; a tricked or mistaken agent can break a client's account.

## Decision

Draft and wait. The agent has no write tools at all, and nothing reaches a client without a person's click.

## Consequences

- A prompt injection can at worst produce a bad draft, which a person sees first; it can't change an account.
- The time saved is the investigation and the writing, not the click.
- Approvals, edits and take-overs are recorded, which gives the data needed before ever letting simple tickets go
  out alone.
