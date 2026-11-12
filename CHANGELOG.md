# Changelog

Notable changes to the system, newest first; how a change reaches a running system is in `docs/SETUP.md` section 12.
The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[semantic versioning](https://semver.org/).

## [1.8.0] - 2026-11-12

### Added
- **Change channel:** staff can move a client to another Slack channel from the client's side panel (Change channel →
  pick a channel → Move client), for example after the client moves to a new Slack workspace. The list offers only
  channels the bot is in that no client holds; the channel's name comes from Slack, never from the form. The move shows
  in the client's history and on History.

### Changed
- **Migration 024:** a ticket now belongs to its client by the client's key and keeps the channel it was written in, so
  a move doesn't rewrite old threads. A new table, `client_channels`, records every channel a client has had: a channel
  belongs to one client, ever (moving back to a client's own old channel is fine). Add client and Change channel lock
  the channel, so two staff claiming one channel at once wait for each other instead of failing.

**Upgrading:** stop the admin app (`docker compose -f compose.dev.yaml stop admin`), apply migration 024, then put the
1.8.0 image live (it starts the admin app again); an Add client under way while 024 commits could leave its channel
unrecorded. Before moving a client, approve or take over its waiting drafts: cards from before a move can't be clicked
afterwards (SETUP section 9). Rolling back to 1.7.1 is the previous image only; migration 024 stays.

## [1.7.1] - 2026-11-09

### Fixed
- **Sign-in tolerates small clock differences.** The admin app compared Cloudflare's signed pass with its own clock to
  the second, so a host clock a few seconds slow made a fresh pass look issued in the future and the first sign-in
  showed "Not signed in". The check now allows up to a minute either way; a pass therefore works up to a minute past
  its expiry. The staff list is still read on every request.

## [1.7.0] - 2026-11-06

### Added
- **Sign out** from the staff member's own name (side menu on a computer, the top bar's avatar on a phone). It goes to
  Cloudflare's sign-out address (`/cdn-cgi/access/logout`), which ends the Cloudflare Access session; on a shared
  computer, also sign out of the account used to sign in to Access. It shows only behind a real Cloudflare team.
- **Restore a backup by its time:** `deploy/restore.sh staged|server --at <time>` restores exactly that backup into a
  new, empty machine (SETUP section 11); without it the newest complete backup is used.

### Changed
- **Every live data change takes a fresh backup first:** `deploy/apply.sh live migration` and `live workflow` now do,
  as `live images` already did. A failed backup stops the change with "nothing changed", and the backup's time is
  printed for use with `--at`.
- `live workflow` keeps its own export file from the checks to the import, so a parallel run can't swap in a workflow
  that didn't pass. The menu's test step runs 4 tests at once instead of 8.

## [1.6.0] - 2026-11-06

### Added
- **Slack channel names** instead of IDs on Clients, the cards and the client panel. The admin app reads the channels
  the bot is in a minute after it starts and then hourly (the `groups:read` permission it already has); Add client
  stores the chosen channel's name. Migration 023 adds the stored name and a function that may set only that.

### Changed
- The chart's line colours are green, blue, plum and pink, which stay distinct with red-green colour blindness.

### Fixed
- Deactivate or Reactivate in a panel no longer discards unsaved typing in the person's details.
- On a phone, the current tab no longer shifts the tab bar when the page changes.
- An empty Clients page shows one Add client button, not two.

Rolling back to 1.5.2 is the previous image only; migration 023 stays.

## [1.5.2] - 2026-11-06

### Security
- **A link can no longer show a change message or an Undo.** The "Deactivated …" message and its Undo were drawn for
  any address that asked for them, so a crafted link (`/staff/7?done=deactivated`) could offer an Undo that restores a
  deactivated person's access. They now appear only after a request one of the app's own pages made.

## [1.5.1] - 2026-11-06

### Fixed
- **Staff emails show as text behind Cloudflare.** Cloudflare's email obfuscation replaced addresses with
  "[email protected]", and its restoring script can't run under the app's Content Security Policy. Each address now
  carries Cloudflare's opt-out marker, and tests fail on any unmarked address.

## [1.5.0] - 2026-11-05

### Changed
- **Staff details and Add staff open as side panels** over the Staff list, as clients do over Clients: links, reloads,
  Back and Forward, Escape and (on a phone) a drag all behave the same way. Saving keeps the panel open and updates the
  row; a refused save keeps what was typed. Deactivate and Reactivate offer Undo (never on yourself). Every form still
  works without the script; both screens share one panel script.
- Includes the changes listed under 1.4.2, which was not released on its own.

## [1.4.2] - 2026-11-05

Not released on its own; shipped in 1.5.0.

