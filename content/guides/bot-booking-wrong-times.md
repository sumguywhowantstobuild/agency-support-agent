# AI bot booking wrong times

Bluejay Funnel Co. support guide. Applies to: Conversation AI bots that book appointments into a GHL calendar.
(Voice AI agents have their own settings and logs; this guide doesn't cover them.)
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says their AI bot books people at the wrong time or offers times they're not available. Common ways
clients put it: "the bot booked them an hour off", "the bot offered a time I'm closed", "the bot said 3pm but the
appointment says 4pm", "the bot booked on the wrong calendar", "the bot keeps saying there are no times".

A booking bot doesn't keep its own schedule. It checks availability on the booking calendar it's set to use, offers
slots, and books one. A "wrong time" usually comes from the calendar's availability, a timezone mix-up between what
the contact meant and what was saved, or the bot using a different calendar than expected. The bot's Agent Logs
show which one.

## Check first

Before starting, confirm the client's sub-account, the bot's name, and one wrong booking: the contact, the time the
contact asked for or was offered, and the time the appointment was saved.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **What did the bot actually do?** AI Agents → **Agent Logs** (also reachable from the conversation or contact).
   Find the session and look at the booking step: which calendar and time the bot sent to the booking action, and
   what came back. This shows whether the bot misunderstood the contact or booked exactly what it offered.
2. **Does the offered time match the calendar's real availability?** Compare the time the bot offered with the
   calendar's actual availability for that day. If the calendar itself offers times the client is closed, check it
   as in the guide "Calendar shows no times or the wrong times".
3. **Is it a timezone mix-up?** Compare three things: the timezone the contact meant, the timezone shown in the
   conversation, and the time saved on the appointment. Use the Agent Logs to see which time and calendar were
   actually used; don't assume how the bot picks a timezone. Check the timezone on the contact's record and the
   business timezone (Settings → Business Profile): a wrong value and a missing value are different problems.
4. **Is the bot booking on the right calendar?** Check the bot's appointment booking settings: the calendar
   selected (Guided and Flow Builder bots use a single calendar), and for prompt-based bots with several calendars,
   the calendar descriptions and keywords it uses to choose, and whether a fallback calendar is on.
5. **Do the bot's instructions mention times?** Opening hours or "we book 9 to 5" written into the prompt can
   contradict the calendar. The calendar should be the only place hours are set.
6. **Was the contact's request ambiguous?** "Tuesday at 3" from someone in another timezone, or "next Friday" said
   on a Thursday. The Agent Logs show what the bot understood.

## Fix

**Bot misunderstood the contact.** Adjust the bot's instructions to confirm the day, time and timezone back to the
contact before booking. Test the exact wording from the failed conversation.

**Calendar availability wrong.** Fix the calendar (see the guide "Calendar shows no times or the wrong times").

**Timezone wrong.** Correct the contact's timezone if it's wrong, and set the business timezone to the business's
real timezone. If contacts often lack a timezone or are in other timezones, have the bot confirm the timezone with
the contact before booking.

**Wrong calendar.** Select the right booking calendar in the bot's settings, or tighten the calendar descriptions
and keywords so requests match the intended calendar. A fallback calendar for requests that match none is optional:
agree with the client whether to use one or turn it off.

**Hours written into the prompt.** Remove opening hours from the instructions and let the calendar decide.

**Wrong appointments already booked.** Reschedule each affected appointment with the client's OK, and tell the
contact the corrected time; don't move appointments silently.

**After any fix: verify the full path.** Chat with the bot as a test contact (your own details), ask for a specific
time, and check the Agent Logs and the saved appointment match what was agreed in the chat. If the problem involved
timezones, also test as a contact in another timezone and one with no timezone set. Cancel the test appointments
afterwards.

**Then recover what was missed, where applicable.** List the appointments the bot booked while it was wrong and
check each one against the conversation. Count the ones that need rescheduling so the reply to the client is exact.

**Hand to a person** when the Agent Logs show the bot sent the correct calendar and time to the booking action but
the appointment was saved at a different time, or when the booking action returns an error you can't explain.
Include in the handoff: the contact, the conversation, the Agent Logs session, and the saved appointment.

## Tell the client

Keep it short and calm: what went wrong in plain words, what we changed, and which appointments need attention.
Don't blame the client for the bot's settings. Example:

> Thanks for flagging this. The bot was booking at the wrong time because [the contact's timezone on file was
> wrong / your calendar was still offering Saturday mornings / it was booking into your old calendar]. We've fixed
> it and tested it: a test booking now lands at exactly the time agreed in the chat. [Number] appointments were
> booked at the wrong time; here they are so we can move them with your OK. Let us know if anything else looks off.
