"""Settings load from environment variables, with safe defaults and hidden secrets."""

import pytest
from pydantic import ValidationError

from support_agent.config import get_settings

REQUIRED_ENV = {
    "OPENAI_API_KEY": "sk-test-openai",
    "OPENAI_MODEL": "gpt-test-model",
    "DATABASE_URL": "postgresql://agent:db-pass@localhost:5432/support",
    "AGENT_API_KEY": "agent-test-key",
    "GHL_KEY_PRIVATE": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",  # 32 bytes of 0x01, base64: a test key only
}


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Keep tests away from the real .env file and from cached settings."""
    monkeypatch.chdir(tmp_path)  # .env is looked up in the current folder; tmp_path has none
    for name in [*REQUIRED_ENV, "JUDGE_MODEL", "GHL_TOKENS"]:  # optional settings a test sets must not leak in from the real environment
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def set_required_env(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


def test_settings_defaults(monkeypatch):
    set_required_env(monkeypatch)
    s = get_settings()
    assert s.max_concurrent_drafts == 8
    assert s.max_ticket_chars == 8000
    assert s.tool_call_cap == 8
    assert s.search_top_k == 5
    assert s.search_max_per_doc == 2
    assert s.agent_time_budget == 75
    assert s.embedding_model == "text-embedding-3-small"


def test_ghl_key_private_is_required(monkeypatch):
    """Client keys come from the database, locked; the agent needs the private half to open them."""
    set_required_env(monkeypatch)
    monkeypatch.delenv("GHL_KEY_PRIVATE")
    with pytest.raises(ValidationError) as error:
        get_settings()
    assert "ghl_key_private" in str(error.value)


@pytest.mark.parametrize("bad", ["not base64!", "c2hvcnQ="])  # junk, 5 bytes
def test_a_malformed_ghl_key_private_stops_the_start(monkeypatch, bad):
    set_required_env(monkeypatch)
    monkeypatch.setenv("GHL_KEY_PRIVATE", bad)
    with pytest.raises(ValidationError) as error:
        get_settings()
    assert "ghl_key_private" in str(error.value) and bad not in str(error.value)


def test_ghl_tokens_are_no_longer_read(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("GHL_TOKENS", '{"loc_bright": "pit-bright"}')  # an old .env still holding them starts too
    assert not hasattr(get_settings(), "ghl_tokens")


def test_secrets_hidden_when_printed(monkeypatch):
    set_required_env(monkeypatch)
    shown = repr(get_settings())
    for secret in ("sk-test-openai", "db-pass", "agent-test-key", "AQEBAQEB", "slack-test-secret"):
        assert secret not in shown


def test_missing_required_setting_fails(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValidationError):
        get_settings()


def test_limits_must_be_positive(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("TOOL_CALL_CAP", "0")
    with pytest.raises(ValidationError):
        get_settings()

def test_errors_never_show_secret_values(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.delenv("OPENAI_MODEL")
    with pytest.raises(ValidationError) as error:
        get_settings()
    shown = str(error.value)
    assert "openai_model" in shown  # still says which setting is wrong
    assert "input_value" not in shown  # but never echoes what was read: pydantic shows the start and end of it
    for secret in ("sk-test-openai", "db-pass", "agent-test-key", "AQEBAQEB", "slack-test-secret"):
        assert secret not in shown


def test_draft_slots_must_be_positive(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("MAX_CONCURRENT_DRAFTS", "0")
    with pytest.raises(ValidationError):
        get_settings()


def test_ticket_length_limit_must_be_positive(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("MAX_TICKET_CHARS", "0")
    with pytest.raises(ValidationError):
        get_settings()


def test_slack_signing_secret_is_not_needed(monkeypatch):
    """Clicks are checked in the database (migration 016), which holds the secret; the agent never sees it (v1.0.0)."""
    set_required_env(monkeypatch)  # without it
    s = get_settings()
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "left-over-in-an-old-env")  # an old .env still holding it starts too
    get_settings.cache_clear()
    assert get_settings() and not hasattr(s, "slack_signing_secret")


def test_judge_model_is_its_own_setting(monkeypatch):
    """The eval judge must not be the model it grades: a cheaper agent model can't make grading easier."""
    set_required_env(monkeypatch)
    monkeypatch.setenv("JUDGE_MODEL", "gpt-test-judge")
    s = get_settings()
    assert s.judge_model == "gpt-test-judge"
    assert s.openai_model == "gpt-test-model"