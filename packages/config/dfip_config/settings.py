"""Application settings loaded from environment variables.

P0 requirement: settings must load with empty/placeholder values so a fresh
developer checkout works without production credentials.

Production-grade environments (staging, production) read process/container
environment only and never silently consume a repository-local `.env`.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})
PRODUCTION_GRADE_ENVIRONMENTS = frozenset({"staging", "production"})
DEV_TOKEN_ENVIRONMENTS = frozenset({"development", "test"})
ALLOWED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
MIN_JWT_SECRET_LENGTH = 32
BOOTSTRAP_TOKEN_HEADER = "X-DFIP-Bootstrap-Token"

# Literal blocklist only. Not entropy scoring. Compared case-insensitively.
KNOWN_BAD_JWT_SECRETS = frozenset(
    {
        "changeme",
        "secret",
        "password",
        "jwt-secret",
        "dfip-auth-secret",
        "p5-test-jwt-secret-not-for-production",
        "production-secret-not-for-reuse",
        "health-secret-not-for-reuse",
        "test",
        "demo",
        "dfip",
        "your-secret-here",
        "supersecret",
        "changemechangemechangemechangeme",
    }
)


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
    # In-process upload ThreadPoolExecutor size. Transform work stays serialized.
    dfip_worker_concurrency: int = 1

    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    dfip_storage_bucket: str = "dfip-source-files"
    dfip_storage_endpoint: str = ""
    # Optional operator last-backup directory. Readiness may report the
    # manifest timestamp only. Empty means backup age is not configured.
    dfip_backup_last_dir: str = ""

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
    # Production/default remains 3600. Local demo may set DFIP_AUTH_TOKEN_TTL_SECONDS=43200.
    dfip_auth_token_ttl_seconds: int = 3600
    # Access JWT stamped into Excel Settings BearerToken. Keep short.
    # Post-expiry Refresh All uses excel_workbook_grant, not this TTL.
    dfip_excel_access_ttl_seconds: int = 3600
    # How long a client/reader workbook may call POST /auth/refresh after the
    # stamped access JWT expires. Default 30 days. Not a publisher/admin token.
    dfip_excel_grant_ttl_seconds: int = 2_592_000
    # Empty: clone tracked excel/Client_Report.xlsx. Set only to an
    # Excel-authored disposable renewal template. Do not set in the normal
    # DFIP runtime. Never overwrite the tracked master through this path.
    dfip_client_report_template: str = ""
    # PBKDF2 iterations for newly stored password verifiers. Tests may lower this.
    dfip_password_pbkdf2_iterations: int = 210_000
    # Production-grade first-publisher setup. Empty means setup is denied.
    # Compared against header X-DFIP-Bootstrap-Token. Never a query parameter.
    dfip_bootstrap_token: str = ""

    # Multipart workbook ingest and published-slice download caps.
    # These are not persistence or identity settings.
    # 64 MiB fits the FY-2026 Raw workbook (53_832_336 bytes) with headroom.
    dfip_upload_max_bytes: int = 64 * 1024 * 1024
    # Maximum workbook parts in one multipart upload. Catalog upload is one file.
    dfip_upload_max_files: int = 5
    # Total multipart payload cap. 0 means twice DFIP_UPLOAD_MAX_BYTES.
    dfip_upload_max_total_bytes: int = 128 * 1024 * 1024
    # Non-multipart JSON/body cap. Multipart workbook uploads are not this limit.
    dfip_json_max_body_bytes: int = 256 * 1024
    dfip_download_max_rows: int = 75_000
    # Maximum non-empty source facts/rows per workbook. 0 disables the cap.
    # Default is above the 500_000 engineering target so 200K is not a hard reject.
    dfip_upload_max_facts: int = 750_000
    # Safety cutoff for one upload job. 0 disables. This is not the 10-minute SLA.
    dfip_upload_max_processing_seconds: int = 0

    # Days after deactivation before a company is purge-eligible.
    # Unset/None: purge_eligible_after stays NULL (not clock-eligible).
    # 0: stamp now() so the company is immediately eligible. Never auto-purges.
    dfip_company_purge_min_age_days: int | None = None
    # Backup identify-eligible keep-count. 0 means CLI must pass --keep-count.
    dfip_backup_keep_count: int = 0
    # Optional age filter in days for backup identify-eligible. 0 disables.
    dfip_backup_keep_days: int = 0
    # Age for leftover dfip-upload-* temp directories. Identify/delete helper only.
    dfip_temp_upload_max_age_hours: int = 24
    # Read GET /publications/history/facts from publication_history_grain.
    # False keeps the original per-page DISTINCT ON query. Publish still rebuilds
    # the serving table so the flag can be switched without a backfill.
    dfip_history_serving_table: bool = True

    # D8 Contextual Ask. Default none keeps template explanations only.
    # Optional wording providers: openai (chat completions), gemini (generateContent).
    # Neither provider classifies intents or calculates analytics. The model never
    # receives a database connection or executes SQL. When provider=gemini,
    # DFIP_ASK_MODEL must be a Gemini API model id from configuration — not a
    # Cursor IDE model name.
    dfip_ask_provider: str = "none"
    dfip_ask_api_key: str = ""
    dfip_ask_api_base: str = "https://api.openai.com/v1"
    dfip_ask_model: str = "gpt-4o-mini"
    dfip_ask_timeout_seconds: float = 20
    # Shared with Gemini 3.x thinking tokens. 300 truncates visible Ask wording.
    dfip_ask_max_output_tokens: int = 4096
    dfip_ask_max_question_chars: int = 500

    @property
    def upload_max_total_bytes(self) -> int:
        if self.dfip_upload_max_total_bytes > 0:
            return self.dfip_upload_max_total_bytes
        return 2 * self.dfip_upload_max_bytes

    @property
    def environment(self) -> str:
        return self.dfip_env

    @property
    def environment_name(self) -> str:
        return self.dfip_env.strip().lower()

    @property
    def is_production_grade(self) -> bool:
        return self.environment_name in PRODUCTION_GRADE_ENVIRONMENTS

    @property
    def secrets_required(self) -> bool:
        """True only when a later architecture (V2) starts requiring credentials."""
        return False


def process_environment_is_production_grade() -> bool:
    """True when DFIP_ENV in the process environment is staging or production."""
    return os.environ.get("DFIP_ENV", "").strip().lower() in PRODUCTION_GRADE_ENVIRONMENTS


def normalized_log_level(settings: Settings) -> str:
    """Return an allowlisted log level. Unknown non-production values become INFO."""
    level = settings.dfip_log_level.strip().upper()
    if level in ALLOWED_LOG_LEVELS:
        return level
    return "INFO"


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Load settings from the process environment and optional local `.env`.

    Production-grade process environments do not read a repository-local `.env`.
    Development and test keep dotenv behavior.
    """
    if process_environment_is_production_grade():
        return Settings(_env_file=None)
    return Settings()
