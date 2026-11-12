# Missed call text back not working

Bluejay Funnel Co. support guide. Applies to: GHL (LC Phone) numbers with Missed Call Text Back, including calls
forwarded to a personal cell phone.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says callers aren't getting the automatic text after a missed call. Common ways clients put it: "missed
call text back isn't working", "people who called me didn't get a text", "it only works sometimes", "callers got
two texts", "the text came from a different number".

Missed Call Text Back sends a text when an incoming call rings past the **incoming call timeout** without being
answered. It's set in Settings → Phone System → Voice → **Voicemail & Missed Call Text Back**, then **Missed Call
Text Back** in the side panel. Only calls GHL records as missed send the text. Without **Call Connect**, a forwarded
cell phone's voicemail can make GHL treat the call as answered. The text is an ordinary SMS, so it needs a number
that is allowed to send texts.

## Check first

Before starting, confirm the client's sub-account, the number people call, how calls are answered (in GHL, the
mobile app, or forwarded to a personal cell), and one missed call: the caller's number and roughly when.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is it turned on?** Settings → Phone System → Voice → Voicemail & Missed Call Text Back → Missed Call Text Back:
   **Enable Missed Call Text Back** must be ticked and the message set (**Customize**).
2. **How did GHL record the call?** Find the call in the contact's conversation or the call log. Recorded as missed
   but no text: check 4 onwards. Recorded as Completed: GHL didn't treat it as missed, but Completed alone doesn't
   prove a person answered (check 3).
3. **Did a forwarded cell's voicemail pick up?** Without Call Connect, a forwarded cell phone's voicemail can make
   GHL treat the call as answered, so no missed-call text is sent. Check the recorded call outcome, compare the
   **incoming call timeout** with how long the cell rings before its voicemail answers, check whether **Call
   Connect** is on for the number, and test the forwarding route.
4. **Did the text fail?** Look in the conversation for a failed text with an error code. A failed missed-call text
   is an SMS problem: see the guide "SMS not sending".
5. **Is there a number allowed to send texts?** GHL tries the number that was called first, then a number that has
   texted this contact before, then the default outbound number, then any other number in the account that can
   send texts. At least one must be an eligible SMS sending number, with A2P 10DLC registration (local) or
   toll-free verification where required.
6. **Does the caller have DND on?** Missed Call Text Back respects the contact's DND settings.

## Fix

**Turned off or no message.** Tick **Enable Missed Call Text Back**, set the message with **Customize**, send the
test message from the settings, and save.

**Cell voicemail treated as answered.** Two options, to agree with the client:
- Turn on **Call Connect** for the number: Settings → Phone System → Phone Numbers (some screens label this entry
  Phone Numbers directly) → the number's three-dot menu → **Edit Configuration** → **Advanced Settings** → enable
  **Call Connect** and **Play Whisper Message**, set the prompt the person answering hears, and save. The person
  answering must press a key to take the call, so a voicemail pickup isn't counted as answered.
- Or set the incoming call timeout shorter than the time the cell rings before its voicemail picks up. HighLevel
  recommends about 10–20 seconds for Missed Call Text Back. Too short, and the client can't answer in time.

**Text failed to send.** Fix the sending side as in the guide "SMS not sending": registration, linked number,
error code.

**Text came from a different number.** Expected when the number called can't send texts: GHL falls back to another
eligible number. If the client wants replies on the called number, get that number registered and linked for
texting.

**Callers got several texts.** GHL sends a text for every missed call, even repeated calls from the same person a
few minutes apart. If that's a problem, the client can replace it with a workflow that waits or checks for a recent
text before sending; agree the design with them, and turn off the built-in Missed Call Text Back when the workflow
goes live so both don't send.

**After any fix: verify the full path.** From your own phone, make two test calls. First let one ring out (don't
answer, and let a forwarded cell ring past the timeout): the call should show as missed in GHL and the text should
arrive from the expected number. Then make one the client answers: no text should be sent.

**Then recover what was missed, where applicable.** List the missed calls from while it wasn't working. The client
may want to call or text those people back by hand; an automatic text days later reads oddly. Don't text anyone with
DND on.

**Hand to a person** when a call shows as missed in GHL, the sending number can send other texts fine, the caller
has no DND, and still no missed-call text is created. Include in the handoff: the number called, the caller's
number, the call time, and screenshots of the call log and the Missed Call Text Back settings.

## Tell the client

Keep it short and calm: why the texts didn't go out, what we changed, and that we tested it with real calls. Don't
blame the client for their phone setup. Example:

> Thanks for flagging this. Callers weren't getting the text because [your cell phone's voicemail was picking up the
> calls first, so the system counted them as answered / the feature had been switched off]. We've [added a short
> "press 1 to accept" step for forwarded calls / switched it back on] and tested it: a missed call got the text
> within seconds, and an answered call didn't. You had [number] missed calls while it wasn't working: here's the
> list if you'd like to follow up. Let us know if anything else looks off.
