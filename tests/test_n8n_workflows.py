"""How the two Slack workflows are wired: who counts as staff comes from the database's staff list, and a paused
client's message never reaches the AI. Reads the workflow files; the test copy's n8n runs them for real."""

import json
import re
import subprocess
from pathlib import Path

import pytest

N8N = Path(__file__).parent.parent / "n8n"


def workflow(name: str) -> dict:
    return json.loads((N8N / f"{name}.json").read_text(encoding="utf-8"))


def node(w: dict, name: str) -> dict:
    return next(n for n in w["nodes"] if n["name"] == name)


@pytest.mark.parametrize("name", ["intake", "approval"])
def test_staff_come_from_the_database_not_a_typed_list(name):
    w = workflow(name)
    settings = [a["name"] for a in node(w, "Settings")["parameters"]["assignments"]["assignments"]]
    assert "staff_user_ids" not in settings
    assert not any("staff_user_ids" in json.dumps(n["parameters"]) for n in w["nodes"])
    assert any(re.search(r"is_staff\(\$\d\)", n["parameters"].get("query") or "") for n in w["nodes"])


def reachable(w: dict, start: str, output: int | None = None, without: str | None = None) -> set[str]:
    """Steps a run can reach from start (only from one of its outputs, if given), never passing through without."""
    outs = w["connections"].get(start, {}).get("main", [])
    todo = [o["node"] for i, links in enumerate(outs) if output is None or i == output for o in links or []]
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen or name == without:
            continue
        seen.add(name)
        todo += [o["node"] for links in w["connections"].get(name, {}).get("main", []) for o in links or []]
    return seen


