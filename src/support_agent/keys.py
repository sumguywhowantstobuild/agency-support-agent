"""Client keys locked with a key pair (libsodium sealed boxes, through PyNaCl). The admin app holds the public half: it can lock a
key but never open one. The agent holds the private half and is the only part that can open a locked key. Halves
travel as base64 text, so they fit in .env."""

import base64

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox


class KeyOpenError(Exception):
    """A locked key that won't open: the wrong private half, or damaged data."""


def make_key_pair() -> tuple[str, str]:
    """A new pair as (public, private), base64."""
    private = PrivateKey.generate()
    return _b64(bytes(private.public_key)), _b64(bytes(private))


def seal(public_key_b64: str, secret: str) -> bytes:
    return SealedBox(PublicKey(base64.b64decode(public_key_b64))).encrypt(secret.encode())


def open_sealed(private_key_b64: str, sealed: bytes) -> str:
    try:
        return SealedBox(PrivateKey(base64.b64decode(private_key_b64))).decrypt(sealed).decode()
    except (CryptoError, ValueError, TypeError):  # ValueError: a private half that isn't 32 bytes of base64
        raise KeyOpenError("the locked key would not open") from None


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()
