"""The admin app's one Slack call: which private channels the support bot is in (users.conversations, needs the
read-only groups:read permission). Add client offers those that aren't a client's yet (it works that out from the whole
list), and the hourly timer reads every client's channel name from it."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx2
from fastapi import Depends

from support_agent.admin.settings import AdminSettings, get_admin_settings

CONVERSATIONS_URL = "https://slack.com/api/users.conversations"
SLACK_SECONDS = 10  # each wait of each page; Add client stops waiting for the whole list sooner (its SLACK_SECONDS)


class SlackUnavailable(Exception):
    """Slack refused, was slow or answered something unexpected: the channel list can't be shown right now."""


@dataclass(frozen=True)
class Channel:
    id: str
    name: str


def bot_channels(bot_token: str, http: httpx2.Client) -> list[Channel]:
    """Every private, unarchived channel the bot is in, following Slack's pages. Which of them are free is for the
    caller to work out."""
    found = []
    cursor = None
    while True:
        params = {"types": "private_channel", "exclude_archived": "true", "limit": "200"}
        if cursor:
            params["cursor"] = cursor
        try:
            page = http.get(CONVERSATIONS_URL, params=params, headers={"Authorization": f"Bearer {bot_token}"}).json()
            if not page.get("ok"):
                # Slack's own error code (missing_scope, invalid_auth, ratelimited): never holds the token.
                raise SlackUnavailable(str(page.get("error", "not ok")))
            found += [Channel(channel["id"], channel["name"]) for channel in page["channels"]]
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
        except httpx2.HTTPError as exc:  # slow, unreachable: the type name only, as the other admin logs
            raise SlackUnavailable(type(exc).__name__) from None
        except (ValueError, KeyError, TypeError, AttributeError):  # not JSON, or not Slack's shape
            raise SlackUnavailable("unexpected answer") from None
        if not cursor:
            return found


ChannelLister = Callable[[], list[Channel]]  # every channel the bot is in


def channel_lister(settings: AdminSettings) -> ChannelLister:
    """The lister the pages and the hourly timer share, reading with the settings' bot token."""
    def everything() -> list[Channel]:
        with httpx2.Client(timeout=SLACK_SECONDS) as http:
            return bot_channels(settings.slack_bot_token.get_secret_value(), http=http)
    return everything


def get_channel_lister(settings: AdminSettings = Depends(get_admin_settings)) -> ChannelLister:
    return channel_lister(settings)