def test_a_paused_clients_message_never_reaches_the_ai():
    w = workflow("intake")
    ai_steps = {"Claim slot", "Ask the agent"}
    # Every way to the AI passes the pause check, and its "paused" side records the card without it.
    assert not ai_steps & reachable(w, "Slack message", without="Client paused?")
    assert not ai_steps & reachable(w, "Client paused?", output=0)
    assert {"Save 'client paused'", "Build card", "Post card"} <= reachable(w, "Client paused?", output=0)
    assert ai_steps <= reachable(w, "Client paused?", output=1)
    # The status is read again after the ticket is saved, not taken from Find client: a pause made in between still
    # gets the "paused" card.
    assert links(w, "Draft needed?") == ["Client status now"] and links(w, "Client status now") == ["Client paused?"]
    now = " ".join(node(w, "Client status now")["parameters"]["query"].split())
    assert "select c.status, c.pause_reason from (select 1) as one left join client_map c on c.channel_id = $1;" in now
    assert "$('Read message').first().json.channel_id" in node(w, "Client status now")["parameters"]["options"]["queryReplacement"]
    condition = node(w, "Client paused?")["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert condition == "={{ $json.status === 'paused' }}"
    assert "$('Client status now').first().json.pause_reason" in node(w, "Save 'client paused'")["parameters"]["options"]["queryReplacement"]


def run_code(w: dict, step: str, steps: dict, input: dict | None = None) -> list[dict]:
    """What a Code step returns, run by Node with the earlier steps' output the test gives."""
    code = node(w, step)["parameters"]["jsCode"]
    done = subprocess.run(["node", str(Path(__file__).parent / "run_n8n_code.js")], check=True, capture_output=True,
                          input=json.dumps({"code": code, "steps": steps, "input": input}), text=True, encoding="utf-8")
    return json.loads(done.stdout)


DRAFT_ID = "0b6f1c2e-3d4a-4b5c-8d6e-7f8091a2b3c4"
CARD_STEPS = {
    "Settings": {"slack_workspace_url": "https://example.slack.com/", "support_queue_channel": "C0QUEUE"},
    "Find client": {"display_name": "Bright <Smile>"},  # no reason here: the card takes it from the saved draft
    "Read message": {"channel_id": "C0CLIENT", "thread_ts": "1730000000.000100"},
    "Save message": {"full_text": "My booking form is broken"},
}


def test_a_paused_clients_card_says_answer_by_hand_with_the_reason():
    row = {"draft_id": DRAFT_ID, "status": "client_paused", "draft": {"paused": "privacy_hold"}}
    [card] = run_code(workflow("intake"), "Build card", CARD_STEPS, row)
    blocks = json.loads(card["json"]["blocks"])["blocks"]
    sections = " ".join(b["text"]["text"] for b in blocks if b["type"] == "section")
    assert card["json"]["text"] == "Paused: answer by hand · Privacy hold · Bright &lt;Smile&gt;"
    assert "*Paused: answer by hand* · Privacy hold" in sections
    assert "Bright &lt;Smile&gt;" in sections and "Bright <Smile>" not in sections
    assert "> My booking form is broken" in sections
    [actions] = [b for b in blocks if b["type"] == "actions"]
    assert [(e["action_id"], e["value"]) for e in actions["elements"]] == [("take_over", DRAFT_ID)]


def test_a_paused_card_can_be_taken_over_and_replaced_like_a_limit_card():
    take_over = " ".join(node(workflow("approval"), "Decide")["parameters"]["query"].split())
    assert "$2 = 'taken_over' and d.status in ('agent_unavailable', 'limit_reached', 'client_paused')" in take_over
    replace = " ".join(node(workflow("intake"), "Remember card")["parameters"]["query"].split())
    assert "d.status in ('pending', 'agent_unavailable', 'limit_reached', 'client_paused')" in replace


def test_an_approved_reply_is_saved_as_a_past_ticket_only_while_the_client_is_active():
    # Saving sends the reply to OpenAI for the search index, which a privacy hold must not.
    # The status is read again right before Embed, not taken from the click: a pause made while the reply was being
    # sent to Slack (seconds) must still stop it.
    w = workflow("approval")
    saving = {"Embed", "Save past ticket"}
    assert links(w, "Past ticket text") == ["Client status now"]
    assert links(w, "Client status now") == ["Client active?"]
    assert links(w, "Client active?", 0) == ["Embed"]
    assert links(w, "Client active?", 1) == ["Note: past ticket not saved (paused)"]
    assert not saving & reachable(w, "Slack click", without="Client active?")
    assert "Update card" in reachable(w, "Send reply", output=0, without="Client active?")  # the card still updates
    now = " ".join(node(w, "Client status now")["parameters"]["query"].split())
    assert "select coalesce((select c.status = 'active' from client_map c where c.channel_id = $1), false)" in now
    assert "$('Decide').first().json.channel_id" in node(w, "Client status now")["parameters"]["options"]["queryReplacement"]
    condition = node(w, "Client active?")["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert condition == "={{ $json.client_active === true }}"
    assert "client_status" not in node(w, "Decide")["parameters"]["query"]  # no stale copy left to use by mistake
    # No automatic retry: a retry would go out later without this check. A failed call stops the run, and
    # the error workflow alerts the team; only that past ticket is lost.
    assert not node(w, "Embed").get("retryOnFail")


def links(w: dict, step: str, output: int = 0) -> list[str]:
    """The steps one output of a step leads to directly."""
    return [o["node"] for o in w["connections"][step]["main"][output]]


CLICK_SETTINGS = {"support_queue_channel": "C0QUEUE", "slack_workspace_url": "https://example.slack.com",
                  "embedding_model": "text-embedding-3-small", "alerts_channel": "C0ALERTS"}
TAKE_OVER = {"type": "block_actions", "user": {"id": "U0STAFF01"}, "trigger_id": "1.2.3",
             "actions": [{"action_id": "take_over", "value": DRAFT_ID}]}


@pytest.mark.parametrize(("is_staff", "kind"), [(False, "not_staff"), (True, "decide")])
def test_a_click_is_refused_unless_the_staff_list_says_yes(is_staff, kind):
    w = workflow("approval")
    assert links(w, "Settings") == ["Check staff"] and links(w, "Check staff") == ["Read click"]
    assert "select is_staff($1) as is_staff" in node(w, "Check staff")["parameters"]["query"]
    assert "$('Parse click').first().json.user?.id" in node(w, "Check staff")["parameters"]["options"]["queryReplacement"]
    steps = {"Settings": CLICK_SETTINGS, "Parse click": TAKE_OVER, "Check staff": {"is_staff": is_staff}}
    [click] = run_code(w, "Read click", steps)
    assert click["json"]["kind"] == kind


def test_a_staff_members_message_ends_before_any_ticket():
    w = workflow("intake")
    assert links(w, "Read message") == ["Find client"]
    find = node(w, "Find client")["parameters"]
    assert " ".join(find["query"].split()).endswith("where not is_staff($2);")  # staff: no row, the run ends
    assert find["options"]["queryReplacement"] == "={{ [ $json.channel_id, $json.slack_user ] }}"
    assert not {"Save message", "Alert: unknown channel"} & reachable(w, "Slack message", without="Find client")


def test_the_card_updates_before_the_past_ticket_save():
    # n8n ("v1" order) runs a step's branches one at a time, the highest on the canvas first. A failed OpenAI call or
    # status read stops the run, so the card ("Approved and sent by ...") must already be updated by then.
    w = workflow("approval")
    assert w["settings"]["executionOrder"] == "v1"
    pos = {n["name"]: n["position"] for n in w["nodes"]}
    branches = sorted(links(w, "Send reply"), key=lambda name: (pos[name][1], pos[name][0]))
    assert branches == ["Get card", "Past ticket text"]


ADDED_STEPS = [("intake", "Client status now"), ("intake", "Client paused?"), ("intake", "Save 'client paused'"),
               ("approval", "Check staff"), ("approval", "Client status now"), ("approval", "Client active?")]


@pytest.mark.parametrize(("name", "added"), ADDED_STEPS)
def test_a_step_after_an_added_step_names_its_source(name, added):
    # An added step changes what $json holds for the step after it: Embed lost the past-ticket text that way.
    # So the steps right after an added one read earlier steps by name. Only the checks that follow their
    # own lookup ("Client paused?" and "Client active?" after "Client status now") read $json, on purpose.
    w = workflow(name)
    after = {n for i in range(len(w["connections"][added]["main"])) for n in links(w, added, i)}
    for step in after - {"Client paused?", "Client active?"}:
        assert "$json" not in json.dumps(node(w, step)["parameters"]), f"{step} reads $json after {added}"


def test_find_client_skips_removed_clients():
    # A removed client's channel finds no client, so it raises the unknown-channel alert (the bot should have left it).
    query = " ".join(node(workflow("intake"), "Find client")["parameters"]["query"].split())
    assert "left join client_map c on c.channel_id = $1 and c.status <> 'removed'" in query
    assert "pause_reason" not in query  # the pause is read after the ticket is saved ("Client status now")
