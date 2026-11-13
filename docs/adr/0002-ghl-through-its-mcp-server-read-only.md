# 0002: GoHighLevel only through its MCP server, read-only, allow-listed

Status: accepted

## Context

The agent has to look inside a client's GoHighLevel sub-account: contacts, conversations, forms and submissions,
calendars and free slots, workflows, pipelines, users. GHL offers a REST API, an older MCP server whose tools miss
several of these checks, and a newer MCP server (v2) that exposes its whole public API as a searchable catalog of
actions, write actions included.

## Options

- **REST API directly.** Full control; every check written and maintained by hand.
- **Old MCP server plus REST for the gaps.** Two integrations to keep working.
- **MCP server v2 only.** One integration covering every check; the catalog lists write actions and isn't filtered
  by the key's permissions, and GHL can change it.

## Decision

MCP server v2 only, behind three locks:

1. Each client's key is a Private Integration Token with read-only permissions, so GHL refuses writes.
2. GHL binds each key to its own sub-account; another client's location is refused or ignored.
3. The agent's code allows exactly sixteen read actions by name, checks them against the catalog before use, and
   turns a check off if the catalog changes it.

## Consequences

- One integration, and every check the tickets need.
- Isolation doesn't rest on the prompt: the key, GHL and the allow-list each enforce it.
- GHL's answers include text written to steer an AI; the agent passes on only the data.
- Workflow triggers and actions aren't visible through the API, so workflow tickets are diagnosed from evidence
  (is it published, did the form submission arrive, is the contact in the pipeline) with medium confidence.
