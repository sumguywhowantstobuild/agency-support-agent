"""n8n's Postgres step fills in $1, $2... everywhere in a query, comments and quoted text included. A value with a line
break inside a comment ends the comment, and the rest of the value runs as SQL: anyone who can post a two-line Slack
message could run their own SQL. So a dollar sign followed by a number may only appear where a value goes."""

import json
import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).parent.parent / "n8n").glob("*.json"))


def postgres_queries():
    for path in WORKFLOWS:
        for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
            if node["type"] == "n8n-nodes-base.postgres" and node["parameters"].get("query"):
                yield pytest.param(node["parameters"]["query"], id=f"{path.stem}: {node['name']}")


def comments_and_quoted_text(sql: str) -> list[str]:
    """The parts of a query n8n should leave alone but doesn't: -- comments and '...' text."""
    return re.findall(r"--[^\n]*|'(?:[^']|'')*'", sql)


def test_workflows_are_found():
    assert WORKFLOWS and list(postgres_queries())  # an empty folder would make the check below pass on nothing


@pytest.mark.parametrize("query", list(postgres_queries()))
def test_no_placeholder_in_comments_or_quoted_text(query):
    for part in comments_and_quoted_text(query):
        assert not re.search(r"\$\d", part), f"n8n would fill a value in here: {part}"
