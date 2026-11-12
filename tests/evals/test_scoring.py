"""Scoring for the accuracy test set: the pure parts only, no model calls."""

import pytest
from pydantic import ValidationError

from support_agent.evals.scoring import (Case, headline, load_cases, pick_judge_model, report, score, speed_line,
                                         unknown_guides, verdict_entry)
from support_agent.models import Draft, Source

GUIDE = Source(title="Form leads not reaching the pipeline", url="https://docs.google.com/document/d/form-guide/edit")


def draft_with(**changes) -> Draft:
    fields = {
        "likely_cause": "The New Patient Form → Pipeline workflow is in Draft.",
        "confidence": "high",
        "evidence": ["Workflow status: draft"],
        "fix_steps": ["Publish the workflow after checking its trigger"],
        "client_reply": "Thanks, we're looking into it and will update you here.",
        "sources": [GUIDE],
        "needs_person": False,
        "needs_person_reason": None,
    }
    return Draft(**(fields | changes))


def case(**changes) -> Case:
    fields = {
        "id": "b1-form-leads",
        "client_key": "brightsmile",
        "text": "People are filling in our new patient form, but nobody shows up in the pipeline.",
        "planted_cause": "The workflow that adds form leads to the pipeline is in draft.",
        "expected_sources": ["Form leads not reaching the pipeline"],
        "should_flag": False,
        "kind": "normal",
    }
    return Case(**(fields | changes))


def yes(case, draft):
    return True, "same cause"


def test_flag_mismatch_fails():
    s = score(draft_with(needs_person=False), case(should_flag=True), judge=yes)
    assert s.flag_ok is False


def test_never_do_uses_runtime_guard():
    s = score(draft_with(client_reply="Good news, it's fixed now."), case(), judge=yes)
    assert s.never_do_ok is False


def test_never_do_counts_rules_the_live_agent_recorded():
    # The live agent knows what the scorer can't (customer details GHL showed, links search returned).
    s = score(draft_with(checks_failed=["personal_details"]), case(), judge=yes)
    assert s.never_do_ok is False


def test_guide_check_fails_when_another_guide_is_cited():
    other = Source(title="Workflow not triggering", url="https://docs.google.com/document/d/workflow-guide/edit")
    s = score(draft_with(sources=[other]), case(), judge=yes)
    assert s.sources_ok is False


def test_guide_check_matches_cited_part_titles_in_any_case():
    part = Source(title="form leads not reaching the pipeline, Fix (part 1 of 2)", url=GUIDE.url)
    s = score(draft_with(sources=[part]), case(), judge=yes)
    assert s.sources_ok is True


def test_guide_check_passes_when_no_guide_is_expected():
    s = score(draft_with(sources=[]), case(kind="uncovered", expected_sources=[]), judge=yes)
    assert s.sources_ok is True


def test_cause_check_takes_the_judges_verdict_and_reason():
    def no(case, draft):
        return False, "The draft blames text delivery, not the draft workflow."

    s = score(draft_with(), case(), judge=no)
    assert (s.cause_ok, s.judge_reason) == (False, "The draft blames text delivery, not the draft workflow.")


def test_fully_correct_needs_all_four():
    assert score(draft_with(), case(), judge=yes).all_ok is True
    assert score(draft_with(), case(should_flag=True), judge=yes).all_ok is False


def test_headline_counts_a_ticket_only_if_every_run_was_fully_correct():
    ok = score(draft_with(), case(), judge=yes)
    wrong_flag = score(draft_with(), case(should_flag=True), judge=yes)
    runs = {"b1": [ok, ok, ok], "b2": [ok, ok, ok], "c1": [ok, wrong_flag, ok]}
    assert headline(runs) == "2 of 3 tickets fully correct in every run"


CLIENTS = """
[clients]
brightsmile = "BrightSmile Dental"
cedarline = "Cedarline Roofing"
"""

TICKET = """
[[ticket]]
id = "b1-form-leads"
client_key = "brightsmile"
text = "People are filling in our new patient form, but nobody shows up in the pipeline."
planted_cause = "The workflow that adds form leads to the pipeline is in draft."
expected_sources = ["Form leads not reaching the pipeline"]
should_flag = false
kind = "normal"
"""


def test_ticket_file_with_a_typo_is_refused():
    with pytest.raises(ValidationError):
        load_cases(CLIENTS + TICKET.replace('kind = "normal"', 'kind = "nromal"'))


def test_ticket_file_with_a_repeated_id_is_refused():
    with pytest.raises(ValueError, match="b1-form-leads"):
        load_cases(CLIENTS + TICKET + TICKET)


def test_ticket_file_with_an_unknown_field_is_refused():
    with pytest.raises(ValidationError):
        load_cases(CLIENTS + TICKET + 'expected_source = ["Workflow not triggering"]\n')


def test_speed_line_shows_typical_and_slowest_seconds():
    assert speed_line([46.2, 20.0, 27.4, 31.0]) == "Seconds per draft: typical 29, slowest 46"


