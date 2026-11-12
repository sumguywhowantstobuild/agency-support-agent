"""Each client's GHL key, fetched from the database for every ticket and opened with the agent's private half.
Fresh per ticket, so a client added, paused or removed in the admin app counts from its next message, with
no restart. The database answers only for active clients: paused, removed and unknown ones get no key."""

from collections.abc import Callable
from functools import lru_cache

from pydantic import SecretStr

from support_agent.config import get_settings
from support_agent.keys import open_sealed
from support_agent.knowledge.search import get_db

FetchSealed = Callable[[str], bytes | None]  # location id -> the locked key, or None


class KeyStore:
    def __init__(self, fetch_sealed: FetchSealed, private_key: SecretStr):
        self._fetch = fetch_sealed
        self._private = private_key

    def token_for(self, location_id: str) -> str | None:
        """The client's GHL key, or None when it has none (unknown, paused or removed). A key that won't open (the
        pair was changed without re-entering the keys) raises KeyOpenError: a setup fault, not a refusal."""
        sealed = self._fetch(location_id)
        if sealed is None:
            return None
        return open_sealed(self._private.get_secret_value(), bytes(sealed))


@lru_cache
def get_keystore() -> KeyStore:
    return KeyStore(get_db().sealed_key_for_location, get_settings().ghl_key_private)
