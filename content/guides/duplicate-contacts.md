# Duplicate contacts

Bluejay Funnel Co. support guide. Applies to: duplicate contact records in a GHL sub-account, from forms,
integrations, messaging channels or imports.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says the same person shows up more than once. Common ways clients put it: "I have duplicate contacts",
"this lead is in there three times", "the conversation is split across two contacts", "my contact count is too
high", "the same person got my email twice".

Whether GHL creates a new contact or updates an existing one depends on the sub-account's **Contact Deduplication
Preferences** (Settings → Business Profile): the **Allow Duplicate Contact** setting, the field GHL matches on first
(**Find Existing Contacts Based On**: email by default, or phone) and an optional second field. Duplicates usually
come from that setting being on, or from two records that share neither the matching email nor phone.

## Check first

Before starting, confirm the client's sub-account and one duplicated person: their name and the email and phone on
each record.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Are they really the same person?** Compare email, phone and history. Two family members sharing a phone, or a
   business sharing an inbox, are different people and shouldn't be merged.
2. **Is Allow Duplicate Contact on?** With it on, forms and integrations such as Zapier create a new record even
   when the email or phone already exists.
3. **Do the records share the matching field?** If GHL matches on email and one record only has a phone (or a
   different email), it can't match them. Check the first and second search fields against how leads usually
   arrive.
4. **Where did each record come from?** The contact's activity shows the source: a form, Facebook or Instagram, an
   import, an integration, or a messaging channel such as WhatsApp. One source creating most duplicates points to
   that channel's setup.
5. **How many are there?** Contacts → three-dot menu → **Manage Duplicates** (admins only): find by Email, Phone or
   Name to see the scale before merging anything.

## Fix

**Merging a few contacts.** Contacts → Smart Lists → select the records (up to 10) → **More** → **Merge**, and choose
the **master** record: the one with the most reliable information. Merges can't be undone. Review conflicting
fields and select the values to keep. Existing custom-field values on the master are kept; blank custom fields on
the master can be filled from the other records. Conversations, notes, tags, tasks and opportunities are combined.
Review appointments, payments, orders and invoices on each record before merging, as HighLevel warns, and check
them again afterwards. Active workflows: a run on a duplicate can move to the master, or end if the master is
already running that workflow, so check what each record was in the middle of.

**Merging many.** Use **Manage Duplicates** to review matches in batches. Agree with the client which records to
merge and which to leave, especially when matching by Name, which catches different people with the same name.

**Settings creating duplicates.** Turning Allow Duplicate Contact off, or changing the search fields, changes how
every form and integration matches contacts from then on. It doesn't fix existing duplicates. Agree it with the
client: some businesses want separate records on purpose.

**One channel creating duplicates.** Fix that channel's setup (the form's fields, the integration's matching, the
import's identifiers). For imports, see the guide "Contact or tag import problems".

**After any fix: verify the full path.** Submit a test from the source that created the duplicates, using details
of an existing test contact, and check it updates that contact instead of creating a new one (or creates a new one,
if that's what the client chose).

**Then recover what was missed, where applicable.** After merging, check the master record has the conversations,
open opportunities, appointments and payment records you expected, and that workflows the person was in the middle
of are still running where they should be (a merge can end a duplicate run). Count the merges and note what you
checked, so the reply to the client is exact.

**Hand to a person** before merging large numbers of contacts at once, when records hold payments or invoices that
look inconsistent, or when a merge produced an unexpected result. Include in the handoff: the records involved,
their sources, and the deduplication settings.

## Tell the client

Keep it short and calm: why the duplicates appeared in plain words, what we merged, and what changes for new
contacts. Don't blame the client's team. Example:

> Thanks for flagging this. The duplicates came from [your account allowing a new record for every form
> submission / leads sometimes giving a phone number and sometimes an email]. We've merged [number] duplicate
> records and checked that each merged contact has its [conversations, deals and appointments] in one place. [With your OK, we've also changed the setting so new
> submissions from the same person update their existing record.] Let us know if anything else looks off.
