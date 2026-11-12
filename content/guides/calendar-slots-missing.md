# Calendar shows no times or the wrong times

Bluejay Funnel Co. support guide. Applies to: GHL booking calendars and their booking links or embedded widgets.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says people can't book, or the booking page shows the wrong times. Common ways clients put it: "my
calendar says no availability", "no times show on Tuesday", "people can't book me", "it shows times when I'm
closed", "the times are off by an hour".

Which times a GHL calendar offers depends on many settings working together: the team members assigned, their
weekly hours and date-specific overrides, slot duration, interval and buffer, minimum scheduling notice, how far
ahead people can book, booking limits, connected Google or Outlook calendars, and timezones. One of them being off
is enough to hide slots or show the wrong ones.

## Check first

Before starting, confirm the client's sub-account, which calendar and booking link (or page with the widget), and
one specific case: the date and time the client expected to see. Ask for the timezone of the person booking only
when times show at the wrong hour or on the wrong day (check 9).

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is it the right calendar?** Calendars → Calendar Settings, find the calendar (pencil icon to edit). Check the
   booking link or the widget on the page belongs to this calendar, not an older copy.
2. **What does the Troubleshoot tool say?** In Calendar Settings, click the wrench icon next to the calendar (or
   **Troubleshoot Calendar** in the calendar's header). Pick the date and hover over an unavailable slot: the tooltip
   gives the reason, for example **USER** (no team member available), **CONFLICT** (a Google or Outlook event),
   **BOOKED**, **BLOCKED** (time blocked by hand), **BUFFER**, **NOTICE** (minimum scheduling notice), **LOOKBUSY**
   (hidden by the Look Busy setting) or **PAST**. This is usually the fastest way to the cause. The tool only shows
   slots generated from the calendar's availability and intervals: a closed day or a time outside them doesn't
   appear at all, so use checks 3 to 5 for those. It works in the logged-in user's timezone.
3. **Is a team member assigned?** In the calendar's service details, at least one team member must be selected,
   or no times appear at all.
4. **Are the hours right?** On the **Availability** tab, check the weekly hours and any date-specific overrides
   that block the day. If recurring meetings are on, date-specific hours may not apply.
5. **Do duration, interval and buffer fit together?** A slot duration, slot interval or buffer that don't divide the
   working hours well leave gaps or remove times the client expects.
6. **Is a booking rule hiding it?** Minimum scheduling notice (too soon to book), date range (too far ahead), and
   the limits on appointments per day or per slot.
7. **Is Look Busy hiding slots?** On the **Availability** tab, **Look Busy** hides a set percentage of open times
   on the booking page, so the calendar looks fuller (staff still see them when booking from inside GHL). Check
   whether it's on and whether the client wants it.
8. **Is a connected calendar blocking it?** Connected calendars are set in Settings → Calendars → **Connections**.
   An event marked Busy on a selected conflict calendar blocks that team member for that time. An all-day event
   marked Busy can remove that team member's availability for the day. On a round-robin calendar, another team
   member may still offer the time. Confirm the affected slots in the Troubleshoot tool.
9. **Is it a timezone mix-up?** When times are "off by an hour" or appear on the wrong day, compare the user's
   profile timezone, the calendar's timezone, and the timezone shown to the person booking.

## Fix

**Wrong calendar or link.** Point the booking link or widget at the right calendar, or fix the calendar the link
already uses.

**No team member available (USER).** Assign the right team member to the calendar and check that member's own
availability covers the hours.

**Hours or overrides wrong.** Correct the weekly hours on the Availability tab and remove or fix overrides that
block the day. Check with the client before changing their hours; they may have blocked the day on purpose.

**Duration, interval or buffer leave gaps.** Adjust them so the slots line up with the working hours (for example
a 60-minute slot with a 60-minute interval inside hours that start on the hour).

**Booking rules too strict.** Shorten the minimum scheduling notice, extend the date range, or raise the daily or
per-slot limits, as the client wants. These are business choices: agree them with the client.

**Look Busy hiding slots (LOOKBUSY).** Ask the client whether they set it on purpose. If they want all open times
shown, turn Look Busy off or lower the percentage; if they want it, explain that some open times are hidden by
design.

**Connected calendar conflict (CONFLICT).** Find the busy event in the client's Google or Outlook calendar. If it
shouldn't block bookings, the client can mark it Free or move it; don't edit their personal calendar for them.

**Times shown when the client is closed.** Usually the reverse of the above: weekly hours too wide, a team member
whose own hours are wider, or a timezone mismatch. Fix the hours or the timezone.

**Timezone mix-up.** Set the calendar and the user's profile to the client's real timezone, and check what timezone
the booking page shows to visitors.

**After any fix: verify the full path.** Open the live booking link in a private browser window and check the
expected date shows the expected times. Run the Troubleshoot tool on that date: only times the client wants
blocked should show a reason. The Troubleshoot tool shows times in your own (logged-in user's) timezone, and the
booking page in the visitor's: compare the same moment in time, not the same clock time. If you make a test
booking, use your own details and cancel it afterwards.

**Then follow up on missed bookings, where applicable.** GHL has no record of people who gave up because no times
showed. If the client knows of people who couldn't book, suggest they reach out; don't contact them for the client.

**Hand to a person** when the Troubleshoot tool shows a time as available but the live booking page doesn't offer
it (after allowing for the timezone difference), or shows a reason you can't trace to any setting. Include in the handoff: the calendar, the date and time,
the timezone of the person booking, and a screenshot of the Troubleshoot tool.

## Tell the client

Keep it short and calm: what was hiding the times in plain words, what we changed, and that we tested it. Don't
blame the client, even if they changed a setting. Example:

> Thanks for flagging this. We found why those times weren't showing: [a busy event in your Google Calendar was
> blocking that day / the calendar had no team member assigned after a recent change / the booking page was set to
> a different timezone]. We've fixed it and checked your booking page: [day] now shows [times] as expected. Let us
> know if anything else looks off.
