# 0009: Where Slack signatures are checked

Status: accepted

## Context

Slack signs every message event and every button click with the app's signing secret. Anything reaching the public
webhook addresses without a valid signature, or with a stale timestamp, must be refused. Messages arrive through
n8n's Slack trigger; button clicks arrive at an n8n webhook. The buttons include "Take over" on the "agent
unavailable" card, the human fallback for when the agent is down.

## Options

- **The agent checks clicks.** One place in Python; while the agent is down every button stops working, including
  the fallback meant for exactly that moment.
- **n8n's own code checks clicks.** No extra hop; the secret sits in n8n's workflow data and the check is code on the
  canvas, hard to test.
- **The database checks clicks.** Every click needs the database anyway; the secret can sit where no application
  login can read it.

## Decision

Messages: n8n's Slack trigger, with the signing secret in n8n's credential, refuses unsigned events.

Clicks: the approval workflow passes Slack's two headers and the exact body bytes to `verify_slack_request()` in
Postgres, which checks the signature and refuses anything more than five minutes old. The secret is in a table only
a no-login role can read; n8n's login may run the check but not read the secret.

## Consequences

- Buttons keep working while the agent is down, so "Take over" is always there.
- A database check exercises wrong bodies, wrong timestamps, wrong secrets and stale clicks.
- The secret is stored in two places (n8n's credential and the database), so rotating it means updating both.
- If the check can't run (no secret stored, database trouble), the clicker sees an error and an alert goes to
  `#support-alerts`.
