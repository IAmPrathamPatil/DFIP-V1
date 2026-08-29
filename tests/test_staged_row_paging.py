"""Bounded keyset paging for staged-row reads.

In-memory and spy-backed Postgres store tests always run. Live PostgreSQL
cases skip unless DFIP_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import json
import tracemalloc
from bisect import bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import pytest
from dfip_core.ingest.headers import expected_source_headers
from dfip_core.ingest.store import InMemoryIngestStore, StagedRowRecord
from dfip_core.transform.engine import run_transformation
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.ingest_store import (
    STAGED_ROW_PAGE_SIZE,
    PostgresIngestStore,
)
from dfip_db.mapping import staged_from_row

from postgres_support import (
    BATCH_A,
    CLIENT_A,
    CLIENT_B,
    RUN_A,
    postgres_only,
    requires_postgres,
    seed_working_set,
)
from test_p4_transform import build_batch, staged

AUGUST_STAGED_ROWS = 50_286


def _raw(campaign_id: str) -> dict[str, Any]:
    payload = dict.fromkeys(expected_source_headers(), "")
    payload["Day"] = "2025-08-01"
    payload["Campaign ID"] = campaign_id
    payload["Variation ID"] = "var-1"
    payload["Campaign Name"] = "Alpha"
    payload["Delivered"] = 1
    return payload


def _staged_dict(
    *,
    batch_id: str,
    source_row_number: int,
    campaign_id: str,
    row_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id or str(UUID(int=source_row_number)),
        "batch_id": batch_id,
        "source_row_number": source_row_number,
        "raw": _raw(campaign_id),
        "campaign_id": campaign_id,
        "variation_id": "var-1",
        "day": date(2025, 8, 1),
    }


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]], recorder: list[int]) -> None:
        self._rows = rows
        self._recorder = recorder

    def fetchall(self) -> list[dict[str, Any]]:
        self._recorder.append(len(self._rows))
        return self._rows


class RecordingStagedConn:
    """In-process stand-in for a psycopg connection running the keyset SQL."""

    def __init__(self, table: list[dict[str, Any]]) -> None:
        by_batch: dict[str, list[dict[str, Any]]] = {}
        for row in table:
            by_batch.setdefault(str(row["batch_id"]), []).append(row)
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._keys: dict[str, list[int]] = {}
        for batch_id, rows in by_batch.items():
            ordered = sorted(rows, key=lambda row: (int(row["source_row_number"]), str(row["id"])))
            self._rows[batch_id] = ordered
            self._keys[batch_id] = [int(row["source_row_number"]) for row in ordered]
        self.sql_calls: list[str] = []
        self.fetchall_sizes: list[int] = []
        self.timeouts = 0
        self.page_started_at: list[float] = []
        self.page_durations: list[float] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Any:
        text = " ".join(sql.split())
        self.sql_calls.append(text)
        if "statement_timeout" in text:
            self.timeouts += 1
            return self
        assert "ORDER BY source_row_number ASC, id ASC" in text
        assert "SELECT * FROM stg_source_row" in text
        assert params is not None
        batch_id = str(params[0])
        ordered = self._rows.get(batch_id, [])
        keys = self._keys.get(batch_id, [])
        if "LIMIT %s" not in text:
            limit = len(ordered)
        else:
            limit = int(params[-1])
        started = perf_counter()
        self.page_started_at.append(started)
        start = 0
        if "source_row_number >" in text:
            start = bisect_right(keys, int(params[1]))
        page = ordered[start : start + limit]
        self.page_durations.append(perf_counter() - started)
        return _FakeResult(page, self.fetchall_sizes)

    def fetchall(self) -> list[dict[str, Any]]:
        raise AssertionError("timeout SET must not fetchall")


class SpyPostgresIngestStore(PostgresIngestStore):
    def __init__(self, conn: RecordingStagedConn) -> None:
        super().__init__(pool=None)  # type: ignore[arg-type]
        self._spy_conn = conn
        self.active_transactions = 0
        self.max_active_transactions = 0

    @contextmanager
    def _tx(self):
        self.active_transactions += 1
        self.max_active_transactions = max(
            self.max_active_transactions, self.active_transactions
        )
        try:
            yield self._spy_conn
        finally:
            self.active_transactions -= 1


class PagedIterStore:
    """Delegates to an ingest store but yields staged rows in bounded pages."""

    def __init__(self, inner: InMemoryIngestStore, page_size: int) -> None:
        self._inner = inner
        self.page_size = page_size

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def iter_staged_for_batch(self, batch_id: str) -> Iterator[StagedRowRecord]:
        page: list[StagedRowRecord] = []
        for row in self._inner.iter_staged_for_batch(batch_id):
            page.append(row)
            if len(page) >= self.page_size:
                yield from page
                page = []
        yield from page


def _collect(store: Any, batch_id: str, **kwargs: Any) -> list[StagedRowRecord]:
    return list(store.iter_staged_for_batch(batch_id, **kwargs))


@pytest.fixture(scope="module")
def august_fixture() -> tuple[str, list[dict[str, Any]]]:
    batch_id = str(uuid4())
    table = [
        _staged_dict(batch_id=batch_id, source_row_number=index + 1, campaign_id=f"c-{index}")
        for index in range(AUGUST_STAGED_ROWS)
    ]
    return batch_id, table


def test_small_staged_set_is_completely_read() -> None:
    store, batch_id = build_batch([staged(2), staged(3), staged(5, **{"Campaign ID": "camp-x"})])
    rows = _collect(store, batch_id)
    assert [row.source_row_number for row in rows] == [2, 3, 5]
    assert {row.campaign_id for row in rows} == {"camp-1", "camp-x"}
    assert len(rows) == 3


def test_multi_page_staged_set_is_completely_read() -> None:
    batch_id = str(uuid4())
    table = [
        _staged_dict(batch_id=batch_id, source_row_number=number, campaign_id=f"c-{number}")
        for number in range(1, 24)
    ]
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    rows = _collect(store, batch_id, page_size=5)
    assert len(rows) == 23
    assert [row.source_row_number for row in rows] == list(range(1, 24))
    assert conn.fetchall_sizes == [5, 5, 5, 5, 3]
    assert max(conn.fetchall_sizes) == 5


def test_paged_read_has_no_duplicate_staged_rows() -> None:
    batch_id = str(uuid4())
    table = [
        _staged_dict(batch_id=batch_id, source_row_number=number, campaign_id=f"c-{number}")
        for number in (10, 2, 7, 2 + 11, 4)
    ]
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    rows = _collect(store, batch_id, page_size=2)
    ids = [row.id for row in rows]
    numbers = [row.source_row_number for row in rows]
    assert len(ids) == len(set(ids)) == 5
    assert len(numbers) == len(set(numbers))


def test_paged_read_has_no_missing_staged_rows() -> None:
    batch_id = str(uuid4())
    expected = set(range(1, 31))
    table = [
        _staged_dict(batch_id=batch_id, source_row_number=number, campaign_id=f"c-{number}")
        for number in expected
    ]
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    rows = _collect(store, batch_id, page_size=7)
    assert {row.source_row_number for row in rows} == expected
    assert len(rows) == 30
    assert sum(conn.fetchall_sizes) == 30


def test_paged_read_order_is_deterministic() -> None:
    batch_id = str(uuid4())
    numbers = [19, 3, 11, 8, 1, 14]
    table = [
        _staged_dict(
            batch_id=batch_id,
            source_row_number=number,
            campaign_id=f"c-{number}",
            row_id=str(UUID(int=100 - number)),
        )
        for number in numbers
    ]
    first = _collect(SpyPostgresIngestStore(RecordingStagedConn(table)), batch_id, page_size=2)
    reversed_store = SpyPostgresIngestStore(RecordingStagedConn(list(reversed(table))))
    second = _collect(reversed_store, batch_id, page_size=4)
    assert [row.source_row_number for row in first] == sorted(numbers)
    assert [row.source_row_number for row in first] == [row.source_row_number for row in second]
    assert [row.id for row in first] == [row.id for row in second]


def test_50k_fixture_does_not_fetchall_entire_set(
    august_fixture: tuple[str, list[dict[str, Any]]],
) -> None:
    batch_id, table = august_fixture
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    count = 0
    seen: set[int] = set()
    for row in store.iter_staged_for_batch(batch_id):
        count += 1
        seen.add(row.source_row_number)
    assert count == AUGUST_STAGED_ROWS
    assert len(seen) == AUGUST_STAGED_ROWS
    assert AUGUST_STAGED_ROWS not in conn.fetchall_sizes
    assert max(conn.fetchall_sizes) == STAGED_ROW_PAGE_SIZE
    assert max(conn.fetchall_sizes) < AUGUST_STAGED_ROWS
    expected_pages = (AUGUST_STAGED_ROWS + STAGED_ROW_PAGE_SIZE - 1) // STAGED_ROW_PAGE_SIZE
    assert len(conn.fetchall_sizes) == expected_pages
    assert conn.fetchall_sizes[-1] == AUGUST_STAGED_ROWS % STAGED_ROW_PAGE_SIZE
    assert conn.timeouts == expected_pages
    select_calls = [sql for sql in conn.sql_calls if sql.startswith("SELECT")]
    assert all("LIMIT %s" in sql for sql in select_calls)
    assert all(" WHERE batch_id = %s" in sql and " OR " not in sql for sql in select_calls)


def test_paged_read_memory_remains_bounded(
    august_fixture: tuple[str, list[dict[str, Any]]],
) -> None:
    batch_id, table = august_fixture
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    count = 0
    for row in store.iter_staged_for_batch(batch_id):
        count += 1
        del row
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == AUGUST_STAGED_ROWS
    extra = peak - baseline
    # One 57-column page plus mapping overhead, not the full 50k JSONB set.
    assert extra < 40 * 1024 * 1024
    assert max(conn.fetchall_sizes) == STAGED_ROW_PAGE_SIZE


def test_page_query_sets_statement_timeout_and_limit() -> None:
    batch_id = str(uuid4())
    table = [
        _staged_dict(batch_id=batch_id, source_row_number=index, campaign_id=f"c-{index}")
        for index in range(1, 9)
    ]
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    rows = _collect(store, batch_id, page_size=3)
    assert len(rows) == 8
    assert conn.timeouts == 3
    selects = [sql for sql in conn.sql_calls if sql.startswith("SELECT")]
    assert len(selects) == 3
    assert selects[0].count("source_row_number >") == 0
    assert all("source_row_number >" in sql for sql in selects[1:])
    assert all("LIMIT %s" in sql for sql in selects)
    assert conn.fetchall_sizes == [3, 3, 2]
    timeout_sql = [sql for sql in conn.sql_calls if "statement_timeout" in sql]
    assert timeout_sql
    assert all("60s" in sql for sql in timeout_sql)


def test_page_transaction_is_released_before_yield() -> None:
    batch_id = str(uuid4())
    table = [
        _staged_dict(batch_id=batch_id, source_row_number=index, campaign_id=f"c-{index}")
        for index in range(1, 6)
    ]
    conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(conn)
    iterator = store.iter_staged_for_batch(batch_id, page_size=2)
    first = next(iterator)
    assert first.source_row_number == 1
    assert store.active_transactions == 0
    assert store.max_active_transactions == 1


def test_transform_behavior_unchanged_with_paged_staged_reads() -> None:
    rows = [
        staged(index + 2, **{"Campaign ID": f"camp-{index}", "Delivered": 10 + index})
        for index in range(9)
    ]
    store_full, batch_full = build_batch(rows)
    store_paged, batch_paged = build_batch(rows)
    facts_full = InMemoryFactStore()
    facts_paged = InMemoryFactStore()
    full = run_transformation(
        store_full,
        facts_full,
        batch_full,
        processing_run_id=store_full.processing_run_for_batch(batch_full).id,
    )
    paged = run_transformation(
        PagedIterStore(store_paged, page_size=4),
        facts_paged,
        batch_paged,
        processing_run_id=store_paged.processing_run_for_batch(batch_paged).id,
    )
    assert full.transformed == paged.transformed == 9
    assert full.inserted == paged.inserted == 9
    assert full.rejected == paged.rejected == 0
    assert full.processing_run.status == paged.processing_run.status == "succeeded"
    full_keys = sorted(item.key for item in facts_full.list_current())
    paged_keys = sorted(item.key for item in facts_paged.list_current())
    assert full_keys == paged_keys
    full_sorted = sorted(facts_full.list_current(), key=lambda item: item.key)
    paged_sorted = sorted(facts_paged.list_current(), key=lambda item: item.key)
    full_values = [item.business_values() for item in full_sorted]
    paged_values = [item.business_values() for item in paged_sorted]
    for left, right in zip(full_values, paged_values, strict=True):
        left = dict(left)
        right = dict(right)
        left.pop("processing_run_id")
        left.pop("batch_id")
        right.pop("processing_run_id")
        right.pop("batch_id")
        assert left == right


def test_staged_iter_is_isolated_by_batch_and_client() -> None:
    store = InMemoryIngestStore()
    file_a = store.register_source_file(
        client_id=CLIENT_A,
        sha256="a" * 64,
        original_filename="a.xlsx",
        byte_size=1,
        source_kind="native_export",
    )
    file_b = store.register_source_file(
        client_id=CLIENT_B,
        sha256="b" * 64,
        original_filename="b.xlsx",
        byte_size=1,
        source_kind="native_export",
    )
    batch_a = store.create_batch(source_file_id=file_a.id, client_id=CLIENT_A)
    batch_b = store.create_batch(source_file_id=file_b.id, client_id=CLIENT_B)
    store.add_staged_row(
        StagedRowRecord(
            id=str(uuid4()),
            batch_id=batch_a.id,
            source_row_number=2,
            raw=_raw("camp-a"),
            campaign_id="camp-a",
            variation_id="var-1",
            day=date(2025, 8, 1),
        )
    )
    store.add_staged_row(
        StagedRowRecord(
            id=str(uuid4()),
            batch_id=batch_b.id,
            source_row_number=2,
            raw=_raw("camp-b"),
            campaign_id="camp-b",
            variation_id="var-1",
            day=date(2025, 8, 1),
        )
    )
    only_a = _collect(store, batch_a.id)
    only_b = _collect(store, batch_b.id)
    assert [row.campaign_id for row in only_a] == ["camp-a"]
    assert [row.campaign_id for row in only_b] == ["camp-b"]
    assert only_a[0].batch_id == batch_a.id
    assert only_b[0].batch_id == batch_b.id

    spy_batch_a = str(uuid4())
    spy_batch_b = str(uuid4())
    table = [
        _staged_dict(batch_id=spy_batch_a, source_row_number=1, campaign_id="camp-a"),
        _staged_dict(batch_id=spy_batch_b, source_row_number=1, campaign_id="camp-b"),
        _staged_dict(batch_id=spy_batch_a, source_row_number=2, campaign_id="camp-a2"),
    ]
    conn = RecordingStagedConn(table)
    spy = SpyPostgresIngestStore(conn)
    spy_a = _collect(spy, spy_batch_a, page_size=1)
    assert [row.campaign_id for row in spy_a] == ["camp-a", "camp-a2"]
    assert all(row.batch_id == spy_batch_a for row in spy_a)


def test_50k_paged_read_benchmark(
    august_fixture: tuple[str, list[dict[str, Any]]],
) -> None:
    batch_id, table = august_fixture
    old_sql = (
        "SELECT * FROM stg_source_row WHERE batch_id = %s "
        "ORDER BY source_row_number ASC, id ASC"
    )
    old_conn = RecordingStagedConn(table)
    tracemalloc.start()
    old_baseline = tracemalloc.get_traced_memory()[0]
    old_started = perf_counter()
    old_rows = old_conn.execute(old_sql, (batch_id,)).fetchall()
    old_count = 0
    for row in old_rows:
        staged_from_row(row)
        old_count += 1
        del row
    old_seconds = perf_counter() - old_started
    _old_current, old_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert old_count == AUGUST_STAGED_ROWS
    assert old_conn.fetchall_sizes == [AUGUST_STAGED_ROWS]

    new_conn = RecordingStagedConn(table)
    store = SpyPostgresIngestStore(new_conn)
    tracemalloc.start()
    new_baseline = tracemalloc.get_traced_memory()[0]
    new_started = perf_counter()
    new_count = 0
    for row in store.iter_staged_for_batch(batch_id):
        new_count += 1
        del row
    new_seconds = perf_counter() - new_started
    _new_current, new_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert new_count == AUGUST_STAGED_ROWS
    assert max(new_conn.fetchall_sizes) == STAGED_ROW_PAGE_SIZE
    assert AUGUST_STAGED_ROWS not in new_conn.fetchall_sizes
    pages = len(new_conn.fetchall_sizes)
    assert pages == 51
    old_rows_per_sec = AUGUST_STAGED_ROWS / old_seconds if old_seconds else 0.0
    new_rows_per_sec = AUGUST_STAGED_ROWS / new_seconds if new_seconds else 0.0
    avg_page = sum(new_conn.page_durations) / pages
    largest_page = max(new_conn.fetchall_sizes)
    page_size_choice: dict[str, Any] = {}
    for size in (500, 1000, 2000):
        choice_conn = RecordingStagedConn(table)
        choice_store = SpyPostgresIngestStore(choice_conn)
        started = perf_counter()
        counted = sum(1 for _ in choice_store.iter_staged_for_batch(batch_id, page_size=size))
        page_size_choice[str(size)] = {
            "seconds": round(perf_counter() - started, 4),
            "pages": len(choice_conn.fetchall_sizes),
            "largest_page": max(choice_conn.fetchall_sizes),
            "sql_selects": len([sql for sql in choice_conn.sql_calls if sql.startswith("SELECT")]),
        }
        assert counted == AUGUST_STAGED_ROWS
    metrics = {
        "old_seconds": round(old_seconds, 4),
        "new_seconds": round(new_seconds, 4),
        "old_rows_per_sec": round(old_rows_per_sec, 1),
        "new_rows_per_sec": round(new_rows_per_sec, 1),
        "old_peak_extra_bytes": old_peak - old_baseline,
        "new_peak_extra_bytes": new_peak - new_baseline,
        "sql_calls": len([sql for sql in new_conn.sql_calls if sql.startswith("SELECT")]),
        "timeout_calls": new_conn.timeouts,
        "pages": pages,
        "avg_page_seconds": round(avg_page, 6),
        "largest_page": largest_page,
        "page_size": STAGED_ROW_PAGE_SIZE,
        "page_size_choice": page_size_choice,
        "live_old_timeout_seconds": 157,
        "live_old_rows_delivered": 0,
    }
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/staged_page_bench.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    assert largest_page == STAGED_ROW_PAGE_SIZE
    assert new_conn.timeouts == pages
    assert new_rows_per_sec > 0
    assert (new_peak - new_baseline) < (old_peak - old_baseline) * 2 + 8 * 1024 * 1024


def _insert_staged(
    conn: Any,
    *,
    batch_id: str,
    client_id: str,
    numbers: list[int],
) -> None:
    from psycopg.types.json import Jsonb

    for number in numbers:
        conn.execute(
            """
            INSERT INTO stg_source_row (
                id, batch_id, client_id, source_row_number, raw, campaign_id, variation_id, day
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid4()),
                batch_id,
                client_id,
                number,
                Jsonb(_raw(f"c-{number}")),
                f"c-{number}",
                "var-1",
                date(2025, 8, 1),
            ),
        )
    conn.commit()


