"""Who is asking. Cloudflare Access signs people in at the gate and adds a signed pass to every request; the app checks
that pass itself (signature, audience, issuer, expiry), then the staff list, on every request. So a request that slips
past the gate, or comes from someone taken off the list, is refused."""

from functools import lru_cache
from pathlib import Path
from typing import Protocol

import jwt
import psycopg
from fastapi import Depends, Request

from support_agent.admin.db import READ_ONLY_METHODS, AdminDb, Staff, get_admin_db
from support_agent.admin.settings import AdminSettings, get_admin_settings

PASS_HEADER = "Cf-Access-Jwt-Assertion"

# How far this server's clock may differ from Cloudflare's when a pass is checked (PyJWT applies it to exp, nbf and iat).
# Why: a server clock a few seconds behind Cloudflare's saw a fresh pass as issued in the future and refused it (seen
# live in version 1.7.0). Cost: a pass works up to a minute past its expiry; the staff list is still read on every request,
# so someone taken off it is refused at once either way.
CLOCK_ALLOWANCE = 60  # seconds

Page = tuple[str, str]  # heading, message
NOT_SIGNED_IN: Page = ("Not signed in", "You're not signed in to Bluejay support admin, or not on the staff list.")
NOT_FROM_HERE: Page = ("Change refused",
                       "That change didn't come from a Bluejay support admin page. Open the app and try again.")
NO_DATABASE: Page = ("Can't load this page",
                     "Bluejay support admin can't reach its database right now. Try again in a minute.")
NO_CLOUDFLARE: Page = ("Can't check your sign-in",
                       "Bluejay support admin can't reach Cloudflare to check your sign-in right now. Try again in a "
                       "minute.")


class Refused(Exception):
    """Any reason a request is refused. The reason (never a pass, key or email) goes to the log; the person sees one
    plain page."""

    def __init__(self, reason: str, page: Page, status_code: int = 403):
        super().__init__(reason)
        self.heading, self.message = page
        self.status_code = status_code


class KeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class FileKeys:
    """The test copy's keys, from the file scripts/mint_test_pass.py writes. Read on every request, so a new pass works
    without a restart."""

    def __init__(self, path: Path):
        self._path = path

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        try:
            text = self._path.read_text(encoding="utf-8")
            key_set = jwt.PyJWKSet.from_json(text)
        except (OSError, ValueError):  # gone or garbled: a setup fault on the copy, refused like any bad pass
            raise jwt.PyJWKClientError("test key file unreadable") from None
        for key in key_set.keys:
            if key.key_id == kid:
                return key
        raise jwt.PyJWKClientError("no key with this kid")


@lru_cache
def _cloudflare_keys(certs_url: str) -> jwt.PyJWKClient:
    # Cloudflare's published keys, kept for 5 minutes. A pass with a key id not in them fetches them again (Cloudflare
    # changed its key), but at most once per 30 s, so forged passes can't make us call Cloudflare on every request.
    # Cost: for up to 30 s after a key change, passes with the new key are refused; sign-in works again by itself.
    return jwt.PyJWKClient(certs_url, cache_jwk_set=True, lifespan=300, timeout=10, cooldown_duration=30)


def get_key_source(settings: AdminSettings = Depends(get_admin_settings)) -> KeySource:
    if settings.admin_test_keys is not None:  # settings allow this only with the team test.invalid
        return FileKeys(settings.admin_test_keys)
    return _cloudflare_keys(settings.cf_certs_url)


def current_staff(
    request: Request,
    settings: AdminSettings = Depends(get_admin_settings),
    keys: KeySource = Depends(get_key_source),
    db: AdminDb = Depends(get_admin_db),
) -> Staff:
    token = request.headers.get(PASS_HEADER)
    if not token:
        raise Refused("no pass", NOT_SIGNED_IN)
    try:
        claims = jwt.decode(
            token,
            keys.get_signing_key_from_jwt(token).key,
            algorithms=["RS256"],
            audience=settings.cf_audience,
            issuer=settings.issuer,
            leeway=CLOCK_ALLOWANCE,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWKClientConnectionError:  # Cloudflare's keys can't be fetched: nobody's pass can be checked
        raise Refused("Cloudflare keys unreachable", NO_CLOUDFLARE, 503) from None
    except jwt.PyJWTError as exc:
        raise Refused(f"pass refused: {type(exc).__name__}", NOT_SIGNED_IN) from None  # never the pass itself
    email = claims.get("email")
    try:
        member = db.staff_for_email(email) if isinstance(email, str) and email else None
    except psycopg.Error as exc:  # also the pool's timeout; the error text can name the login, so only its type
        raise Refused(f"staff list unreachable: {type(exc).__name__}", NO_DATABASE, 503) from None
    if member is None:
        raise Refused("not on the staff list", NOT_SIGNED_IN)  # no email in the log: they may not be staff at all
    return member


def made_by_this_app(request: Request) -> bool:
    """True only for a request one of the app's own pages made, the redirect after its form post and the script's own
    fetch included: the browser says so with Sec-Fetch-Site: same-origin. A message an address asks for (?done=, ?added=,
    ?removed=) is drawn only then, and so is the Undo that comes with it: a link sent by someone, or typed, arrives as
    "cross-site" or "none", and an Undo behind it would act on whoever the address names. A header that is missing or
    says anything else draws neither; the page itself still shows."""
    return request.headers.get("sec-fetch-site") == "same-origin"


def require_same_origin(request: Request, settings: AdminSettings = Depends(get_admin_settings)) -> None:
    """A request that changes something must come from one of the app's own pages. Browsers always send Origin on a
    POST, and a page elsewhere can't fake it; a missing one is refused too."""
    if request.method not in READ_ONLY_METHODS and request.headers.get("origin") != settings.admin_origin:
        raise Refused("change from another origin", NOT_FROM_HERE)
