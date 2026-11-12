# Workflow not triggering or not finishing

Bluejay Funnel Co. support guide. Applies to: any GHL workflow (Automation → Workflows).
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says an automation isn't working. Common ways clients put it: "my automation stopped", "nobody got the
follow-up text", "the tag was added but nothing happened", "it worked last week", "only some people get it".

A workflow runs in two stages. First the **trigger** decides whether a contact enters (the event, plus every
trigger filter). Then the contact moves through the **actions**, where waits, If/Else branches, goals and workflow
settings decide how far they get. "Not working" can mean the contact never entered, or entered and stopped
part-way.

## Check first

Before starting, confirm the client's sub-account, the workflow name, and one affected contact: who it was and
roughly when the event happened (form sent, tag added, appointment booked).

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is the workflow published?** Automation → Workflows. A workflow in **Draft** does nothing.
2. **Did the event actually happen?** Check the contact's record: was the tag really added, the form really
   submitted, the appointment really booked, at the time the client says? If not, the workflow is fine and the
   problem is earlier (for forms, see the guide "Form leads not reaching the pipeline").
3. **What do the trigger stats say?** In the workflow builder, open **Stats View** and select the trigger.
   **Attempted** = contacts the trigger checked, **Matched** = met every condition, **Unmatched** = didn't qualify.
   Click a number to see each contact and the **reason for unmatch** (for example a value mismatch or a missing
   field). Stats only cover the last 30 days.
   - Event within the last 30 days but the contact isn't listed: clear the stats filters and check the right
     trigger. Still no attempt: see the fix "Wrong trigger or wrong item".
   - Event older than 30 days: the stats can't tell you; use Enrollment History and the contact's record instead.
   - Listed as unmatched: see the fix "Trigger filter doesn't match".
4. **Was the contact already in the workflow?** If they entered before, check **Allow Re-entry** in the workflow
   settings and which trigger the workflow uses. See the fix "Contact can't re-enter".
5. **Did the contact enter but stop?** Open **Enrollment History**, find the contact, then **Execution Logs** to
   follow their path. Look for: a Wait still pending, an If/Else branch that sent them another way, a goal or exit
   that ended the workflow, a step paused by the **Time Window** setting, the workflow ended by **Stop on
   Response** because they replied, or an action with an error.
6. **Did an action say it ran, but the change isn't there?** For example "Add Tag" shows as executed but the
   contact has no tag. Two possible causes: another workflow or a person changed it back afterwards (check the
   contact's activity), or two updates happened in the same second (a race condition). See the fix "Two steps at
   the same moment".

## Fix

**Workflow in Draft.** Publish the workflow, then check Enrollment History for the affected contacts. Don't assume
publishing will recover events missed while it was in Draft. Add contacts who still need the workflow only after
reviewing what it will send (see "Then recover the missed contacts" below).

**Wrong trigger or wrong item.** Check the trigger type matches the real event (for example Form Submitted for a
form, not Survey Submitted; Contact Tag for a tag being added) and that it watches the right item: the live form,
the right calendar, the right pipeline. A copied form, calendar or pipeline is a new item, so a trigger still set to
the old one never fires.

**Trigger filter doesn't match.** Use the reason for unmatch in Trigger Stats. Correct the filter so it matches the
value the contact really has, for example the tag's exact name. The Contact Tag trigger with no Added/Removed
filter fires on both, which can enrol people when a tag is removed.

**Contact can't re-enter.** With **Allow Re-entry** off, a contact who has been through the workflow once won't
enter again. With it on, they can only enter again after they finished the workflow or were removed from it; a
contact still active in it won't re-enter. Whether repeat entries are wanted depends on what the workflow does;
agree it with the client before turning it on.

Exception: workflows triggered by appointments or invoices enter once per appointment or invoice, whatever the
Allow Re-entry setting, and a new appointment can enter even while the contact is still active in the workflow.
For those, look at the appointment or invoice itself, not the re-entry setting.

**Contact entered but stopped part-way.** Fix whatever stopped them: the branch condition, the wait, or the goal.
Two settings stop or pause contacts on purpose, so check them before calling it a fault:
- **Time Window** pauses messages outside the set hours and resumes them at the next allowed time. Explain this
  to the client if a message was simply sent later.
- **Stop on Response** ends the workflow for a contact who replied to one of its messages. Usually intended.

If an action failed, the fix depends on the action. A text that failed: see the guide "SMS not sending".

**Two steps at the same moment.** First rule out a later change: if another workflow or a person removed the tag
or changed the value afterwards, fix that instead. If not, it may be a race condition: when two changes happen in
the same second (for example a contact is added to a workflow and tagged at once), one can show as executed without
taking effect, or a trigger filter can check a value before it's set. HighLevel's advice is a **Wait** of 1 minute
before the step that depends on the earlier change.

**After any fix: verify the full path.** Use a fresh test contact (details not in the account yet) and cause the
real event. Check that Trigger Stats shows the contact as matched, they appear in Enrollment History, and the
Execution Logs show the expected path running (branches and goals can rightly skip steps). If the problem was
about re-entry, also test with an existing contact.

**Then recover the missed contacts, where applicable.** Find the people whose event happened while it was broken
from the source records: form submissions, appointments, or the contacts' activity for those dates. Trigger Stats
alone isn't enough: it only covers 30 days. Before adding them to the workflow, check what it sends: a "see you
tomorrow" reminder from last week now reads wrong, and re-enrolling can re-send messages people already got.
Add only those who still need it, and count them so the reply to the client is exact.

**Hand to a person** when the workflow is published, the trigger type and item are right, the event clearly
happened within the last 30 days, but Trigger Stats (filters cleared) shows no attempt for the contact; or when an
action keeps showing as executed without effect, nothing else changed it back, and adding a Wait didn't help. Include in the handoff: the contact, the workflow, the event time, and screenshots
of Trigger Stats and the Execution Logs.

## Tell the client

Keep it short and calm: what was wrong in plain words, what we did, and who was affected. Don't blame the client,
even if they edited the workflow. Only mention re-sent messages or recovered contacts once you've checked them.
Example:

> Thanks for letting us know. We found why the follow-up didn't go out: [the automation was paused after a recent
> edit / it was still watching an older copy of your form / it was only set to run once per person]. We've fixed it
> and tested it with a new contact, and it ran as expected. We've also sent the follow-up to the [number] people it
> missed between [date] and today. Let us know if anything else looks off.
