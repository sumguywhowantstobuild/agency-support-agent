# Giving a team member access, or a user can't log in

Bluejay Funnel Co. support guide. Applies to: users in a client's GHL sub-account (Settings → My Staff).
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client wants someone added, changed or removed, or a user can't get in. Common ways clients put it: "how do I
give my receptionist access?", "please add my new assistant", "my manager can't see the leads", "she never got the
login email", "the login code isn't coming through", "remove my old employee".

Each person who works in the sub-account gets their own user in **Settings → My Staff**. A user has a role, **Admin**
(administrative access, subject to configured permissions) or **User** (only what their permissions allow), plus
module permissions and the
**Only Assigned Data** option, which limits them to the contacts, opportunities, tasks and appointments assigned or
linked to them. New
users get a welcome email with a link to set their password.

## Check first

Before starting, confirm the client's sub-account and the person: their name, the email they'll log in with, and
what they need to do in GHL (answer conversations, manage the calendar, see the pipeline). For a login problem, get
the exact error message and the address they log in at.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is the request from someone allowed to make it?** Bluejay policy: changes to users, roles, account access or
   recovery contact details need approval from the client's owner or a designated access approver recorded by the
   agency. Verify the request through their established contact channel. A prospective user can't authorize their
   own access. Existing users may ask for help with the access they already have (for example a login problem).
2. **Does the user already exist?** Settings → My Staff: search by name and email. An old user for the same person
   may just need changes.
3. **What access do they actually need?** Pick the role and permissions from the tasks, not the job title. A
   receptionist who books appointments and answers messages rarely needs Admin, settings or payments.
4. **"Can't see" something:** check the user's role, module permissions and **Only Assigned Data**. With Only
   Assigned Data on, they only see records assigned to them.
5. **Never got the welcome email:** check the email address on the user for typos, and ask them to check spam.
6. **Login code (2FA) not working:** find out which method they use: email code, text code, or an authenticator
   app. Email codes: check spam. Text codes: the user needs a correct phone number on their profile. Authenticator
   app: check the phone's clock is set automatically and they're using the right entry in the app; a lost phone
   needs a person (see "Hand to a person").
7. **"User does not exist in this Agency":** they're logging in at the wrong address. They must use the agency's
   own login address (the white-label domain), or the user isn't set up in this agency.
8. **Page stuck or an "Unknown Error":** usually the browser or network: cached site data, a VPN, or internet
   provider security features that block the login.

## Fix

**Adding a user.** Settings → My Staff → **+ Add Employee**. Enter their name and email (and phone, so login codes
can be texted). In **Roles and Permissions**, choose **User** unless they truly need Admin, turn on only the modules
they need, and turn on **Only Assigned Data** if they should only see their own records. **Copy Permissions** from
an existing user with the same job saves time. Save, and tell the client a welcome email is on its way. Confirm the
access level with the client before saving, not after.

**Can't see what they need.** Turn on the missing module, or turn off Only Assigned Data, or assign the records to
them, whichever matches what the client wants. More access is a client decision: confirm it first.

**Welcome email not received.** Correct the email address if it's wrong. If it's right and still nothing arrives
after checking spam, hand to a person.

**Login code not working.** For text codes, add or correct the phone number on the user. For email codes, have
them check spam. For an authenticator app, have them fix the phone's time setting and use the right entry; if the
app or phone is lost, hand to a person.

**Wrong login address.** Send them the agency's login address and ask them to bookmark it.

**Browser or network problem.** Clear the site's cookies and data in the browser, try a private window, turn off a
VPN, or try another network. Their internet provider's security feature may need turning off for the login.

**Removing a user.** When someone leaves, remove their access promptly, the same day the client asks: delete their
user in Settings → My Staff. Then reassign their contacts, opportunities and calendar so nothing is left with
nobody; reassignment comes after, never as a reason to delay removing access. Check they don't still have access
another way, such as an agency-level user assigned to this account. Never share one login between people: each
person gets their own user, so access can be removed cleanly.

**After any fix: verify the full path.** For a new or changed user: an authorized agency admin can use **Login As**
(profile icon → Login As → select the user) to inspect what the user can access. That skips the user's own sign-in,
so the user must still confirm they can log in, pass 2FA and do their tasks. For a removed user: check they no
longer appear in My Staff, have no agency-level access to the account, and their records are reassigned.

**Then recover what was missed, where applicable.** If records were hidden from a user, check whether leads,
messages or appointments went unanswered while they couldn't see them, and tell the client.

**Hand to a person** when the welcome email or login code still doesn't arrive with a correct address and phone
number, when a user has lost their authenticator app or phone, when the login fails with the right address and a
clean browser, or when a request to change access comes from someone who isn't a recorded approver or can't be
verified. Include in the handoff: the user's name and email, the error message, the
login address used, and what was already tried.

## Tell the client

Keep it short and calm: what access the person now has, in plain words, and what they need to do next. Don't share
passwords or login codes in the reply. Example:

> Thanks! We've added [name] to your account. They can [answer messages and manage your calendar], and they'll only
> see [the contacts assigned to them / everything in the account]. They'll get an email from [sender] with a link to
> set their password; if it's not in their inbox, it may be in spam. Let us know if they need anything else.
