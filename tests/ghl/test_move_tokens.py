"""The one-time move of GHL keys from .env into the database, locked. Its SQL carries only locked
values, so it can be piped to psql without a key ever showing."""

import re

from support_agent.ghl.move_tokens import move_sql
from support_agent.keys import make_key_pair, open_sealed

PUBLIC, PRIVATE = make_key_pair()
TOKENS = {"LOC_BRIGHT": "pit-bright-secret", "LOC_CEDAR": "pit-cedar-secret"}


def test_move_script_prints_no_token():
    sql = move_sql(TOKENS, PUBLIC)
    assert "pit-bright-secret" not in sql and "pit-cedar-secret" not in sql
    assert "LOC_BRIGHT" in sql and "LOC_CEDAR" in sql


def test_each_location_gets_its_own_key_back():
    sql = move_sql(TOKENS, PUBLIC)
    pairs = re.findall(r"\('(LOC_\w+)', '\\x([0-9a-f]+)'::bytea\)", sql)
    assert {location: open_sealed(PRIVATE, bytes.fromhex(sealed)) for location, sealed in pairs} == TOKENS


def test_a_location_with_no_client_stops_the_whole_move():
    # The SQL raises when a location has no client row, so a typo can't leave half the keys moved.
    assert "raise exception" in move_sql(TOKENS, PUBLIC)
