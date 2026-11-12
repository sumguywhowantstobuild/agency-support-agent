# SMS not sending (A2P 10DLC and delivery errors)

Bluejay Funnel Co. support guide. Applies to: text messages from GHL (LC Phone) to US numbers, sent by hand or by
workflows, from local (10DLC) or toll-free numbers.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says texts aren't going out or aren't arriving. Common ways clients put it: "my texts aren't sending",
"clients say they never got my message", "there's a red error on my messages", "texts stopped working", "my
reminders didn't go out".

US carriers only deliver business texts from local (10DLC) numbers that are registered: a **brand** (the business)
and a **campaign** (what the texts are for) must be approved, and the sending number linked to that campaign.
Toll-free numbers go through a separate Toll-Free Verification instead. Texts
can also fail because of the person receiving them (opted out, landline, wrong number) or the message itself
(filtered content). The error code on the failed message usually says which.

## Check first

Before starting, confirm the client's sub-account, the sending number, and one failed message: who it went to and
when. Ask whether all texts fail or only some.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **How wide is it?** All messages from the account, one number, or one contact? All failing points to
   registration or the account; one contact points to that contact.
2. **What's the error code?** Open the failed message in Conversations: failed messages show a red status and an
   error code. Workflow texts also show the failure in the workflow's Execution Logs.
3. **Is the business registered?** Settings → Phone System → **Trust Center** (Brands & Campaigns): brand and
   campaign status is Pending, Approved or Rejected. Error **30034** means the number isn't fully registered. If
   the sending number is toll-free, check its Toll-Free Verification instead: error **30032** means it isn't
   verified.
4. **Is the number linked to the approved campaign?** Settings → Phone System → **Phone Numbers**: each number that
   sends texts should be linked to the campaign and show the green **A2P Verified** label. A Sole Proprietor
   registration allows only one number.
5. **Can this contact receive texts?** Check the contact's DND for SMS (in their record) and whether they replied
   STOP. Error **21610** means an opt-out or SMS DND applies; a STOP opt-out and DND set by hand are handled
   differently. Check the number is a mobile in the right format (errors **21211**, **21614**, **21635**,
   **30005**, **30006**).
6. **Was the message filtered or limited?** Error **30007** means carriers filtered the message: the cause can be
   the content, missing consent, how the sender identifies itself, or carrier rules. **30023** means the daily
   limit was reached. **30002**, **30033** and **30037** mean the account or campaign is suspended or outbound
   texts are off.
7. **Is the sending number itself the problem?** Error **21606** means the From number is invalid or unavailable
   (check it belongs to this sub-account, its format, and that it's fully set up); **21661** means it can't send
   texts at all.

## Fix

**Not registered yet, or still pending.** Complete the brand and campaign registration in the Trust Center with
the client's real business details. Texts use the registered route only after the campaign is approved and the
number is linked. After approval, confirm the number is linked and shows A2P Verified. Carrier activation can take
additional time (HighLevel mentions several business days): verify delivery with a test before saying it's ready.
Don't promise the client an approval date: HighLevel publishes estimates, but they aren't a promise for any one
application.

**Toll-free number not verified (30032).** Complete Toll-Free Verification for the number, or send from a
registered local number instead. Same rule: verify delivery before saying it's ready.

**Campaign rejected.** In Trust Center → Brands & Campaigns → **Campaigns** tab, open the rejected campaign and
click **View required fixes** for each reason. Fix every listed issue before resubmitting. Common ones: the opt-in
wording on the client's forms (SMS consent must be its own optional checkbox, not pre-ticked, with message type,
frequency, rates and STOP instructions), the client's website (business name, contact details, privacy policy,
publicly reachable), business details that don't match the registration, and sample messages (business name and
opt-out wording, no link shorteners such as bit.ly). Some fields can't be edited: that needs a new campaign.

**Number not linked.** Link the sending number to the approved campaign in Phone Numbers and wait for the A2P
Verified label. On a Sole Proprietor registration, send from the one registered number only.

**Contact opted out (21610 after a STOP reply).** Don't resend. A contact who texted STOP has to opt back in themselves by texting
START to the same sending number, or HighLevel Support can review proof that they consented again. Never clear
an opt-out quietly.

**Other SMS DND on the contact.** DND set by hand or by a workflow (not by a STOP reply) can be changed in the
contact's record, in the DND section. Check why it was set before turning it off, and agree it with the client.

**Wrong or landline number.** Correct the number to the full format with country code (for example +1 and ten
digits), or use another channel if it's a landline.

**Message filtered (30007).** Check more than the wording: that the contact consented, that the message names
the business, and that the sender is registered for this kind of message. Then fix the content: remove link
shorteners and http links, avoid spam-like wording, and keep attachments under 5 MB. Don't resend the same text
unchanged.

**Sending number problem (21606, 21661).** Send from a number that belongs to this sub-account and is set up for
texting, or fix the number's setup. A number with no SMS capability can't be fixed by registration: use another
number.

**Daily limit reached (30023).** Pause bulk sending until the next day. If the limit is lower than the client
needs, hand to a person.

**After any fix: verify the full path.** Send a test from the same number to your own mobile, the way the client
sends (by hand or through the workflow). Check it shows as delivered in Conversations and arrives on the phone. For
workflow texts, check the Execution Logs too.

**Then recover what was missed, where applicable.** List the important texts that failed (appointment reminders,
replies to leads). Resend only those that still make sense: a reminder for an appointment that's already passed
doesn't. Never resend to contacts who opted out. Count them so the reply to the client is exact.

**Hand to a person** for a suspended campaign or account (30002, 30033), outbound texts turned off (30037),
unknown errors that keep happening (30008), filtered messages (30007) that keep failing after the compliance and
content fixes (collect 3 or more examples), a daily limit that's too low, or a rejection that needs business
details only the client has (legal name, tax ID, website changes). Include in the handoff: the error codes, the
sending number, example failed messages with times, and the Trust Center status.

## Tell the client

Keep it short and calm, in plain words: US carriers require businesses to register before their texts are
delivered. Say what was wrong, what we did, and what happens next. Never promise an approval date. Only say we'll
tell them when it's approved if someone is set to check the Trust Center status. Example:

> Thanks for flagging this. Your texts weren't arriving because [your number wasn't yet linked to your approved
> texting registration / the carriers asked for a few changes to your registration / the message was being filtered
> because of the link in it]. We've [fixed it and tested it: a test text arrived straight away / made the changes and
> resubmitted, and we'll tell you as soon as it's approved]. [We've resent the
> [number] reminders that are still upcoming.] Let us know if anything else looks off.
