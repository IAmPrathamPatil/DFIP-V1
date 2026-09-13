"""RUN 011: upload auto-progress, zombie recovery, engine_version, mashup paging."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from pathlib import Path

from dfip_api.app import create_app
from dfip_api.recovery import ZOMBIE_RUN_REASON
from dfip_api.service import batch_to_response
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.engine import ENGINE_VERSION
from dfip_core.transform.store import InMemoryFactStore
from dfip_web.client_workbook import mashup_text
from fastapi.testclient import TestClient

from http_ingest_support import source_row, upload_workbook, wait_for_upload, workbook_bytes
from test_p5_api import AUTH, CLIENT_ID
from test_p7_publication import _publish, publisher_settings


def _app(**overrides):
    ingest = overrides.pop("ingest_store", InMemoryIngestStore())
    facts = overrides.pop("fact_store", InMemoryFactStore())
    return create_app(
        settings=publisher_settings(dfip_dev_auth_client_id=CLIENT_ID, **overrides),
        ingest_store=ingest,
        fact_store=facts,
    )


def _wait_run(http: TestClient, run_id: str, *, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        response = http.get(f"/api/v1/processing-runs/{run_id}", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"processing run {run_id} did not finish")


def test_normal_upload_completes_without_retry(tmp_path: Path) -> None:
    http = TestClient(_app())
    content = workbook_bytes(tmp_path / "auto.xlsx", [source_row(campaign_id="run011-a")])
    accepted = upload_workbook(
        http, content, "auto.xlsx", headers=AUTH, wait=False, client_id=CLIENT_ID
    )
    assert accepted.status_code == 202
    assert accepted.json()["batch"]["status"] == "received"
    body = wait_for_upload(http, accepted.json(), headers=AUTH)
    assert body["batch"]["status"] == "processed"
    assert body["processing_run"]["status"] == "succeeded"
    assert body["processing_run"]["engine_version"] == ENGINE_VERSION
    assert body["batch"]["stuck"] is False
    assert body["batch"]["stage"] in {"succeeded", "processed"}


def test_zombie_pending_run_is_failed_and_rescheduled(tmp_path: Path) -> None:
    ingest = InMemoryIngestStore()
    app = _app(ingest_store=ingest)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "zombie.xlsx", [source_row(campaign_id="run011-z")])
    accepted = upload_workbook(
        http, content, "zombie.xlsx", headers=AUTH, wait=False, client_id=CLIENT_ID
    )
    body = wait_for_upload(http, accepted.json(), headers=AUTH)
    batch_id = body["batch"]["batch_id"]
    zombie = ingest.add_processing_run(
        batch_id=batch_id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version=ENGINE_VERSION,
    )
    assert zombie.status == "pending"
    result = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH)
    assert result.status_code == 202
    fresh = result.json()["processing_run"]
    assert fresh["processing_run_id"] != zombie.id
    abandoned = ingest.get_processing_run(zombie.id)
    assert abandoned is not None
    assert abandoned.status == "failed"
    assert abandoned.error_summary == ZOMBIE_RUN_REASON
    completed = _wait_run(http, fresh["processing_run_id"])
    assert completed["status"] == "succeeded"


def test_resume_orphaned_received_batch_without_manual_retry(tmp_path: Path) -> None:
    ingest = InMemoryIngestStore()
    app = _app(ingest_store=ingest)
    service = app.state.upload_service
    content = workbook_bytes(tmp_path / "orphan.xlsx", [source_row(campaign_id="run011-o")])
    digest = hashlib.sha256(content).hexdigest()
    source = ingest.register_source_file(
        client_id=CLIENT_ID,
        sha256=digest,
        original_filename="orphan.xlsx",
        byte_size=len(content),
        source_kind="native_export",
    )
    service._persist_source_bytes(source, content)
    batch = ingest.create_batch(source_file_id=source.id, client_id=CLIENT_ID)
    assert batch.status == "received"
    listed = service.enrich_batch(batch_to_response(batch), resume=False)
    assert listed.stuck is True
    assert listed.stage == "received"
    started = service.resume_orphaned_work()
    assert started >= 1
    deadline = time.time() + 20
    latest = ingest.get_batch(batch.id)
    run = ingest.processing_run_for_batch(batch.id)
    while time.time() < deadline:
        latest = ingest.get_batch(batch.id)
        run = ingest.processing_run_for_batch(batch.id)
        if (
            latest is not None
            and latest.status == "processed"
            and run is not None
            and run.status == "succeeded"
        ):
            break
        time.sleep(0.05)
    assert latest is not None and latest.status == "processed"
    assert run is not None and run.status == "succeeded"
    assert run.engine_version == ENGINE_VERSION


def test_changed_value_republish_history_newest_wins(tmp_path: Path) -> None:
    """Controlled Month 1 v1→v2: newest numeric wins, no duplicate month, other months unchanged."""
    http = TestClient(_app())

    def _upload_and_publish(name: str, row: dict[str, object]) -> dict:
        content = workbook_bytes(tmp_path / name, [source_row(**row)])
        accepted = upload_workbook(
            http, content, name, headers=AUTH, wait=False, client_id=CLIENT_ID
        )
        assert accepted.status_code == 202
        body = wait_for_upload(http, accepted.json(), headers=AUTH)
        assert body["batch"]["status"] == "processed"
        assert body["processing_run"]["status"] == "succeeded"
        published = _publish(
            http, processing_run_id=body["processing_run"]["processing_run_id"]
        )
        assert published.status_code == 201
        return body

    _upload_and_publish(
        "month_other.xlsx",
        {
            "Day": datetime(2025, 10, 1),
            "Campaign ID": "run011-other-month",
            "Sent": 40,
            "Delivered": 40,
            "Unique Impressions": 40,
        },
    )
    _upload_and_publish(
        "month1_v1.xlsx",
        {
            "Day": datetime(2025, 11, 1),
            "Campaign ID": "run011-changed-value",
            "Sent": 100,
            "Delivered": 100,
            "Unique Impressions": 100,
        },
    )
    _upload_and_publish(
        "month1_v2.xlsx",
        {
            "Day": datetime(2025, 11, 1),
            "Campaign ID": "run011-changed-value",
            "Sent": 175,
            "Delivered": 175,
            "Unique Impressions": 175,
        },
    )
    history = http.get(
        "/api/v1/publications/history/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200},
    )
    assert history.status_code == 200
    items = history.json()["items"]
    assert history.json()["pagination"]["total"] == 2
    by_campaign = {item["campaign_id"]: item for item in items}
    assert by_campaign["run011-changed-value"]["sent"] == 175
    assert by_campaign["run011-changed-value"]["month_label"] == "Nov-25"
    assert by_campaign["run011-other-month"]["sent"] == 40
    assert by_campaign["run011-other-month"]["month_label"] == "Oct-25"
    months = [item["month_label"] for item in items]
    assert months.count("Nov-25") == 1
    assert months.count("Oct-25") == 1


def test_publishedfacts_m_reuses_first_page_and_refreshes_access_token() -> None:
    mashup = mashup_text()
    assert "Csv.Document" in mashup
    assert "Table.PromoteHeaders" in mashup
    assert "PageLimit" not in mashup
    assert "layout = \"table\"" not in mashup
    assert "/api/v1/auth/refresh" in mashup
    assert "ManualStatusHandling" in mashup
    assert "Session expired. Download a new Refreshable Workbook" in mashup
    assert "/api/v1/publications/history/facts.csv" in mashup
    assert "DFIP_AUTH_SECRET" not in mashup
