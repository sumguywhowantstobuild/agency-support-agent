"""Client keys locked with a key pair: the admin app holds the public half and can only lock; the agent holds the
private half and is the only one that can open."""

import pytest

from support_agent.keys import KeyOpenError, make_key_pair, open_sealed, seal


def test_a_sealed_secret_opens_with_its_private_key():
    public, private = make_key_pair()
    assert open_sealed(private, seal(public, "pit-abc")) == "pit-abc"


def test_sealing_twice_gives_different_bytes():
    public, _ = make_key_pair()
    assert seal(public, "pit-abc") != seal(public, "pit-abc")  # two clients with one key can't be told apart


def test_another_private_key_cannot_open_it():
    public, _ = make_key_pair()
    _, other_private = make_key_pair()
    with pytest.raises(KeyOpenError):
        open_sealed(other_private, seal(public, "pit-abc"))


@pytest.mark.parametrize("damage", [
    lambda sealed: sealed[:-1] + bytes([sealed[-1] ^ 1]),  # one bit changed
    lambda sealed: sealed[:20],  # cut short
    lambda sealed: b"",  # empty
])
def test_damaged_data_cannot_be_opened(damage):
    public, private = make_key_pair()
    with pytest.raises(KeyOpenError):
        open_sealed(private, damage(seal(public, "pit-abc")))


@pytest.mark.parametrize("bad_private", ["not base64 at all!", "c2hvcnQ=", ""])  # junk, 5 bytes, empty
def test_a_malformed_private_key_is_a_key_open_error(bad_private):
    public, _ = make_key_pair()
    with pytest.raises(KeyOpenError):
        open_sealed(bad_private, seal(public, "pit-abc"))


def test_the_error_never_contains_the_secret_or_the_key():
    public, _ = make_key_pair()
    _, other_private = make_key_pair()
    with pytest.raises(KeyOpenError) as caught:
        open_sealed(other_private, seal(public, "pit-secret-value"))
    shown = str(caught.value) + repr(caught.value) + str(caught.value.__cause__)
    assert "pit-secret-value" not in shown and other_private not in shown and public not in shown
    assert caught.value.__cause__ is None and caught.value.__suppress_context__  # nothing chained into a traceback
