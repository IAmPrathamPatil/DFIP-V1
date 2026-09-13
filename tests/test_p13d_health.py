"""P13D monitoring + health.

Public /health stays liveness. Authenticated /ops/ready is cheap readiness.
Does not connect to hosted or August databases.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest
from dfip_api.app import create_app
from dfip_api.ops import probe_database
from dfip_api.recovery import fail_abandoned_processing_runs
from dfip_api.source_storage import FilesystemSourceObjectStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.connection import create_pool
from fastapi.testclient import TestClient

from postgres_support import requires_postgres, seed_identity
from test_p5_api import (
    AUTH,
    CLIENT_ID,
    DEV_TOKEN,
    JWT_SECRET,
    _encode_jwt,
    inspector_settings,
    make_settings,
)

SECRET_TOKENS = (
    DEV_TOKEN,
    JWT_SECRET,
    "DATABASE_URL",
    "DFIP_AUTH_SECRET",
    "DFIP_BOOTSTRAP_TOKEN",
    "postgresql://",
    "password",
)


def _publisher_app(**overrides):
    ingest = InMemoryIngestStore()
    facts = InMemoryFactStore()
    app = create_app(
        settings=inspector_settings(**overrides),
        ingest_store=ingest,
        fact_store=facts,
    )
    return app, ingest


def _body_has_secrets(text: str) -> list[str]:
    lowered = text.lower()
    return [token for token in SECRET_TOKENS if token.lower() in lowered]


def test_health_remains_public_liveness() -> None:
    app, _ingest = _publisher_app()
    http = TestClient(app)
    response = http.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "not_checked"
    assert _body_has_secrets(response.text) == []


def test_health_does_not_open_dead_pool() -> None:
    app, _ingest = _publisher_app()
    app.state.db_pool = create_pool("postgresql://postgres@127.0.0.1:1/dfip_p13d_none")
    http = TestClient(app)
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json()["database"]["status"] == "not_checked"


def test_ready_requires_authentication() -> None:
    app, _ingest = _publisher_app()
    http = TestClient(app)
    assert http.get("/api/v1/ops/ready").status_code == 401


def test_ready_rejects_client_role() -> None:
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    headers = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_ID)}"}
    response = http.get("/api/v1/ops/ready", headers=headers)
    assert response.status_code == 403
    assert "error" in response.json()


def test_ready_rejects_reader_dev_token() -> None:
    app = create_app(
        settings=make_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    response = http.get("/api/v1/ops/ready", headers=AUTH)
    assert response.status_code == 403


def test_publisher_ready_is_ready_when_in_memory() -> None:
    app, _ingest = _publisher_app()
    http = TestClient(app)
    response = http.get("/api/v1/ops/ready", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["application"] == "dfip-api"
    assert body["database"]["status"] == "ok"
    assert body["storage"]["status"] == "not_configured"
    assert body["worker"] == "idle"
    assert body["backup"]["status"] == "not_configured"
    assert _body_has_secrets(response.text) == []
    assert CLIENT_ID not in response.text


def test_ready_database_unavailable_is_503() -> None:
    app, _ingest = _publisher_app()
    app.state.db_pool = create_pool("postgresql://postgres@127.0.0.1:1/dfip_p13d_none")
    http = TestClient(app)
    live = http.get("/health")
    assert live.status_code == 200
    response = http.get("/api/v1/ops/ready", headers=AUTH)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"]["status"] == "unavailable"
    assert "error" not in body
    assert "postgresql://" not in response.text.lower()
    assert "127.0.0.1" not in response.text


def test_ready_jwt_structured_when_identity_store_cannot_reach_db() -> None:
    from dfip_api.identity_store import PostgresIdentityStore

    dead = create_pool("postgresql://postgres@127.0.0.1:1/dfip_p13d_none")
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        identity_store=PostgresIdentityStore(dead),
    )
    app.state.db_pool = dead
    http = TestClient(app)
    headers = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"}
    live = http.get("/health")
    assert live.status_code == 200
    response = http.get("/api/v1/ops/ready", headers=headers)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"]["status"] == "unavailable"
    assert body.get("error", {}).get("code") != "PERSISTENCE_UNAVAILABLE"
    assert "error" not in body


def test_probe_database_closed_port() -> None:
    pool = create_pool("postgresql://postgres@127.0.0.1:1/dfip_p13d_none")
    assert probe_database(pool) == "unavailable"
    assert probe_database(None) == "ok"


def test_ready_storage_ok_and_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = FilesystemSourceObjectStore(root)
    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        source_store=store,
    )
    http = TestClient(app)
    ok = http.get("/api/v1/ops/ready", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["storage"]["status"] == "ok"
    assert str(root) not in ok.text
    shutil.rmtree(root)
    missing = http.get("/api/v1/ops/ready", headers=AUTH)
    assert missing.status_code == 503
    assert missing.json()["storage"]["status"] == "unavailable"
    assert str(root) not in missing.text


def test_ready_worker_busy() -> None:
    app, _ingest = _publisher_app()
    app.state.upload_service._in_flight[(CLIENT_ID, "a" * 64)] = "batch-1"
    http = TestClient(app)
    body = http.get("/api/v1/ops/ready", headers=AUTH).json()
    assert body["worker"] == "busy"
    assert body["status"] == "ready"


def test_ready_does_not_walk_archive_or_verify_backup(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "archive"
    store = FilesystemSourceObjectStore(root)
    (root / "dfip-source-files" / "nested").mkdir(parents=True)
    (root / "dfip-source-files" / "nested" / "file.xlsx").write_bytes(b"x")

    def boom(*_args, **_kwargs):
        raise AssertionError("archive walk or backup verify is not allowed")

    monkeypatch.setattr("dfip_api.source_storage.FilesystemSourceObjectStore.object_count", boom)
    monkeypatch.setattr("dfip_api.backup.verify_backup", boom)
    monkeypatch.setattr("dfip_api.backup.sha256_file", boom)
    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        source_store=store,
    )
    http = TestClient(app)
    response = http.get("/api/v1/ops/ready", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["storage"]["status"] == "ok"


def test_ready_backup_manifest_age_without_hash(tmp_path: Path, monkeypatch) -> None:
    backup = tmp_path / "last-backup"
    backup.mkdir()
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": "2026-01-01T00:00:00Z",
                "source_archive": {
                    "inventory": [{"path": "secret-company/file.xlsx", "sha256": "ab"}]
                },
            }
        ),
        encoding="utf-8",
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("backup verify is not allowed")

    monkeypatch.setattr("dfip_api.backup.verify_backup", boom)
    app, _ingest = _publisher_app(dfip_backup_last_dir=str(backup))
    http = TestClient(app)
    body = http.get("/api/v1/ops/ready", headers=AUTH).json()
    assert body["backup"]["status"] == "configured"
    assert body["backup"]["manifest_created_at"] == "2026-01-01T00:00:00Z"
    assert isinstance(body["backup"]["manifest_age_seconds"], int)
    assert "secret-company" not in json.dumps(body)
    assert str(backup) not in json.dumps(body)


def test_unexpected_500_logs_type_and_route(caplog) -> None:
    class BoomRepository:
        def list_source_files(self, **_kwargs):
            raise RuntimeError("DATABASE_URL=postgresql://secret-user:secret-pass@db/dfip")

    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        repository=BoomRepository(),  # type: ignore[arg-type]
    )
    with caplog.at_level(logging.ERROR, logger="dfip_api.errors"):
        response = TestClient(app, raise_server_exceptions=False).get(
            "/api/v1/source-files", headers=AUTH
        )
    assert response.status_code == 500
    assert response.json()["error"]["message"] == "An unexpected error occurred."
    assert "postgresql://secret-user" not in response.text
    assert "unexpected-error type=RuntimeError" in caplog.text
    assert "/api/v1/source-files" in caplog.text
    assert "secret-pass" not in caplog.text
    assert "DATABASE_URL=" not in caplog.text


def test_persistence_unavailable_logs_safely(caplog) -> None:
    from dfip_api.errors import PersistenceUnavailableError

    class DownRepository:
        def list_facts(self, **_kwargs):
            raise PersistenceUnavailableError()

    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        repository=DownRepository(),  # type: ignore[arg-type]
    )
    with caplog.at_level(logging.ERROR, logger="dfip_api.errors"):
        response = TestClient(app).get("/api/v1/facts", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PERSISTENCE_UNAVAILABLE"
    assert "persistence-unavailable type=PersistenceUnavailableError" in caplog.text
    assert "/api/v1/facts" in caplog.text
    assert "postgresql://" not in caplog.text


def test_abandoned_sweep_logs_count(caplog) -> None:
    ingest = InMemoryIngestStore()
    source = ingest.register_source_file(
        client_id=CLIENT_ID,
        sha256="a" * 64,
        original_filename="a.xlsx",
        byte_size=1,
        source_kind="native_export",
    )
    batch = ingest.create_batch(source_file_id=source.id, client_id=CLIENT_ID)
    running = ingest.add_processing_run(
        batch_id=batch.id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="0.4.0",
    )
    running.status = "running"
    ingest.save_processing_run(running)
    with caplog.at_level(logging.INFO, logger="dfip_api.recovery"):
        count = fail_abandoned_processing_runs(ingest)
    assert count == 1
    assert "abandoned-run-sweep marked=1" in caplog.text
    assert "password" not in caplog.text.lower()
    assert "bearer" not in caplog.text.lower()


def test_processing_run_exposes_progress_and_error() -> None:
    app, ingest = _publisher_app()
    source = ingest.register_source_file(
        client_id=CLIENT_ID,
        sha256="b" * 64,
        original_filename="b.xlsx",
        byte_size=1,
        source_kind="native_export",
    )
    batch = ingest.create_batch(source_file_id=source.id, client_id=CLIENT_ID)
    run = ingest.add_processing_run(
        batch_id=batch.id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="0.4.0",
    )
    run.status = "failed"
    run.error_summary = (
        "Processing abandoned because the API process restarted. Retry is available."
    )
    ingest.save_processing_run(run)
    http = TestClient(app)
    listed = http.get(
        "/api/v1/processing-runs",
        headers=AUTH,
        params={"status": "failed"},
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert "progress_at" in item
    assert "abandoned" in (item.get("error_summary") or "").lower()


@pytest.mark.postgres
@requires_postgres
def test_postgres_ready_select_one(tmp_path: Path, pg_conn, postgres_url) -> None:
    seed_identity(pg_conn, subject="ops-publisher", role="publisher", client_id=CLIENT_ID)
    archive = tmp_path / "pg-archive"
    archive.mkdir()
    http = TestClient(
        create_app(
            settings=make_settings(
                dfip_auth_mode="jwt",
                dfip_auth_secret=JWT_SECRET,
                database_url=postgres_url,
                dfip_env="test",
                dfip_storage_endpoint=str(archive),
            )
        )
    )
    headers = {
        "Authorization": (
            "Bearer " + _encode_jwt(role="publisher", client_id=CLIENT_ID, sub="ops-publisher")
        )
    }
    try:
        live = http.get("/health")
        assert live.status_code == 200
        assert live.json()["database"]["configured"] is True
        assert live.json()["database"]["status"] == "not_checked"
        ready = http.get("/api/v1/ops/ready", headers=headers)
        assert ready.status_code == 200, ready.text
        body = ready.json()
        assert body["status"] == "ready"
        assert body["database"]["status"] == "ok"
        assert body["storage"]["status"] == "ok"
        assert body["worker"] == "idle"
        assert postgres_url not in ready.text
    finally:
        http.app.state.upload_executor.shutdown(wait=False, cancel_futures=True)
        http.close()