### Changed
- The side menu no longer has the "This hour" list (one database read fewer per page); Needs you, the cards and the
  chart still show who is close to a limit.
- Panel and list animations are slower (a panel rises in 560 ms).

### Fixed
- On a phone, Staff details and Add staff have room at their top, and a swipe there scrolls the page.
- On a desktop, opening a panel no longer shifts the page sideways.
- Cards and List switch at once, with their bars already filled.

## [1.4.1] - 2026-11-05

### Fixed
- Phone fixes: the current tab's outline is no longer cut off; the browser's tap flash is off and long presses don't
  select card names; the tab bar stays put between pages; view transitions, which flickered the bar on real phones,
  are replaced by a short rise-in of the new results.
- Every panel's top has more room; the Add button sits beside the page title on Clients and Staff at every width.
- On a desktop, each page keeps room for its scroll bar, so the side menu's text no longer flickers.

## [1.4.0] - 2026-11-04

### Added
- **The admin app's new look:** a collapsible side menu (a slim bar on phones), dark and light themes, the Drafts today
  chart on Clients, and History grouped by day in the agency's time zone (`TIME_ZONE`, default America/New_York).
  Migration 022 adds a read-only view for the chart.

### Changed
- The style files now live in this repository; static files are served under content-hashed addresses.
- Running the full test suite needs Node 22+.

### Fixed
- An uncaught error now answers with the security headers.

Rolling back to 1.3.0 is the previous image only; migration 022 stays. Don't restore a backup to undo a release that
only changed how things look: restoring wipes every ticket since the backup.

## [1.3.0] - 2026-11-03

