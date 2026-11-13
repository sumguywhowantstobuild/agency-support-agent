# 0003: One machine behind a tunnel, ready to move to a server

Status: accepted

## Context

Slack needs a public HTTPS address to deliver messages and button clicks. The system is small (a database, n8n, the
agent, an admin app) and its running cost should stay as low as possible while still being run properly: no open
ports, restarts on its own, off-machine backups with tested restores, monitoring.

## Options

- **A small VPS with a reverse proxy** (for example Caddy, ports 80 and 443 open). A few euros a month; a public
  machine to keep patched and firewalled.
- **A managed platform per part.** Least to run; costs grow per service, and n8n and Postgres with pgvector add up.
- **Any machine behind a Cloudflare tunnel.** No open ports at all; Cloudflare Access can guard the admin app; the
  machine must stay on.

## Decision

Docker Compose on one machine behind a Cloudflare tunnel. The same compose setup runs on a server (`deploy/`), so a
move is a restore of the latest backup onto the new machine.

## Consequences

- Nothing listens on the internet; the tunnel connects outwards, and the admin app needs a Cloudflare Access sign-in.
- The system answers only while the machine is on. An outside watcher (Healthchecks.io) emails when it goes quiet,
  and Slack retries deliveries for a while.
- A test copy runs beside the live system on the same machine, so every change is tried there first.
- For a client, a small server holding live and a capped test copy is the recommended home.
