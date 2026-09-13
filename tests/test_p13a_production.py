"""P13A production environment + secrets.

Does not connect to hosted databases. Does not print secrets.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import pytest
from dfip_api.account_policy import MIN_PASSWORD_LENGTH, validate_new_account
from dfip_api.app import create_app
from dfip_api.errors import AuthConfigurationError, PersistenceConfigurationError
from dfip_config.settings import (
    BOOTSTRAP_TOKEN_HEADER,
    KNOWN_BAD_JWT_SECRETS,
    MIN_JWT_SECRET_LENGTH,
    load_settings,
    normalized_log_level,
)
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.connection import dsn_is_local_host, dsn_sslmode, redact_dsn
from dfip_db.local_demo_guard import DEMO_PUBLISHER_SUBJECT
from fastapi.testclient import TestClient

from test_company_management_acceptance import (
    OPERATOR_PASS,
    OPERATOR_USER,
    _setup_app,
    _user_body,
)
from test_company_registry import (
    DEFAULT_CLIENT_ID,
    PUBLISHER_PASSWORD,
    _bearer,
    _memory_app,
    _token,
)
from test_p5_api import (
    JWT_SECRET,
    PRODUCTION_BOOTSTRAP_TOKEN,
    PRODUCTION_JWT_SECRET,
    REMOTE_TLS_DSN,
    make_settings,
    production_settings,
)
from test_p7_publication import FORBIDDEN_TEMPLATE_TOKENS
from test_p9_authz import _error

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
WEB_STATIC = ROOT / "apps" / "web" / "static"
TEMPLATE = ROOT / "excel" / "Client_Report.xlsx"
LOG_CALL_RE = re.compile(
    r"log\.(?:info|debug|warning|error|exception|critical)\("
    r".{0,240}(?:Authorization|Cookie|DATABASE_URL|database_url|"
    r"dfip_auth_secret|password=)",
    re.IGNORECASE | re.DOTALL,
)
JWT_BLOB_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
REMOTE_DSN_RE = re.compile(
    r"postgres(?:ql)?://[^:\s/]+:[^@\s/]+@(?:(?:[\w.-]+\.)?(?:supabase|"
    r"amazonaws|neon\.tech|azure|googleusercontent))",
    re.IGNORECASE,
)


OPERATOR_PROD_PASS = "operator-chosen-passphrase"


def _prod_app(**overrides):
    return create_app(
        settings=production_settings(**overrides),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )


def _prod_setup_app(**overrides):
    app = _setup_app()
    app.state.settings = production_settings(**overrides)
    return app


def test_unknown_dfip_env_is_rejected() -> None:
    with pytest.raises(AuthConfigurationError, match="Unsupported DFIP_ENV"):
        create_app(
            settings=make_settings(dfip_env="prod"),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_accepted_environments_start() -> None:
    create_app(settings=make_settings(dfip_env="development"))
    create_app(settings=make_settings(dfip_env="test"))
    _prod_app(dfip_env="staging")
    _prod_app(dfip_env="production")


def test_production_refuses_dev_token() -> None:
    with pytest.raises(AuthConfigurationError, match="DFIP_AUTH_MODE=jwt"):
        _prod_app(dfip_auth_mode="dev_token")


def test_production_requires_jwt_secret() -> None:
    with pytest.raises(AuthConfigurationError, match="DFIP_AUTH_SECRET"):
        _prod_app(dfip_auth_secret="")


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(AuthConfigurationError, match="32 characters"):
        _prod_app(dfip_auth_secret="a" * (MIN_JWT_SECRET_LENGTH - 1))


@pytest.mark.parametrize("secret", sorted(KNOWN_BAD_JWT_SECRETS))
def test_known_bad_jwt_secret_is_rejected(secret: str) -> None:
    with pytest.raises(AuthConfigurationError):
        _prod_app(dfip_auth_secret=secret)


def test_valid_production_secret_is_accepted() -> None:
    app = _prod_app()
    assert app.state.settings.dfip_auth_secret == PRODUCTION_JWT_SECRET


def test_development_still_accepts_fixture_jwt_secret() -> None:
    create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )


def test_production_requires_database_url() -> None:
    with pytest.raises(PersistenceConfigurationError, match="DATABASE_URL"):
        _prod_app(database_url="")


def test_production_requires_storage_endpoint() -> None:
    with pytest.raises(PersistenceConfigurationError, match="DFIP_STORAGE_ENDPOINT"):
        _prod_app(dfip_storage_endpoint="")


def test_production_refuses_remote_storage_endpoint() -> None:
    with pytest.raises(PersistenceConfigurationError, match="local directory"):
        _prod_app(dfip_storage_endpoint="https://storage.example.invalid/bucket")


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", ""])
def test_remote_production_dsn_without_tls_is_rejected(mode: str) -> None:
    query = f"?sslmode={mode}" if mode else ""
    dsn = f"postgresql://dfip@db.example.internal:5432/dfip{query}"
    with pytest.raises(PersistenceConfigurationError, match="TLS"):
        _prod_app(database_url=dsn)


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_remote_production_dsn_with_tls_is_accepted(mode: str) -> None:
    dsn = f"postgresql://dfip@db.example.internal:5432/dfip?sslmode={mode}"
    _prod_app(database_url=dsn)
    assert dsn_sslmode(dsn) == mode
    assert dsn_is_local_host(dsn) is False


def test_local_disposable_dsn_does_not_require_tls() -> None:
    _prod_app(database_url="postgresql://postgres@127.0.0.1:5433/dfip")
    _prod_app(database_url="postgresql://postgres@dfip_db:5432/dfip")
    assert dsn_is_local_host("postgresql://postgres@localhost:5432/dfip")


def test_production_load_settings_ignores_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DFIP_AUTH_MODE=dev_token",
                "DFIP_DEV_AUTH_TOKEN=dotenv-leaked-token",
                "DFIP_LOG_LEVEL=DEBUG",
                "DFIP_AUTH_SECRET=changeme",
                "DFIP_WEB_ORIGIN=http://127.0.0.1:3000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DFIP_ENV", "production")
    monkeypatch.setenv("DFIP_AUTH_MODE", "jwt")
    monkeypatch.setenv("DFIP_AUTH_SECRET", PRODUCTION_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", REMOTE_TLS_DSN)
    monkeypatch.setenv("DFIP_WEB_ORIGIN", "https://app.example.invalid")
    monkeypatch.setenv("DFIP_API_BASE_URL", "https://api.example.invalid")
    monkeypatch.delenv("DFIP_DEV_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DFIP_LOG_LEVEL", raising=False)
    load_settings.cache_clear()
    try:
        settings = load_settings()
        assert settings.dfip_dev_auth_token == ""
        assert settings.dfip_log_level == "INFO"
        assert settings.dfip_auth_secret == PRODUCTION_JWT_SECRET
        assert settings.dfip_web_origin.startswith("https://")
        assert "changeme" not in settings.dfip_auth_secret
    finally:
        load_settings.cache_clear()


def test_development_still_reads_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("DFIP_LOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DFIP_ENV", raising=False)
    monkeypatch.delenv("DFIP_LOG_LEVEL", raising=False)
    load_settings.cache_clear()
    try:
        settings = load_settings()
        assert settings.environment_name == "development"
        assert settings.dfip_log_level == "WARNING"
    finally:
        load_settings.cache_clear()


def test_production_http_origin_is_rejected() -> None:
    with pytest.raises(AuthConfigurationError, match="HTTPS DFIP_WEB_ORIGIN"):
        _prod_app(dfip_web_origin="http://app.example.invalid")


def test_production_http_api_base_is_rejected() -> None:
    with pytest.raises(AuthConfigurationError, match="HTTPS DFIP_API_BASE_URL"):
        _prod_app(dfip_api_base_url="http://api.example.invalid")


def test_production_https_origins_are_accepted() -> None:
    _prod_app(
        dfip_web_origin="https://reports.example.invalid",
        dfip_api_base_url="https://api.example.invalid",
    )


def test_local_http_origins_still_work_in_development() -> None:
    create_app(
        settings=make_settings(
            dfip_env="development",
            dfip_web_origin="http://127.0.0.1:3000",
            dfip_api_base_url="http://127.0.0.1:8000",
        )
    )


def test_production_setup_denied_without_bootstrap_token() -> None:
    http = TestClient(_prod_setup_app())
    status = http.get("/api/v1/auth/setup-status")
    assert status.status_code == 200
    assert status.json() == {"publisher_setup_required": False}
    refused = http.post(
        "/api/v1/auth/setup-publisher",
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert refused.status_code == 401
    assert _error(refused)["code"] == "AUTHENTICATION_FAILED"
    assert PRODUCTION_BOOTSTRAP_TOKEN not in refused.text
    assert OPERATOR_PASS not in refused.text


def test_production_setup_denied_for_invalid_token() -> None:
    http = TestClient(_prod_setup_app(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN))
    refused = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: "wrong-bootstrap-token-value"},
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert refused.status_code == 401
    assert "wrong-bootstrap-token-value" not in refused.text
    assert PRODUCTION_BOOTSTRAP_TOKEN not in refused.text


def test_production_setup_ignores_query_parameter_token() -> None:
    http = TestClient(_prod_setup_app(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN))
    refused = http.post(
        "/api/v1/auth/setup-publisher",
        params={BOOTSTRAP_TOKEN_HEADER: PRODUCTION_BOOTSTRAP_TOKEN},
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert refused.status_code == 401


def test_production_setup_allows_first_publisher_with_header() -> None:
    http = TestClient(_prod_setup_app(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN))
    created = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: PRODUCTION_BOOTSTRAP_TOKEN},
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["username"] == OPERATOR_USER
    assert "password" not in created.json()
    assert PRODUCTION_BOOTSTRAP_TOKEN not in created.text
    closed = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: PRODUCTION_BOOTSTRAP_TOKEN},
        json={
            "username": "second.publisher",
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert closed.status_code == 409
    assert _error(closed)["code"] == "CONFLICT"
    assert http.get("/api/v1/auth/setup-status").json() == {"publisher_setup_required": False}


def test_production_setup_status_hides_window_even_when_open() -> None:
    http = TestClient(_prod_setup_app(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN))
    assert http.get("/api/v1/auth/setup-status").json() == {"publisher_setup_required": False}


def test_development_setup_behavior_is_unchanged() -> None:
    http = TestClient(_setup_app())
    assert http.get("/api/v1/auth/setup-status").json() == {"publisher_setup_required": True}
    created = http.post(
        "/api/v1/auth/setup-publisher",
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert created.status_code == 201, created.text


def test_new_account_password_minimum_length() -> None:
    settings = make_settings()
    with pytest.raises(Exception, match="minimum length"):
        validate_new_account(settings, username="ops.user", password="short")
    validate_new_account(settings, username="ops.user", password="a" * MIN_PASSWORD_LENGTH)


def test_login_does_not_enforce_creation_password_rules() -> None:
    http = TestClient(_memory_app())
    response = http.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "x"},
    )
    assert response.status_code == 401
    assert "minimum length" not in response.text.lower()


def test_production_refuses_demo_username_and_fixture_password() -> None:
    http = TestClient(_prod_setup_app(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN))
    demo_user = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: PRODUCTION_BOOTSTRAP_TOKEN},
        json={
            "username": DEMO_PUBLISHER_SUBJECT,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert demo_user.status_code == 422
    fixture_password = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: PRODUCTION_BOOTSTRAP_TOKEN},
        json={
            "username": OPERATOR_USER,
            "password": "ops-publisher-pass",
            "confirm_password": "ops-publisher-pass",
        },
    )
    assert fixture_password.status_code == 422
    assert "ops-publisher-pass" not in fixture_password.text


def test_production_client_user_refuses_demo_username() -> None:
    http = TestClient(_memory_app())
    http.app.state.settings = production_settings()
    publisher = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
    refused = http.post(
        f"/api/v1/clients/{DEFAULT_CLIENT_ID}/users",
        headers=publisher,
        json=_user_body("demo-client-extra", "long-enough-password"),
    )
    assert refused.status_code == 422


def test_dfip_log_level_is_functional() -> None:
    settings = make_settings(dfip_log_level="WARNING")
    assert normalized_log_level(settings) == "WARNING"
    main_src = (PACKAGES / "api" / "dfip_api" / "__main__.py").read_text(encoding="utf-8")
    assert "log_level=level_name.lower()" in main_src
    assert "normalized_log_level" in main_src
    logging.getLogger("dfip_api.upload_service")


def test_packages_do_not_log_secrets() -> None:
    for path in PACKAGES.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = LOG_CALL_RE.search(text)
        assert match is None, f"{path} logs a secret-bearing field: {match.group(0)[:80]}"


def test_redact_dsn_strips_password() -> None:
    redacted = redact_dsn("postgresql://dfip:hunter2@db.example.internal:5432/dfip?sslmode=require")
    assert "hunter2" not in redacted
    assert "***@" in redacted
    assert "sslmode=require" in redacted


def test_api_errors_do_not_leak_secrets() -> None:
    http = TestClient(_prod_setup_app(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN))
    response = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: "not-the-token"},
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    payload = response.text.lower()
    assert PRODUCTION_BOOTSTRAP_TOKEN.lower() not in payload
    assert OPERATOR_PASS.lower() not in payload
    assert PRODUCTION_JWT_SECRET.lower() not in payload
    assert "traceback" not in payload
    assert "postgresql://" not in payload


def test_env_file_is_not_tracked() -> None:
    listed = subprocess.check_output(
        ["git", "ls-files", ".env"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert listed == ""
    example = ROOT / ".env.example"
    assert example.is_file()
    text = example.read_text(encoding="utf-8")
    assert "DFIP_BOOTSTRAP_TOKEN" in text
    assert JWT_SECRET not in text
    assert PRODUCTION_JWT_SECRET not in text


def test_tracked_packages_have_no_jwt_blobs_or_remote_dsn_passwords() -> None:
    scanned = [PACKAGES, ROOT / "apps", ROOT / "excel", ROOT / "documentation"]
    for folder in scanned:
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in {".pyc", ".png", ".jpg", ".xlsx", ".bin"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            assert JWT_BLOB_RE.search(text) is None, f"JWT blob in {path}"
            assert REMOTE_DSN_RE.search(text) is None, f"remote DSN password in {path}"
            assert PRODUCTION_JWT_SECRET not in text
            assert PRODUCTION_BOOTSTRAP_TOKEN not in text


def test_frontend_has_no_server_secrets() -> None:
    forbidden = (
        "DFIP_AUTH_SECRET",
        "DFIP_BOOTSTRAP_TOKEN",
        "DATABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        PRODUCTION_JWT_SECRET,
        JWT_SECRET,
        "postgresql://",
    )
    for path in WEB_STATIC.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} contains {token}"
    auth_js = (WEB_STATIC / "js" / "auth.js").read_text(encoding="utf-8")
    assert "sessionStorage" in auth_js
    assert "localStorage" not in auth_js


def test_tracked_excel_template_has_no_secrets() -> None:
    data = TEMPLATE.read_bytes()
    for token in FORBIDDEN_TEMPLATE_TOKENS:
        assert token.encode("utf-8") not in data
    assert PRODUCTION_JWT_SECRET.encode("utf-8") not in data
    assert b"DFIP_AUTH_SECRET" not in data
    assert b"DATABASE_URL" not in data


def test_no_startup_demo_database_scan() -> None:
    app_src = (PACKAGES / "api" / "dfip_api" / "app.py").read_text(encoding="utf-8")
    assert "demo-publisher" not in app_src
    assert "inspector_exists" not in app_src


def test_no_global_log_scrubber() -> None:
    combined = ""
    for path in PACKAGES.rglob("*.py"):
        combined += path.read_text(encoding="utf-8")
    assert "logging.Filter" not in combined
    assert "RedactingFilter" not in combined
