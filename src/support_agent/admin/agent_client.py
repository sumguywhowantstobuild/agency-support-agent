"""The admin app's one call to the agent: does this locked key open this HighLevel account (the agent's
POST /clients/test-connection)? The admin app can lock a key but never open one, so the agent tests it."""

import base64
import logging
from collections.abc import Callable
from typing import Literal

import httpx2
from fastapi import Depends

from support_agent.admin.settings import AdminSettings, get_admin_settings

logger = logging.getLogger(__name__)

Verdict = Literal["works", "wrong_account", "unreachable"]
VERDICTS = ("works", "wrong_account", "unreachable")
ConnectionChecker = Callable[[str, bytes], Verdict]  # (location id, locked key) -> verdict
# The agent gives up at 20 s (CONNECTION_TEST_SECONDS) and answers "unreachable". This limit counts each wait apart
# (connect, send, each read), so it only ends a stuck call in the background; Add client bounds the whole call to
# its 25 s (ANSWER_SECONDS).
AGENT_SECONDS = 25


def check_connection(location_id: str, sealed: bytes, *, agent_url: str, agent_key: str,
                     http: httpx2.Client) -> Verdict:
    """The agent's verdict; anything else (agent down, slow, refusing, an odd answer) counts as "unreachable":
    nothing gets saved on it, and the person can try again."""
    try:
        response = http.post(f"{agent_url}/clients/test-connection", headers={"X-API-Key": agent_key},
                             json={"location_id": location_id, "sealed_key": base64.b64encode(sealed).decode()})
        response.raise_for_status()
        verdict = response.json()["result"]
    except (httpx2.HTTPError, ValueError, KeyError, TypeError) as exc:  # type name only: no URL, no key in the log
        logger.warning("connection test for %s failed: %s", location_id, type(exc).__name__)
        return "unreachable"
    if verdict not in VERDICTS:
        logger.warning("connection test for %s: odd verdict", location_id)
        return "unreachable"
    return verdict


def get_connection_checker(settings: AdminSettings = Depends(get_admin_settings)) -> ConnectionChecker:
    def check(location_id: str, sealed: bytes) -> Verdict:
        with httpx2.Client(timeout=AGENT_SECONDS) as http:
            return check_connection(location_id, sealed, agent_url=settings.agent_url,
                                    agent_key=settings.agent_api_key.get_secret_value(), http=http)
    return check
