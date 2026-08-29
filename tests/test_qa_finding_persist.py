"""QA finding bulk persistence: uniqueness, batching, isolation, failure safety."""

from __future__ import annotations

import time
import tracemalloc

import pytest
from dfip_analytics.qa import (
    DuplicateQaFindingKeyError,
    QaContext,
    QaFinding,
    assert_unique_qa_finding_keys,
    evaluate_qa,
)
from dfip_api.app import create_app
from dfip_api.qa_store import InMemoryQaFindingStore
from dfip_api.qa_workflow import evaluate_and_persist_run
from dfip_core.transform.fact import FactRecord
from dfip_db.analytics_repository import (
    QA_FINDING_INSERT_CHUNK,
    QA_PERSIST_STATEMENT_TIMEOUT,
    PostgresAnalyticsRepository,
    qa_persist_plan,
)
from fastapi.testclient import TestClient

from test_p5_api import AUTH, BATCH_A, CLIENT_ID, RUN_A, RUN_B, seed_stores
from test_p7_publication import publisher_settings
from test_v2_http_qa import _error
from test_v2_phase2a_qa import BATCH, CLIENT, RUN, _fact


FIXTURE_SIZES = (125, 1_000, 10_000, 25_000)


class Recorder:
    def __init__(self) -> None:
        self.transactions = 0
        self.statements: list[str] = []
        self.insert_statements = 0
        self.delete_statements = 0
        self.rows_attempted = 0
        self.fail_after_inserts: int | None = None

    def transaction(self):
        rec = self

        class _Tx:
            def __enter__(self) -> RecordingConn:
                rec.transactions += 1
                return RecordingConn(rec)

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        return _Tx()


class RecordingConn:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        self.recorder.statements.append(text)
        if text.startswith("DELETE FROM qa_finding"):
            self.recorder.delete_statements += 1
        if "INSERT INTO qa_finding" in text:
            self.recorder.insert_statements += 1
            if params is not None:
                self.recorder.rows_attempted += len(params[0])
            if (
                self.recorder.fail_after_inserts is not None
                and self.recorder.insert_statements >= self.recorder.fail_after_inserts
            ):
                raise RuntimeError("forced persist failure")
        return self

    def fetchall(self):
        return []


class BoomQaStore(InMemoryQaFindingStore):
    def replace_for_run(self, processing_run_id: str, findings) -> None:
        raise RuntimeError("forced finding persist failure")


def _recording_repo(recorder: Recorder) -> PostgresAnalyticsRepository:
    repo = PostgresAnalyticsRepository.__new__(PostgresAnalyticsRepository)
    repo._pool = None  # type: ignore[assignment]
    repo._tx = recorder.transaction  # type: ignore[method-assign]
    return repo


def make_findings(
    count: int,
    *,
    run_id: str = RUN_A,
    client_id: str = CLIENT_ID,
    batch_id: str = BATCH_A,
    rule_id: str = "QA-RATIO-GT-ONE",
) -> list[QaFinding]:
    return [
        QaFinding(
            rule_id=rule_id,
            severity="warning",
            description="A count relationship that should be <= 1 is inverted",
            entity_type="fact",
            entity_key=f"camp-{index}|var|2025-08-01:unique_clicks:unique_impressions",
            message="unique_clicks exceeds unique_impressions",
            client_id=client_id,
            processing_run_id=run_id,
            batch_id=batch_id,
            diagnostics={"unique_clicks": 2, "unique_impressions": 1, "i": index},
        )
        for index in range(count)
    ]


def test_qa_persist_plan_matches_bounded_batches() -> None:
    assert QA_FINDING_INSERT_CHUNK == 1000
    assert QA_PERSIST_STATEMENT_TIMEOUT == "120s"
    expected = {
        125: (1, 1),
        1_000: (1, 1),
        10_000: (1, 10),
        25_000: (1, 25),
    }
    for count, (deletes, inserts) in expected.items():
        plan = qa_persist_plan(count)
        assert plan["delete_transactions"] == deletes
        assert plan["insert_transactions"] == inserts
        assert plan["insert_statements"] == inserts
        assert plan["transaction_count"] == deletes + inserts
        assert plan["chunk_size"] == QA_FINDING_INSERT_CHUNK


def test_fixture_sizes_persist_in_batches_without_row_by_row() -> None:
    for count in FIXTURE_SIZES:
        recorder = Recorder()
        repo = _recording_repo(recorder)
        findings = make_findings(count)
        started = time.perf_counter()
        repo.replace_for_run(RUN_A, findings)
        elapsed = time.perf_counter() - started
        plan = qa_persist_plan(count)
        assert recorder.transactions == plan["transaction_count"]
        assert recorder.insert_statements == plan["insert_statements"]
        assert recorder.delete_statements == 1
        assert recorder.rows_attempted == count
        assert elapsed < 5
        assert not any(
            "VALUES" in item and "INSERT INTO qa_finding" in item
            for item in recorder.statements
        )
        assert all(
            "unnest(" in item.lower()
            for item in recorder.statements
            if "INSERT INTO qa_finding" in item
        )
        timeouts = [item for item in recorder.statements if "SET LOCAL statement_timeout" in item]
        assert len(timeouts) == plan["transaction_count"]


