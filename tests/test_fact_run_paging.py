"""Bounded keyset paging for processing-run fact reads used by QA."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from dfip_db.fact_store import (
    FACT_QA_LOAD_PAGE_SIZE,
    FACT_RUN_PAGE_SIZE,
    PostgresFactStore,
    _FACT_RUN_FIRST_PAGE_SQL,
    _FACT_RUN_NEXT_PAGE_SQL,
)
from dfip_db.mapping import FACT_COLUMNS, fact_from_row

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


def test_qa_load_keeps_keyset_pages() -> None:
    assert FACT_QA_LOAD_PAGE_SIZE == 5000
    assert "LIMIT %s" in _FACT_RUN_FIRST_PAGE_SQL
    assert "processing_run_id = %s" in _FACT_RUN_FIRST_PAGE_SQL


def test_for_run_pages_complete_dataset_without_unbounded_select() -> None:
    rows = [_row(index) for index in range(3)]
    store = PostgresFactStore.__new__(PostgresFactStore)
    store.count_for_run = lambda processing_run_id: 3  # type: ignore[method-assign]
    store._load_run_facts = lambda processing_run_id: [  # type: ignore[method-assign]
        fact_from_row(row) for row in rows
    ]
    facts = store.for_run(RUN)
    assert len(facts) == 3
    assert facts[0].campaign_id == "camp-00000"
    assert facts[-1].campaign_id == "camp-00002"


def test_for_run_rejects_partial_load() -> None:
    store = PostgresFactStore.__new__(PostgresFactStore)
    store.count_for_run = lambda processing_run_id: 10  # type: ignore[method-assign]
    store._load_run_facts = lambda processing_run_id: []  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="QA fact load incomplete"):
        store.for_run(RUN)


def test_identifiers_are_uuids() -> None:
    UUID(CLIENT)
    UUID(RUN)
    UUID(BATCH)
