# Emails going to spam or not delivered

Bluejay Funnel Co. support guide. Applies to: emails sent from GHL (LC Email), and the dedicated sending domain
that sends them.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says their emails aren't reaching people. Common ways clients put it: "my emails go to spam", "nobody
gets my emails", "my domain won't verify", "emails bounce", "my email sending got suspended".

Mailbox providers such as Gmail and Yahoo decide whether an email lands in the inbox. They check that the email is
authenticated (SPF, DKIM and DMARC records in the domain's DNS), the domain's reputation, the list (bounces and spam
complaints), and the content. A **dedicated sending domain**, usually a subdomain such as `mg.clientdomain.com`,
lets the client's emails build their own reputation instead of sharing one. "Delivered" only means the receiving
server accepted the email; it doesn't tell you whether it landed in the inbox or in spam.

## Check first

Before starting, confirm the client's sub-account, the from address they send from, how the email was sent (one-to-one,
campaign, bulk or workflow), and one email that went wrong: who it went to, when, and whether it went to spam,
bounced, or never arrived.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Is email sending suspended for the account?** If bounces reached HighLevel's limit (about 5%, once a minimum
   number of emails and at least 25 bounces have been counted), sending is suspended for the account and nothing goes out until that's resolved. See the fix "Sending suspended".
2. **Can this contact receive email?** Check the contact's email DND and whether they unsubscribed. Contacts whose
   email bounced may have email DND turned on automatically.
3. **What happened to the email?** Check the message's available delivery details in Conversations. For campaign,
   bulk or workflow email, also check the email statistics and that recipient's result. Bounced or failed: note the
   reason. Delivered but the person can't find it: it was accepted, so check spam placement (keep going).
4. **Is a dedicated sending domain set up and verified?** Settings → **Email Services** → **Dedicated Domain and
   IP** tab: is there a domain, and do its DNS records show as verified?
5. **If it won't verify: are the DNS records exactly right?** Compare each record at the domain's DNS host with
   the values GHL shows. DNS changes can take up to 24 hours. On Cloudflare, the records must be **DNS only** (grey
   cloud), not Proxied (orange cloud), or verification fails.
6. **Which domain is it actually sent from, and does authentication pass?** Check the real sending domain and the
   from-address rules for this sending route: they differ between shared and dedicated domains (on shared domains,
   LC Email campaigns can rewrite a public from address such as gmail.com). Open a received copy in Gmail → **Show
   original** and check SPF, DKIM and DMARC. DMARC passes when SPF or DKIM passes for a domain that aligns with the
   from address: hello@clientdomain.com can align with a sending subdomain such as mg.clientdomain.com.
7. **Is the list the problem?** Check the bounce rate and spam complaints. Bounces approaching 5% risk suspension;
   spam complaints over about 0.3% hurt the domain's reputation. Purchased, old or unconsented lists cause both.
8. **Is it the content or the pattern?** Link shorteners, image-only emails, ALL CAPS or pushy subject lines, no
   unsubscribe link, or a sudden big send from a domain that rarely sends.
9. **Sending to the client's own domain?** Emails from the client's domain to their own mailbox (for example form
   notifications to info@ the same domain) can be flagged as spoofing by their mail server.

## Fix

**Sending suspended.** Hand to a person (see below): it needs HighLevel and a cleaned list before sending resumes.
Stop any scheduled campaigns in the meantime.

**Contact has email DND or unsubscribed.** Don't send to them. Only the contact can resubscribe; don't clear an
unsubscribe or a bounce-triggered DND without a clear reason and the client's agreement.

**Email bounced.** A permanent failure (hard bounce: the address doesn't exist) means stop sending to it: suppress
the address from sending rather than deleting the contact. A temporary failure (soft bounce: mailbox full, server
busy) may succeed later. If many contacts bounce, pause sending to the whole list and suppress the invalid
addresses.

**No dedicated domain, or it won't verify.** Add a subdomain in Settings → Email Services → Dedicated Domain and IP
→ **Add Domain**. Let GHL add the DNS records automatically where it can, or copy each value exactly at the DNS
host. Set Cloudflare records to DNS only. Wait for DNS to update, then verify. The client usually owns the DNS
login: ask them to add the records or give access, and never ask for their password in a ticket.

**Authentication fails or the from address doesn't align.** Send from an address on the client's own domain that
aligns with the authenticated sending domain (for example hello@clientdomain.com with mg.clientdomain.com), and
check the from-address rules for the route they use.

**List problems.** Suppress hard bounces and people who reported spam, stop sending to contacts who haven't
engaged in months, and don't send to bought lists. Changing who the client emails is their decision: explain why
and agree it with them.

**Content or sending pattern.** Use direct links instead of shorteners, balance text and images, keep subjects
honest, keep a visible unsubscribe link, and grow volume gradually on a new domain.

**Own-domain mail flagged.** Ask the client's mail administrator to allow GHL's sending for their domain, or send
notifications to an address on another domain.

**After any fix: verify the full path.** Send a test email the way the client sends (one-to-one, campaign or
workflow) to addresses you control at Gmail and Outlook. Check it lands in the inbox and **Show original** shows
SPF, DKIM and DMARC passing.

**Then recover what was missed, where applicable.** List the important emails that didn't arrive (booking
confirmations, invoices, replies to leads). Resend only those that still matter: emails that failed temporarily
can be retried; never resend to permanently failed addresses or people who unsubscribed.

**Hand to a person** when email sending is suspended for the account, when the DNS records match exactly and still
won't verify after 24 hours, or when authentication passes and the list is clean but emails still go to spam (a
reputation problem that needs longer work, such as Google Postmaster Tools monitoring). Include in the handoff:
the domain, a screenshot of the DNS records and verification status, example emails with times, and the bounce and
complaint figures.

## Tell the client

Keep it short and calm, in plain words: email providers trust senders who prove the email really comes from their
domain. Say what was wrong, what we did, and what they need to do (for example add DNS records). Don't blame the
client for their list. Example:

> Thanks for flagging this. Your emails were landing in spam because [your sending domain wasn't fully set up / your
> emails weren't passing the providers' identity checks / a lot of old addresses on your list were bouncing]. We've
> [finished the setup and confirmed it's verified / fixed the sending address / stopped sending to the addresses that
> bounce], and a test email now lands in the inbox. Let us know if anything else looks off.