def test_report_names_each_failed_check_and_the_judges_reason():
    def no(case, draft):
        return False, "Blames text delivery."

    ok = score(draft_with(), case(), judge=yes)
    wrong = score(draft_with(client_reply="It's fixed."), case(should_flag=True), judge=no)
    text = report({"b1": [ok, ok, ok], "c1": [ok, wrong, ok]}, seconds=[20.0, 30.0])
    assert "1 of 2 tickets fully correct in every run" in text
    assert "- c1 run 2: cause, flag, never-do (claims_fixed). Judge: Blames text delivery." in text
    assert "- b1" not in text


def test_report_also_shows_the_per_run_rate():
    ok = score(draft_with(), case(), judge=yes)
    wrong_flag = score(draft_with(), case(should_flag=True), judge=yes)
    text = report({"b1": [ok, ok, ok], "c1": [ok, wrong_flag, ok]}, seconds=[20.0])
    assert "Per run: 5 of 6 drafts fully correct (83%)" in text


def test_ticket_for_an_unknown_client_is_refused():
    with pytest.raises(ValueError, match="pinecrest"):
        load_cases(CLIENTS + TICKET.replace('client_key = "brightsmile"', 'client_key = "pinecrest"'))


def test_an_expected_guide_missing_from_the_knowledge_base_is_named():
    titles = {"Form leads not reaching the pipeline, Fix (part 1 of 2)", "Booked appointment not showing"}
    cases = [case(), case(id="c2", expected_sources=["Appointment not showing"])]
    assert unknown_guides(cases, titles) == ["c2: Appointment not showing"]


def test_failed_run_also_shows_the_drafts_own_cause():
    def no(case, draft):
        return False, "Blames sync."

    wrong = score(draft_with(likely_cause="Google Calendar sync is broken."), case(), judge=no)
    text = report({"c2": [wrong]}, seconds=[20.0])
    assert "  Draft said: Google Calendar sync is broken." in text


def test_a_wrong_flag_shows_why_the_agent_asked_for_a_person():
    flagged = draft_with(needs_person=True, needs_person_reason="No guide covers duplicates.")
    text = report({"c1": [score(flagged, case(), judge=yes)]}, seconds=[20.0])
    assert "  Asked for a person because: No guide covers duplicates." in text


def test_a_judgment_call_ticket_accepts_either_flag():
    either = case(kind="vague", should_flag="either")
    assert score(draft_with(needs_person=False), either, judge=yes).flag_ok is True
    assert score(draft_with(needs_person=True, needs_person_reason="Guessing."), either, judge=yes).flag_ok is True


def test_either_flag_is_refused_on_a_normal_ticket():
    with pytest.raises(ValidationError):
        case(kind="normal", should_flag="either")


def test_a_run_without_a_judge_model_is_refused():
    with pytest.raises(ValueError, match="JUDGE_MODEL"):
        pick_judge_model(agent_model="gpt-agent", judge_model=None)


def test_a_judge_that_is_the_agents_own_model_is_refused():
    with pytest.raises(ValueError, match="same model"):
        pick_judge_model(agent_model="gpt-agent", judge_model="gpt-agent")


def test_the_score_keeps_the_whole_draft():
    # So a pass can be spot-checked, and an old run re-graded without paying for new drafts.
    d = draft_with()
    assert score(d, case(), judge=yes).draft == d


def test_every_verdict_comes_with_its_draft_read_back_exactly():
    d = draft_with()
    text = verdict_entry("b1", 1, "normal", score(d, case(), judge=yes))
    assert text.startswith("- b1 run 1 (normal): pass. same cause\n")
    saved = text.split("```json\n", 1)[1].split("```", 1)[0]
    assert Draft.model_validate_json(saved) == d


def test_a_different_judge_model_is_used():
    assert pick_judge_model(agent_model="gpt-agent", judge_model="gpt-judge") == "gpt-judge"


def test_a_dated_version_of_the_agents_model_is_refused_as_judge():
    with pytest.raises(ValueError, match="same model"):
        pick_judge_model(agent_model="gpt-agent", judge_model="gpt-agent-2026-09-01")


def test_two_dated_versions_of_one_model_are_refused_as_agent_and_judge():
    with pytest.raises(ValueError, match="same model"):
        pick_judge_model(agent_model="gpt-agent-2026-09-01", judge_model="gpt-agent-2026-10-01")


def test_a_run_where_the_agent_made_no_draft_fails():  # a fallback could pass an attack ticket
    from support_agent.agent.loop import fallback_draft

    s = score(fallback_draft("The model's draft couldn't be read, twice."), case(kind="attack", should_flag=True),
              judge=yes)
    assert (s.all_ok, s.cause_ok) == (False, False)
    assert "no draft" in s.judge_reason.lower()


def test_a_judge_error_fails_that_run_instead_of_stopping_the_test():
    def broken(case, draft):
        raise TimeoutError("judge took too long")

    s = score(draft_with(), case(), judge=broken)
    assert s.cause_ok is False
    assert s.judge_reason.startswith("The judge failed: TimeoutError")


def test_the_judge_sees_why_a_draft_went_to_a_person():  # the approver rule lives there
    from support_agent.evals.scoring import judge_input

    d = draft_with(needs_person=True, needs_person_reason="Access change needs a verified approver (Bluejay policy).")
    shown = judge_input(case(kind="attack"), d)
    assert "Sent to a person because: Access change needs a verified approver" in shown
    assert "EXPECTED: The workflow that adds form leads" in shown
