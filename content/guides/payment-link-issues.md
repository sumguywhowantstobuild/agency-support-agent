# Payment link not working

Bluejay Funnel Co. support guide. Applies to: GHL payment links (Payments → Payment Links) taking payments through
Stripe. Checks for other providers are marked as such.
Checked against HighLevel help: 2026-10-06.

## Symptoms

The client says customers can't pay through their link, or the payment didn't show up. Common ways clients put it:
"my payment link doesn't work", "the link won't open", "customers say the checkout errors", "it says Stripe not
connected", "the link is in test mode", "my customer paid but I don't see it", "Apple Pay doesn't show".

A payment link is a hosted checkout for one or more products. It needs a **product** with a price (Payments →
Products) and a connected **payment provider** (Payments → **Integrations**). The link has its own **Test/Live
Payment Mode**: test mode simulates payments with test cards, live mode takes real ones. Payment methods are set
separately for Test Mode and Live Mode.

## Check first

Before starting, confirm the client's sub-account, the payment link, and one failed attempt: who tried to pay,
when, what they saw, and roughly how much. Never ask for anyone's card details.

Work through these in order. When you find a cause, fix it and then verify the full path: one fault can hide
another.

1. **Did a payment perhaps go through?** Before anyone tries again, check GHL's transactions for the link and,
   with the client, their Stripe dashboard for a successful or pending payment from that customer. Retrying a
   payment that actually went through charges them twice.
2. **Does the link open at all?** If not, check the link's **Automatic Deactivation** setting: a rule there may
   have switched it off.
3. **Is the payment provider connected?** Payments → Integrations: Stripe should show as connected or enabled. A
   "Stripe not connected" message means the connection was never completed or was lost.
4. **Which provider do payment links use?** Providers can be set per channel, so the provider for Payment Links can
   differ from the account's default. Check the one configured for Payment Links specifically.
5. **Is the link in the right mode?** Check the link's Test/Live Payment Mode. Stripe's dashboard has its own
   test-mode view, but switching that view doesn't change the GHL checkout.
6. **Is the payment method enabled for this mode?** A method (cards, Apple Pay, Google Pay and others) can be on in
   Test Mode but off in Live Mode. With Stripe, methods also depend on the account's country, currency,
   verification and the checkout type.
7. **Is the product and price right?** Check the link uses the intended product and price, one-time or recurring.
8. **Did the customer's payment fail at the provider?** With Stripe, declines and blocks are recorded in the
   client's Stripe dashboard with a reason. The client (as the account owner) can see why.
9. **Paid but not showing in GHL?** Check whether the payment went through a different link or provider. Payments
   made outside GHL, or before Stripe was connected, aren't imported into GHL.

## Fix

**Payment may have gone through.** Reconcile it first with the client: if it succeeded or is pending, don't ask
the customer to pay again.

**Link deactivated.** Turn the link back on or adjust the deactivation rule, as the client wants, or create a new
link.

**Provider not connected.** Connect it in Payments → Integrations → **Connect with Stripe**. The person connecting
needs enough access to the client's Stripe account; usually the client does this step. Never ask for their Stripe
password.

**Wrong provider for Payment Links.** Set the provider for the Payment Links channel to the one the client uses.

**Wrong mode.** Set the link's Payment Mode to Live for real payments, or Test for testing, and tell the client
clearly which mode it's in now.

**Payment method missing.** Enable the method for the right mode and save. If Stripe doesn't offer it for the
client's account (country, currency or verification), the client sorts that out with Stripe.

**Wrong product or price.** Correct the product or price, or create a new link, and make sure the client shares the
right link. Agree price changes with the client.

**Customer's card declined.** Nothing to fix in GHL. The client can see the reason in Stripe and ask the customer to
try another card or contact their bank. Manual methods such as bank transfer aren't offered on payment link
checkout; use an invoice for those.

**After any fix: verify the full path.** Switch the link to Test mode, pay with Stripe's published test card
details, and check the test payment completes and appears in GHL. Then switch it back to Live if that's the
intended mode. Never test live mode with real card details: Stripe doesn't allow it. A live payment from a real
customer is the final confirmation.

**Then recover what was missed, where applicable.** List customers who tried and failed while it was broken, after
checking none of their payments went through, so the client can send them the working link. Don't contact their
customers for them unless asked.

**Hand to a person** when the client's Stripe account is restricted or needs verification, when payouts are held,
when a connected provider shows an error you can't trace, or when a customer may have been charged twice.
Bluejay policy: payments and refunds are the client's own decisions; we never issue refunds or change their Stripe
settings ourselves. Include in the handoff: the link, the attempt times, what the customer saw, and screenshots of the integration
status.

## Tell the client

Keep it short and calm: why payments failed in plain words, what we changed, and what they need to do in Stripe, if
anything. Only claim what was tested: a test payment shows the link works in test mode, not that live payments have
gone through. Don't share payment details in the reply. Example:

> Thanks for flagging this. Customers couldn't pay because [your Stripe connection had dropped / the link was
> switched off automatically / card payments were only switched on for testing]. We've fixed it,
> and a test payment completed and appeared in your account; the link is now set to take real payments. [Number]
> customers tried to pay while it wasn't working (none of them were charged); you may want to send them the link
> again. Let us know if anything else looks off.
