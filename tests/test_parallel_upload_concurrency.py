"""DFIP multi-workbook concurrency: two uploads overlap at the same gate.

Uses a real ThreadPoolExecutor(max_workers=2) and a custom
InMemoryIngestStore that deterministically blocks the first staged row of
each batch until the test releases them. Proves both jobs reach the same
gate before either is allowed to finish.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dfip_api.app import create_app
from dfip_core.ingest.store import InMemoryIngestStore, StagedRowRecord
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import (
    post_upload,
    source_row,
    upload_workbook,
    upload_workbooks,
    wait_for_upload,
    workbook_bytes,
)
from test_p5_api import AUTH, CLIENT_ID
from test_p7_publication import publisher_settings


class BlockingIngestStore(InMemoryIngestStore):
    """Block every batch at its first staged row on a shared gate event."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.first_reached = threading.Event()
        self.second_reached = threading.Event()
        self._order: list[str] = []
        self._order_lock = threading.Lock()

    def add_staged_row(self, record: StagedRowRecord) -> None:
        with self._order_lock:
            if record.batch_id not in self._order:
                self._order.append(record.batch_id)
            position = self._order.index(record.batch_id)
        if position == 0:
            self.first_reached.set()
            self.gate.wait(timeout=60)
        elif position == 1:
            self.second_reached.set()
            self.gate.wait(timeout=60)
        super().add_staged_row(record)


def test_concurrent_uploads_overlap(tmp_path: Path) -> None:
    store = BlockingIngestStore()
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        app = create_app(
            settings=publisher_settings(),
            ingest_store=store,
            fact_store=InMemoryFactStore(),
            executor=executor,
        )
        http = TestClient(app)

        content_a = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
        content_b = workbook_bytes(tmp_path / "b.xlsx", [source_row(**{"Campaign ID": "camp-b"})])

        # Submit job A and prove it reaches the gate.
        accepted_a = post_upload(http, content_a, "a.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert accepted_a.status_code == 202
        assert store.first_reached.wait(timeout=15)

        # Submit job B and prove it reaches the same gate before A is released.
        accepted_b = post_upload(http, content_b, "b.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert accepted_b.status_code == 202
        assert store.second_reached.wait(timeout=15)
        assert not store.gate.is_set()

        # Release both.
        store.gate.set()

        completed_a = wait_for_upload(http, accepted_a.json(), headers=AUTH)
        completed_b = wait_for_upload(http, accepted_b.json(), headers=AUTH)

        assert completed_a["processing_run"]["status"] == "succeeded"
        assert completed_b["processing_run"]["status"] == "succeeded"
        assert completed_a["batch"]["status"] == "processed"
        assert completed_b["batch"]["status"] == "processed"
    finally:
        store.gate.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_inmemory_ingest_store_thread_safe() -> None:
    """Concurrent mutating operations on InMemoryIngestStore must not corrupt state."""
    from dfip_core.ingest.store import (
        InMemoryIngestStore,
        StagedRowRecord,
    )

    store = InMemoryIngestStore()
    thread_count = 8
    ops_per_thread = 25
    barrier = threading.Barrier(thread_count)
    errors: list[BaseException] = []
    error_lock = threading.Lock()

    def worker(thread_index: int) -> None:
        try:
            barrier.wait(timeout=30)
            for offset in range(ops_per_thread):
                index = thread_index * ops_per_thread + offset
                batch = store.create_batch(
                    source_file_id=f"src-{index}",
                    client_id=CLIENT_ID,
                )
                store.add_staged_row(
                    StagedRowRecord(
                        id=f"row-{index}",
                        batch_id=batch.id,
                        source_row_number=index + 1,
                        raw={"Campaign ID": f"camp-{index}"},
                        campaign_id=f"camp-{index}",
                        variation_id=f"var-{index}",
                        day=None,
                    )
                )
                fetched = store.get_batch(batch.id)
                if fetched is None or fetched.id != batch.id:
                    raise AssertionError(f"batch {batch.id} missing after create")
                store.save_batch_progress(
                    batch.id,
                    stage="staging",
                    current=1,
                    total=1,
                    message="ok",
                )
        except BaseException as exc:  # noqa: BLE001 - capture for assertion
            with error_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"ingest-{i}") for i in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert len(store.batches) == thread_count * ops_per_thread
    assert len(store.staged_rows) == thread_count * ops_per_thread
    for batch in store.batches.values():
        assert batch.progress_stage == "staging"
        assert batch.progress_current == 1
        assert batch.progress_total == 1


def test_inmemory_fact_store_thread_safe() -> None:
    """Concurrent upserts on InMemoryFactStore must not corrupt facts or history."""
    from datetime import date

    from dfip_core.transform.fact import FactRecord
    from dfip_core.transform.store import InMemoryFactStore

    store = InMemoryFactStore()
    thread_count = 8
    ops_per_thread = 25
    barrier = threading.Barrier(thread_count)
    errors: list[BaseException] = []
    error_lock = threading.Lock()

    def worker(thread_index: int) -> None:
        try:
            barrier.wait(timeout=30)
            for offset in range(ops_per_thread):
                index = thread_index * ops_per_thread + offset
                record = FactRecord(
                    client_id=CLIENT_ID,
                    campaign_id=f"camp-{index}",
                    variation_id=f"var-{index}",
                    variation_id_key=f"var-{index}",
                    day=date(2025, 8, 1),
                    sent=index,
                    processing_run_id=f"run-{index}",
                    batch_id=f"batch-{index}",
                )
                result = store.upsert(record)
                if result != "inserted":
                    raise AssertionError(f"expected inserted, got {result}")
        except BaseException as exc:  # noqa: BLE001 - capture for assertion
            with error_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"fact-{i}") for i in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert len(store.facts) == thread_count * ops_per_thread
    assert store.history == []
    current = store.list_current()
    assert len(current) == thread_count * ops_per_thread
    for record in current:
        fetched = store.get(record.key)
        assert fetched is not None
        assert fetched.sent == record.sent


