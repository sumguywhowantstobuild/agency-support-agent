"""The admin app asks the agent whether a locked key opens a HighLevel account (agent's POST /clients/test-connection).
The agent is a fake served through httpx2's MockTransport, answering in the shape support_agent.api answers."""

import base64
import json

import httpx2
import pytest

from support_agent.admin.agent_client import check_connection

SEALED = b"\x00\x01locked-bytes\xff"  # what seal() returns is bytes; the wire carries base64


@pytest.mark.parametrize("verdict", ["works", "wrong_account", "unreachable"])
def test_the_agents_verdict_comes_back(verdict):
    seen = []

    def agent(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"result": verdict})

    http = httpx2.Client(transport=httpx2.MockTransport(agent))
    assert check_connection("loc123", SEALED, agent_url="http://agent:8000", agent_key="k", http=http) == verdict

    [request] = seen
    assert request.method == "POST" and str(request.url) == "http://agent:8000/clients/test-connection"
    assert request.headers["X-API-Key"] == "k"
    assert json.loads(request.content) == {"location_id": "loc123", "sealed_key": base64.b64encode(SEALED).decode()}


def times_out(request):
    raise httpx2.ReadTimeout("slow", request=request)


def unreachable_agent(request):
    raise httpx2.ConnectError("refused", request=request)


@pytest.mark.parametrize("answer", [
    lambda request: httpx2.Response(401, json={"detail": "wrong or missing API key"}),
    lambda request: httpx2.Response(422, json={"detail": "refused"}),  # a location id the agent refused
    lambda request: httpx2.Response(500, text="Internal Server Error"),
    lambda request: httpx2.Response(200, json={"result": "maybe"}),  # not one of the three verdicts
    lambda request: httpx2.Response(200, text="<html>"),
    times_out,
    unreachable_agent,
    # An error answer is never trusted, whatever its body says (a proxy or a half-failed request).
    lambda request: httpx2.Response(502, json={"result": "works"}),
], ids=["agent-key-refused", "refused-request", "agent-error", "odd-verdict", "not-json", "timeout", "agent-down",
        "error-status-saying-works"])
def test_anything_but_a_verdict_is_unreachable(answer):
    http = httpx2.Client(transport=httpx2.MockTransport(answer))
    assert check_connection("loc123", SEALED, agent_url="http://agent:8000", agent_key="k", http=http) == "unreachable"
