# Pipeline stage automation not running

Bluejay Funnel Co. support guide. Applies to: GHL workflows that should run when an opportunity is created, moves
stage, or changes status (Open, Won, Lost, Abandoned).
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says moving a deal in the pipeline doesn't do what it should. Common ways clients put it: "when I move a
lead to Booked nothing happens", "the won email didn't go out", "it works for some leads but not others", "it ran
twice", "moving the card back started it again".

GHL has several opportunity triggers, and each fires on something different: **Pipeline Stage Changed** (the
opportunity moves to another stage), **Opportunity Status Changed** (Open, Won, Lost or Abandoned changes),
**Opportunity Changed** (any change, such as assignment, value or custom fields) and **Opportunity Created**. They
are independent: a workflow only runs for the events its trigger watches. These triggers only react to changes as
they happen; they don't look back at earlier moves.

## Check first

Before starting, confirm the client's sub-account, the pipeline and stage (or status), the workflow, and one
opportunity that didn't trigger it: the contact and roughly when it was moved.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is the workflow published, and did the move really happen?** Check the workflow isn't in Draft, and the
   opportunity's history shows the move at the time the client says. For the general workflow checks (Trigger
   Stats, Enrollment History, Execution Logs), see the guide "Workflow not triggering or not finishing".
2. **Does the trigger match the event?** Moving a card between stages fires Pipeline Stage Changed, not Opportunity
   Status Changed; marking it Won fires Opportunity Status Changed. If a new opportunity created directly into this
   stage should start the workflow, that's the **Opportunity Created** trigger; don't rely on Pipeline Stage Changed
   for it.
3. **Do the trigger filters match?** Check **In Pipeline** and **Pipeline Stage** point at the pipeline and stage
   the client uses now (a copied pipeline is a different pipeline), and any extra filters such as Assigned To, Tag,
   Lead Value, Lost Reason or Status.
4. **Is "Allow multiple Opportunities" on?** In the workflow settings, this lets one contact run the workflow once
   per opportunity. It's on by default for new workflows but off for older ones, so a contact's second opportunity
   may not start its own run.
5. **Did it run more than once, or again?** Look at the execution history before deciding why. Moving an
   opportunity back to an earlier stage and forward again can fire the trigger again when the filters match, but a
   matching event doesn't necessarily restart a run that's still active: runs are kept per contact and per
   opportunity, depending on the enrollment settings. Another workflow that moves stages can also start this one.

## Fix

**Wrong trigger for the event.** Use the trigger that matches what the client does: Pipeline Stage Changed for
moving cards, Opportunity Status Changed for Won/Lost. If creation directly into this stage should start the
workflow, use Opportunity Created and check its filters point at the intended pipeline (its filter list isn't the
same as Pipeline Stage Changed's, so check what it offers). If later moves should also start it, add Pipeline Stage
Changed as well, and test both paths for unwanted repeated actions.

**Filters out of date.** Point the filters at the live pipeline and stage, and remove filters the client doesn't
need.

**Second opportunity didn't run.** Turn on **Allow multiple Opportunities** if each opportunity should get its own
run. Check with the client first: it changes how often contacts with several deals get messages.

**Ran twice or again on moving back.** Check the enrollment settings and execution history first: it may be a
second trigger, a second opportunity, a move back and forward, or another workflow. If repeats for the same deal
are unwanted, add a check against something stored on that opportunity (for example an opportunity field set on
the first run). A contact-level tag would also block the contact's other deals. Agree with the client what should
happen when a card moves back. If two workflows are triggering each other, change one so they don't loop.

**After any fix: verify the full path.** Move a test opportunity (on a test contact with your own details) into the
stage the way the client does, and check the workflow runs the expected path in Execution Logs. If the problem was
about second opportunities or moving back, test those cases too.

**Then recover what was missed, where applicable.** The trigger won't look back at earlier moves. Find the
opportunities that entered the stage while it was broken from their movement history: filtering the pipeline by
current stage misses deals that entered and then moved on. Check what the workflow sends and that you're enrolling
the contact for the right opportunity, then add only those who still need it by hand.

**Hand to a person** when the move is in the opportunity's history, the workflow is published, the trigger and
filters match, and Trigger Stats shows no attempt within the last 30 days. Include in the handoff: the opportunity,
the move time, the workflow, and screenshots of the trigger settings.

## Tell the client

Keep it short and calm: what wasn't connected in plain words, what we changed, and who was affected. Don't blame
the client for moving cards. Example:

> Thanks for flagging this. Moving deals to [stage] wasn't starting the follow-up because [the automation was
> watching your old pipeline / it only ran once per person, so second deals were skipped]. We've fixed it and tested
> it with a test deal, and it ran as expected. [Number] deals moved to [stage] while it wasn't working; we've sent
> the follow-up to the [number] that still needed it. Let us know if anything else looks off.
