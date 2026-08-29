"""Background workbook ingest must not block the API event loop."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from tempfile import gettempdir

import pytest
from dfip_api.app import create_app
from dfip_core.ingest.store import InMemoryIngestStore, StagedRowRecord
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import (
    post_upload,
    source_row,
    upload_workbook,
    wait_for_upload,
    workbook_bytes,
)
from test_p5_api import AUTH, CLIENT_ID, JWT_SECRET, _encode_jwt, make_settings
from test_p7_publication import publisher_settings

ROOT = Path(__file__).resolve().parents[1]
REAL_AUG25 = ROOT / "tmp" / "real_e2e_aug25" / "Raw_Aug25.xlsx"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"
HEALTH_BUDGET_S = 2.0


class GatedIngestStore(InMemoryIngestStore):
    """Real staging path that can pause after ingest has started."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def add_staged_row(self, record: StagedRowRecord) -> None:
        self.started.set()
        self.release.wait(timeout=30)
        super().add_staged_row(record)


def _publisher_app(**overrides):
    ingest = overrides.pop("ingest_store", InMemoryIngestStore())
    return create_app(
        settings=publisher_settings(**overrides),
        ingest_store=ingest,
        fact_store=overrides.pop("fact_store", InMemoryFactStore()),
    )


def _latency(client: TestClient, method: str, path: str, **kwargs) -> tuple[float, object]:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    return time.perf_counter() - started, response


