"""The checks deploy/apply.sh runs on a workflow before it leaves the test copy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from check_workflow import login_problems, sql_problems  # noqa: E402

LIVE = {"CredSlack0000001": "slackApi", "CredPgsql0000002": "postgres"}  # made-up IDs of the logins live has
SLACK = {"slackApi": {"id": "CredSlack0000001", "name": "Slack bot"}}


def workflow(*nodes):
    return [{"name": "Ticket intake", "nodes": list(nodes)}]


def test_a_slack_step_with_its_login_cleared_is_caught():
    wf = workflow({"name": "Post card", "type": "n8n-nodes-base.slack", "parameters": {}})
    assert login_problems(wf, LIVE) == ["Ticket intake: step 'Post card' has no Slack login"]


def test_a_slack_call_through_an_http_step_needs_the_login_too():
    wf = workflow({"name": "Get card", "type": "n8n-nodes-base.httpRequest",
                   "parameters": {"authentication": "predefinedCredentialType", "nodeCredentialType": "slackApi"}})
    assert login_problems(wf, LIVE) == ["Ticket intake: step 'Get card' has no Slack login"]


def test_a_login_the_live_system_does_not_have_is_caught():
    wf = workflow({"name": "Post card", "type": "n8n-nodes-base.slack", "parameters": {},
                   "credentials": {"slackApi": {"id": "NEWONE", "name": "Slack bot 2"}}})
    assert login_problems(wf, LIVE) == ["Ticket intake: step 'Post card' uses login 'Slack bot 2', which live doesn't have"]


def test_a_workflow_with_every_login_in_place_passes():
    wf = workflow({"name": "Slack message", "type": "n8n-nodes-base.slackTrigger", "parameters": {}, "credentials": SLACK},
                  {"name": "Save", "type": "n8n-nodes-base.postgres", "parameters": {},
                   "credentials": {"postgres": {"id": "CredPgsql0000002", "name": "Support DB"}}},
                  {"name": "Settings", "type": "n8n-nodes-base.set", "parameters": {}})
    assert login_problems(wf, LIVE) == []


def test_a_value_inside_a_sql_comment_is_caught():
    wf = workflow({"name": "Save", "type": "n8n-nodes-base.postgres",
                   "parameters": {"query": "select 1; -- uses $1 here\nselect $2"}})
    assert sql_problems(wf) == ["Ticket intake: step 'Save': n8n would fill a value in here: -- uses $1 here"]