def test_25000_fixture_memory_and_timeout_budget() -> None:
    tracemalloc.start()
    recorder = Recorder()
    repo = _recording_repo(recorder)
    findings = make_findings(25_000)
    started = time.perf_counter()
    repo.replace_for_run(RUN_A, findings)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert elapsed < 5
    assert peak < 200 * 1024 * 1024
    assert recorder.insert_statements == 25
    assert recorder.transactions == 26


def test_duplicate_finding_keys_fail_before_sql() -> None:
    first, second = make_findings(2)
    duplicate = QaFinding(
        rule_id=first.rule_id,
        severity=first.severity,
        description=first.description,
        entity_type=first.entity_type,
        entity_key=first.entity_key,
        message=first.message,
        client_id=first.client_id,
        processing_run_id=first.processing_run_id,
        batch_id=first.batch_id,
    )
    recorder = Recorder()
    repo = _recording_repo(recorder)
    with pytest.raises(DuplicateQaFindingKeyError):
        repo.replace_for_run(RUN_A, [first, second, duplicate])
    assert recorder.transactions == 0
    assert recorder.statements == []


def test_evaluate_qa_keys_are_unique_for_failed_and_ratio_rules() -> None:
    facts: list[FactRecord] = []
    for index in range(125):
        facts.append(
            _fact(
                campaign_id=f"camp-{index}",
                variation_id_key=f"var-{index}",
                sent=10,
                failed=12,
                delivered=4,
                unique_impressions=5,
                unique_clicks=6,
                unique_conversions=7,
                unique_click_through_conversions=8,
                unique_impression_through_conversions=9,
            )
        )
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=facts,
            expected_client_id=CLIENT,
        )
    )
    assert_unique_qa_finding_keys(findings)
    by_rule: dict[str, int] = {}
    for item in findings:
        by_rule[item.rule_id] = by_rule.get(item.rule_id, 0) + 1
    assert by_rule["QA-IMPOSSIBLE-FAILED"] == 125
    assert by_rule["QA-RATIO-GT-ONE"] == 125 * 5
    keys = [(item.rule_id, item.entity_key) for item in findings]
    assert len(keys) == len(set(keys))


def test_replace_for_run_is_deterministic_and_does_not_multiply() -> None:
    store = InMemoryQaFindingStore()
    first = make_findings(125)
    store.replace_for_run(RUN_A, first)
    once = store.list_for_run(RUN_A)
    store.replace_for_run(RUN_A, first)
    twice = store.list_for_run(RUN_A)
    assert len(once) == 125
    assert len(twice) == 125
    assert [(row["rule_id"], row["entity_key"]) for row in once] == [
        (row["rule_id"], row["entity_key"]) for row in twice
    ]
    assert {row["processing_run_id"] for row in twice} == {RUN_A}
    assert {row["client_id"] for row in twice} == {CLIENT_ID}


def test_replace_for_run_leaves_another_run_untouched() -> None:
    store = InMemoryQaFindingStore()
    store.replace_for_run(RUN_A, make_findings(20, run_id=RUN_A))
    before = [dict(row) for row in store.list_for_run(RUN_A)]
    store.replace_for_run(RUN_B, make_findings(20, run_id=RUN_B))
    after = store.list_for_run(RUN_A)
    assert [row["finding_id"] for row in after] == [row["finding_id"] for row in before]
    assert all(row["processing_run_id"] == RUN_B for row in store.list_for_run(RUN_B))


def test_partial_insert_failure_deletes_this_run_findings() -> None:
    recorder = Recorder()
    recorder.fail_after_inserts = 2
    repo = _recording_repo(recorder)
    with pytest.raises(RuntimeError, match="forced persist failure"):
        repo.replace_for_run(RUN_A, make_findings(2_500))
    assert recorder.delete_statements == 2
    assert recorder.insert_statements == 2


def test_persist_failure_records_unavailable_never_pass() -> None:
    ingest, facts = seed_stores()
    ingest.processing_runs[RUN_A].status = "succeeded"
    ingest.processing_runs[RUN_A].qa_verdict = None
    verdict = evaluate_and_persist_run(
        ingest_store=ingest,
        fact_store=facts,
        qa_store=BoomQaStore(),
        run=ingest.processing_runs[RUN_A],
        client_id=CLIENT_ID,
    )
    assert verdict == "unavailable"
    assert ingest.processing_runs[RUN_A].qa_verdict == "unavailable"


def test_unavailable_still_blocks_publish() -> None:
    ingest, facts = seed_stores()
    ingest.processing_runs[RUN_A].status = "succeeded"
    ingest.processing_runs[RUN_A].qa_verdict = "unavailable"
    blocked = TestClient(
        create_app(
            settings=publisher_settings(),
            ingest_store=ingest,
            fact_store=facts,
        )
    ).post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": RUN_A},
    )
    assert blocked.status_code == 422
    assert _error(blocked)["message"] == "Processing run QA did not complete."


def test_in_memory_25000_replace_loses_no_rows() -> None:
    store = InMemoryQaFindingStore()
    findings = make_findings(25_000)
    store.replace_for_run(RUN_A, findings)
    listed = store.list_for_run(RUN_A)
    assert len(listed) == 25_000
    assert len({(row["rule_id"], row["entity_key"]) for row in listed}) == 25_000
    store.replace_for_run(RUN_A, findings)
    assert len(store.list_for_run(RUN_A)) == 25_000
