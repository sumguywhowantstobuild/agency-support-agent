Source: https://help.gohighlevel.com/support/solutions/articles/155000002220-how-to-add-a-domain-and-verify-dns-record

# Adding an email sending domain and verifying its DNS records

Our summary of a HighLevel help article (checked 2026-10-06). Open the source link for the full steps.

## Summary

Explains adding a dedicated email sending domain and getting its DNS records (SPF, DKIM, DMARC) verified.

- GHL shows the DNS records to add at the domain's DNS host, then checks them.
- On Cloudflare, records used for email authentication must be set to DNS only (grey cloud), not Proxied: proxied CNAME records for email authentication fail verification.
- Wrong or missing SPF and DKIM records can cause spam placement or failed authentication; check each value before verifying.
