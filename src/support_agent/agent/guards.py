"""Exact checks for the never-dos, run on every draft before a person sees it (and by the test set's scorer).

Simple word rules on purpose: they behave the same every time, and a false alarm only sends a draft to a person.
"""

import re

from support_agent.models import Draft, DraftAnswer

MAX_REPLY_WORDS = 120

CLAIMS_FIXED = re.compile(r"\b(fixed|resolved|sorted)\b", re.IGNORECASE)
PROMISE = re.compile(
    r"\b(refunds?|refunded|guarantee[ds]?|promise[ds]?"
    r"|will be (done|fixed|ready|working|live|back)"
    r"|(by|before) (today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|end of (the )?(day|week))"
    r"|(within|in) \d+\s*(minutes?|hours?|days?))\b",
    re.IGNORECASE,
)


def check_draft(
    draft: Draft | DraftAnswer,
    other_client_names: list[str],
    known_urls: set[str] | None = None,
    personal_details: set[str] | None = None,
) -> list[str]:
    """The never-dos this draft breaks, in a fixed order. An empty list means it passed."""
    reply = draft.client_reply
    problems = []
    if CLAIMS_FIXED.search(reply):
        problems.append("claims_fixed")
    if PROMISE.search(reply):
        problems.append("promise")
    everything = " ".join([draft.likely_cause, *draft.evidence, *draft.fix_steps, reply]).lower()
    if any(name.strip() and name.strip().lower() in everything for name in other_client_names):
        problems.append("other_client")
    if len(reply.split()) > MAX_REPLY_WORDS:
        problems.append("too_long")
    if known_urls is not None and any(s.url not in known_urls for s in draft.sources):
        problems.append("unknown_source")
    if known_urls is not None and any(link not in known_urls for link in _links(reply)):
        problems.append("unknown_link")  # a link the client (or an attacker) supplied never reaches customers
    if personal_details and _repeats_personal_details(reply, personal_details):
        problems.append("personal_details")
    return problems


# Full links, and bare domains an attacker might write without https:// (not the domain of an email address).
# Any domain ending counts (attackers use .xyz, .dev, ...); a false alarm only sends the draft to a person. Endings
# need 2+ letters, so "a.m." and "e.g." aren't links.
LINK = re.compile(r"(?:https?://|www\.)[^\s<>\"']+"
                  r"|(?<![@\w.-])(?:[a-z0-9-]+\.)+[a-z]{2,24}\b[^\s<>\"']*",
                  re.IGNORECASE)


def _links(reply: str) -> list[str]:
    """Web addresses in the reply, without the punctuation that ends a sentence."""
    return [link.rstrip(".,;:!?)]") for link in LINK.findall(reply)]


def names_a_customer(draft: Draft | DraftAnswer, details: set[str], name_parts: set[str] = frozenset()) -> bool:
    """Would saving this draft as a past ticket keep a customer's details? Past tickets are searched for every
    client, so the cause, fix steps and reply must hold no name, email or phone GHL showed. Stricter than the reply
    check: a lone first or last name counts too (from a full name, or GHL's single-name fields), written with a
    capital first letter or all in capitals however GHL stored it ("jane" is caught as "Jane" or "JANE", "will" in a
    sentence is not); a false alarm only skips one save."""
    text = " ".join([draft.likely_cause, *draft.fix_steps, draft.client_reply])
    names_only = {detail for detail in details if not re.search(r"[\d@]", detail)} | name_parts
    parts = {part for name in names_only for part in name.split()}  # "Mary Ann" as a first name: "Mary" counts too
    names = {form for part in parts if len(part.strip()) >= 2 for form in (part[:1].upper() + part[1:], part.upper())}
    return _repeats_personal_details(text, details) or any(
        re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) for name in names)


def _repeats_personal_details(reply: str, details: set[str]) -> bool:
    """Does the client reply repeat a customer's name, email or phone that GHL showed the agent? (Bluejay policy:
    no personal details in Slack.) Phones are compared by their last 10 digits, however they are written."""
    text = reply.lower()
    digits = re.sub(r"\D", "", reply)
    for detail in details:
        detail_digits = re.sub(r"\D", "", detail)
        if len(detail_digits) >= 7 and detail_digits == re.sub(r"[\s()+.-]", "", detail):
            if detail_digits[-10:] in digits:
                return True
        elif len(detail.strip()) >= 3 and re.search(rf"(?<!\w){re.escape(detail.strip().lower())}(?!\w)", text):
            return True
    return False