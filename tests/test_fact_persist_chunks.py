"""Chunked fact persistence and worker failure safety.

In-memory stores only unless a test is marked postgres.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_config.catalog import InMemoryCatalogStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.engine import FACT_PERSIST_CHUNK_SIZE, run_transformation
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import (
    complete_reprocess_response,
    source_row,
    upload_workbook,
    workbook_bytes,
)
from test_p4_transform import build_batch, staged
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings
from test_v2c_catalog import CLIENT_A


class RecordingFactStore(InMemoryFactStore):
    def __init__(self) -> None:
        super().__init__()
        self.many_sizes: list[int] = []

    def upsert_many(self, records: Sequence[FactRecord]) -> list[str]:
        self.many_sizes.append(len(records))
        return super().upsert_many(records)


class BoomAfterFirstChunk(InMemoryFactStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def upsert_many(self, records: Sequence[FactRecord]) -> list[str]:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("forced chunk failure")
        return super().upsert_many(records)


def _unique_rows(count: int) -> list:
    return [
        staged(
            index + 2,
            **{"Campaign ID": f"camp-{index}", "Delivered": 10 + index},
        )
        for index in range(count)
    ]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_A)}"}


def _app(**overrides) -> TestClient:
    return TestClient(
        create_app(
            settings=make_settings(
                dfip_auth_mode="jwt",
                dfip_auth_secret=JWT_SECRET,
                database_url="",
            ),
            ingest_store=overrides.get("ingest_store", InMemoryIngestStore()),
            fact_store=overrides.get("fact_store", InMemoryFactStore()),
            publication_store=overrides.get("publication_store", InMemoryPublicationStore()),
            catalog_store=overrides.get("catalog_store", InMemoryCatalogStore()),
        )
    )


def test_chunked_persist_commits_in_bounded_batches() -> None:
    store, batch_id = build_batch(_unique_rows(25))
    facts = RecordingFactStore()
    result = run_transformation(
        store,
        facts,
        batch_id,
        processing_run_id=store.processing_run_for_batch(batch_id).id,
        persist_chunk_size=10,
    )
    assert result.transformed == 25
    assert result.processing_run.status == "succeeded"
    assert facts.many_sizes == [10, 10, 5]
    assert result.processing_run.progress_at is not None
    assert len(facts.list_current()) == 25
    assert len({item.key for item in facts.list_current()}) == 25


def test_large_synthetic_fixture_is_not_one_transaction_per_fact() -> None:
    store, batch_id = build_batch(_unique_rows(120))
    facts = RecordingFactStore()
    result = run_transformation(
        store,
        facts,
        batch_id,
        processing_run_id=store.processing_run_for_batch(batch_id).id,
        persist_chunk_size=40,
    )
    assert result.transformed == 120
    assert facts.many_sizes == [40, 40, 40]
    assert sum(facts.many_sizes) == 120
    assert len(facts.many_sizes) < 120
    assert FACT_PERSIST_CHUNK_SIZE == 500


def test_worker_exception_marks_run_failed_and_preserves_partial_facts() -> None:
    store, batch_id = build_batch(_unique_rows(5))
    oldest = store.processing_run_for_batch(batch_id)
    assert oldest is not None
    oldest.status = "succeeded"
    oldest.finished_at = oldest.started_at
    store.save_processing_run(oldest)
    failing = store.add_processing_run(
        batch_id=batch_id,
        campaign_label_version_id=oldest.campaign_label_version_id,
        template_label_version_id=oldest.template_label_version_id,
        rate_card_version_id=oldest.rate_card_version_id,
        label_group_version_id=oldest.label_group_version_id,
        engine_version="0.4.0",
    )
    facts = BoomAfterFirstChunk()
    with pytest.raises(RuntimeError, match="forced chunk failure"):
        run_transformation(
            store,
            facts,
            batch_id,
            processing_run_id=failing.id,
            persist_chunk_size=2,
        )
    failed = store.get_processing_run(failing.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.finished_at is not None
    assert failed.started_at == failing.started_at
    assert failed.error_summary
    assert "fact persistence" in failed.error_summary
    assert len(facts.list_current()) == 2
    frozen = store.get_processing_run(oldest.id)
    assert frozen is not None
    assert frozen.status == "succeeded"
    assert frozen.started_at == oldest.started_at
    assert frozen.finished_at == oldest.finished_at


def test_replacement_run_restates_partial_facts_without_duplicates() -> None:
    store, batch_id = build_batch(_unique_rows(5))
    first = store.processing_run_for_batch(batch_id)
    assert first is not None
    facts = BoomAfterFirstChunk()
    with pytest.raises(RuntimeError):
        run_transformation(
            store, facts, batch_id, processing_run_id=first.id, persist_chunk_size=2
        )
    assert len(facts.list_current()) == 2
    recovered = InMemoryFactStore()
    recovered.facts.update(facts.facts)
    recovered.history.extend(facts.history)
    replacement = store.add_processing_run(
        batch_id=batch_id,
        campaign_label_version_id=first.campaign_label_version_id,
        template_label_version_id=first.template_label_version_id,
        rate_card_version_id=first.rate_card_version_id,
        label_group_version_id=first.label_group_version_id,
        engine_version="0.4.0",
    )
    result = run_transformation(
        store,
        recovered,
        batch_id,
        processing_run_id=replacement.id,
        persist_chunk_size=2,
    )
    assert result.succeeded
    current = recovered.list_current()
    assert len(current) == 5
    assert len({item.key for item in current}) == 5
    assert all(item.processing_run_id == replacement.id for item in current)
    assert len(recovered.list_history()) == 2
    assert all(item.superseded_by_run_id == replacement.id for item in recovered.list_history())
    assert store.get_processing_run(first.id).status == "failed"


def test_http_reprocess_exception_does_not_leave_running(tmp_path: Path, monkeypatch) -> None:
    http = _app()
    headers = _headers()
    content = workbook_bytes(
        tmp_path / "chunk.xlsx",
        [source_row(**{"Campaign ID": "chunk-1", "Delivered": 10})],
    )
    uploaded = upload_workbook(http, content, "chunk.xlsx", headers=headers)
    batch_id = uploaded.json()["batch"]["batch_id"]
    old_run_id = uploaded.json()["processing_run"]["processing_run_id"]
    pub_before = http.get("/api/v1/publications/current", headers=headers).json()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced transform failure")

    monkeypatch.setattr("dfip_api.upload_service.run_transformation", _boom)
    response = http.post(f"/api/v1/batches/{batch_id}/process", headers=headers)
    body = complete_reprocess_response(http, response, headers=headers).json()
    new_run = body["processing_run"]
    assert new_run["processing_run_id"] != old_run_id
    assert new_run["status"] == "failed"
    assert new_run["status"] != "running"
    assert new_run["finished_at"] is not None
    assert new_run["error_summary"]
    frozen = http.get(f"/api/v1/processing-runs/{old_run_id}", headers=headers).json()
    assert frozen["status"] == "succeeded"
    facts = http.get(
        "/api/v1/facts",
        headers=headers,
        params={"processing_run_id": old_run_id, "limit": 50},
    ).json()
    assert facts["pagination"]["total"] == 1
    pub_after = http.get("/api/v1/publications/current", headers=headers).json()
    assert pub_after == pub_before
    listed = http.get(
        "/api/v1/processing-runs",
        headers=headers,
        params={"batch_id": batch_id, "limit": 20},
    ).json()
    statuses = {item["processing_run_id"]: item["status"] for item in listed["items"]}
    assert statuses[old_run_id] == "succeeded"
    assert statuses[new_run["processing_run_id"]] == "failed"
    assert "running" not in statuses.values()
