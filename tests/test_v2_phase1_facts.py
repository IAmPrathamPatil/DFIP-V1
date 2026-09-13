"""PostgreSQL fact insert / restatement / history / duplicate grain / locking."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from decimal import Decimal

from dfip_core.transform.engine import run_transformation
from dfip_core.transform.fact import FactRecord

from postgres_support import (
    BATCH_A,
    CLIENT_A,
    RUN_A,
    postgres_only,
    requires_postgres,
    sample_fact,
    seed_working_set,
)

pytestmark = [postgres_only, requires_postgres]


def test_fact_insert_unchanged_restatement_and_history(pg_conn, pg_stores) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    first = sample_fact(total_cost=Decimal("1.00"), sent=1)
    assert facts.upsert(first) == "inserted"
    unchanged = sample_fact(total_cost=Decimal("1.00"), sent=1, last_seen_at=first.last_seen_at)
    # last_seen_at is lineage-only; business values match.
    assert facts.upsert(unchanged) == "unchanged"
    stored = facts.get(first.key)
    assert stored is not None
    assert stored.first_seen_at == first.first_seen_at
    later = sample_fact(
        total_cost=Decimal("1.00"),
        sent=1,
        last_seen_at=datetime(2025, 8, 1, 12, 0, 0, tzinfo=UTC),
    )
    assert facts.upsert(later) == "unchanged"
    stored = facts.get(first.key)
    assert stored is not None
    assert stored.first_seen_at == first.first_seen_at
    assert stored.last_seen_at == later.last_seen_at
    restated = sample_fact(total_cost=Decimal("2.50"), sent=2)
    assert facts.upsert(restated) == "restated"
    current = facts.get(first.key)
    assert current is not None
    assert current.total_cost == Decimal("2.50")
    assert current.first_seen_at == first.first_seen_at
    history = facts.list_history()
    assert len(history) == 1
    assert history[0].fact.total_cost == Decimal("1.00")
    assert history[0].superseded_by_run_id == RUN_A


def test_same_run_duplicate_grain_keeps_first(pg_conn, pg_stores) -> None:
    ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    from dfip_core.ingest.headers import expected_source_headers
    from dfip_core.ingest.store import StagedRowRecord

    raw = dict.fromkeys(expected_source_headers())
    raw["Day"] = "2025-08-01"
    raw["Campaign ID"] = "camp-dup"
    raw["Campaign Name"] = "Dup"
    raw["Variation ID"] = "var-1"
    raw["Delivered"] = "3"
    ingest.add_staged_row(
        StagedRowRecord(
            id="aaaaaaaa-0000-4000-8000-000000000001",
            batch_id=BATCH_A,
            source_row_number=2,
            raw=raw,
            campaign_id="camp-dup",
            variation_id="var-1",
            day=date(2025, 8, 1),
        )
    )
    ingest.add_staged_row(
        StagedRowRecord(
            id="aaaaaaaa-0000-4000-8000-000000000002",
            batch_id=BATCH_A,
            source_row_number=3,
            raw=raw,
            campaign_id="camp-dup",
            variation_id="var-1",
            day=date(2025, 8, 1),
        )
    )
    result = run_transformation(ingest, facts, BATCH_A, processing_run_id=RUN_A)
    assert any(item.reason_code == "DUPLICATE_GRAIN_KEY" for item in result.rejections)
    matches = [item for item in facts.list_current() if item.campaign_id == "camp-dup"]
    assert len(matches) == 1


def test_concurrent_restatement_does_not_lose_updates(pg_conn, pg_stores) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact(sent=1, total_cost=Decimal("1.00")))
    errors: list[BaseException] = []

    def _write(sent: int) -> None:
        try:
            facts.upsert(sample_fact(sent=sent, total_cost=Decimal(sent)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=_write, args=(2,))
    second = threading.Thread(target=_write, args=(3,))
    first.start()
    second.start()
    first.join()
    second.join()
    assert errors == []
    current = facts.get(
        FactRecord(
            client_id=CLIENT_A,
            campaign_id="camp-1",
            variation_id="var-1",
            variation_id_key="var-1",
            day=date(2025, 8, 1),
        ).key
    )
    assert current is not None
    assert current.sent in {2, 3}
    assert len(facts.list_history()) >= 1


def test_empty_variation_key_is_preserved(pg_conn, pg_stores) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    record = sample_fact(variation_id=None, variation_id_key="", campaign_id="camp-blank")
    assert facts.upsert(record) == "inserted"
    stored = facts.get(record.key)
    assert stored is not None
    assert stored.variation_id is None
    assert stored.variation_id_key == ""


def test_upsert_many_uses_one_transaction_per_chunk(pg_conn, pg_stores) -> None:
    from contextlib import contextmanager

    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    original = facts._tx
    calls = {"n": 0}

    @contextmanager
    def counting_tx():
        calls["n"] += 1
        with original() as conn:
            yield conn

    facts._tx = counting_tx
    rows = [sample_fact(campaign_id=f"bulk-{index}", sent=index) for index in range(8)]
    actions = facts.upsert_many(rows)
    assert actions == ["inserted"] * 8
    assert calls["n"] == 1
    facts._tx = original
    stored = [item for item in facts.list_current() if str(item.campaign_id).startswith("bulk-")]
    assert len(stored) == 8
    assert len({item.key for item in stored}) == 8


def test_upsert_many_conflict_restates_without_duplicates(pg_conn, pg_stores) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    first = [sample_fact(campaign_id=f"copy-{index}", sent=1, total_cost=Decimal("1.00")) for index in range(6)]
    assert facts.upsert_many(first) == ["inserted"] * 6
    restated = [
        sample_fact(campaign_id=f"copy-{index}", sent=2, total_cost=Decimal("2.00"))
        for index in range(6)
    ]
    assert facts.upsert_many(restated) == ["restated"] * 6
    current = [item for item in facts.list_current() if str(item.campaign_id).startswith("copy-")]
    assert len(current) == 6
    assert len({item.key for item in current}) == 6
    assert all(item.sent == 2 for item in current)
    assert len(facts.list_history()) == 6


def test_upsert_many_mixed_insert_and_restate(pg_conn, pg_stores) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    existing = [
        sample_fact(campaign_id=f"mix-{index}", sent=1, total_cost=Decimal("1.00"))
        for index in range(3)
    ]
    assert facts.upsert_many(existing) == ["inserted"] * 3
    mixed = [
        sample_fact(campaign_id=f"mix-{index}", sent=2, total_cost=Decimal("2.00"))
        for index in range(3)
    ] + [
        sample_fact(campaign_id=f"mix-new-{index}", sent=3, total_cost=Decimal("3.00"))
        for index in range(3)
    ]
    assert facts.upsert_many(mixed) == ["restated"] * 3 + ["inserted"] * 3
    current = [item for item in facts.list_current() if str(item.campaign_id).startswith("mix-")]
    assert len(current) == 6
    assert len({item.key for item in current}) == 6
    assert len(facts.list_history()) == 3
