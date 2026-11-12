import pytest
from pydantic import ValidationError

from support_agent.models import Draft, Passage, Ticket, ToolResult

VALID_DRAFT = {
    "likely_cause": "The workflow is still a draft, so form leads never reach the pipeline.",
    "confidence": "medium",
    "evidence": ["Form submitted on Oct 6", "Workflow status: draft"],
    "fix_steps": ["Open the workflow", "Publish it", "Submit a test form"],
    "client_reply": "Thanks for flagging this. We found the setting that stopped new leads reaching your pipeline.",
    "sources": [{"title": "Form to pipeline", "url": "https://docs.google.com/document/d/example"}],
    "needs_person": False,
    "needs_person_reason": None,
    "checks_failed": [],
}


def test_valid_draft_is_accepted():
    draft = Draft(**VALID_DRAFT)
    assert draft.sources[0].title == "Form to pipeline"


def test_draft_rejects_unknown_confidence():
    with pytest.raises(ValidationError):
        Draft(**{**VALID_DRAFT, "confidence": "certain"})


def test_needs_person_requires_reason():
    with pytest.raises(ValidationError):
        Draft(**{**VALID_DRAFT, "needs_person": True, "needs_person_reason": None})


def test_ticket_keeps_other_client_names():
    ticket = Ticket(
        ticket_id="t1",
        client_key="brightsmile",
        location_id="LOC_A",
        text="Leads from my form are missing",
        other_client_names=["Cedarline Roofing"],
    )
    assert ticket.other_client_names == ["Cedarline Roofing"]

def test_passage_rejects_unknown_source():
    with pytest.raises(ValidationError):
        Passage(
            content="Publish the workflow so form leads reach the pipeline.",
            title="Form to pipeline",
            heading="Check the workflow",
            url="https://docs.google.com/document/d/example",
            source="blog",
            score=0.8,
        )


def test_tool_error_requires_note():
    with pytest.raises(ValidationError):
        ToolResult(status="error")


def test_empty_tool_result_needs_no_data():
    result = ToolResult(status="empty")
    assert result.data is None and result.note is None