"""Configuration loads without production secrets."""

from __future__ import annotations

from dfip_config import load_settings
from dfip_config.settings import Settings


def test_settings_load_with_defaults(monkeypatch) -> None:
    monkeypatch.delenv("DFIP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("DFIP_AUTH_SECRET", raising=False)
    monkeypatch.delenv("DFIP_DEV_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DFIP_DEV_AUTH_ROLE", raising=False)
    monkeypatch.delenv("DFIP_DEV_AUTH_CLIENT_ID", raising=False)
    load_settings.cache_clear()

    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.dfip_log_level == "INFO"
    assert settings.dfip_api_port == 8000
    assert settings.database_url == ""
    assert settings.supabase_service_role_key == ""
    assert settings.dfip_auth_secret == ""
    assert settings.dfip_api_prefix == "/api/v1"
    assert settings.dfip_auth_mode == "dev_token"
    assert settings.dfip_dev_auth_token == ""
    assert settings.dfip_dev_auth_role == "reader"
    assert settings.dfip_dev_auth_client_id == ""
    assert settings.dfip_web_port == 3000
    assert settings.dfip_auth_token_ttl_seconds == 3600
    assert settings.dfip_password_pbkdf2_iterations == 210_000
    assert settings.secrets_required is False


def test_settings_read_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("DFIP_ENV", "staging")
    monkeypatch.setenv("DFIP_LOG_LEVEL", "WARNING")
    load_settings.cache_clear()

    settings = load_settings()

    assert settings.environment == "staging"
    assert settings.dfip_log_level == "WARNING"
    load_settings.cache_clear()
