"""P13B durable processing / recovery.

In-memory tests do not open PostgreSQL. Postgres tests use DFIP_TEST_DATABASE_URL
and a disposable filesystem archive. They do not touch hosted/live data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dfip_api.app import create_app
from dfip_api.errors import PersistenceConfigurationError
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_api.recovery import ABANDONED_RUN_REASON, ARCHIVE_MISSING_MESSAGE
from dfip_api.source_storage import InMemorySourceObjectStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row

from http_ingest_support import (
    complete_reprocess_response,
    source_row,
    upload_workbook,
    workbook_bytes,
)
from postgres_support import (
    CLIENT_A,
    CLIENT_B,
    requires_postgres,
    seed_identity,
    seed_working_set,
)
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings, production_settings

AUTH_A = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_A)}"}
AUTH_B = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_B)}"}


def _memory_app(source_store=None, **overrides):
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url="",
            **overrides,
        ),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        publication_store=InMemoryPublicationStore(),
        source_store=source_store or InMemorySourceObjectStore(),
    )


def _upload(http: TestClient, tmp_path: Path, headers, *, name: str = "raw.xlsx", **row):
    content = workbook_bytes(tmp_path / name, [source_row(**row)])
    return upload_workbook(http, content, name, headers=headers), content


def test_production_requires_durable_source_archive() -> None:
    with pytest.raises(PersistenceConfigurationError, match="DFIP_STORAGE_ENDPOINT"):
        create_app(
            settings=production_settings(dfip_storage_endpoint=""),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_development_keeps_in_memory_source_store() -> None:
    app = create_app(
        settings=make_settings(dfip_storage_endpoint=""),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    assert type(app.state.source_store).__name__ == "InMemorySourceObjectStore"


def test_sha_replay_after_completed_processing(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    first, content = _upload(http, tmp_path, AUTH_A, name="done.xlsx")
    assert first.status_code == 201
    assert first.json()["processing_run"]["status"] == "succeeded"
    second = upload_workbook(http, content, "done.xlsx", headers=AUTH_A)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["batch"]["batch_id"] == first.json()["batch"]["batch_id"]
    assert len(http.app.state.ingest_store.batches) == 1


def test_sha_after_staged_is_recovery_not_replay(tmp_path: Path) -> None:
    app = _memory_app()
    http = TestClient(app)
    first, content = _upload(http, tmp_path, AUTH_A, name="staged.xlsx")
    batch_id = first.json()["batch"]["batch_id"]
    old_run = first.json()["processing_run"]["processing_run_id"]
    ingest = app.state.ingest_store
    batch = ingest.get_batch(batch_id)
    run = ingest.get_processing_run(old_run)
    assert batch is not None and run is not None
    batch.status = "staged"
    run.status = "failed"
    run.error_summary = ABANDONED_RUN_REASON
    ingest.save_batch(batch)
    ingest.save_processing_run(run)
    second = upload_workbook(http, content, "staged.xlsx", headers=AUTH_A)
    assert second.status_code in {200, 201}
    assert second.json()["replayed"] is False
    assert second.json()["batch"]["batch_id"] == batch_id
    assert second.json()["processing_run"]["processing_run_id"] != old_run
    assert second.json()["processing_run"]["status"] == "succeeded"
    assert second.json()["published"] is False
    assert len(ingest.batches) == 1


def test_abandoned_pending_and_running_runs_are_failed() -> None:
    store = InMemoryIngestStore()
    source = store.register_source_file(
        client_id=CLIENT_A,
        sha256="a" * 64,
        original_filename="a.xlsx",
        byte_size=1,
        source_kind="native_export",
    )
    batch = store.create_batch(source_file_id=source.id, client_id=CLIENT_A)
    pending = store.add_processing_run(
        batch_id=batch.id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="0.3.0",
    )
    pending.status = "pending"
    store.save_processing_run(pending)
    running = store.add_processing_run(
        batch_id=batch.id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="0.3.0",
    )
    running.status = "running"
    store.save_processing_run(running)
    first = store.fail_abandoned_processing_runs(reason=ABANDONED_RUN_REASON)
    second = store.fail_abandoned_processing_runs(reason=ABANDONED_RUN_REASON)
    assert first == 2
    assert second == 0
    assert store.get_processing_run(pending.id).status == "failed"
    assert store.get_processing_run(running.id).status == "failed"
    assert store.get_processing_run(pending.id).error_summary == ABANDONED_RUN_REASON


def test_received_recovery_uses_same_batch(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _memory_app(store)
    http = TestClient(app)
    first, _content = _upload(http, tmp_path, AUTH_A, name="recv.xlsx")
    batch_id = first.json()["batch"]["batch_id"]
    ingest = app.state.ingest_store
    batch = ingest.get_batch(batch_id)
    run = ingest.get_processing_run(first.json()["processing_run"]["processing_run_id"])
    assert batch is not None and run is not None
    run.status = "failed"
    run.error_summary = ABANDONED_RUN_REASON
    ingest.save_processing_run(run)
    ingest.reset_staging_for_batch(batch_id)
    recovered = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_A)
    body = complete_reprocess_response(http, recovered, headers=AUTH_A).json()
    assert body["batch_id"] == batch_id
    assert body["processing_run"]["processing_run_id"] != run.id
    assert body["processing_run"]["status"] == "succeeded"
    assert body["published"] is False
    assert len(ingest.batches) == 1


def test_missing_archive_fails_clearly(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _memory_app(store)
    http = TestClient(app)
    first, _content = _upload(http, tmp_path, AUTH_A, name="gone.xlsx")
    batch_id = first.json()["batch"]["batch_id"]
    ingest = app.state.ingest_store
    ingest.reset_staging_for_batch(batch_id)
    store._objects.clear()
    response = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_A)
    assert response.status_code == 422
    assert ARCHIVE_MISSING_MESSAGE in response.json()["error"]["message"]


def test_retry_does_not_publish(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    first, _content = _upload(http, tmp_path, AUTH_A, name="nopub.xlsx")
    batch_id = first.json()["batch"]["batch_id"]
    run_id = first.json()["processing_run"]["processing_run_id"]
    published = http.post(
        "/api/v1/publications",
        headers=AUTH_A,
        json={"client_id": CLIENT_A, "processing_run_id": run_id},
    )
    assert published.status_code == 201
    pointer = published.json()["publication"]["publication_id"]
    retried = complete_reprocess_response(
        http,
        http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_A),
        headers=AUTH_A,
    ).json()
    assert retried["published"] is False
    current = http.get("/api/v1/publications/current", headers=AUTH_A).json()
    assert current["publication"]["publication_id"] == pointer
    assert current["publication"]["processing_run_id"] == run_id


def test_company_b_cannot_recover_company_a(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    first, _content = _upload(http, tmp_path, AUTH_A, name="iso.xlsx")
    batch_id = first.json()["batch"]["batch_id"]
    denied = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_B)
    assert denied.status_code == 404


def test_company_a_cannot_recover_company_b(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    first, _content = _upload(
        http, tmp_path, AUTH_B, name="iso-b.xlsx", **{"Campaign ID": "camp-b"}
    )
    batch_id = first.json()["batch"]["batch_id"]
    denied = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_A)
    assert denied.status_code == 404


def test_upload_worker_finalizes_incomplete_run() -> None:
    app = _memory_app()
    ingest = app.state.ingest_store
    source = ingest.register_source_file(
        client_id=CLIENT_A,
        sha256="b" * 64,
        original_filename="a.xlsx",
        byte_size=1,
        source_kind="native_export",
    )
    batch = ingest.create_batch(source_file_id=source.id, client_id=CLIENT_A)
    run = ingest.add_processing_run(
        batch_id=batch.id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="0.3.0",
    )
    run.status = "running"
    ingest.save_processing_run(run)
    app.state.upload_service._finalize_incomplete_job(batch.id)
    assert ingest.get_processing_run(run.id).status == "failed"


def test_partial_facts_are_restated_not_duplicated(tmp_path: Path) -> None:
    app = _memory_app()
    http = TestClient(app)
    first, _content = _upload(http, tmp_path, AUTH_A, name="facts.xlsx")
    batch_id = first.json()["batch"]["batch_id"]
    old_run = first.json()["processing_run"]["processing_run_id"]
    facts_before = http.get("/api/v1/facts", headers=AUTH_A, params={"limit": 50}).json()
    grain_count = facts_before["pagination"]["total"]
    ingest = app.state.ingest_store
    batch = ingest.get_batch(batch_id)
    run = ingest.get_processing_run(old_run)
    batch.status = "staged"
    run.status = "failed"
    ingest.save_batch(batch)
    ingest.save_processing_run(run)
    retried = complete_reprocess_response(
        http,
        http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_A),
        headers=AUTH_A,
    ).json()
    new_run = retried["processing_run"]["processing_run_id"]
    assert new_run != old_run
    facts_after = http.get("/api/v1/facts", headers=AUTH_A, params={"limit": 50}).json()
    assert facts_after["pagination"]["total"] == grain_count
    assert all(item["processing_run_id"] == new_run for item in facts_after["items"])


def test_qa_remains_runnable_after_succeeded_run(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    first, _content = _upload(http, tmp_path, AUTH_A, name="qa.xlsx")
    run_id = first.json()["processing_run"]["processing_run_id"]
    again = http.post(f"/api/v1/processing-runs/{run_id}/qa", headers=AUTH_A)
    assert again.status_code == 200
    assert again.json()["qa_verdict"] in {"pass", "warn", "fail", "unavailable"}


def test_source_archive_not_exposed(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    first, _content = _upload(http, tmp_path, AUTH_A, name="uri.xlsx")
    listed = http.get("/api/v1/source-files", headers=AUTH_A)
    assert listed.status_code == 200
    assert "storage_uri" not in listed.text
    assert "dfip-source://" not in listed.text
    assert "storage_uri" not in first.json()


def _pg_app(postgres_url: str, archive: Path) -> TestClient:
    return TestClient(
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


def _pg_auth(subject: str, client_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=client_id, sub=subject)}"
    }


@pytest.mark.postgres
@requires_postgres
def test_postgres_restart_sweep_and_staged_retry(tmp_path: Path, pg_conn, postgres_url) -> None:
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-1", role="publisher", client_id=CLIENT_A)
    archive = tmp_path / "source-archive"
    first = _pg_app(postgres_url, archive)
    headers = _pg_auth("publisher-1", CLIENT_A)
    content = workbook_bytes(tmp_path / "pg.xlsx", [source_row()])
    uploaded = upload_workbook(first, content, "pg.xlsx", headers=headers)
    assert uploaded.status_code == 201
    batch_id = uploaded.json()["batch"]["batch_id"]
    old_run = uploaded.json()["processing_run"]["processing_run_id"]
    source_id = uploaded.json()["source_file_id"]
    first.app.state.upload_executor.shutdown(wait=True, cancel_futures=False)
    first.close()
    with connect(postgres_url, row_factory=dict_row, autocommit=True) as crash:
        crash.execute(
            """
            UPDATE processing_run
            SET status = 'running', finished_at = NULL, error_summary = NULL
            WHERE id = %s
            """,
            (old_run,),
        )
        crash.execute("UPDATE batch SET status = 'staged' WHERE id = %s", (batch_id,))
        leftover = crash.execute(
            "SELECT status, error_summary FROM processing_run WHERE id = %s",
            (old_run,),
        ).fetchone()
    assert leftover is not None
    assert leftover["status"] == "running"
    assert leftover["error_summary"] is None
    second = _pg_app(postgres_url, archive)
    abandoned = second.get(f"/api/v1/processing-runs/{old_run}", headers=headers).json()
    assert abandoned["status"] == "failed"
    assert "abandoned" in (abandoned.get("error_summary") or "").lower()
    listed = second.get("/api/v1/batches", headers=headers, params={"limit": 20}).json()
    assert any(item["batch_id"] == batch_id for item in listed["items"])
    retried = complete_reprocess_response(
        second,
        second.post(f"/api/v1/batches/{batch_id}/process", headers=headers),
        headers=headers,
    ).json()
    new_run = retried["processing_run"]["processing_run_id"]
    assert new_run != old_run
    assert retried["processing_run"]["status"] == "succeeded"
    assert retried["published"] is False
    facts = second.get("/api/v1/facts", headers=headers, params={"limit": 50}).json()
    grains = {
        (item["campaign_id"], item["variation_id_key"], item["day"]) for item in facts["items"]
    }
    assert len(grains) == facts["pagination"]["total"]
    current = second.get("/api/v1/publications/current", headers=headers).json()
    assert (
        current.get("publication") is None or current["publication"]["processing_run_id"] != new_run
    )
    qa = second.post(f"/api/v1/processing-runs/{new_run}/qa", headers=headers)
    assert qa.status_code == 200, qa.json()
    published = second.post(
        "/api/v1/publications",
        headers=headers,
        json={"client_id": CLIENT_A, "processing_run_id": new_run},
    )
    assert published.status_code == 201, published.json()
    pointer = second.get("/api/v1/publications/current", headers=headers).json()
    assert pointer["publication"]["processing_run_id"] == new_run
    stored = second.app.state.source_store
    assert stored.exists(
        client_id=CLIENT_A, source_file_id=source_id, sha256=uploaded.json()["sha256"]
    )
    second.close()


@pytest.mark.postgres
@requires_postgres
def test_postgres_received_same_batch_and_missing_archive(
    tmp_path: Path, pg_conn, postgres_url
) -> None:
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-1", role="publisher", client_id=CLIENT_A)
    archive = tmp_path / "source-archive-b"
    http = _pg_app(postgres_url, archive)
    headers = _pg_auth("publisher-1", CLIENT_A)
    content = workbook_bytes(tmp_path / "recv.xlsx", [source_row()])
    uploaded = upload_workbook(http, content, "recv.xlsx", headers=headers)
    batch_id = uploaded.json()["batch"]["batch_id"]
    source_id = uploaded.json()["source_file_id"]
    sha = uploaded.json()["sha256"]
    pg_conn.execute(
        "UPDATE processing_run SET status = 'failed', error_summary = %s WHERE batch_id = %s",
        (ABANDONED_RUN_REASON, batch_id),
    )
    pg_conn.execute(
        """
        UPDATE batch SET status = 'received', row_count_staged = 0, completed_at = NULL
        WHERE id = %s
        """,
        (batch_id,),
    )
    pg_conn.execute("DELETE FROM stg_source_row WHERE batch_id = %s", (batch_id,))
    pg_conn.commit()
    recovered = complete_reprocess_response(
        http,
        http.post(f"/api/v1/batches/{batch_id}/process", headers=headers),
        headers=headers,
    ).json()
    assert recovered["batch_id"] == batch_id
    assert recovered["processing_run"]["status"] == "succeeded"
    batches = http.get("/api/v1/batches", headers=headers, params={"limit": 50}).json()
    matching = [item for item in batches["items"] if item["source_file_id"] == source_id]
    assert len(matching) == 1
    http.app.state.source_store._path(CLIENT_A, source_id, sha).unlink(missing_ok=True)
    pg_conn.execute(
        "UPDATE batch SET status = 'received', row_count_staged = 0 WHERE id = %s",
        (batch_id,),
    )
    pg_conn.commit()
    missing = http.post(f"/api/v1/batches/{batch_id}/process", headers=headers)
    assert missing.status_code == 422
    assert ARCHIVE_MISSING_MESSAGE in missing.json()["error"]["message"]
    http.close()


@pytest.mark.postgres
@requires_postgres
def test_postgres_tenant_isolation_on_retry(tmp_path: Path, pg_conn, postgres_url) -> None:
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-a", role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="publisher-b", role="publisher", client_id=CLIENT_B)
    archive = tmp_path / "source-archive-c"
    http = _pg_app(postgres_url, archive)
    headers_a = _pg_auth("publisher-a", CLIENT_A)
    headers_b = _pg_auth("publisher-b", CLIENT_B)
    uploaded = upload_workbook(
        http,
        workbook_bytes(tmp_path / "a.xlsx", [source_row()]),
        "a.xlsx",
        headers=headers_a,
    )
    batch_id = uploaded.json()["batch"]["batch_id"]
    denied = http.post(f"/api/v1/batches/{batch_id}/process", headers=headers_b)
    assert denied.status_code == 404
    http.close()
