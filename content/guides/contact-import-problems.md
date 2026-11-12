# Contact or tag import problems

Bluejay Funnel Co. support guide. Applies to: CSV imports of contacts (and contacts with opportunities) into a GHL
sub-account, including tags added during the import.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says an import didn't work as expected. Common ways clients put it: "my import is missing contacts",
"only half my list came in", "the tags didn't get added", "my import created duplicates", "contacts got
overwritten", "importing my list sent texts to everyone".

A CSV import goes through Contacts → Smart Lists → **Import Contacts** (only users with Admin access can import): the client picks the type (contacts,
opportunities or both) and the mode (**Create**, **Update** or **Create & Update**), maps each column to a GHL field,
and can add tags, add everyone to a workflow, or create a smart list at the end. Every import runs as a bulk action.
Some records can fail while the rest succeed, and some errors only drop one field while the contact still comes in.

## Check first

Before starting, confirm the client's sub-account, the import (file name and roughly when it ran), and one contact
that came in wrong or not at all, with the row it came from in the file.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **What does the import result say?** Contacts → **Bulk Actions** → find the import → **Show Stats** → **Error**.
   Check how many records succeeded and failed, and read the error for each. Each error has a code and a reason.
2. **Were records or fields rejected for their values?** Some errors reject the whole record; others import the
   contact with the affected field left empty. Field-level errors include **1013** (phone not in full
   international format; non-US numbers need their country code), **1014** (dates not in the format chosen at
   import), **1005** (a dropdown or option value that doesn't match an existing option) and **1016** (an option
   value that matches more than one option, so GHL can't tell which). Others: **1008** (email not in
   name@domain.com form), **1004** (a required property has an invalid value; that row isn't imported); for **1019**,
   HighLevel's table points to 1005. Before retrying, check whether the contact already exists with the field missing.
3. **Was the file too big?** HighLevel's help articles give different import limits. Bluejay practice (not GHL's
   confirmed maximum): split files into batches under 20 MB and under 10,000 rows, and check each result before
   importing the next.
4. **Were "missing" contacts matched to existing ones?** Imports normally match existing contacts by the selected
   identifiers (email or phone). A row that matches an existing contact may update it instead of creating a new
   one, depending on the mode. Check the import's matching choices, rows that repeat the same email or phone, and the
   error log. The contact count can go up by less than the row count.
5. **Did the import create duplicates?** Usually two contacts that share neither email nor phone (a typo, a
   different number), or a file with repeated identifiers. Check which field the account matches on: Settings →
   Business Profile → **Contact Deduplication Preferences** (**Find Existing Contacts Based On**, and the second
   search field).
6. **Were existing contacts overwritten or blanked?** Check the mode used (Update or Create & Update overwrite mapped
   fields) and whether **Don't update Empty Values** was ticked. Without it, an empty cell can clear a field.
7. **Are tags missing?** Tags come in either from a column with the header `tags` (several tags separated by commas
   in one cell) or from the **Add Tags** step at the end, which tags every imported contact. Check the column was
   mapped and the tags are spelled as the client expects.
8. **Did the import set off messages?** Tags added by an import fire Contact Tag workflows, and the **Add to
   Workflow** step enrols everyone. Check which workflows contacts entered at the import time. A DND column in the
   file turns on DND for all channels, not just one.

## Fix

**Records or fields rejected for their values.** Fix only the failed values from the error list (email format,
phone with country code, dates in one format, option values spelled exactly and unambiguously as in GHL). For
contacts that came in with a field missing, update just that field (Update mode, **Don't update Empty Values**
ticked); for records that failed entirely, import just those rows. Re-importing the whole file risks overwriting
contacts that came in fine.

**File too big.** Split it into batches under 20 MB and under 10,000 rows, and import them one at a time,
checking each result.

**Rows matched to existing contacts.** Usually correct: the person was already in the account. Show the client
which rows matched existing contacts. If two different people share an email or phone (for example a couple), they
end up as one contact; agree with the client how to record them.

**Duplicates created.** Merge the duplicates, then fix the data at the source (the typo, the outdated number, the
repeated row) so the next import matches. Change the deduplication preferences only with the client's agreement:
it changes how every form and integration matches contacts, not just imports.

**Fields overwritten or cleared.** Re-import the correct values for the affected contacts with **Update** and
**Don't update Empty Values** ticked. If the old values aren't in any file, check the contacts' activity history
and tell the client what can't be restored.

**Tags missing.** Re-import with the tags column mapped, or select the affected contacts and add the tag in bulk.
Remember that adding the tag can fire Contact Tag workflows.

**Import set off unwanted messages.** Pause or switch the workflow back to Draft if more contacts are still
entering, and remove the imported contacts from it. Tell the client exactly who received what: a message already
sent can't be called back. For the next import, decide with the client which workflows should run before adding
tags or the Add to Workflow step.

**Wrong DND applied.** DND set by the import's DND column covers every channel. If the client only meant one
channel, correct it per channel for those contacts, but never clear DND for anyone who opted out themselves.

**After any fix: verify the full path.** Re-import a small test file (two or three rows, with fresh details where
appropriate and one existing contact if the problem was about matching or overwriting). Check the Bulk Actions
result, the contacts' fields and tags, and which workflows they entered.

**Then recover what was missed, where applicable.** Count the outcomes separately (created, updated, failed, and
corrected afterwards) and the contacts who got unwanted messages, so the reply to the client is exact.

**Hand to a person** when the Bulk Actions result shows records as successful but the contacts or fields aren't in
the account, or when an import sent messages to a large list. Include in the handoff: the file, the import time,
the Bulk Actions result and errors, and example rows.

## Tell the client

Keep it short and calm: what happened to their list in plain words, what we fixed, and what's still to do. Don't
blame the client for the file. If messages went out by mistake, say so plainly. Example:

> Thanks for flagging this. Of the [number] rows in your file, [number] came in as new contacts and [number] matched
> people already in your account, so we updated their existing records. [Number] couldn't be imported at first
> because [the phone numbers were missing the country code / the dates were in a different format]; we've corrected
> and imported [number] of them. [The other [number] need [what's missing] from you.] We've also checked the tags are
> on. Let us know if anything else looks off.
