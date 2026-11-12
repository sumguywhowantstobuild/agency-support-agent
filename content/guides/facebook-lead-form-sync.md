# Facebook lead form leads not reaching GHL

Bluejay Funnel Co. support guide. Applies to: Facebook and Instagram Lead Ads (instant forms) connected to a GHL
sub-account.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says their Facebook ads are getting leads, but they're not in GHL. Common ways clients put it: "my
Facebook leads aren't coming in", "Ads Manager shows 20 leads but GHL has 5", "leads stopped after I changed my
password", "the new ad's leads aren't showing up".

Facebook lead forms don't live in GHL. Meta passes each new lead to GHL through the **LeadConnector** app, using the
permissions of the Facebook page admin who connected the page. For a lead to arrive, the page must be connected,
LeadConnector must have **Leads Access** to the page, and the form must be mapped in GHL. Workflows then pick the
lead up with the **Facebook Lead Form Submitted** trigger, not the Form Submitted trigger used for GHL forms.

## Check first

Before starting, confirm the client's sub-account, the Facebook page and ad, the lead form's name, and one missing
lead: their name or email and when they submitted.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Did this submission really arrive?** Search contacts for the lead's email and phone. A contact that already
   existed doesn't prove this particular submission synced: check the contact's activity for a Facebook form
   submission at the time the lead submitted, and whether the **Facebook Lead Form Submitted** workflow enrolled
   them then. Only if the submission arrived is the problem in the automation afterwards (see the guide "Workflow
   not triggering or not finishing").
2. **Does the integration show a problem?** Settings → Integrations → the Facebook card. Use **Troubleshoot** on the
   card: it flags unmapped forms and permission problems.
3. **Is the form mapped?** Settings → Integrations → **Facebook Form Field Mapping**: the form used by the ad must
   have a blue tick. A new ad often uses a new or copied form that hasn't been mapped yet. Compare the form name in
   Ads Manager with the mapped one.
4. **Does LeadConnector have Leads Access?** In Meta Business Suite: Settings → Integrations → **Leads Access** →
   **Assign CRMs**: LeadConnector must be assigned to the page.
5. **Does the person who connected the page still have admin access?** The page admin who granted the permissions
   must still be a page admin. If they left, changed their password, or the connection expired, leads stop.
6. **Do the page and ad account belong together?** The page and the ad account running the ad should be in the same
   business, and the ad must use a form from the connected page.

## Fix

**Form not mapped.** Map the form in Facebook Form Field Mapping and check the blue tick appears.

**Leads Access missing.** In Meta Business Suite → Settings → Integrations → Leads Access → Assign CRMs, select
LeadConnector and click **Assign**. Usually the client has to do this, as the page admin.

**Connection expired or the admin lost access.** Reconnect: on the Facebook card, disconnect and connect again,
signed in as a current page admin, select the page and click **Connect Page**. The client's page admin usually has
to sign in themselves; never ask for their Facebook password.

**Page or ad account mismatch.** Have the client run the ad from the connected page's business, or connect the page
the ad actually uses.

**After any fix: verify the full path.** Use Meta's **Lead Ads Testing Tool** (on developers.facebook.com) to send a
test lead from the form, then check the contact appears in GHL and the Facebook Lead Form Submitted workflow runs
the expected path.

**Then recover what was missed, where applicable.** Use **Sync Leads** (under Troubleshoot on the Facebook card) to
bring in unprocessed leads for a chosen time range. Review its summary before confirming: it covers all mapped
forms across all connected pages, not just this one. Don't promise recovery of leads from before the page was
connected. Afterwards, check which missing submissions were recovered. For any still missing, the client can export
their leads from Meta and we import them, after checking for existing contacts and which workflows the import would
set off (see the guide "Contact or tag import problems"). Count what was recovered so the reply is exact.

**Hand to a person** when the form is mapped, Leads Access is assigned, the connection is fresh, and the Lead Ads
Testing Tool still doesn't deliver a test lead to GHL. Include in the handoff: the page, the form, the test time,
and screenshots of the mapping and Leads Access screens.

## Tell the client

Keep it short and calm: why the leads didn't arrive, what we fixed, and what happened to the missed ones. Don't
blame the client, even if they changed their password or made a new form. If they need to do a step in Facebook,
say exactly what. Example:

> Thanks for flagging this. Your Facebook leads weren't arriving because [your new ad uses a new form that wasn't
> connected yet / the Facebook connection expired after a password change]. We've [connected the form / reconnected
> it with your help] and sent a test lead, which arrived straight away. We've also brought in the [number] leads
> from [date] to today, so they're now in your account. Let us know if anything else looks off.
