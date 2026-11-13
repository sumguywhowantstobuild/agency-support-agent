# Architecture

How the system is put together, why, and which test proves each rule it promises. Setup and day-to-day operation
are in [`SETUP.md`](SETUP.md); the reasoning behind the main choices is in the [decision records](adr/README.md);
threats and their defences are in [`SECURITY.md`](../SECURITY.md).

## What it does

A client of a GoHighLevel (GHL) marketing agency posts a tech problem in their own Slack channel. The system finds
which client the channel belongs to, the agent investigates (the client's GHL account, read-only, and the agency's
knowledge base) and a draft lands in `#support-queue`: likely cause and confidence, the evidence, fix steps for the
support person, a plain-English reply for the client and the sources. A person approves, edits or takes over. Nothing
reaches the client, and nothing changes in their account, without that person.

## Parts

```
                    ┌──────────────────────── Slack ────────────────────────┐
                    │ #client-<name> channels            #support-queue      │
                    └──────┬──────────────────────────────────▲──────┬──────┘
                           │ message (signed)                 │ card │ button click (signed)
                           ▼                                  │      ▼
   Cloudflare tunnel ──> n8n: Ticket intake ──> agent ──> n8n: card ── n8n: Ticket approval ──> reply in the thread
   (no open ports)          │ channel → client    │                                   │
                            │                     ├── GHL MCP server v2 (read-only, one key per client)
                            │                     └── Postgres: hybrid search over guides, GHL help, past tickets
                            ▼                                                        ▼
                    Postgres: tickets, drafts, limits                     solved ticket saved as a past ticket

   n8n: Knowledge sync (hourly, Google Drive guides → passages + embeddings)
   n8n: Clean-up, health check, backup check, alerts to #support-alerts
   Admin app (behind Cloudflare Access): clients, keys, limits, channels, staff, history
   Backup service: nightly encrypted dump → object storage
```

| Part | Built with | Job |
|---|---|---|
| Ticket intake | n8n | Receives client-channel messages, finds the client from the channel, claims a slot under the client's hourly limit, logs the ticket, asks the agent, posts the card |
| Agent | Python, FastAPI, OpenAI Responses API | `POST /draft`: a tool-using loop with a fixed output schema; returns a structured draft |
| Knowledge search | Postgres + pgvector | `hybrid_search`: meaning (embeddings) and exact words (full-text) merged by reciprocal rank fusion, a few passages per document at most |
| GHL access | GHL's official MCP server v2 | Sixteen allow-listed read actions, locked to the ticket's sub-account |
| Ticket approval | n8n | Verifies each button click, applies first-click-wins, posts the reply, saves the solved ticket without customer details |
| Knowledge sync | n8n | Re-processes only new or edited guides (content fingerprint); removes deleted ones |
| Admin app | FastAPI, server-rendered pages | Staff add, pause and remove clients, replace keys, change limits and channels, manage staff, read the history |
| Backups | A small Python job in its own container | Nightly `pg_dump`, encrypted with `age`, uploaded to object storage; restore tested |
| Monitoring | n8n + Healthchecks.io | Health and backup checks, alerts in Slack, an outside watcher for when the whole machine is down |

## The agent

1. The ticket arrives with the client's key, decided by n8n from the channel. The agent opens that client's sealed
   GHL key and builds a toolbox locked to that one sub-account. The model never sees or chooses a client.
2. The model sorts the ticket (forms, workflows, calendars, texts, access, other), picks checks and runs them: up
   to 8 tool calls, within a 75-second budget (n8n stops waiting at 90).
3. The tools are three knowledge searches and the GHL read checks. There are no write tools. GHL's answers are
   unwrapped so only data reaches the model; any text GHL adds to steer an AI is dropped.
4. The model returns the draft as structured output. Code then checks it against the rules (no "it's fixed", no
   promises, no other client's name, no customer details, no links or sources the searches didn't return, a reply
   short enough for Slack). A broken rule sends the draft to a person with the reason.
5. A malformed draft is retried once, then replaced by a "needs a person" draft. A step still running at the time
   limit gives a hand-over draft in time. A failed GHL check is named in the draft's evidence, so the support
   person knows what couldn't be checked.

The model is one setting (`OPENAI_MODEL`); the [accuracy test set](../evals/README.md) picks the cheapest model that
passes. Responses are sent with `store=False`.

## Data

One Postgres database, `knowledge`, next to n8n's own:

- **Knowledge:** documents and their passages with embeddings (guides, GHL help summaries, past tickets).
- **Tickets and drafts:** every ticket, its thread messages and drafts; queue cards; approvals. Text is deleted
  after 90 days; a stats table keeps numbers only.
