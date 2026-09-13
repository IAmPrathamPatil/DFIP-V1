"""Cancel and delete for non-authoritative uploads.

In-memory tests. Does not open PostgreSQL or change published history.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.ingest.store import InMemoryIngestStore, StagedRowRecord
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import post_upload, source_row, workbook_bytes
from test_p5_api import (
    AUTH,
    BATCH_A,
    BATCH_B,
    CLIENT_ID,
    JWT_SECRET,
    RUN_A,
    _encode_jwt,
    inspector_settings,
    make_settings,
    seed_stores,
)
from test_p7_publication import _publish, publisher_settings

CLIENT_B = "a0000000-0000-4000-8000-000000000002"


class GatedIngestStore(InMemoryIngestStore):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def add_staged_rows(self, records: list[StagedRowRecord]) -> None:
        self.started.set()
        self.release.wait(timeout=30)
        super().add_staged_rows(records)


class GatedFactStore(InMemoryFactStore):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def upsert_many(self, records):
        self.started.set()
        self.release.wait(timeout=30)
        return super().upsert_many(records)


def _publisher_app(**overrides):
    ingest = overrides.pop("ingest_store", InMemoryIngestStore())
    facts = overrides.pop("fact_store", InMemoryFactStore())
    publications = overrides.pop("publication_store", InMemoryPublicationStore())
    return create_app(
        settings=publisher_settings(**overrides),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=publications,
    )


def _wait_batch(http: TestClient, batch_id: str, *, status: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = http.get(f"/api/v1/batches/{batch_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == status or last.get("stage") == status:
            return last
        time.sleep(0.05)
    raise AssertionError(f"batch {batch_id} did not reach {status}: {last}")


def _cancel_gated_upload(tmp_path: Path, rows: list[dict], store: GatedIngestStore) -> str:
    store.release.clear()
    app = _publisher_app(ingest_store=store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "raw.xlsx", rows)
    accepted = post_upload(
        http, content, "raw.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert accepted.status_code == 202, accepted.text
    batch_id = accepted.json()["batch"]["batch_id"]
    assert store.started.wait(timeout=10)
    running = http.get(f"/api/v1/batches/{batch_id}", headers=AUTH).json()
    assert running["stage"] in {"received", "ingesting", "staging", "cancelling", "processing"}
    cancelled = http.post(f"/api/v1/batches/{batch_id}/cancel", headers=AUTH)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["stage"] in {"cancelling", "cancelled"}
    store.release.set()
    body = _wait_batch(http, batch_id, status="cancelled")
    assert body["status"] == "cancelled"
    assert body["stage"] == "cancelled"
    assert body["progress_percent"] is None
    retry = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH)
    assert retry.status_code == 422
    facts = http.get("/api/v1/facts", headers=AUTH, params={"limit": 50})
    assert facts.status_code == 200
    assert facts.json()["pagination"]["total"] == 0
    pubs = http.get("/api/v1/publications", headers=AUTH, params={"client_id": CLIENT_ID})
    assert pubs.json()["pagination"]["total"] == 0
    staged = http.get(f"/api/v1/batches/{batch_id}/staged-rows", headers=AUTH)
    assert staged.status_code == 200
    assert staged.json()["pagination"]["total"] == 0
    return batch_id


def test_cancel_tiny_file_during_staging(tmp_path: Path) -> None:
    _cancel_gated_upload(tmp_path, [source_row()], GatedIngestStore())


def test_cancel_medium_file_during_staging(tmp_path: Path) -> None:
    rows = [source_row(**{"Campaign ID": f"camp-{i}", "Variation ID": f"var-{i}"}) for i in range(50)]
    _cancel_gated_upload(tmp_path, rows, GatedIngestStore())


def test_cancel_large_file_during_staging(tmp_path: Path) -> None:
    rows = [source_row(**{"Campaign ID": f"camp-{i}", "Variation ID": f"var-{i}"}) for i in range(200)]
    _cancel_gated_upload(tmp_path, rows, GatedIngestStore())


def test_cancel_during_processing(tmp_path: Path) -> None:
    facts = GatedFactStore()
    facts.release.clear()
    app = _publisher_app(fact_store=facts)
    http = TestClient(app)
    content = workbook_bytes(
        tmp_path / "raw.xlsx",
        [source_row(**{"Campaign ID": f"camp-{i}", "Variation ID": f"var-{i}"}) for i in range(20)],
    )
    accepted = post_upload(http, content, "raw.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert accepted.status_code == 202, accepted.text
    batch_id = accepted.json()["batch"]["batch_id"]
    assert facts.started.wait(timeout=20)
    response = http.post(f"/api/v1/batches/{batch_id}/cancel", headers=AUTH)
    assert response.status_code == 200, response.text
    facts.release.set()
    body = _wait_batch(http, batch_id, status="cancelled")
    assert body["status"] == "cancelled"
    listed = http.get("/api/v1/facts", headers=AUTH, params={"limit": 200}).json()
    assert listed["pagination"]["total"] == 0


def test_cancel_processed_batch_is_blocked() -> None:
    ingest, facts = seed_stores()
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
    )
    http = TestClient(app)
    blocked = http.post(f"/api/v1/batches/{BATCH_A}/cancel", headers=AUTH)
    assert blocked.status_code == 409
    failed = http.post(f"/api/v1/batches/{BATCH_B}/cancel", headers=AUTH)
    assert failed.status_code == 409


def test_delete_failed_and_unpublished_processed() -> None:
    ingest, facts = seed_stores()
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
    )
    http = TestClient(app)
    before = http.get("/api/v1/facts", headers=AUTH, params={"limit": 50}).json()["pagination"]["total"]
    assert before > 0
    deleted = http.delete(f"/api/v1/batches/{BATCH_A}", headers=AUTH)
    assert deleted.status_code == 200, deleted.text
    gone = http.get(f"/api/v1/batches/{BATCH_A}", headers=AUTH)
    assert gone.status_code == 404
    after = http.get("/api/v1/facts", headers=AUTH, params={"limit": 50}).json()
    assert all(item["processing_run_id"] != RUN_A for item in after["items"])
    failed = http.delete(f"/api/v1/batches/{BATCH_B}", headers=AUTH)
    assert failed.status_code == 200, failed.text
    missing = http.get(f"/api/v1/batches/{BATCH_B}", headers=AUTH)
    assert missing.status_code == 404


def test_delete_published_batch_is_blocked() -> None:
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    http = TestClient(app)
    created = _publish(http)
    assert created.status_code == 201, created.text
    blocked = http.delete(f"/api/v1/batches/{BATCH_A}", headers=AUTH)
    assert blocked.status_code == 409
    still = http.get(f"/api/v1/batches/{BATCH_A}", headers=AUTH)
    assert still.status_code == 200
    current = http.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert current.json()["publication"]["processing_run_id"] == RUN_A


def test_delete_cancelled_upload(tmp_path: Path) -> None:
    store = GatedIngestStore()
    batch_id = _cancel_gated_upload(tmp_path, [source_row()], store)
    # Recreate: previous helper built its own app. Delete against a fresh cancelled seed.
    ingest, facts = seed_stores()
    ingest.batches[BATCH_B].status = "cancelled"
    ingest.batches[BATCH_B].progress_stage = "cancelled"
    ingest.batches[BATCH_B].cancel_requested = True
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
    )
    http = TestClient(app)
    deleted = http.delete(f"/api/v1/batches/{BATCH_B}", headers=AUTH)
    assert deleted.status_code == 200, deleted.text
    assert http.get(f"/api/v1/batches/{BATCH_B}", headers=AUTH).status_code == 404
    del batch_id


def test_tenant_isolation_blocks_foreign_cancel_and_delete() -> None:
    ingest, facts = seed_stores()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
    )
    http = TestClient(app)
    foreign = {
        "Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_B)}"
    }
    cancel = http.post(f"/api/v1/batches/{BATCH_B}/cancel", headers=foreign)
    assert cancel.status_code == 404
    deleted = http.delete(f"/api/v1/batches/{BATCH_B}", headers=foreign)
    assert deleted.status_code == 404
    still = http.get(
        f"/api/v1/batches/{BATCH_B}",
        headers={"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"},
    )
    assert still.status_code == 200


def test_publish_progress_is_truthful_after_create() -> None:
    ingest, facts = seed_stores()
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
    )
    http = TestClient(app)
    created = _publish(http)
    assert created.status_code == 201, created.text
    progress = http.get(
        "/api/v1/publications/progress",
        headers=AUTH,
        params={"processing_run_id": RUN_A, "client_id": CLIENT_ID},
    )
    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["stage"] == "published"
    assert body["status"] == "succeeded"
    assert body["progress_percent"] == 100
    assert body["publication_id"] == created.json()["publication"]["publication_id"]
