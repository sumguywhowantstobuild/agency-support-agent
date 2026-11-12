# Booked appointment not showing

Bluejay Funnel Co. support guide. Applies to: appointments in GHL calendars and their sync to Google or Outlook.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says someone booked, but the appointment isn't where they look. Common ways clients put it: "someone
booked but it's not on my calendar", "the appointment disappeared", "it's not in my Google Calendar", "I got the
booking notification but can't find it".

There are two different problems here. Either the appointment is missing in GHL, or it's in GHL but hasn't reached
the client's Google or Outlook calendar. Find out which first: the fixes are different.

## Check first

Before starting, confirm the client's sub-account, the person who booked (name or email), roughly when they booked
and for when, and where the client is looking: the GHL calendar, the GHL mobile app, or Google or Outlook.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Does the appointment exist in GHL?** Calendars → **Appointment List View** tab. Filter by the contact and
   clear the status filter, so every status shows, including New (unconfirmed) and Cancelled, not just upcoming.
   Also check the contact's record for appointments.
   - Not found: see check 4.
   - Found, but the client can't see it on their calendar: see checks 2 and 3.
2. **Is it hidden by the calendar view?** Check the **Users** and **Calendars** filters in the client's calendar
   view (Services v2 bookings use the Staff, Services and Categories filters instead), and whether the appointment is
   assigned to another team member or another calendar. Check its
   status: a cancelled or rescheduled appointment isn't where the client expects.
3. **Is it on a different day or time because of timezone?** Each user picks the timezone they view appointments in
   (Calendar Settings → Preferences → **View Appointments In**). A user viewing in another timezone sees the
   appointment at a different time, sometimes on a different day.
4. **Did the booking really happen in this account?** If no appointment exists: check the contact's activity for a
   booking, whether the website or ad links to this calendar (an old link, another sub-account's calendar, or a
   different booking tool), and whether the person got a confirmation.
5. **Is it in GHL but missing from Google or Outlook?** Check the calendar connections (Settings → Calendars →
   **Connections**): is the integration shown as broken or disconnected, is a **Linked Calendar** selected, and is
   **Write Back Appointments** on? Google connections often break after a password change, revoked permissions, a
   Google security block, or an expired sign-in.

## Fix

**Hidden by the calendar view.** Show the client how to select the right calendars and team members in their view.
If the appointment belongs to another team member who should have it, leave it; if it was assigned wrongly, reassign
it and tell the client.

**Viewing timezone.** Set the client's **View Appointments In** to their real timezone. It only changes how times
are displayed to that user, not the booking itself.

**Booking happened somewhere else.** Point the website or ad at the right calendar's booking link. If the person
booked through a different tool, add the appointment to the right GHL calendar by hand with the client's OK.

**Not reaching Google or Outlook.** Reconnect the integration from Settings → Calendars → Connections. If that
doesn't work, remove the Google account and add it again (Settings → My Profile → Calendar Settings: delete the
connected account, then **+ Add New**). The team member who owns the Google account must sign in and grant the
permissions themselves: "login as" can't complete this connection, so the agency can't do it for them. Check a
Linked Calendar is selected and **Write Back Appointments** is on. Appointments already in GHL stay there while it's
disconnected, and upcoming appointments that didn't sync are synced automatically after reconnecting.

**After any fix: verify the full path.** Book a test appointment through the live booking link with fresh details
(your own, not a real contact). Check it appears in GHL as the client's user sees it, and in their Google or
Outlook calendar if they use one. Cancel the test afterwards.

**Then recover what was missed, where applicable.** If the sync was broken, list the appointments booked while it
was down. Upcoming ones should sync on their own after reconnecting: check each one now shows in Google or Outlook.
Past ones may not, and usually don't matter any more unless the client needs them for records. If any upcoming
ones are still missing, tell the client which ones so they don't miss them; don't add events to their personal
calendar for them.

**Hand to a person** when the appointment exists in GHL as Confirmed, on the right calendar and team member, the
integration shows connected with write-back on, and it still doesn't reach Google or Outlook after reconnecting; or
when the person received a booking confirmation but no appointment exists anywhere in GHL. Include in the handoff:
the contact, the calendar, the booking time, and screenshots of the appointment and the Connections tab.

## Tell the client

Keep it short and calm: where the appointment was, why they couldn't see it, and what we changed. Don't blame the
client. If any appointments were missed in their Google or Outlook calendar, list them clearly. Example:

> Thanks for flagging this. The appointment on [date] at [time] is booked and safe in your calendar. You couldn't
> see it because [your Google Calendar connection had disconnected / your calendar view was showing a different
> team member / your calendar was set to a different timezone]. We've fixed it and tested it with a new booking.
> [The [number] appointments booked while it was disconnected, on [dates and times], are now showing.] Let us know
> if anything else looks off.