- **Clients:** `client_map` (client key, GHL location, sealed GHL key, status, hourly limit) and `client_channels`
  (every channel a client has had; a channel belongs to one client, ever).
- **Staff:** who may approve in Slack and sign in to the admin app.

Each component signs in with its own least-privilege login: `agent_reader` (search and sealed keys only),
`n8n_support`, `kb_sync`, `admin_app`, `slack_verifier` and `backup`. Writes that need rules (limits, past tickets,
client changes, staff, click checks) go through database functions that enforce them.

## Where it runs

Docker Compose on one machine: Postgres, n8n, the agent, the admin app, the backup service and a Cloudflare tunnel.
No port is open to the internet; Slack reaches n8n through the tunnel, and the admin app sits behind Cloudflare
Access. Outside images are pinned by digest. Every change goes to a test copy first and reaches the live system only
through a scripted, checked switch (`deploy/apply.sh`); the same compose file runs on a server (`deploy/`). See
[ADR 0003](adr/0003-one-machine-behind-a-tunnel.md).

## Failure handling

| Failure | What happens |
|---|---|
| Agent down, crashed or too slow | n8n posts the ticket as "agent unavailable, needs a person" and alerts |
| All agent slots busy | The agent answers 503; n8n hands the ticket to a person |
| A GHL check fails or is rate-limited | The draft names the failed check in its evidence |
| GHL returns nothing | Treated as unknown, never as proof of nothing (a missing permission can look empty) |
| Malformed draft | One retry, then a "needs a person" draft |
| A rule broken in the draft | Sent to a person with the reason |
| Slack delivers an event twice | De-duplicated by event ID |
| Two people click Approve | The first click wins; the second sees "already handled" |
| A client goes over its hourly limit | A "limit reached" card for a person and one alert per busy stretch |
| The machine is off | The outside watcher emails; Slack retries its deliveries |

## Requirements and the tests that prove them

Every pytest test below runs on each pull request and weekly ([checks](../.github/workflows/checks.yml)); the
badge at the top of the README shows the latest result. The accuracy test set
is run by hand after any model or prompt change ([`evals/README.md`](../evals/README.md)). Database checks
(`db/checks/`) are scripts that exercise a migration's rules inside a rolled-back transaction; they run by hand
against a test database holding at least two clients.

