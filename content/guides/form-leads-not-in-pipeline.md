# Form leads not reaching the pipeline

Bluejay Funnel Co. support guide. Applies to: GHL forms on funnels, websites or embedded on the client's own site.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says people are filling in their form, but no new opportunities show up in the pipeline. Common ways
clients put it: "leads are missing", "my form stopped working", "nothing is coming into my pipeline", "I got the
email notification but the lead isn't in the pipeline".

A GHL form does not create an opportunity by itself. A workflow does: trigger **Form Submitted** (filtered to that
form), then an opportunity action (**Create Opportunity**, or the older **Create/Update Opportunity**) with the
pipeline and stage. If form leads stop reaching the pipeline, the break is in one of four places: the form
submission, the workflow, the opportunity action, or what the client can see in the pipeline.

## Check first

Before starting, confirm the client's sub-account, the live page URL, and one affected submission: the person's
email and roughly when they submitted. That gives you one specific case to trace from form to pipeline.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Did the form submission arrive?** Sites → Forms → Submissions (also inside the form's builder), filtered to
   the form and the dates the client mentions. No submissions: see the fix "No form submissions arriving".
2. **Is the workflow watching the right form?** Compare the form on the live page with the form in the workflow's
   Form Submitted trigger. A copied or rebuilt form is a new form, so a workflow still set to the old one never
   fires.
3. **Is the workflow published?** Automation → Workflows. A workflow in **Draft** does nothing. Recently edited
   workflows are often left in Draft by accident.
4. **Did the contact enter the workflow?** Open the workflow → **Enrollment History** and find the contact. Not
   enrolled: see the fix "Contact did not enter the workflow".
5. **Did the contact reach the opportunity action?** Enrollment alone isn't enough. Open **Execution Logs** for the
   contact and follow their path: waits still pending, If/Else branches that sent them another way, goals or exits
   that ended the workflow early, and whether their path has an opportunity action at all. If the action ran, check
   its result.
6. **Was the opportunity created or changed somewhere else?** Search Opportunities for the contact's name or email
   with owner, status and date filters cleared, and include Won, Lost and Abandoned. It may be in another pipeline
   or stage, or an existing opportunity may have been used instead of a new one (see the fix "Opportunity action
   did not create a new opportunity").
7. **Can the client see it?** If the opportunity exists but the client can't see it: check the filters on their
   pipeline view (a recent "created date" filter hides older opportunities), and whether their user has **Only
   Assigned Data** turned on (Settings → My Staff → edit the user → Roles & Permissions), which shows them only
   opportunities assigned to them.

## Fix

**No form submissions arriving.** Open the live page and submit a test with fresh contact details (a new email
and phone that aren't in the account yet). If it fails, check the form is still on the page (a page edit can
remove it), the embed code on the client's own site points at the current form, and nothing on the page (a
script, a cookie banner) blocks the form. If the test arrives but real leads don't, ask the client where the
leads came from: it may be a different form or a Facebook lead form (see the guide "Facebook lead form sync").

**Workflow in Draft or watching the wrong form.** Set the Form Submitted trigger to the live form, then
**Publish** the workflow.

**Contact did not enter the workflow.** Check every trigger filter (the form, plus any extra filters such as
tags). If the person submitted before, check the workflow settings: **Allow Re-entry** only lets a contact enrol
again after they have finished the workflow or been removed from it. A contact who is still active in the
workflow can't re-enter from another form submission, even with Allow Re-entry on.

**Contact entered but never reached the opportunity action.** Fix the path in the workflow: the branch condition,
the wait, or the goal or exit that stopped them. If their path has no opportunity action, add one in the right
place.

**Opportunity action did not create a new opportunity.** Check which opportunity action the workflow uses. The
older Create/Update Opportunity action can update an existing opportunity instead of creating one. With Create
Opportunity, if duplicate opportunities are not allowed and the contact already has one, no new opportunity is
created, and the existing one is not updated either. Update Opportunity never creates one. Look at the existing
opportunity and the execution details before changing any duplicate setting: whether a repeat enquiry should
reuse the old opportunity or create a new one depends on the client's sales process, so agree it with them.

**Opportunity exists but the client can't see it.** Clear or widen the filters on their pipeline view and show
them how to save a view that includes new opportunities. If their user has Only Assigned Data on, either assign
the form's opportunities to them in the opportunity action, or ask the agency owner whether that user should see
all opportunities. Don't change permissions without that agreement.

**After any fix: verify the full path.** Submit a test from the live page with fresh contact details, then check
each step: the submission arrives, the contact is enrolled, the opportunity action runs, and the opportunity
appears in the right pipeline and stage, visible to the client. If the problem was with repeat submissions
(re-entry or duplicate opportunities), also test with an existing contact and check the result matches what was
agreed with the client.

**Then recover the missed leads.** Find everyone who submitted while it was broken (form submissions for those
dates). For each one, check whether they already have an opportunity before adding one, so nobody ends up with
two. Bulk "Add to workflow" also works, but first check what else that workflow does: re-enrolling can re-send
emails or texts to people who already got them. Count what you recovered so the reply to the client is exact.

**Hand to a person** when the opportunity action was reached and appears successful, but the opportunity can't be
found after checking every pipeline, status, filter and permission. Include in the handoff: the contact, the
workflow, the submission time, and what the execution log shows.

## Tell the client

Keep it short and calm: what was wrong in plain words, what we did, and what happened to the leads they missed.
Don't blame the client, even if they changed the form. Only mention recovered leads once you've counted and
checked them. Example:

> Thanks for flagging this. We found the cause: [the automation that moves form leads into your pipeline was
> switched off after a recent edit / was still connected to an older copy of your form]. We've fixed it and
> tested it with a new submission, which went straight into your pipeline. We've also recovered the [number]
> enquiries that came in between [date] and today and confirmed they're now in your pipeline. Let us know if
> anything else looks off.
