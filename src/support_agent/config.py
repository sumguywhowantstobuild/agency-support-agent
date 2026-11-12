"""Every setting the agent needs, read once from environment variables (or .env in development)."""

import base64
import binascii
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptySecret = Annotated[SecretStr, Field(min_length=1)]


class Settings(BaseSettings):
    """Checked at startup: a missing, empty or invalid value stops the agent before it touches a ticket."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

    openai_api_key: NonEmptySecret
    openai_model: str = Field(min_length=1)  # one setting, so the test set can pick the cheapest model that passes
    judge_model: str | None = None  # the eval judge only; the live agent never uses it, so it isn't required here
    embedding_model: str = "text-embedding-3-small"
    database_url: NonEmptySecret  # holds the database password
    agent_api_key: NonEmptySecret  # n8n must send this to call the agent
    # Opens the clients' GHL keys, which the admin app locks into the database. Checked at start, so a
    # damaged value stops the agent at once instead of failing every ticket.
    ghl_key_private: NonEmptySecret
    ghl_mcp_url: str = Field("https://services.leadconnectorhq.com/mcp/openai/v2/", pattern=r"^https://")  # GHL MCP v2
    tool_call_cap: int = Field(8, ge=1)
    max_concurrent_drafts: int = Field(8, ge=1)  # drafts at a time; more get "busy" (503). A load test checks it
    max_ticket_chars: int = Field(8000, ge=1)  # longer tickets are refused before any AI call (Slack allows 40,000)
    agent_time_budget: int = Field(75, ge=10)  # seconds per ticket; n8n stops waiting at 90
    search_top_k: int = Field(5, ge=1)
    search_max_per_doc: int = Field(2, ge=1)  # so one long guide can't fill every result; the test set tunes it

    @field_validator("ghl_key_private")
    @classmethod
    def _is_a_private_key(cls, value: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except binascii.Error:
            raw = b""
        if len(raw) != 32:
            raise ValueError("must be the base64 private key from scripts/make_key_pair.py (32 bytes)")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()