def test_worker_concurrency_configuration() -> None:
    """The app-owned upload executor honors dfip_worker_concurrency=2."""
    app = create_app(
        settings=publisher_settings(dfip_worker_concurrency=2),
        executor=None,
    )
    try:
        executor = app.state.upload_executor
        assert executor is not None
        assert getattr(executor, "_max_workers", None) == 2
    finally:
        executor = app.state.upload_executor
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


def test_overlapping_fact_grain_is_correct(tmp_path: Path) -> None:
    """A restated fact keeps first_seen_at, moves the old value to history,
    and exposes the new processing_run_id as the live grain."""
    from dfip_core.transform.store import InMemoryFactStore

    app = create_app(
        settings=publisher_settings(),
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)

    content_a = workbook_bytes(
        tmp_path / "a.xlsx",
        [source_row(**{"Campaign ID": "camp-a", "Sent": 100})],
    )
    content_b = workbook_bytes(
        tmp_path / "b.xlsx",
        [source_row(**{"Campaign ID": "camp-a", "Sent": 200})],
    )

    completed_a = upload_workbook(
        http, content_a, "a.xlsx", headers=AUTH, client_id=CLIENT_ID
    ).json()
    completed_b = upload_workbook(
        http, content_b, "b.xlsx", headers=AUTH, client_id=CLIENT_ID
    ).json()

    assert completed_a["processing_run"]["status"] == "succeeded"
    assert completed_b["processing_run"]["status"] == "succeeded"

    run_a = completed_a["processing_run"]["processing_run_id"]
    run_b = completed_b["processing_run"]["processing_run_id"]
    assert run_a != run_b

    fact_store = app.state.fact_store
    history = fact_store.list_history()
    assert len(history) == 1
    superseded = history[0]
    assert superseded.superseded_by_run_id == run_b
    assert superseded.fact.processing_run_id == run_a
    assert superseded.fact.sent == 100

    live = fact_store.list_current()
    assert len(live) == 1
    live_fact = live[0]
    assert live_fact.processing_run_id == run_b
    assert live_fact.sent == 200
    assert live_fact.first_seen_at == superseded.fact.first_seen_at
    assert live_fact.last_seen_at > superseded.fact.last_seen_at


def test_partial_failure_one_invalid_of_five(tmp_path: Path) -> None:
    """A 5-file upload where one workbook has bad headers must not fail the others."""
    from dfip_core.transform.store import InMemoryFactStore

    app = create_app(
        settings=publisher_settings(),
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)

    parts: list[tuple[str, bytes]] = []
    for index in range(4):
        content = workbook_bytes(
            tmp_path / f"valid-{index}.xlsx",
            [source_row(**{"Campaign ID": f"camp-{index}"})],
        )
        parts.append((f"valid-{index}.xlsx", content))
    bad_content = workbook_bytes(
        tmp_path / "bad-headers.xlsx",
        [source_row()],
        header_override={12: "Campaign"},
    )
    parts.append(("bad-headers.xlsx", bad_content))

    completed = upload_workbooks(http, parts, headers=AUTH, client_id=CLIENT_ID).json()

    assert completed["file_count"] == 5
    assert len(completed["items"]) == 5

    succeeded = [
        item
        for item in completed["items"]
        if (item.get("processing_run") or {}).get("status") == "succeeded"
    ]
    failed = [
        item for item in completed["items"] if (item.get("batch") or {}).get("status") == "failed"
    ]
    assert len(succeeded) == 4
    assert len(failed) == 1
    assert failed[0]["original_filename"] == "bad-headers.xlsx"

    facts = http.get("/api/v1/facts", headers=AUTH, params={"client_id": CLIENT_ID}).json()
    assert facts["pagination"]["total"] == 4

    fact_store = app.state.fact_store
    assert len(fact_store.list_current()) == 4
    assert fact_store.list_history() == []


def test_sha_replay_is_idempotent_concurrently(tmp_path: Path) -> None:
    """Same-SHA upload during in-flight returns the same batch_id; replay after
    completion returns 200 with the same batch_id."""
    store = BlockingIngestStore()
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        app = create_app(
            settings=publisher_settings(),
            ingest_store=store,
            fact_store=InMemoryFactStore(),
            executor=executor,
        )
        http = TestClient(app)

        content = workbook_bytes(
            tmp_path / "same.xlsx",
            [source_row(**{"Campaign ID": "camp-a"})],
        )

        first = post_upload(http, content, "same.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert first.status_code == 202
        assert store.first_reached.wait(timeout=15)
        batch_id_a = first.json()["batch"]["batch_id"]

        second = post_upload(http, content, "same.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert second.status_code == 202
        assert second.json()["batch"]["batch_id"] == batch_id_a

        assert len(store.batches) == 1
        assert store.list_processing_runs_for_batch(batch_id_a) == []

        store.gate.set()
        completed = wait_for_upload(http, first.json(), headers=AUTH)
        assert completed["processing_run"]["status"] == "succeeded"
        assert completed["batch"]["status"] == "processed"

        assert len(store.list_processing_runs_for_batch(batch_id_a)) == 1

        third = post_upload(http, content, "same.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert third.status_code == 200
        assert third.json()["batch"]["batch_id"] == batch_id_a
        assert third.json()["replayed"] is True
    finally:
        store.gate.set()
        executor.shutdown(wait=True, cancel_futures=True)
