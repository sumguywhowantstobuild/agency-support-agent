"""Make a sign-in pass for the admin app on the test copy, which has no Cloudflare gate.

    python scripts/mint_test_pass.py <email> <key file> <audience>

Writes a fresh key set to <key file> (the copy's ADMIN_TEST_KEYS points to it) and prints a pass signed for <email>,
valid for 8 hours, to send as the Cf-Access-Jwt-Assertion header. Earlier passes stop working, since their key is
gone. The live app refuses to start with a key file, so these passes can never open it.
"""

import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

TEST_ISSUER = "https://test.invalid"


def make_keys() -> tuple[rsa.RSAPrivateKey, str, str]:
    """A new private key, its key id, and the public key set as JSON (the shape Cloudflare publishes)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = secrets.token_hex(8)
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True) | {"kid": kid, "alg": "RS256", "use": "sig"}
    return private_key, kid, json.dumps({"keys": [public]})


def sign_pass(private_key: rsa.RSAPrivateKey, kid: str, email: str, audience: str, *, issuer: str = TEST_ISSUER,
              lifetime: timedelta = timedelta(hours=8), issued_at: datetime | None = None) -> str:
    now = issued_at or datetime.now(timezone.utc)
    claims = {"aud": [audience], "iss": issuer, "email": email, "iat": now, "exp": now + lifetime,
              "sub": secrets.token_hex(8)}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    email, key_file, audience = argv
    private_key, kid, jwks = make_keys()
    Path(key_file).write_text(jwks, encoding="utf-8")
    print(sign_pass(private_key, kid, email, audience))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
