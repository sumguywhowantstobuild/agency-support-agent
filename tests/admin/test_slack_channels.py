"""The private channels the support bot is in, from Slack's users.conversations: Add client offers those that aren't a
client's yet (worked out by Add client from the whole list), and the hourly timer names every client's. Slack is a fake
served through httpx2's MockTransport, answering in Slack's own shape."""

import httpx2
import pytest

from support_agent.admin.add_client import _free_channels
from support_agent.admin.slack import Channel, SlackUnavailable, bot_channels
from fakes import a_client


def a_channel(channel_id, name):
    # The fields Slack sends for a private channel in users.conversations (api.slack.com/methods/users.conversations).
    return {"id": channel_id, "name": name, "is_channel": False, "is_group": True, "is_im": False, "is_mpim": False,
            "is_private": True, "created": 1761609600, "is_archived": False, "is_general": False, "unlinked": 0,
            "name_normalized": name, "is_shared": False, "is_org_shared": False, "is_pending_ext_shared": False,
            "pending_shared": [], "context_team_id": "T0BLUEJAY", "updated": 1761609600000,
            "parent_conversation": None, "creator": "U0ANA", "is_ext_shared": False, "shared_team_ids": ["T0BLUEJAY"],
            "pending_connected_team_ids": [], "topic": {"value": "", "creator": "", "last_set": 0},
            "purpose": {"value": "", "creator": "", "last_set": 0}}


class FakeSlack:
    """Two pages of channels, joined by Slack's cursor; records every request it was sent."""

    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.params.get("cursor", "") == "":
            return httpx2.Response(200, json={
                "ok": True,
                "channels": [a_channel("C0CEDAR", "cedarline-roofing"), a_channel("C0NEWA", "harbor-yoga")],
                "response_metadata": {"next_cursor": "dGVhbTpDMDYxRkE1UEI="},
            })
        return httpx2.Response(200, json={
            "ok": True,
            "channels": [a_channel("C0GONE", "gone-plumbing"), a_channel("C0NEWB", "maple-dental")],
            "response_metadata": {"next_cursor": ""},
        })


def test_free_channels_are_the_bots_channels_less_the_clients():
    slack = FakeSlack()
    http = httpx2.Client(transport=httpx2.MockTransport(slack))

    everything = bot_channels("slack-test-bot-token", http=http)  # every channel, following Slack's pages

    assert everything == [Channel("C0CEDAR", "cedarline-roofing"), Channel("C0NEWA", "harbor-yoga"),
                          Channel("C0GONE", "gone-plumbing"), Channel("C0NEWB", "maple-dental")]
    # Active and paused clients' channels are taken; a removed client's comes back through the client sheet (018).
    clients = [a_client("cedar", "Cedarline Roofing"), a_client("gone", "Gone Plumbing", status="paused",
                                                                reason="client_asked"),
               a_client("newb", "Maple Dental", status="removed")]
    free = _free_channels(clients, {}, lambda: everything)  # {}: no channel has belonged to another client (024)
    assert free == [Channel("C0NEWA", "harbor-yoga"), Channel("C0NEWB", "maple-dental")]
    # A channel that belonged to another client once is left out, whoever holds nothing of it now.
    assert _free_channels(clients, {"C0NEWA": "moved-away"}, lambda: everything) == [Channel("C0NEWB", "maple-dental")]
    assert [request.url.params.get("cursor") for request in slack.requests] == [None, "dGVhbTpDMDYxRkE1UEI="]
    for request in slack.requests:
        assert request.url.host == "slack.com" and request.url.path == "/api/users.conversations"
        assert request.headers["Authorization"] == "Bearer slack-test-bot-token"
        assert request.url.params["types"] == "private_channel"
        assert request.url.params["exclude_archived"] == "true"


def refuses(error):
    return lambda request: httpx2.Response(200, json={"ok": False, "error": error})


def times_out(request):
    raise httpx2.ReadTimeout("slow", request=request)


@pytest.mark.parametrize("answer", [
    refuses("missing_scope"),  # groups:read not added yet
    refuses("invalid_auth"),  # a wrong or revoked bot token
    lambda request: httpx2.Response(429, headers={"Retry-After": "30"}, json={"ok": False, "error": "ratelimited"}),
    lambda request: httpx2.Response(502, text="<html>Bad gateway</html>"),
    lambda request: httpx2.Response(200, json={"ok": True}),  # no channels field
    times_out,
], ids=["missing-scope", "bad-token", "rate-limited", "not-json", "odd-shape", "timeout"])
def test_any_slack_failure_is_slack_unavailable(answer):
    http = httpx2.Client(transport=httpx2.MockTransport(answer))
    with pytest.raises(SlackUnavailable):
        bot_channels("slack-test-bot-token", http=http)


@pytest.mark.parametrize("error", ["missing_scope", "invalid_auth", "ratelimited"])
def test_slacks_own_reason_is_kept_for_the_log(error):
    # "missing_scope" in the log says the groups:read permission is missing; a bare "unexpected answer" wouldn't.
    http = httpx2.Client(transport=httpx2.MockTransport(refuses(error)))
    with pytest.raises(SlackUnavailable, match=f"^{error}$"):
        bot_channels("slack-test-bot-token", http=http)
