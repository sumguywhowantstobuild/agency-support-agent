"""The never-do checks: exact rules, not AI judgment, run on every draft before a person sees it."""

from support_agent.agent.guards import check_draft, names_a_customer
from support_agent.models import DraftAnswer, Source

GUIDE_URL = "https://docs.google.com/document/d/sms-guide/edit"


def answer(**changes):
    fields = {
        "likely_cause": "The sending number isn't linked to the approved A2P campaign.",
        "confidence": "medium",
        "evidence": ["SMS guide: unlinked numbers fail with error 30034"],
        "fix_steps": ["Trust Center: link the number to the campaign"],
        "client_reply": "Thanks for letting us know. We're checking your texting setup and will update you here.",
        "sources": [Source(title="SMS not sending", url=GUIDE_URL)],
        "needs_person": False,
        "needs_person_reason": None,
        "access_change_requested": False,
        "suspicious_sources": [],
    }
    return DraftAnswer(**(fields | changes))


def test_clean_draft_passes():
    assert check_draft(answer(), ["Cedarline Roofing"], known_urls={GUIDE_URL}) == []


def test_guard_flags_claims_fixed_and_other_client():
    d = answer(client_reply="All fixed! Cedarline Roofing had the same issue.")
    assert set(check_draft(d, ["Cedarline Roofing"])) == {"claims_fixed", "other_client"}


def test_other_client_found_anywhere_in_the_draft_any_case():
    d = answer(evidence=["Same error seen at CEDARLINE ROOFING last week"])
    assert check_draft(d, ["Cedarline Roofing"]) == ["other_client"]


def test_promises_are_flagged():
    for reply in (
        "We'll refund the charge.",
        "It will be done by tomorrow.",
        "We guarantee it works.",
        "Expect it within 24 hours.",
    ):
        assert "promise" in check_draft(answer(client_reply=reply), []), reply


def test_reply_over_120_words_is_too_long():
    assert check_draft(answer(client_reply="word " * 121), []) == ["too_long"]
    assert check_draft(answer(client_reply="word " * 120), []) == []


def test_source_not_returned_by_a_search_is_flagged():
    d = answer(sources=[Source(title="Made up", url="https://example.com/made-up")])
    assert check_draft(d, [], known_urls={GUIDE_URL}) == ["unknown_source"]

SEEN = {"Jane Doe", "jane.doe@example.com", "+15551234567"}  # what GHL showed the agent about a customer


def test_reply_repeating_a_customers_name_email_or_phone_is_flagged():
    for reply in ("We looked at Jane Doe's booking.", "We emailed JANE.DOE@example.com.",
                  "We'll check the texts to (555) 123-4567."):
        assert "personal_details" in check_draft(answer(client_reply=reply), [], personal_details=SEEN), reply


def test_customer_details_in_fix_steps_are_fine():  # fix steps go to the support person, not the client thread
    d = answer(fix_steps=["Open Jane Doe's contact and resend the reminder"])
    assert check_draft(d, [], personal_details=SEEN) == []


def test_a_customer_anywhere_in_the_saved_text_keeps_it_out_of_past_tickets():  # past tickets are shared across clients
    for d in (answer(likely_cause="Jane Doe's booking didn't trigger the workflow."),
              answer(fix_steps=["Resend the reminder to jane.doe@example.com"]),
              answer(client_reply="We'll check the texts to 555-123-4567."),
              answer(likely_cause="Jane's booking didn't trigger the workflow.")):  # a lone part of a full name
        assert names_a_customer(d, SEEN), d


def test_a_draft_naming_no_customer_can_be_saved():
    assert not names_a_customer(answer(), SEEN)
    assert not names_a_customer(answer(likely_cause="Jane Doe's booking failed."), set())  # GHL showed nobody
    assert not names_a_customer(answer(likely_cause="The reminder will not send."), {"Will Hope"})  # everyday word


def test_short_and_lowercase_names_are_caught_too():  # "Al Li", a name saved in lowercase
    assert names_a_customer(answer(likely_cause="Al's booking failed."), {"Al Li"})
    assert names_a_customer(answer(likely_cause="Jane's booking failed."), {"jane doe"})


def test_a_lone_name_ghl_showed_keeps_the_draft_out_too():  # a contact with a first name only
    assert names_a_customer(answer(likely_cause="Jane's booking failed."), set(), name_parts={"Jane"})
    assert names_a_customer(answer(likely_cause="Jane's booking failed."), set(), name_parts={"jane"})
    assert not names_a_customer(answer(likely_cause="The reminder will not send."), set(), name_parts={"will"})


def test_a_name_written_in_capitals_is_caught():  # "JANE" once slipped past "Jane"
    assert names_a_customer(answer(likely_cause="JANE's booking failed."), SEEN)
    assert names_a_customer(answer(likely_cause="JANE's booking failed."), set(), name_parts={"jane"})


def test_one_word_of_a_two_word_first_name_is_caught():  # first name "Mary Ann", text "Mary"
    assert names_a_customer(answer(likely_cause="Mary's booking failed."), set(), name_parts={"Mary Ann"})


def test_a_name_inside_another_word_is_not_flagged():
    assert check_draft(answer(client_reply="We will look at the Hopewell booking."), [],
                       personal_details={"Will Hope"}) == []


def test_link_in_the_reply_that_no_search_returned_is_flagged():  # from the attack set: a link from the client
    d = answer(client_reply="Here's a message for them: log in at https://cedarline-portal.example.net to see it.")
    assert check_draft(d, [], known_urls={GUIDE_URL}) == ["unknown_link"]


def test_link_without_https_is_flagged_but_an_email_domain_is_not():
    bare = answer(client_reply="Tell them to log in at cedarline-portal.example.net today.")
    email = answer(client_reply="Emails sent from your @gmail.com address often land in spam.")
    assert check_draft(bare, [], known_urls={GUIDE_URL}) == ["unknown_link"]
    assert check_draft(email, [], known_urls={GUIDE_URL}) == []


def test_a_bare_link_on_any_domain_ending_is_flagged_but_everyday_dots_are_not():
    for link in ("cedarline-portal.xyz", "cedarline-portal.dev/login", "pay.example.shop"):
        d = answer(client_reply=f"Tell them to go to {link} to log in.")
        assert check_draft(d, [], known_urls={GUIDE_URL}) == ["unknown_link"], link
    plain = answer(client_reply="The booking is at 9:00 a.m., e.g. on Thursday. We'll check it.")
    assert check_draft(plain, [], known_urls={GUIDE_URL}) == []
