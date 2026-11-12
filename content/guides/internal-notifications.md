# Internal notifications not arriving

Bluejay Funnel Co. support guide. Applies to: notifications to the client's own team from GHL workflows (the
Internal Notification action): email, SMS, in-app and WhatsApp.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client or their team aren't being told about new leads, bookings or messages. Common ways clients put it: "I'm
not getting new lead alerts", "my team doesn't get notified", "I don't get a text when a customer replies", "only
some people get the notification".

GHL tells the team through workflows: a trigger (new form, new booking, customer replied) and an **Internal
Notification** action that sends an email, SMS, in-app notification or WhatsApp message to the chosen team members.
GHL doesn't text staff about incoming messages by itself; that needs such a workflow. A notification can be
skipped, fail on one channel, or be sent but not delivered.

## Check first

Before starting, confirm the client's sub-account, which notification is missing (for what event, by which channel)
and for whom, and one example: the event and roughly when it happened.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is there a workflow that sends this notification?** Find the workflow with the right trigger and an Internal
   Notification action. If there isn't one, the notification was never set up. If there is, check it ran for the
   example (see the guide "Workflow not triggering or not finishing").
2. **Who is it sent to?** Check the recipient setting: **Assigned User**, specific users, a user role, or extra
   email addresses (CC/BCC). With Assigned User, the step is skipped when the contact has no assigned user.
3. **Is the channel set up for that person?** An SMS notification needs a valid phone number on the user; a
   WhatsApp notification needs WhatsApp connected. If the channel isn't set up, that channel fails.
4. **Could DND be blocking it?** HighLevel notes that a recipient marked DND can have notifications show as sent in
   the workflow but not be delivered. Its advice is unclear about which record to change, so don't toggle DND to
   test it: note the intended recipient and the affected channel, and hand it to a person if this looks like the
   cause.
5. **Was the notification sent but not received?** An executed step doesn't prove delivery. Email notifications can
   land in spam, especially when sent from the client's domain to their own inbox; SMS notifications are texts and
   need a number that can send texts.
6. **Too many at once?** By default each user can receive up to 500 email, SMS or in-app notifications every 5
   minutes; a bulk action can hit that. Extra ones are queued and may arrive late or be dropped.

## Fix

**No workflow for it.** Build one with the client: the right trigger (for example **Customer Replied** for incoming
messages, with **Reply Channel** set if only one channel should alert) and an Internal Notification to the people
they name. Agree who gets what, so nobody is flooded.

**Wrong or missing recipient.** Set the recipients the client wants. If they rely on Assigned User, make sure
contacts get assigned (or add a fallback recipient).

**Channel not set up for the user.** Add the user's phone number for SMS, or connect WhatsApp, or switch to a
channel they have.

**Sent but not received.** For email, check spam and see the guide "Emails going to spam or not delivered". For SMS,
see the guide "SMS not sending".

**Too many at once.** Spread out bulk actions, or send one summary instead of one notification per contact.

**After any fix: verify the full path.** Cause the real event with a test contact (your own details) and check
each intended person receives the notification on each channel, and that people who shouldn't get it don't.

**Then recover what was missed, where applicable.** List the leads, bookings or messages that came in while the team
wasn't notified, and check whether any are still waiting for a reply. That list matters more to the client than the
missed notifications.

**Hand to a person** when the workflow ran and the recipient and channel are set up, but the notification keeps
failing on the intended channel, or when DND may be blocking it. Include in the handoff: the workflow, the event
time, the recipient, the channel, and the Execution Logs for the step.

## Tell the client

Keep it short and calm: why the alerts didn't arrive, what we changed, and which leads or messages may still need a
reply. Don't blame the client's team. Example:

> Thanks for flagging this. You weren't getting [new lead alerts / reply alerts] because [the alert only went to the
> lead's assigned team member, and new leads weren't being assigned / your user had no phone number for text
> alerts]. We've fixed it and tested it: a test lead sent the alert to [who] straight away. While it wasn't working,
> [number] [leads / messages] came in; here they are in case any still need a reply. Let us know if anything else
> looks off.