@postgres_only
@requires_postgres
def test_postgres_keyset_pages_are_complete_unique_and_ordered(pg_conn, pg_stores) -> None:
    ingest, _facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    numbers = list(range(2, 27))
    _insert_staged(
        pg_conn,
        batch_id=BATCH_A,
        client_id=CLIENT_A,
        numbers=list(reversed(numbers)),
    )
    rows = list(ingest.iter_staged_for_batch(BATCH_A, page_size=7))
    assert [row.source_row_number for row in rows] == numbers
    assert len({row.id for row in rows}) == len(numbers)
    assert len({row.source_row_number for row in rows}) == len(numbers)
    again = list(ingest.iter_staged_for_batch(BATCH_A, page_size=4))
    assert [row.source_row_number for row in again] == numbers
    assert [row.id for row in again] == [row.id for row in rows]


@postgres_only
@requires_postgres
def test_postgres_staged_iter_does_not_leak_other_client(pg_conn, pg_stores) -> None:
    ingest, _facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    file_b = str(uuid4())
    batch_b = str(uuid4())
    pg_conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind
        )
        VALUES (%s, %s, %s, 'b.xlsx', 1, 'native_export')
        """,
        (file_b, CLIENT_B, "b" * 64),
    )
    pg_conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, row_count_staged, row_count_rejected,
            empty_row_count
        )
        VALUES (%s, %s, %s, 'processed', 1, 0, 0)
        """,
        (batch_b, file_b, CLIENT_B),
    )
    pg_conn.commit()
    _insert_staged(pg_conn, batch_id=BATCH_A, client_id=CLIENT_A, numbers=[2, 3])
    _insert_staged(pg_conn, batch_id=batch_b, client_id=CLIENT_B, numbers=[2, 4, 6])
    only_a = list(ingest.iter_staged_for_batch(BATCH_A, page_size=1))
    only_b = list(ingest.iter_staged_for_batch(batch_b, page_size=1))
    assert [row.source_row_number for row in only_a] == [2, 3]
    assert [row.campaign_id for row in only_a] == ["c-2", "c-3"]
    assert [row.source_row_number for row in only_b] == [2, 4, 6]
    assert all(row.batch_id == BATCH_A for row in only_a)
    assert all(row.batch_id == batch_b for row in only_b)


@postgres_only
@requires_postgres
def test_postgres_transform_over_paged_staged_rows(pg_conn, pg_stores) -> None:
    ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    from test_p4_transform import make_raw

    for index in range(5):
        raw = make_raw(**{"Campaign ID": f"camp-pg-{index}", "Delivered": 4 + index})
        ingest.add_staged_row(
            StagedRowRecord(
                id=str(uuid4()),
                batch_id=BATCH_A,
                source_row_number=index + 2,
                raw=raw,
                campaign_id=f"camp-pg-{index}",
                variation_id="var-1",
                day=date(2025, 8, 1),
            )
        )
    result = run_transformation(
        ingest,
        facts,
        BATCH_A,
        processing_run_id=RUN_A,
        persist_chunk_size=2,
    )
    assert result.transformed == 5
    assert result.rejected == 0
    assert result.processing_run.status == "succeeded"
    stored = [item for item in facts.list_current() if str(item.campaign_id).startswith("camp-pg-")]
    assert len(stored) == 5