| Rule | Proved by |
|---|---|
| **Client isolation:** the channel picks the client in code; the model can't choose another; no other client's data in a draft | `tests/agent/test_tools.py::test_model_cannot_switch_location`, `::test_toolbox_refuses_ghl_access_for_another_client`; `tests/ghl/test_access.py::test_every_location_sent_is_the_locked_one`, `::test_users_outside_this_sub_account_are_dropped`; `tests/test_api.py::test_the_agent_gets_its_clients_own_key`, `::test_unknown_client_is_rejected`; `tests/agent/test_guards.py::test_other_client_found_anywhere_in_the_draft_any_case`; test set: the `attack-other-*` tickets |
| **A channel belongs to one client, ever** | `tests/admin/test_client_sheet.py::test_change_channel_refuses_a_channel_no_longer_free`, `::test_a_channel_with_another_clients_tickets_is_refused`, `::test_lists_leave_out_another_clients_old_channel`; `tests/admin/test_add_client.py::test_save_refuses_a_channel_that_isnt_free_any_more`; database check 024 |
| **No write tools** | `tests/agent/test_tools.py::test_tool_list_has_no_write_tools`; `tests/ghl/test_access.py::test_allow_list_is_exactly_the_16_read_actions`, `::test_action_outside_allow_list_is_refused`, `::test_changed_catalog_turns_checks_off` |
| **Never says it's fixed, never promises refunds or deadlines** | `tests/agent/test_guards.py::test_guard_flags_claims_fixed_and_other_client`, `::test_promises_are_flagged`; `tests/agent/test_loop.py::test_guard_violation_forces_person`; `tests/evals/test_scoring.py::test_never_do_uses_runtime_guard` |
| **Doesn't follow instructions inside a ticket** | Test set: 24 attack tickets (injection, fake staff, encoded and foreign-language instructions); `tests/agent/test_loop.py::test_client_text_cannot_close_the_message_block`, `::test_nested_markers_cannot_re_form_after_removal`; `tests/ghl/test_access.py::test_ok_result_keeps_only_ghls_data`; `tests/agent/test_guards.py::test_link_in_the_reply_that_no_search_returned_is_flagged` |
| **Customer details stay out of replies and past tickets** | `tests/agent/test_guards.py::test_reply_repeating_a_customers_name_email_or_phone_is_flagged`, `::test_a_customer_anywhere_in_the_saved_text_keeps_it_out_of_past_tickets`; `tests/agent/test_loop.py::test_reply_repeating_a_customers_details_goes_to_a_person`; database checks 014 and 015 |
| **Staff messages in a client channel never become tickets** | `tests/test_n8n_workflows.py::test_a_staff_members_message_ends_before_any_ticket` |
| **Paused or unknown clients and over-long tickets are refused before any AI call** | `tests/test_api.py::test_a_paused_or_unknown_client_is_refused_before_any_ai_call`, `::test_too_long_ticket_is_refused_before_any_ai_call` |
| **A ticket with no text (a screenshot only) goes straight to a person** | `tests/agent/test_loop.py::test_empty_ticket_skips_model` |
| **No matching guide: the draft says so and flags a person** | `tests/agent/test_loop.py::test_no_sources_forces_person` |
| **Replies stay short (120 words at most)** | `tests/agent/test_guards.py::test_reply_over_120_words_is_too_long` |
| **Tool-call cap per ticket** | `tests/agent/test_loop.py::test_stops_after_cap` |
| **Time limit and busy refusals** | `tests/test_api.py::test_a_draft_past_the_time_limit_is_handed_over_and_its_ghl_access_closed_later`, `::test_busy_when_all_slots_are_taken`, `::test_crash_returns_500_without_details_and_frees_the_slot` |
| **Empty or failed GHL results are unknown, not proof** | `tests/ghl/test_access.py::test_empty_result_is_unknown_not_proof`, `::test_rate_limit_is_an_error_not_a_crash`, `::test_refused_permission_says_so`; `tests/agent/test_loop.py::test_failed_ghl_check_lands_in_draft` |
| **Malformed draft: one retry, then a person** | `tests/agent/test_loop.py::test_malformed_once_is_retried`, `::test_malformed_twice_gives_fallback` |
| **Signed Slack button clicks** | Database check 016 (wrong body, timestamp or secret, and stale clicks are refused); messages use n8n's Slack trigger, which checks the signing secret ([ADR 0009](adr/0009-where-slack-signatures-are-checked.md)) |
| **Duplicate events and first click wins** | Database check 004 |
| **Per-client hourly limits** | Database check 008 |
| **Ticket text deleted after 90 days** | Database check 007 |
| **Client keys sealed; only the agent opens them** | `tests/test_keys.py::test_another_private_key_cannot_open_it`; `tests/admin/test_add_client.py::test_the_key_is_sealed_before_any_database_or_agent_call`; `tests/test_compose_drift.py::test_admin_container_never_gets_the_private_key`; `tests/admin/test_clients.py::test_page_has_no_key_and_no_sealed_bytes`; database check 018 |
| **Least-privilege logins** | `tests/admin/test_auth.py::test_get_never_changes_anything`, `::test_only_the_db_module_opens_connections`; database checks 004, 007, 008, 010, 014, 015 and 018 |
| **Admin app: signed Cloudflare Access pass, staff only** | `tests/admin/test_auth.py::test_missing_pass_is_403`, `::test_expired_pass_is_403`, `::test_signed_by_another_key_is_403`, `::test_valid_pass_not_on_staff_list_is_403`, `::test_every_route_but_health_needs_sign_in_and_same_origin` |
| **Admin app: no cross-site form posts** | `tests/admin/test_auth.py::test_post_from_another_origin_is_403`, `::test_every_answer_carries_safety_headers`; `tests/admin/test_panel_browser.py::test_the_same_origin_check_still_refuses` |
| **Images pinned by digest** | `tests/test_image_pins.py::test_every_image_is_pinned_by_digest`, `::test_a_tag_without_a_digest_is_caught` |
| **Restores pick a complete backup and refuse untested versions** | `tests/test_backup.py::test_restore_picks_the_newest_complete_backup`, `::test_restore_skips_a_newer_set_that_is_missing_a_file`; `tests/test_apply_live_backup.py::test_live_images_without_its_restore_test_takes_no_backup` |
| **No keys in the repository** | The leaked-key scan over the full history on every pull request |
| **Accessible admin screens** | `tests/admin/test_screens_browser.py::test_the_accessibility_scan_finds_nothing`, `::test_tab_reaches_every_control_with_its_focus_ring_in_view` |

### Known gaps

- The database checks run by hand, not on each pull request; they need a database with migrations applied and two
  clients.
- No test checks that OpenAI calls send `store=False`, or that backups are actually encrypted (only their names).
- An OpenAI error inside the time limit is not retried by the agent's own code (the SDK's retries are untested).
- No rule covers promising a new feature.
- n8n's path from a failed agent call to the "agent unavailable" card is checked by hand, not by a test.
- Intake ignoring messages from bots, and alerting on a channel no client holds, live in its "Read message" and
  "Find client" steps; their code isn't run by a test yet.
