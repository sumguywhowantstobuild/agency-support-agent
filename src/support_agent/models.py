"""Data shapes passed between n8n, the agent and its tools."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class Source(BaseModel):
    """A document the draft relied on, so the support person can check it."""

    model_config = ConfigDict(extra="forbid")  # OpenAI's strict format allows no extra fields
    title: str
    url: str


class Ticket(BaseModel):
    """A client's message, as n8n hands it to the agent."""

    ticket_id: str
    client_key: str
    location_id: str  # the client's GHL sub-account, chosen by n8n from the Slack channel, never by the model
    text: str
    other_client_names: list[str]  # used to catch another client's name leaking into the reply


class CheckRun(BaseModel):
    """One lookup the agent made: the tool's name and how it went, never what was asked or found (client data)."""

    name: str
    result: Literal["ok", "empty", "error"]


class Draft(BaseModel):
    """What the agent returns for a person to approve."""

    likely_cause: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[str]
    fix_steps: list[str]
    client_reply: str
    sources: list[Source]
    needs_person: bool
    needs_person_reason: str | None = None
    checks_failed: list[str] = []  # never-do rules the draft broke (guards.py)
    ghl_checks_failed: list[str] = []  # GHL checks that couldn't run; the draft's evidence says why
    keep_out_of_past_tickets: bool = False  # names a customer GHL showed: save_past_ticket refuses it (migration 014)
    checks_ran: list[CheckRun] = []  # every search and GHL check, in order: shown on the card and logged
    suspicious_sources: list[str] = []  # sources the model saw planted instructions in: a warning on the card

    @model_validator(mode="after")
    def _reason_required(self):
        if self.needs_person and not self.needs_person_reason:
            raise ValueError("needs_person_reason is required when needs_person is true")
        return self

class DraftAnswer(BaseModel):
    """What the model fills in. Code then checks it against the never-dos and turns it into a Draft."""

    model_config = ConfigDict(extra="forbid")  # OpenAI's strict format allows no extra fields

    likely_cause: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[str]
    fix_steps: list[str]
    client_reply: str
    sources: list[Source]
    needs_person: bool
    needs_person_reason: str | None
    access_change_requested: bool  # Bluejay policy: a person verifies the approver for any access change
    suspicious_sources: list[str]  # titles of search results with a line aimed at the assistant: staff clean them up


class Passage(BaseModel):
    """One piece of text a search found, with where it came from so the draft can cite it."""

    content: str
    title: str
    heading: str
    url: str
    source: Literal["guide", "ghl_help", "past_ticket"]
    score: float  # how well it matched the search; higher is better


class ToolResult(BaseModel):
    """What every tool hands back to the agent."""

    status: Literal["ok", "empty", "error"]
    data: Any = None
    note: str | None = None  # on an error, what went wrong, so the agent can try another way or flag a person

    @model_validator(mode="after")
    def _note_required_on_error(self):
        if self.status == "error" and not self.note:
            raise ValueError("note is required when status is error")
        return self