# Phone number setup and calls not coming through

Bluejay Funnel Co. support guide. Applies to: buying and setting up an LC Phone number in a GHL sub-account, and
incoming calls that don't ring through.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client wants a new number, can't buy one, or calls to their number don't reach them. Common ways clients put it:
"I need a local number", "it won't let me buy a number", "people say my number doesn't ring", "calls hang up
straight away", "calls go to voicemail without ringing", "the new number doesn't text".

A GHL number is bought per sub-account in Settings → Phone System → **Phone Numbers** (some screens label this entry
Phone Numbers directly). Buying it is only the first step: the number then needs its incoming call settings (who or
what answers, forwarding, voicemail) and, if it will text, the messaging registration that applies to its country
and use (for US texting: A2P 10DLC for local numbers, Toll-Free Verification for toll-free numbers).

## Check first

Before starting, confirm the client's sub-account and the number (or the area code they want), and for call
problems one failed call: the caller's number and roughly when.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Buying: what stops the purchase?** Common blocks: the identity verification (**Persona**) needed for eligible
   +1 numbers (US, Canada, Puerto Rico) and Israel +972 numbers hasn't been completed; an address or regulatory
   requirement for the country; the number was taken (refresh the list); search filters too narrow; the wrong phone
   provider (the account uses Twilio, not LC Phone); or the wrong sub-account.
2. **Is the number in the account?** After purchase it should appear in Phone Numbers.
3. **Calls: where does the number actually send calls?** Open the number's three-dot menu → **Edit Configuration**
   and read the real setup: an IVR or workflow, Voice AI (which can answer before any person is tried), team
   members, an external phone number, forwarding. Who owns the number or the contact can also affect who it rings.
   Check it matches what the client expects.
4. **Calls hang up straight away?** Check the whisper message for custom values: a broken custom value can make
   incoming calls drop. Also check the number can take voice calls.
5. **Calls ring nobody in GHL?** Calls answered in the browser need an eligible team member who is logged in;
   calls answered in the mobile app need the app's permissions on that phone. Check the devices selected for the
   user and their settings. (This doesn't apply to calls forwarded to an ordinary phone.)
6. **Voicemail too soon or too late?** Check the **incoming call timeout**. HighLevel suggests about 20 seconds as
   a starting point.
7. **Texts don't work on the new number?** Check the number's messaging registration and the error on the failed
   text: see the guide "SMS not sending".

## Fix

**Purchase blocked.** Complete the step it's missing: Bluejay policy is that the client completes Persona identity
verification themselves (it's their identity), and any address requirements for the country. Then search again
with wider filters. If the account uses Twilio, the number is bought there instead. If the exact number they want
isn't available, HighLevel has a request process for unavailable numbers.

**Calls routed wrong.** Change the configuration to match who should answer: the IVR, Voice AI, team members or
forwarding number. Agree with the client who answers and what happens when nobody does.

**Calls dropping.** Fix or remove broken custom values in the whisper message. If the number can't take voice calls,
hand to a person.

**Calls not ringing the team.** Have the team member log in on the browser or give the mobile app its permissions,
and check their selected devices.

**Voicemail timing.** Adjust the incoming call timeout from about 20 seconds and test: much longer can send callers
to a forwarding phone's own carrier voicemail instead of GHL's; much shorter leaves no time to answer.

**Texts not working.** Fix what the registration status and error code show (see the guide "SMS not sending").

**After any fix: verify the full path.** Call the number from your own phone: it should reach the intended person,
phone or Voice AI, go to the right voicemail when unanswered, and the call should show in GHL. If the number will
text, send a test text once it's registered.

**Then recover what was missed, where applicable.** List calls that were missed or dropped while it wasn't working
(the call log) so the client can call those people back.

**Hand to a person** when the number can't take voice calls, calls still drop with no custom values in the whisper
message, the purchase fails after identity and address steps are done, or (Bluejay policy) the client wants to bring
(port) an existing number from another provider. Include in the handoff: the number, example call times, and
screenshots of the configuration.

## Tell the client

Keep it short and calm: what was set up or wrong in plain words, what we changed, and anything they need to do (for
example identity verification). Never ask for their ID documents in a ticket. Example:

> Thanks for flagging this. Calls to your number [were dropping because of a broken greeting setting / were
> ringing a team member who wasn't logged in]. We've fixed it and tested it with a call: it now rings [phone or
> person] and goes to your voicemail when nobody answers. Here are the [number] calls that were missed so you can
> call them back. Let us know if anything else looks off.
