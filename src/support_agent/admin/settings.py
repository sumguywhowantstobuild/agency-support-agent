"""Every setting the admin app needs, read once from environment variables (or .env in development). The admin
container gets these and nothing else from .env: no OpenAI key, and only the public half of the key pair."""

import base64
import binascii
import re
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends
from pydantic import Field, FilePath, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptySecret = Annotated[SecretStr, Field(min_length=1)]

# The test copy's team name: .invalid can never be a real domain, so test keys can't sit next to a real team.
TEST_TEAM_DOMAIN = "test.invalid"


class AdminSettings(BaseSettings):
    """Checked at startup: a missing or invalid value stops the admin app before it serves a page."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

    admin_database_url: NonEmptySecret  # the admin_app login: functions only, no table writes
    ghl_key_public: NonEmptySecret  # seals client keys; only the agent's private half opens them
    # A Cloudflare Access team (e.g. example-team.cloudflareaccess.com), or the test copy's name.
    cf_team_domain: str = Field(pattern=r"^([a-z0-9][a-z0-9-]{0,62}\.cloudflareaccess\.com|test\.invalid)$")
    cf_audience: str = Field(min_length=1)  # the Access application's audience tag
    cf_certs_url: str = Field(min_length=1)  # where Cloudflare publishes the keys that sign the pass
    admin_origin: str = Field(min_length=1)  # e.g. https://admin.example.com; every POST must come from it
    agent_url: str = Field(min_length=1)  # the agent on the Docker network, for the connection test
    agent_api_key: NonEmptySecret
    slack_bot_token: NonEmptySecret  # lists the channels the bot is in, for Add client
    admin_test_keys: FilePath | None = None  # test copy only: a key file made by scripts/mint_test_pass.py
    # The agency's own zone: "today" on the chart starts at its midnight, and every time shown is in it. A name from
    # the IANA list (America/New_York); checked at start, so a typo never reaches a page.
    time_zone: str = "America/New_York"

    @field_validator("time_zone")
    @classmethod
    def _is_a_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError, OSError):  # unknown name; a path or empty name; a folder in place of a zone
            raise ValueError("must be a time zone name such as America/New_York") from None
        return value

    @field_validator("ghl_key_public")
    @classmethod
    def _is_a_public_key(cls, value: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except binascii.Error:
            raw = b""
        if len(raw) != 32:
            raise ValueError("must be the base64 public key from scripts/make_key_pair.py (32 bytes)")
        return value

    @model_validator(mode="after")
    def _test_keys_only_on_the_test_copy(self) -> "AdminSettings":
        if self.admin_test_keys is not None and self.cf_team_domain != TEST_TEAM_DOMAIN:
            raise ValueError(f"ADMIN_TEST_KEYS is for the test copy only: CF_TEAM_DOMAIN must be {TEST_TEAM_DOMAIN}")
        if self.admin_test_keys is None and self.cf_certs_url != f"https://{self.cf_team_domain}/cdn-cgi/access/certs":
            raise ValueError("CF_CERTS_URL must be https://<CF_TEAM_DOMAIN>/cdn-cgi/access/certs")
        # Browsers send Origin as scheme://host[:port]; the test copy runs on plain http on the PC.
        scheme = r"https?" if self.admin_test_keys is not None else r"https"
        if not re.fullmatch(scheme + r"://[a-z0-9.-]+(:[0-9]{1,5})?", self.admin_origin):
            raise ValueError("ADMIN_ORIGIN must be the app's https://host, with no path or trailing slash")
        return self

    @property
    def issuer(self) -> str:
        return f"https://{self.cf_team_domain}"  # Cloudflare's "iss" in every pass it signs for this team

    @property
    def sign_out(self) -> str:
        """Where the frame's Sign out goes: Cloudflare's own logout, answered at its edge on the app's own address, and
        only behind a real Cloudflare team. The test copy has no Cloudflare in front, so it has none. A fixed path: nothing
        from a request is ever part of it."""
        return "" if self.cf_team_domain == TEST_TEAM_DOMAIN else "/cdn-cgi/access/logout"

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()


def get_zone(settings: AdminSettings = Depends(get_admin_settings)) -> ZoneInfo:
    """The agency's time zone, for a page that shows times."""
    return settings.zone
