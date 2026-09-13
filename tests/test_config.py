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
    monkeypatch.delenv("DFIP_AUTH_TOKEN_TTL_SECONDS", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_BYTES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_FILES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_TOTAL_BYTES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_FACTS", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_PROCESSING_SECONDS", raising=False)
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
    assert settings.dfip_excel_access_ttl_seconds == 3600
    assert settings.dfip_excel_grant_ttl_seconds == 2_592_000
    assert settings.dfip_client_report_template == ""
    assert settings.dfip_password_pbkdf2_iterations == 210_000
    assert settings.dfip_upload_max_bytes == 10 * 1024 * 1024
    assert settings.dfip_upload_max_files == 5
    assert settings.dfip_upload_max_total_bytes == 20 * 1024 * 1024
    assert settings.dfip_json_max_body_bytes == 256 * 1024
    assert settings.dfip_download_max_rows == 75_000
    assert settings.dfip_upload_max_facts == 750_000
    assert settings.dfip_upload_max_processing_seconds == 0
    assert settings.dfip_ask_provider == "none"
    assert settings.dfip_ask_api_key == ""
    assert settings.dfip_ask_api_base == "https://api.openai.com/v1"
    assert settings.dfip_ask_model == "gpt-4o-mini"
    assert settings.dfip_ask_max_output_tokens == 4096
    assert settings.dfip_ask_max_question_chars == 500
    assert settings.dfip_company_purge_min_age_days is None
    assert settings.dfip_backup_keep_count == 0
    assert settings.dfip_backup_keep_days == 0
    assert settings.dfip_temp_upload_max_age_hours == 24
    assert settings.dfip_history_serving_table is True
    assert settings.secrets_required is False


def test_settings_read_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("DFIP_ENV", "staging")
    monkeypatch.setenv("DFIP_LOG_LEVEL", "WARNING")
    load_settings.cache_clear()

    settings = load_settings()

    assert settings.environment == "staging"
    assert settings.dfip_log_level == "WARNING"
    load_settings.cache_clear()


def test_upload_limits_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("DFIP_UPLOAD_MAX_BYTES", "52428800")
    monkeypatch.setenv("DFIP_UPLOAD_MAX_FILES", "5")
    monkeypatch.setenv("DFIP_UPLOAD_MAX_TOTAL_BYTES", "104857600")
    settings = Settings(_env_file=None)
    assert settings.dfip_upload_max_bytes == 52_428_800
    assert settings.dfip_upload_max_files == 5
    assert settings.upload_max_total_bytes == 104_857_600
    assert settings.dfip_json_max_body_bytes == 256 * 1024


def test_auth_token_ttl_reads_environment_without_changing_default(monkeypatch) -> None:
    monkeypatch.delenv("DFIP_AUTH_TOKEN_TTL_SECONDS", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.dfip_auth_token_ttl_seconds == 3600
    monkeypatch.setenv("DFIP_AUTH_TOKEN_TTL_SECONDS", "43200")
    local_demo = Settings(_env_file=None)
    assert local_demo.dfip_auth_token_ttl_seconds == 43200


def test_history_serving_table_flag_reads_environment_without_changing_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DFIP_HISTORY_SERVING_TABLE", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.dfip_history_serving_table is True
    monkeypatch.setenv("DFIP_HISTORY_SERVING_TABLE", "false")
    fallback = Settings(_env_file=None)
    assert fallback.dfip_history_serving_table is False


def test_ask_provider_gemini_reads_environment_without_changing_default(monkeypatch) -> None:
    monkeypatch.delenv("DFIP_ASK_PROVIDER", raising=False)
    monkeypatch.delenv("DFIP_ASK_API_KEY", raising=False)
    monkeypatch.delenv("DFIP_ASK_MODEL", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.dfip_ask_provider == "none"
    assert defaults.dfip_ask_api_key == ""
    monkeypatch.setenv("DFIP_ASK_PROVIDER", "gemini")
    monkeypatch.setenv("DFIP_ASK_MODEL", "gemini-flash-test")
    loaded = Settings(_env_file=None)
    assert loaded.dfip_ask_provider == "gemini"
    assert loaded.dfip_ask_model == "gemini-flash-test"
    assert loaded.dfip_ask_api_key == ""