def test_upload_returns_202_without_waiting_for_ingest(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    app = _publisher_app(ingest_store=store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    started = time.perf_counter()
    try:
        response = post_upload(
            http, content, "client-a.xlsx", headers=AUTH, client_id=CLIENT_ID
        )
        ack_s = time.perf_counter() - started
        assert response.status_code == 202
        assert ack_s < 2.0
        body = response.json()
        assert body["accepted"] is True
        assert body["published"] is False
        assert body["processing_run"] is None
        assert body["batch"]["status"] == "received"
        assert body["batch"]["batch_id"]
    finally:
        store.release.set()
    completed = wait_for_upload(http, response.json(), headers=AUTH)
    assert completed["processing_run"]["status"] == "succeeded"


def test_health_session_catalog_and_status_stay_up_during_ingest(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    app = _publisher_app(ingest_store=store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    accepted = post_upload(
        http, content, "client-a.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert accepted.status_code == 202
    batch_id = accepted.json()["batch"]["batch_id"]
    assert store.started.wait(timeout=10)
    try:
        health_s, health = _latency(http, "GET", "/health")
        session_s, session = _latency(http, "GET", "/api/v1/session", headers=AUTH)
        catalog_s, catalogs = _latency(
            http,
            "GET",
            "/api/v1/catalogs/logic",
            headers=AUTH,
            params={"client_id": CLIENT_ID},
        )
        batch_s, batch = _latency(http, "GET", f"/api/v1/batches/{batch_id}", headers=AUTH)
        runs_s, runs = _latency(
            http,
            "GET",
            "/api/v1/processing-runs",
            headers=AUTH,
            params={"batch_id": batch_id, "limit": 8, "offset": 0},
        )
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert session.status_code == 200
        assert catalogs.status_code == 200
        assert batch.status_code == 200
        assert batch.json()["status"] in {"received", "staged", "validated", "processed"}
        assert runs.status_code == 200
        assert health_s < HEALTH_BUDGET_S
        assert session_s < HEALTH_BUDGET_S
        assert catalog_s < HEALTH_BUDGET_S
        assert batch_s < HEALTH_BUDGET_S
        assert runs_s < HEALTH_BUDGET_S
        assert store.release.is_set() is False
        assert store.started.is_set()
    finally:
        store.release.set()
    completed = wait_for_upload(http, accepted.json(), headers=AUTH)
    assert completed["processing_run"]["status"] == "succeeded"
    assert completed["batch"]["status"] == "processed"


def test_small_upload_completes_and_cleans_temp_files(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    before = set(Path(gettempdir()).glob("dfip-upload-*"))
    content = workbook_bytes(tmp_path / "small.xlsx", [source_row()])
    response = upload_workbook(
        http, content, "small.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 201
    body = response.json()
    assert body["processing_run"]["status"] == "succeeded"
    assert body["transform"]["transformed"] == 1
    assert body["batch"]["status"] == "processed"
    after = set(Path(gettempdir()).glob("dfip-upload-*"))
    assert after <= before


def test_failed_header_contract_is_surfaced_and_cleans_temp(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    before = set(Path(gettempdir()).glob("dfip-upload-*"))
    content = workbook_bytes(
        tmp_path / "bad-headers.xlsx",
        [source_row()],
        header_override={12: "Campaign"},
    )
    accepted = post_upload(
        http, content, "bad-headers.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert accepted.status_code == 202
    body = wait_for_upload(http, accepted.json(), headers=AUTH)
    assert body["processing_run"] is None
    assert body["batch"]["status"] == "failed"
    assert body["published"] is False
    after = set(Path(gettempdir()).glob("dfip-upload-*"))
    assert after <= before
    facts = http.get("/api/v1/facts", headers=AUTH).json()
    assert facts["pagination"]["total"] == 0


def test_failed_transform_is_surfaced_on_run_status(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "num.xlsx", [source_row(**{"Sent": "twelve"})])
    response = upload_workbook(http, content, "num.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert response.status_code == 201
    body = response.json()
    assert body["processing_run"]["status"] == "failed"
    assert any(item["reason_code"] == "INVALID_NUMERIC" for item in body["rejections"])
    published = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["items"] == []


def test_duplicate_in_flight_sha_does_not_start_a_second_job(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    app = _publisher_app(ingest_store=store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    try:
        first = post_upload(http, content, "a.xlsx", headers=AUTH, client_id=CLIENT_ID)
        second = post_upload(http, content, "a.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["batch"]["batch_id"] == second.json()["batch"]["batch_id"]
        assert len(store.batches) == 1
    finally:
        store.release.set()
    completed = wait_for_upload(http, first.json(), headers=AUTH)
    assert completed["processing_run"]["status"] == "succeeded"


def test_client_cannot_read_another_clients_in_flight_batch(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=store,
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    headers_a = {
        "Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"
    }
    headers_b = {
        "Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_B)}"
    }
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    accepted = None
    try:
        accepted = post_upload(
            http, content, "a.xlsx", headers=headers_a, client_id=CLIENT_ID
        )
        assert accepted.status_code == 202
        batch_id = accepted.json()["batch"]["batch_id"]
        hidden = http.get(f"/api/v1/batches/{batch_id}", headers=headers_b)
        assert hidden.status_code == 404
    finally:
        store.release.set()
    if accepted is not None:
        wait_for_upload(http, accepted.json(), headers=headers_a)


def test_publication_still_requires_explicit_post_after_async_upload(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    uploaded = upload_workbook(
        http, content, "a.xlsx", headers=AUTH, client_id=CLIENT_ID
    ).json()
    run_id = uploaded["processing_run"]["processing_run_id"]
    empty = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert empty["items"] == []
    published = http.post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": run_id},
    )
    assert published.status_code == 201
    facts = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200},
    ).json()
    assert facts["pagination"]["total"] == 1
    assert facts["items"][0]["unique_clicks"] == 2


def test_real_aug25_upload_keeps_api_responsive() -> None:
    if not REAL_AUG25.is_file():
        pytest.skip(f"prepared acceptance fixture is missing: {REAL_AUG25}")
    app = _publisher_app()
    http = TestClient(app)
    payload = REAL_AUG25.read_bytes()
    ack_started = time.perf_counter()
    accepted = post_upload(
        http, payload, "Raw_Aug25.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    ack_s = time.perf_counter() - ack_started
    assert accepted.status_code == 202, accepted.text
    assert ack_s < 15.0
    batch_id = accepted.json()["batch"]["batch_id"]
    health_latencies: list[float] = []
    session_latencies: list[float] = []
    process_started = time.perf_counter()
    deadline = time.monotonic() + 300.0
    completed = None
    while time.monotonic() < deadline:
        health_s, health = _latency(http, "GET", "/health")
        session_s, session = _latency(http, "GET", "/api/v1/session", headers=AUTH)
        catalog_s, catalogs = _latency(
            http,
            "GET",
            "/api/v1/catalogs/logic",
            headers=AUTH,
            params={"client_id": CLIENT_ID},
        )
        status_s, batch = _latency(http, "GET", f"/api/v1/batches/{batch_id}", headers=AUTH)
        assert health.status_code == 200
        assert session.status_code == 200
        assert catalogs.status_code == 200
        assert batch.status_code == 200
        assert health_s < HEALTH_BUDGET_S
        assert session_s < HEALTH_BUDGET_S
        assert catalog_s < HEALTH_BUDGET_S
        assert status_s < HEALTH_BUDGET_S
        health_latencies.append(health_s)
        session_latencies.append(session_s)
        stored_body = http.app.state.upload_service.completed_result(batch_id)
        if stored_body is not None:
            completed = stored_body.model_dump(mode="json")
            break
        time.sleep(0.05)
    assert completed is not None, "50k-row ingest did not finish within 300s"
    elapsed = time.perf_counter() - process_started
    assert completed["published"] is False
    assert completed["processing_run"] is not None
    assert completed["processing_run"]["status"] == "succeeded"
    assert completed["batch"]["status"] == "processed"
    assert completed["batch"]["row_count_staged"] >= 50_000
    assert max(health_latencies) < HEALTH_BUDGET_S
    assert max(session_latencies) < HEALTH_BUDGET_S
    assert elapsed > ack_s
    print(
        "aug25_ack_s={:.3f} aug25_process_s={:.3f} health_max_s={:.3f} "
        "session_max_s={:.3f} staged={} transform={}".format(
            ack_s,
            elapsed,
            max(health_latencies),
            max(session_latencies),
            completed["batch"]["row_count_staged"],
            None if completed.get("transform") is None else completed["transform"],
        )
    )
