"""Application settings loaded from environment variables.

P0 requirement: settings must load with empty/placeholder values so a fresh
developer checkout works without production credentials.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for DFIP.

    Secret-bearing fields default to empty strings. P0 never requires them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    dfip_env: str = "development"
    dfip_log_level: str = "INFO"

    dfip_api_host: str = "127.0.0.1"
    dfip_api_port: int = 8000
    dfip_api_base_url: str = "http://127.0.0.1:8000"
    dfip_api_prefix: str = "/api/v1"

    dfip_web_origin: str = "http://127.0.0.1:3000"
    dfip_web_host: str = "127.0.0.1"
    dfip_web_port: int = 3000
    dfip_worker_concurrency: int = 1

    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    dfip_storage_bucket: str = "dfip-source-files"
    dfip_storage_endpoint: str = ""

    dfip_auth_secret: str = ""
    # dev_token is allowed only in development/test. Production must use jwt.
    # An empty DFIP_DEV_AUTH_TOKEN is not a bypass.
    dfip_auth_mode: str = "dev_token"
    dfip_dev_auth_token: str = ""
    dfip_dev_auth_role: str = "reader"
    # Required when DFIP_AUTH_MODE=dev_token and DATABASE_URL is set. Binds the
    # development token to one client. Never a platform-wide identity.
    dfip_dev_auth_client_id: str = ""
    dfip_auth_issuer: str = ""
    dfip_auth_audience: str = ""
    # Access JWT lifetime issued by password sign-in / refresh.
    dfip_auth_token_ttl_seconds: int = 3600
    # PBKDF2 iterations for newly stored password verifiers. Tests may lower this.
    dfip_password_pbkdf2_iterations: int = 210_000

    # Multipart workbook ingest and published-slice download caps.
    # These are not persistence or identity settings.
    dfip_upload_max_bytes: int = 10 * 1024 * 1024
    dfip_download_max_rows: int = 75_000

    @property
    def environment(self) -> str:
        return self.dfip_env

    @property
    def secrets_required(self) -> bool:
        """True only when a later architecture (V2) starts requiring credentials."""
        return False


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Load settings from the process environment and optional local `.env`."""
    return Settings()
