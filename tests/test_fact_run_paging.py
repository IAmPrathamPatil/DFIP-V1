"""Bounded keyset paging for processing-run fact reads used by QA."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from dfip_db.fact_store import (
    FACT_RUN_PAGE_SIZE,
    PostgresFactStore,
    _FACT_RUN_FIRST_PAGE_SQL,
    _FACT_RUN_NEXT_PAGE_SQL,
)
from dfip_db.mapping import FACT_COLUMNS

CLIENT = "a0000000-0000-4000-8000-000000000001"
RUN = "d0000000-0000-4000-8000-000000000001"
BATCH = "c0000000-0000-4000-8000-000000000001"
NOW = datetime(2025, 8, 1, tzinfo=UTC)


def _row(index: int) -> dict[str, object]:
    payload = {name: None for name in FACT_COLUMNS}
    payload.update(
        {
            "client_id": CLIENT,
            "campaign_id": f"camp-{index:05d}",
            "variation_id": "var-1",
            "variation_id_key": "var-1",
            "day": date(2025, 8, 1),
            "sent": 10,
            "failed": 1,
            "delivered": 8,
            "processing_run_id": RUN,
            "batch_id": BATCH,
            "template_status": "",
            "first_seen_at": NOW,
            "last_seen_at": NOW,
        }
    )
    return payload


def test_run_page_sql_is_keyset_limited() -> None:
    assert FACT_RUN_PAGE_SIZE == 1000
    assert "LIMIT %s" in _FACT_RUN_FIRST_PAGE_SQL
    assert "LIMIT %s" in _FACT_RUN_NEXT_PAGE_SQL
    assert "processing_run_id = %s" in _FACT_RUN_FIRST_PAGE_SQL
    assert "(client_id, campaign_id, variation_id_key, day) >" in " ".join(
        _FACT_RUN_NEXT_PAGE_SQL.split()
    )


def test_for_run_pages_complete_dataset_without_unbounded_select() -> None:
    first = [_row(index) for index in range(FACT_RUN_PAGE_SIZE)]
    second = [_row(index + FACT_RUN_PAGE_SIZE) for index in range(286)]
    store = PostgresFactStore.__new__(PostgresFactStore)
    calls: list[tuple[object, int]] = []

    def fetch(processing_run_id: str, *, after, page_size: int):
        calls.append((after, page_size))
        if after is None:
            return first
        return second

    store._fetch_run_page = fetch  # type: ignore[method-assign]
    store.count_for_run = lambda processing_run_id: 1286  # type: ignore[method-assign]
    facts = store.for_run(RUN)
    assert len(facts) == 1286
    assert facts[0].campaign_id == "camp-00000"
    assert facts[-1].campaign_id == "camp-01285"
    assert calls[0] == (None, FACT_RUN_PAGE_SIZE)
    assert calls[1][1] == FACT_RUN_PAGE_SIZE
    assert calls[1][0] is not None


def test_for_run_rejects_partial_load() -> None:
    store = PostgresFactStore.__new__(PostgresFactStore)
    store.count_for_run = lambda processing_run_id: 10  # type: ignore[method-assign]
    store.iter_for_run = lambda processing_run_id, page_size=None: iter([])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="QA fact load incomplete"):
        store.for_run(RUN)


def test_identifiers_are_uuids() -> None:
    UUID(CLIENT)
    UUID(RUN)
    UUID(BATCH)
