"""The agent's client keys: fetched locked from the database per ticket and opened with the private half."""

import pytest
from pydantic import SecretStr

from support_agent.ghl.keystore import KeyStore
from support_agent.keys import KeyOpenError, make_key_pair, seal

PUBLIC, PRIVATE = make_key_pair()


def store(rows: dict[str, bytes]) -> KeyStore:
    return KeyStore(rows.get, SecretStr(PRIVATE))  # rows.get: what sealed_key_for_location() would answer


def test_token_for_an_active_client():
    assert store({"LOC_A": seal(PUBLIC, "pit-a")}).token_for("LOC_A") == "pit-a"


def test_none_when_the_database_has_no_key():
    # sealed_key_for_location() answers nothing for an unknown, paused or removed client
    assert store({}).token_for("LOC_PAUSED") is None


def test_a_key_locked_with_another_pair_is_an_error_not_a_refusal():
    other_public, _ = make_key_pair()
    with pytest.raises(KeyOpenError):
        store({"LOC_A": seal(other_public, "pit-a")}).token_for("LOC_A")
