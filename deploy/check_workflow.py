"""Checks a workflow exported from the test copy's n8n before it goes live (deploy/apply.sh).

  python deploy/check_workflow.py ID:TYPE ... < workflow.json

ID:TYPE are the logins (credentials) the live n8n has. Prints each problem and exits 1 if there is any:
- a $1-style value inside an SQL comment or quoted text (n8n fills those in too: SETUP section 9);
- a Slack step without its Slack login. The copy has no Slack login on purpose, so its editor warns on every Slack
  step; re-picking or clearing the login there to silence the warning would ship a step that can't post;
- a step pointing to a login the live n8n doesn't have.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from test_n8n_queries import comments_and_quoted_text  # noqa: E402  (one definition of the SQL rule)

SLACK_STEPS = {"n8n-nodes-base.slack", "n8n-nodes-base.slackTrigger"}


def needs_slack(node: dict) -> bool:
    return node["type"] in SLACK_STEPS or node["parameters"].get("nodeCredentialType") == "slackApi"


def login_problems(workflows: list[dict], live: dict[str, str]) -> list[str]:
    problems = []
    for w in workflows:
        for node in w["nodes"]:
            logins = node.get("credentials") or {}
            if needs_slack(node) and "slackApi" not in logins:
                problems.append(f"{w['name']}: step '{node['name']}' has no Slack login")
            for kind, login in logins.items():
                if live.get(login.get("id")) != kind:
                    problems.append(f"{w['name']}: step '{node['name']}' uses login '{login.get('name')}', "
                                    "which live doesn't have")
    return problems


def sql_problems(workflows: list[dict]) -> list[str]:
    return [f"{w['name']}: step '{node['name']}': n8n would fill a value in here: {part}"
            for w in workflows for node in w["nodes"] if node["type"] == "n8n-nodes-base.postgres"
            for part in comments_and_quoted_text(node["parameters"].get("query") or "") if re.search(r"\$\d", part)]


if __name__ == "__main__":
    workflows = json.load(sys.stdin)
    live = dict(arg.split(":", 1) for arg in sys.argv[1:])
    problems = sql_problems(workflows) + login_problems(workflows, live)
    for p in problems:
        print("  " + p)
    sys.exit(1 if problems else 0)
