"""The toolbox: what the model may call, and what it gets back."""

import json

import pytest

from support_agent.agent.tools import MAX_GHL_CHARS, ToolBox
from support_agent.models import DraftAnswer, Passage, ToolResult

GUIDE_URL = "https://docs.google.com/document/d/sms-guide/edit"


def passage():
    return Passage(content="SMS not sending > Fix\n\nLink the number.", title="SMS not sending", heading="Fix",
                   url=GUIDE_URL, source="guide", score=0.0381)


def toolbox(**search):
    return ToolBox("loc_bright", search=search or {"search_guides": lambda q: [passage()]})


def test_definitions_offer_only_the_knowledge_searches():
    tb = ToolBox("loc_bright", search={name: (lambda q: []) for name in
                                        ("search_guides", "search_ghl_help", "find_similar_tickets")})
    names = [d["name"] for d in tb.definitions()]
    assert names == ["search_guides", "search_ghl_help", "find_similar_tickets"]
    for d in tb.definitions():
        assert d["strict"] is True and d["parameters"]["required"] == ["query"]


def test_search_result_comes_back_with_its_link_and_is_remembered():
    tb = toolbox()
    result = tb.call("search_guides", {"query": "texts not sending"})
    assert result.status == "ok"
    assert result.data[0]["url"] == GUIDE_URL and result.data[0]["title"] == "SMS not sending"
    assert tb.seen_urls == {GUIDE_URL}
    assert tb.calls == 1


def test_unknown_tool_is_an_error_not_a_crash():
    result = toolbox().call("delete_contact", {"query": "x"})
    assert result.status == "error" and "unknown tool" in result.note


def test_blank_query_is_an_error_and_does_not_search():
    def must_not_run(q):
        raise AssertionError("should not search")

    result = toolbox(search_guides=must_not_run).call("search_guides", {"query": "  "})
    assert result.status == "error"


def test_no_matches_is_empty_not_error():
    result = toolbox(search_guides=lambda q: []).call("search_guides", {"query": "fax machine"})
    assert result.status == "empty"


def test_search_failure_is_reported_without_the_raw_error():
    def broken(q):
        raise RuntimeError("password authentication failed for user agent_reader")

    result = toolbox(search_guides=broken).call("search_guides", {"query": "texts"})
    assert result.status == "error" and "password" not in result.note


def test_draft_format_is_strict_for_structured_outputs():
    """OpenAI's strict mode needs every field required and no extra fields, on every object."""
    schema = DraftAnswer.model_json_schema()
    for obj in [schema, *schema.get("$defs", {}).values()]:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])

# GHL checks

GHL_NAMES = {"find_contact", "contact_details", "contact_tasks", "recent_messages", "opportunities", "appointments",
             "free_slots", "calendars", "workflows", "forms", "form_submissions", "pipelines", "account", "users",
             "tags"}