### Added
- **A client and Add client open in a side panel** over Clients, keeping the list's search, sort, view and scroll. The
  address changes to the client's, so links, reloads and new tabs open the same panel; Back, X, Escape and a click on
  the dimmed area close it (unless something typed hasn't been sent). Saves and refusals stay in the panel; a refused
  form keeps what was typed except the token. On phones the panel drags down to close.
- Browser tests in Chromium, WebKit and Firefox (development only).

### Fixed
- After a refusal about the channel, Add client could pre-select the first channel in the list; a blank "Choose a
  channel" option now forces a choice.
- A token HighLevel can't confirm no longer says only "Can't reach HighLevel"; it says to check the token and location
  ID, or try again.

## [1.2.1] - 2026-11-02

### Fixed
- The status icon on Clients no longer blanks after a limit change or Undo (the icon set now travels inside each page,
  so the browser never re-fetches the icon file under `no-store`).

### Changed
- Tests run in parallel with `pytest-xdist` (development only).

## [1.2.0] - 2026-11-02

### Added
- **Needs you** on Clients: the clients at their limit, then the paused ones, each with Raise limit or Review; beside it
  "Drafts this hour" and "Paused" counts.
- Motion on Clients (rolling totals, growing bars, gliding sort); everything happens at once with reduced motion or
  without the script.
- Screen checks with worst-case data and axe-core accessibility scans in three browsers (development only).

### Changed
- Sort and Cards / List change in place without a page load, so focus stays on the control (WCAG 2.2, 3.2.2).
- Screens say "token", not "key", for the HighLevel credential.
- Both Dockerfiles start with `# check=error=true`, so a build check warning fails the build.
- The accuracy fingerprint in `deploy/apply.sh` ignores the one-time token move tool (`ghl/move_tokens.py`).

### Fixed
- An Undo left open from before a client was removed and added back now changes nothing.

### Known limit
- View transitions are off in WebKit-based browsers (Playwright's WebKit crashed during one); changes there happen at
  once.

## [1.1.0] - 2026-10-31

### Added
- **The admin app** (`support_agent.admin`, its own `admin` container on the agent's image): Clients (status, limits
  changed in place), Add client with a connection test, the client sheet (pause, resume, replace token, remove), Staff
  and History. Reached only through the tunnel behind Cloudflare Access; the app checks Cloudflare's signed pass and the
  staff list on every request. Setup: SETUP section 14.
- **Staff list in the database** (migration 017, login `admin_app`): who may sign in and approve drafts, changed only in
  the app and logged. The intake and approval workflows read it on every message and click.
- **Client status and history** (migrations 018 to 021): a paused client's messages get a "Paused: answer by hand" card
  and never reach the AI; a removed client's channel raises the unknown-channel alert.

### Changed
- **HighLevel tokens move from `.env` into the database, encrypted** (`client_keys`): the admin app seals each token
  with `GHL_KEY_PUBLIC`, and only the agent can open it with `GHL_KEY_PRIVATE`. `GHL_TOKENS` is gone from `.env` and
  both compose files.
- `deploy/apply.sh copy agent` / `live agent` start the admin app on the same tested image as the agent.
- `deploy/restore.sh` sets the `admin_app` password too; `deploy/new-env.sh` makes the key pair and the admin app's
  settings and no longer asks for HighLevel tokens.
- Libraries: FastAPI 0.162, httpx2 2.16, OpenAI 3.41, Pydantic 2.14, pydantic-settings 2.16, pytest 9.2.

### Fixed
- A removed client can be added back (Add client used to leave its channel out of the list).

### Security
- **cloudflared 2026.10.5** (was 2026.9.3): earlier versions trust eight emSign intermediate certificates with weak
  private keys, and Cloudflare asked every tunnel to run 2026.10.2 or later.

**Upgrading from 1.0.0:** add `groups:read` to the Slack app and reinstall it; put `SLACK_BOT_TOKEN`, the key pair,
`ADMIN_DB_PASSWORD` and the Cloudflare Access settings in `.env`; apply migrations 017 to 021; add the first staff member;
move the HighLevel tokens with `support_agent.ghl.move_tokens`, then delete `GHL_TOKENS` (SETUP section 14). Rolling back
needs the backup taken before the change, and `GHL_TOKENS` back in `.env` and both compose files.

## [1.0.0] - 2026-10-23

The first release. `GET /health` reports the running version.

### Added
- **Test copy routine** (`deploy/apply.sh`, SETUP section 12): every change runs on a test copy first and reaches live
  only in the exact version the copy passed, with a numbered menu, workflow checks (`deploy/check_workflow.py`) and a
  warning when Docker changed since the last pass. A copy refresh starts from a fresh backup of live.
- **New software versions go through the copy too** (`copy images` / `live images`): images pinned by digest, indexes
  rebuilt when the database's sorting rules change, a fresh backup before the switch.
- **Health monitoring** (migration 012, `n8n/health-check.json`, SETUP section 13): the agent, the public address and
  OpenAI checked every 5 minutes, alerts only on a change (down after 2 failures in a row), and Healthchecks.io pings
  with Slack and email alerts when the system goes silent.
- **Approval report** (migration 013, `approval_report()`): what staff did with the drafts per client, numbers only;
  the `test_closed` status keeps test traffic out of the counts.
- **"Looked up" line** on each card: every search and HighLevel check the agent made, in order, with its result (names
  only).
- **Tampered-source warning:** a card says "A source may have been tampered with" when a search result contains a line
  aimed at the AI (SETUP section 3 says how to clean it up).
- The agent answers `429 ai spending limit reached (<code>)` when OpenAI refuses for money, and the card says so.
- Table descriptions in the database (migration 011).

### Changed
- The agent's 75-second limit is a hard limit: at 75 s staff get a "ran out of time" hand-over draft at once.
- `live agent` starts the agent with `--no-deps`, so it never moves the database to a new image.
- The agent's reply says "We can only look into your own account." when a ticket asks about another business's
  account.

### Removed
- **The agent's Slack click check** (`POST /slack/verify`) and its `SLACK_SIGNING_SECRET` setting; the database checks
  clicks now (below). An old `.env` line is ignored and can be deleted.

### Fixed
- **Buttons work while the agent is down** (migration 016): the database checks Slack's signature
  (`verify_slack_request()`), so Take over works on an "agent unavailable" card. Store the signing secret in the
  database after applying 016 (SETUP section 9).
- The tunnel always connects over IPv4 (`TUNNEL_EDGE_IP_VERSION: "4"`); on `auto` it could pick unreachable IPv6
  addresses after a restart.
- `deploy/apply.sh` keeps track of the agent image with Docker Desktop 4.95's image store, which gives every build a
  new ID.
- `deploy/new-env.sh` refuses capitals in key answers whatever the shell's locale (`LC_ALL=C`).

### Security
- **Past tickets are kept free of customer details and expire after 12 months** (migrations 014 and 015): a draft
  that names a customer HighLevel showed the agent is not saved as a past ticket, emails and phone numbers are
  refused, and the daily clean-up deletes past tickets older than 12 months. A staff member's edited reply isn't
  checked for names, so staff never type a customer's name into it.
- Search results are treated as reference, never as instructions; client text can't fake the end of its own block.
- Failed statements from n8n's database login are not written to the database log with their values.
- n8n's public API keys are off (`N8N_PUBLIC_API_DISABLED`), with other unused features; a test keeps them off.
- A Cloudflare rate limit on the public health address.
- Every image in both compose files and both Dockerfiles is pinned by digest (n8n 2.43.6, pgvector 0.8.7 on Debian
  trixie), and `deploy/scan.sh` runs a monthly Trivy scan.
- A test copy refresh deletes the Healthchecks.io ping address in the copy.
