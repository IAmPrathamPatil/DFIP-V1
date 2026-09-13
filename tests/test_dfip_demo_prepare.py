"""Local one-click prepare: guards only. Does not start Docker or touch hosted DBs."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from dfip_db.local_demo_guard import LocalDemoSeedError

ROOT = Path(__file__).resolve().parents[1]
PREPARE_PATH = ROOT / "scripts" / "dfip_demo_prepare.py"

PUBLISHER_PASSWORD = "local-publisher-pass"
CLIENT_PASSWORD = "local-client-pass"


def _load_prepare():
    spec = importlib.util.spec_from_file_location("dfip_demo_prepare", PREPARE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_script_exists() -> None:
    assert PREPARE_PATH.is_file()


def test_validate_refuses_production(monkeypatch) -> None:
    prepare = _load_prepare()
    monkeypatch.setenv("DFIP_ENV", "production")
    monkeypatch.setenv("DFIP_AUTH_MODE", "jwt")
    monkeypatch.setenv("DFIP_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/dfip")
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", PUBLISHER_PASSWORD)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    with pytest.raises(LocalDemoSeedError, match="production"):
        prepare.validate_local_demo_env()


def test_validate_refuses_hosted_url_without_printing_secret(monkeypatch, capsys) -> None:
    prepare = _load_prepare()
    monkeypatch.setenv("DFIP_ENV", "development")
    monkeypatch.setenv("DFIP_AUTH_MODE", "jwt")
    monkeypatch.setenv("DFIP_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres.abc:super-secret-dsn@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    )
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", PUBLISHER_PASSWORD)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    with pytest.raises(LocalDemoSeedError, match="DATABASE_URL"):
        prepare.validate_local_demo_env()
    captured = capsys.readouterr()
    assert "super-secret-dsn" not in captured.out
    assert "super-secret-dsn" not in captured.err
    assert PUBLISHER_PASSWORD not in captured.out
    assert CLIENT_PASSWORD not in captured.err


def test_validate_refuses_empty_database_url(monkeypatch) -> None:
    prepare = _load_prepare()
    monkeypatch.setenv("DFIP_ENV", "development")
    monkeypatch.setenv("DFIP_AUTH_MODE", "jwt")
    monkeypatch.setenv("DFIP_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", PUBLISHER_PASSWORD)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    with pytest.raises(LocalDemoSeedError, match="DATABASE_URL"):
        prepare.validate_local_demo_env()


def test_validate_refuses_dev_token_mode(monkeypatch) -> None:
    prepare = _load_prepare()
    monkeypatch.setenv("DFIP_ENV", "development")
    monkeypatch.setenv("DFIP_AUTH_MODE", "dev_token")
    monkeypatch.setenv("DFIP_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/dfip")
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", PUBLISHER_PASSWORD)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    with pytest.raises(LocalDemoSeedError, match="DFIP_AUTH_MODE=jwt"):
        prepare.validate_local_demo_env()


def test_validate_missing_password_names_the_variable(monkeypatch) -> None:
    prepare = _load_prepare()
    monkeypatch.setenv("DFIP_ENV", "development")
    monkeypatch.setenv("DFIP_AUTH_MODE", "jwt")
    monkeypatch.setenv("DFIP_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/dfip")
    monkeypatch.delenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", raising=False)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    with pytest.raises(LocalDemoSeedError, match="DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD"):
        prepare.validate_local_demo_env()


def test_overlay_dotenv_does_not_override_process_env(tmp_path, monkeypatch) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD=from-file\n"
        "DATABASE_URL=postgresql://postgres@127.0.0.1:5433/dfip\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", "from-process")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    prepare.overlay_dotenv(tmp_path)
    assert os.environ["DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD"] == "from-process"
    assert os.environ["DATABASE_URL"] == "postgresql://postgres@127.0.0.1:5433/dfip"


def test_ensure_local_demo_upload_limits_appends_missing(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text("DFIP_ENV=development\n", encoding="utf-8")
    added = prepare.ensure_local_demo_upload_limits(env_file)
    assert added == [
        "DFIP_UPLOAD_MAX_BYTES",
        "DFIP_UPLOAD_MAX_FILES",
        "DFIP_UPLOAD_MAX_TOTAL_BYTES",
    ]
    parsed = prepare._parse_dotenv(env_file)
    assert parsed["DFIP_UPLOAD_MAX_BYTES"] == "52428800"
    assert parsed["DFIP_UPLOAD_MAX_FILES"] == "5"
    assert parsed["DFIP_UPLOAD_MAX_TOTAL_BYTES"] == "104857600"
    assert parsed["DFIP_ENV"] == "development"


def test_ensure_local_demo_upload_limits_does_not_overwrite(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DFIP_UPLOAD_MAX_BYTES=10485760\n"
        "DFIP_UPLOAD_MAX_FILES=5\n"
        "DFIP_UPLOAD_MAX_TOTAL_BYTES=20971520\n",
        encoding="utf-8",
    )
    assert prepare.ensure_local_demo_upload_limits(env_file) == []
    parsed = prepare._parse_dotenv(env_file)
    assert parsed["DFIP_UPLOAD_MAX_BYTES"] == "10485760"
    assert parsed["DFIP_UPLOAD_MAX_TOTAL_BYTES"] == "20971520"


def test_ensure_local_demo_upload_limits_skips_missing_file(tmp_path) -> None:
    prepare = _load_prepare()
    assert prepare.ensure_local_demo_upload_limits(tmp_path / ".env") == []


def test_ensure_local_demo_jwt_ttl_appends_missing(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text("DFIP_ENV=development\n", encoding="utf-8")
    added = prepare.ensure_local_demo_jwt_ttl(env_file)
    assert added == ["DFIP_AUTH_TOKEN_TTL_SECONDS"]
    parsed = prepare._parse_dotenv(env_file)
    assert parsed["DFIP_AUTH_TOKEN_TTL_SECONDS"] == "43200"
    assert parsed["DFIP_ENV"] == "development"


def test_ensure_local_demo_jwt_ttl_does_not_overwrite(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text("DFIP_AUTH_TOKEN_TTL_SECONDS=3600\n", encoding="utf-8")
    assert prepare.ensure_local_demo_jwt_ttl(env_file) == []
    parsed = prepare._parse_dotenv(env_file)
    assert parsed["DFIP_AUTH_TOKEN_TTL_SECONDS"] == "3600"


def test_ensure_local_demo_jwt_ttl_skips_missing_file(tmp_path) -> None:
    prepare = _load_prepare()
    assert prepare.ensure_local_demo_jwt_ttl(tmp_path / ".env") == []


def test_ensure_local_demo_storage_fills_empty_assignment(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text("DFIP_ENV=development\nDFIP_STORAGE_ENDPOINT=\n", encoding="utf-8")
    added = prepare.ensure_local_demo_storage(env_file, tmp_path)
    assert added == ["DFIP_STORAGE_ENDPOINT"]
    parsed = prepare._parse_dotenv(env_file)
    expected = (tmp_path / "tmp" / "dfip-source-archive").resolve().as_posix()
    assert parsed["DFIP_STORAGE_ENDPOINT"] == expected
    assert (tmp_path / "tmp" / "dfip-source-archive").is_dir()
    assert parsed["DFIP_ENV"] == "development"


def test_ensure_local_demo_storage_does_not_overwrite(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text("DFIP_STORAGE_ENDPOINT=C:/existing-archive\n", encoding="utf-8")
    assert prepare.ensure_local_demo_storage(env_file, tmp_path) == []
    parsed = prepare._parse_dotenv(env_file)
    assert parsed["DFIP_STORAGE_ENDPOINT"] == "C:/existing-archive"


def test_ensure_local_demo_storage_skips_missing_file(tmp_path) -> None:
    prepare = _load_prepare()
    assert prepare.ensure_local_demo_storage(tmp_path / ".env", tmp_path) == []


def test_ensure_local_demo_storage_appends_when_missing(tmp_path) -> None:
    prepare = _load_prepare()
    env_file = tmp_path / ".env"
    env_file.write_text("DFIP_ENV=development\n", encoding="utf-8")
    added = prepare.ensure_local_demo_storage(env_file, tmp_path)
    assert added == ["DFIP_STORAGE_ENDPOINT"]
    parsed = prepare._parse_dotenv(env_file)
    expected = (tmp_path / "tmp" / "dfip-source-archive").resolve().as_posix()
    assert parsed["DFIP_STORAGE_ENDPOINT"] == expected


def test_start_bat_calls_prepare() -> None:
    text = (ROOT / "START_DFIP_DEMO.bat").read_text(encoding="utf-8")
    assert "dfip_demo_prepare.py" in text
    assert "RequireDemoLogin" in text
    assert "python -c" not in text
    assert "Start-Process" not in text
    assert "does NOT create demo users" not in text
    assert "PREPARE_EXIT" in text


def test_start_bat_replaces_stale_website_api_url() -> None:
    text = (ROOT / "START_DFIP_DEMO.bat").read_text(encoding="utf-8")
    assert "DFIP_API_BASE_URL=http://127.0.0.1:8000" in text
    assert "DFIP_API_PORT=8000" in text
    assert "web-config-check" in text
    assert "stop-web" in text
    assert "config.json" in text
    assert "skipping new website window" in text
    assert "python -m dfip_api" in text
    assert "python -m dfip_web" in text


def test_check_bat_rejects_stale_website_config() -> None:
    text = (ROOT / "CHECK_DFIP_DEMO.bat").read_text(encoding="utf-8")
    assert "web-config-check" in text
    assert "[FAIL] Website API configuration is stale/incorrect" in text
    assert "http://127.0.0.1:8000" in text


def test_prepare_treats_seed_zero_as_success(monkeypatch, capsys) -> None:
    prepare = _load_prepare()

    def _ok(_argv=None):
        print("Local demo identities upserted.")
        return 0

    monkeypatch.setattr(prepare, "overlay_dotenv", lambda _root: None)
    monkeypatch.setattr(prepare, "ensure_local_demo_upload_limits", lambda _path: [])
    monkeypatch.setattr(prepare, "ensure_local_demo_jwt_ttl", lambda _path: [])
    monkeypatch.setattr(prepare, "ensure_local_demo_storage", lambda _path, _root=None: [])
    monkeypatch.setattr(prepare, "validate_local_demo_env", lambda: "postgresql://postgres@127.0.0.1:5433/dfip")
    monkeypatch.setattr(prepare, "maybe_start_compose_postgres", lambda *_a, **_k: None)
    monkeypatch.setattr(prepare, "wait_for_postgres", lambda *_a, **_k: None)
    monkeypatch.setattr(prepare, "apply_local_migrations", lambda *_a, **_k: None)
    monkeypatch.setattr(prepare, "run_cli", _ok)
    assert prepare.prepare_local_demo() == 0
    out = capsys.readouterr().out
    assert "Local demo identities upserted." in out
    assert "Local demo preparation complete." in out


def test_prepare_keeps_nonzero_seed_as_failure(monkeypatch, capsys) -> None:
    prepare = _load_prepare()
    monkeypatch.setattr(prepare, "overlay_dotenv", lambda _root: None)
    monkeypatch.setattr(prepare, "ensure_local_demo_upload_limits", lambda _path: [])
    monkeypatch.setattr(prepare, "ensure_local_demo_jwt_ttl", lambda _path: [])
    monkeypatch.setattr(prepare, "ensure_local_demo_storage", lambda _path, _root=None: [])
    monkeypatch.setattr(prepare, "validate_local_demo_env", lambda: "postgresql://postgres@127.0.0.1:5433/dfip")
    monkeypatch.setattr(prepare, "maybe_start_compose_postgres", lambda *_a, **_k: None)
    monkeypatch.setattr(prepare, "wait_for_postgres", lambda *_a, **_k: None)
    monkeypatch.setattr(prepare, "apply_local_migrations", lambda *_a, **_k: None)
    monkeypatch.setattr(prepare, "run_cli", lambda _argv=None: 2)
    assert prepare.prepare_local_demo() == 2
    assert "Local demo preparation complete." not in capsys.readouterr().out
