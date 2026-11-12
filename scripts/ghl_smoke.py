"""Live smoke test of read-only GHL access for one client (run it again at go-live).

Runs inside the agent container so the token never leaves it:
  docker compose -f compose.dev.yaml exec -T agent python - <location id> < scripts/ghl_smoke.py

Asks each of the 15 questions once and prints only the shape of each answer (status, keys, counts), never the
data itself: contact records stay out of the terminal. Also checks the catalog and that a write action is refused.
"""

import sys
import time

from support_agent.config import get_settings
from support_agent.ghl.access import ALLOWED_ACTIONS, CATALOG, ActionNotAllowed, GhlAccess
from support_agent.ghl.keystore import get_keystore


def shape(data) -> str:
    if isinstance(data, dict):
        return "{" + ", ".join(f"{k}: {len(v) if isinstance(v, (list, dict)) else type(v).__name__}"
                               for k, v in data.items()) + "}"
    return type(data).__name__


def check(name: str, call) -> object:
    started = time.monotonic()
    r = call()
    took = time.monotonic() - started
    line = f"{name:18} {r.status:6} {took:5.1f}s  {shape(r.data) if r.data is not None else ''}"
    print(line + (f"  note: {r.note}" if r.note else ""), flush=True)
    return r


location_id = sys.argv[1]
failures = 0
token = get_keystore().token_for(location_id)  # the client's locked key from the database
if token is None:
    sys.exit(f"no active client with a key for {location_id}")
with GhlAccess.for_location(location_id, token, get_settings()) as ghl:
    changed = CATALOG.changed_actions(ghl.session)
    print(f"catalog check: {len(ALLOWED_ACTIONS) - len(changed)} of {len(ALLOWED_ACTIONS)} still read-only"
          + (f"; CHANGED: {sorted(changed)}" if changed else ""), flush=True)
    failures += bool(changed)

    results = {name: check(name, call) for name, call in [
        ("account", ghl.account), ("calendars", ghl.calendars), ("workflows", ghl.workflows), ("forms", ghl.forms),
        ("pipelines", ghl.pipelines), ("tags", ghl.tags), ("users", ghl.users)]}

    calendar_id = ((results["calendars"].data or {}).get("calendars") or [{}])[0].get("id")
    form_id = ((results["forms"].data or {}).get("forms") or [{}])[0].get("id")
    if calendar_id:
        results["free_slots"] = check("free_slots", lambda: ghl.free_slots(calendar_id))
        results["appointments"] = check("appointments", lambda: ghl.appointments(calendar_id))
    if form_id:
        results["form_submissions"] = check("form_submissions", lambda: ghl.form_submissions(form_id, limit=1))

    submission = ((results.get("form_submissions") and results["form_submissions"].data or {})
                  .get("submissions") or [{}])[0]
    contact_id = submission.get("contactId")
    if contact_id:
        results["find_contact"] = check("find_contact", lambda: ghl.find_contact(submission.get("name") or "test"))
        results["contact_details"] = check("contact_details", lambda: ghl.contact_details(contact_id))
        results["contact_tasks"] = check("contact_tasks", lambda: ghl.contact_tasks(contact_id))
        results["recent_messages"] = check("recent_messages", lambda: ghl.recent_messages(contact_id))
        results["opportunities"] = check("opportunities", lambda: ghl.opportunities(contact_id))
    else:
        print("no test contact found: find_contact, contact_details, recent_messages, opportunities skipped")

    try:
        ghl.execute("update-contact", {})
        print("WRITE NOT REFUSED", flush=True)
        failures += 1
    except ActionNotAllowed:
        print("update-contact      refused by our code (as expected)", flush=True)

    errors = [name for name, r in results.items() if r.status == "error"]
    failures += len(errors)
    print(f"\n{len(results)} checks run, {len(errors)} errors{': ' + ', '.join(errors) if errors else ''}")

sys.exit(1 if failures else 0)
