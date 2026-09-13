"""Truthful live processing progress and workbook fact caps.

Does not skip QA, invent percentages, or raise the 10-minute SLA by timeout.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from dfip_api.app import create_app
from dfip_api.service import batch_to_response, derive_processing_stage
from dfip_core.ingest.pipeline import ingest_workbook
from dfip_core.ingest.progress import (
    STAGE_FAILED,
    STAGE_INGESTING,
    STAGE_PROCESSING,
    STAGE_STAGING,
    STAGE_SUCCEEDED,
    STAGE_VALIDATING,
    progress_percent,
)
from dfip_core.ingest.store import (
    BatchRecord,
    InMemoryIngestStore,
    ProcessingRunRecord,
    apply_batch_progress,
)
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import post_upload, source_row, wait_for_upload, workbook_bytes
from test_p5_api import AUTH, CLIENT_ID
from test_p7_publication import publisher_settings
from test_v2_upload_async import GatedIngestStore


def _batch(**kwargs) -> BatchRecord:
    now = datetime.now(tz=UTC)
    values = {
        "id": "b1",
        "source_file_id": "s1",
        "client_id": CLIENT_ID,
        "status": "received",
        "row_count_declared": 10,
        "row_count_staged": 10,
        "row_count_rejected": 0,
        "observed_day_min": None,
        "observed_day_max": None,
        "created_at": now,
        "completed_at": None,
        "error_summary": None,
        "worksheet_name": "Web-Engage Raw",
        "header_row": 1,
        "source_start_column": "A",
        "empty_row_count": 0,
    }
    values.update(kwargs)
    return BatchRecord(**values)


class GatedQaFactStore(InMemoryFactStore):
    """Pause QA fact load so Validating is observable after transform."""

    def __init__(self) -> None:
        super().__init__()
        self.qa_started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def for_run(self, processing_run_id: str):
        self.qa_started.set()
        self.release.wait(timeout=30)
        return super().for_run(processing_run_id)


def test_progress_percent_is_measurable_only() -> None:
    assert progress_percent(STAGE_INGESTING, None, None) is None
    assert progress_percent(STAGE_STAGING, 0, 100) == 0
    assert progress_percent(STAGE_STAGING, 50, 100) == 50
    assert progress_percent(STAGE_STAGING, 100, 100) == 99
    assert progress_percent(STAGE_SUCCEEDED, 100, 100) == 100
    assert progress_percent(STAGE_FAILED, 10, 10) is None


def test_processed_during_qa_is_not_ready_to_publish() -> None:
    batch = _batch(
        status="processed",
        progress_stage=STAGE_VALIDATING,
        progress_current=2,
        progress_total=4,
        progress_message="Running QA checks...",
    )
    run = ProcessingRunRecord(
        id="r1",
        batch_id=batch.id,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="test",
        started_at=batch.created_at,
        finished_at=batch.created_at,
        status="succeeded",
        qa_verdict=None,
        client_id=batch.client_id,
    )
    assert derive_processing_stage(batch, run) == STAGE_VALIDATING
    body = batch_to_response(batch, run)
    assert body.stage == STAGE_VALIDATING
    assert body.status == "processed"
    assert body.progress_percent == 50


def test_legacy_processed_without_progress_is_succeeded() -> None:
    batch = _batch(status="processed")
    assert derive_processing_stage(batch, None) == STAGE_SUCCEEDED
    assert batch_to_response(batch).progress_percent == 100


def test_stage_change_clears_stale_counters() -> None:
    batch = _batch(
        progress_stage=STAGE_STAGING,
        progress_current=100,
        progress_total=100,
        progress_message="Staging rows...",
    )
    apply_batch_progress(
        batch, stage=STAGE_PROCESSING, message="Transforming campaign/day facts..."
    )
    assert batch.progress_stage == STAGE_PROCESSING
    assert batch.progress_current is None
    assert batch.progress_total is None
    assert batch.progress_message == "Transforming campaign/day facts..."


def test_ingest_emits_ingesting_then_staging(tmp_path: Path) -> None:
    path = tmp_path / "progress.xlsx"
    workbook_bytes(path, [source_row(), source_row(**{"Campaign ID": "camp-b"})])
    events: list[tuple[str, int | None, int | None, str | None]] = []

    def on_progress(stage, current, total, message) -> None:
        events.append((stage, current, total, message))

    result = ingest_workbook(path, InMemoryIngestStore(), on_progress=on_progress)
    assert result.batch.status == "staged"
    stages = [item[0] for item in events]
    assert STAGE_INGESTING in stages
    assert STAGE_STAGING in stages
    assert stages[0] == STAGE_INGESTING
    last_staging = [item for item in events if item[0] == STAGE_STAGING][-1]
    assert last_staging[1] == result.staged_count
    assert not any(
        current == 1000 and total == 1000
        for stage, current, total, _message in events
        if stage == STAGE_STAGING
    )


def test_ingest_rejects_over_max_facts_before_transform(tmp_path: Path) -> None:
    path = tmp_path / "over-cap.xlsx"
    rows = [
        source_row(),
        source_row(**{"Campaign ID": "camp-b"}),
        source_row(**{"Campaign ID": "camp-c"}),
    ]
    workbook_bytes(path, rows)
    result = ingest_workbook(path, InMemoryIngestStore(), max_facts=2)
    assert result.batch.status == "failed"
    assert result.processing_run is None
    assert "maximum of 2 source facts" in (result.batch.error_summary or "")
    assert result.staged_count <= 2


def test_http_upload_exposes_live_staging_without_retry(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    app = create_app(
        settings=publisher_settings(),
        ingest_store=store,
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "live.xlsx", [source_row() for _ in range(8)])
    accepted = post_upload(http, content, "live.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert accepted.status_code == 202
    batch_id = accepted.json()["batch"]["batch_id"]
    assert store.started.wait(timeout=15)
    try:
        body = http.get(f"/api/v1/batches/{batch_id}", headers=AUTH).json()
        assert body["stuck"] is False
        assert body["stage"] in {STAGE_INGESTING, STAGE_STAGING}
        assert body["stage"] != "succeeded"
        percent = body.get("progress_percent")
        assert percent in {None, 0} or percent < 100
        assert body.get("started_at")
        assert body.get("progress_at")
    finally:
        store.release.set()
    completed = wait_for_upload(http, accepted.json(), headers=AUTH)
    assert completed["batch"]["status"] == "processed"
    assert completed["batch"]["stage"] in {"succeeded", "processed"}
    assert completed["batch"]["progress_percent"] == 100
    assert completed["batch"]["stuck"] is False
    assert completed["processing_run"]["status"] == "succeeded"


def test_http_upload_does_not_report_100_while_gated(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    seen_stages: list[str] = []
    app = create_app(
        settings=publisher_settings(),
        ingest_store=store,
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "gated.xlsx", [source_row() for _ in range(6)])
    accepted = post_upload(http, content, "gated.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert accepted.status_code == 202
    batch_id = accepted.json()["batch"]["batch_id"]
    assert store.started.wait(timeout=15)
    try:
        for _ in range(3):
            body = http.get(f"/api/v1/batches/{batch_id}", headers=AUTH).json()
            seen_stages.append(body["stage"])
            assert body["stuck"] is False
            assert body.get("progress_percent") != 100
            assert body["status"] in {"received", "staged"}
    finally:
        store.release.set()
    wait_for_upload(http, accepted.json(), headers=AUTH)
    assert STAGE_STAGING in seen_stages or STAGE_INGESTING in seen_stages


def test_http_upload_stays_validating_after_transform(tmp_path: Path) -> None:
    facts = GatedQaFactStore()
    facts.release.clear()
    app = create_app(
        settings=publisher_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=facts,
    )
    http = TestClient(app)
    rows = [source_row(**{"Campaign ID": f"camp-{index}"}) for index in range(4)]
    content = workbook_bytes(tmp_path / "qa-live.xlsx", rows)
    accepted = post_upload(http, content, "qa-live.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert accepted.status_code == 202
    batch_id = accepted.json()["batch"]["batch_id"]
    assert facts.qa_started.wait(timeout=30)
    try:
        body = http.get(f"/api/v1/batches/{batch_id}", headers=AUTH).json()
        assert body["status"] == "processed"
        assert body["stage"] == STAGE_VALIDATING
        assert body.get("progress_percent") != 100
        assert body["stuck"] is False
    finally:
        facts.release.set()
    completed = wait_for_upload(http, accepted.json(), headers=AUTH)
    assert completed["batch"]["stage"] in {STAGE_SUCCEEDED, "processed"}
    assert completed["batch"]["progress_percent"] == 100