class FakeGhl:
    """Stands in for GhlAccess: one client's sub-account. Records which checks ran with which arguments."""

    def __init__(self, location_id="loc_bright", results=None):
        self.location_id = location_id
        self.results = results or {}
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def check(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.results.get(name, ToolResult(status="ok", data={"found": name}))

        return check


def ghl_toolbox(ghl=None):
    return ToolBox("loc_bright", search={}, ghl=ghl or FakeGhl())


def test_ghl_checks_are_offered_when_the_client_has_ghl():
    definitions = ghl_toolbox().definitions()
    assert {d["name"] for d in definitions} == GHL_NAMES
    for d in definitions:  # OpenAI strict mode: every field required, nothing extra
        assert d["strict"] is True and d["parameters"]["additionalProperties"] is False
        assert set(d["parameters"]["required"]) == set(d["parameters"]["properties"])


def test_tool_list_has_no_write_tools():
    names = {d["name"] for d in ghl_toolbox().definitions()}
    assert not any(w in n for n in names for w in ("create", "update", "delete", "send", "add", "remove"))


def test_model_cannot_switch_location():
    ghl = FakeGhl("loc_bright")
    result = ghl_toolbox(ghl).call("find_contact", {"query": "Jane", "location_id": "loc_cedar", "locationId": "x"})
    assert result.status == "ok"
    assert ghl.calls == [("find_contact", (), {"query": "Jane"})]  # only the declared argument reaches GHL


def test_toolbox_refuses_ghl_access_for_another_client():
    with pytest.raises(ValueError):
        ToolBox("loc_bright", search={}, ghl=FakeGhl("loc_cedar"))


@pytest.mark.parametrize("args", [{}, {"calendar_id": "  "}, {"calendar_id": 42}])
def test_ghl_check_without_its_id_is_an_error_and_does_not_call_ghl(args):
    ghl = FakeGhl()
    result = ghl_toolbox(ghl).call("free_slots", args)
    assert result.status == "error" and "calendar_id" in result.note
    assert ghl.calls == []


def test_failed_ghl_check_is_remembered_for_the_draft():
    ghl = FakeGhl(results={"workflows": ToolResult(status="error", note="Couldn't reach GHL just now.")})
    tb = ghl_toolbox(ghl)
    tb.call("workflows", {})
    tb.call("forms", {})
    assert tb.ghl_failed == {"workflows": "Couldn't reach GHL just now."}


def test_lookups_already_read_never_change_under_the_reader():  # the hand-over at the time limit
    ghl = FakeGhl(results={"forms": ToolResult(status="error", note="Couldn't reach GHL just now.")})
    tb = ghl_toolbox(ghl)
    tb.call("workflows", {})
    ran, failed = tb.ran, tb.ghl_failed  # what the hand-over draft reads while late work may still run
    tb.call("forms", {})
    assert [r.name for r in ran] == ["workflows"] and failed == {}
    assert [r.name for r in tb.ran] == ["workflows", "forms"] and "forms" in tb.ghl_failed


def test_ghl_check_that_crashes_is_an_error_without_the_raw_error():
    class BrokenGhl(FakeGhl):
        def workflows(self):
            raise RuntimeError("pit-secret-123 refused at 192.0.2.1")

    tb = ghl_toolbox(BrokenGhl())
    result = tb.call("workflows", {})
    assert result.status == "error" and "pit-secret" not in result.note and "192.0.2" not in result.note
    assert "workflows" in tb.ghl_failed


def test_large_ghl_result_is_trimmed_with_a_note():  # one long history can't blow up a ticket's AI cost
    big = ToolResult(status="ok", data={"messages": [{"body": "x" * 100}] * 500})
    result = ghl_toolbox(FakeGhl(results={"recent_messages": big})).call("recent_messages", {"contact_id": "c1"})
    assert result.status == "ok" and "trimmed" in result.note
    assert len(json.dumps(result.data)) <= MAX_GHL_CHARS  # measured as the model receives it


def test_trimming_keeps_the_original_note():
    big = ToolResult(status="empty", data={"q": ['"quoted"'] * 3000}, note="That doesn't prove it's missing.")
    result = ghl_toolbox(FakeGhl(results={"forms": big})).call("forms", {})
    assert "doesn't prove" in result.note and "trimmed" in result.note
    assert len(json.dumps(result.data)) <= MAX_GHL_CHARS


def test_small_ghl_result_is_passed_whole():
    small = ToolResult(status="ok", data={"forms": [{"id": "f1"}]})
    assert ghl_toolbox(FakeGhl(results={"forms": small})).call("forms", {}) == small


def test_customer_details_from_ghl_are_remembered_for_the_guard():
    contact = {"id": "c1", "firstName": "Jane", "lastName": "Doe", "contactName": "jane doe",
               "email": "jane@example.com", "phone": "+15551234567"}
    ghl = FakeGhl(results={
        "find_contact": ToolResult(status="ok", data={"contacts": [contact]}),
        "forms": ToolResult(status="ok", data={"forms": [{"id": "f1", "name": "New Patient Form"}]}),
    })
    tb = ghl_toolbox(ghl)
    tb.call("find_contact", {"query": "Jane"})
    tb.call("forms", {})
    assert {"Jane Doe", "jane doe", "jane@example.com", "+15551234567"} <= tb.seen_personal
    assert "Jane" not in tb.seen_personal  # lone first names would raise false alarms ("Will", "May")
    assert "New Patient Form" not in tb.seen_personal  # setup names aren't personal details


def test_lone_first_and_last_names_are_remembered_for_saving_only():  # a contact with one name
    found = ToolResult(status="ok", data={"contacts": [{"id": "c1", "firstName": "Jane", "lastName": ""},
                                                       {"id": "c2", "first_name": "", "last_name": "Okafor"}]})
    tb = ghl_toolbox(FakeGhl(results={"find_contact": found}))
    tb.call("find_contact", {"query": "Jane"})
    assert {"Jane", "Okafor"} <= tb.seen_name_parts
    assert "Jane" not in tb.seen_personal  # the reply check keeps ignoring lone names (false alarms)


def test_emails_and_phones_inside_free_text_are_remembered_too():
    found = ToolResult(status="ok", data={
        "messages": [{"body": "Call me at (555) 987-6543 or mail jo.ann@example.org thanks"}],
        "others": {"full_name": "Ann Lee", "first_name": "Ann", "last_name": "Lee"},
    })
    tb = ghl_toolbox(FakeGhl(results={"recent_messages": found}))
    tb.call("recent_messages", {"contact_id": "c1"})
    assert {"(555) 987-6543", "jo.ann@example.org", "Ann Lee"} <= tb.seen_personal


def test_lookups_are_recorded_in_order_by_name_and_result_only():  # which checks ran
    tb = toolbox(search_guides=lambda q: [passage()], search_ghl_help=lambda q: [])
    tb.call("search_guides", {"query": "Jane Doe texts not sending"})
    tb.call("search_ghl_help", {"query": "texts"})
    tb.call("search_guides", {"query": ""})  # refused before searching: still a lookup that failed
    tb.call("Jane Doe's contact", {})  # a made-up tool name could carry anything
    assert [r.model_dump() for r in tb.ran] == [
        {"name": "search_guides", "result": "ok"},
        {"name": "search_ghl_help", "result": "empty"},
        {"name": "search_guides", "result": "error"},
        {"name": "unknown_tool", "result": "error"},
    ]
    assert "Jane" not in json.dumps([r.model_dump() for r in tb.ran])  # never what was searched or found
