"""PostgreSQL FactStore. Reproduces InMemoryFactStore upsert semantics."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime

from dfip_core.transform.fact import FactKey, FactRecord
from dfip_core.transform.store import SupersededFact
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    FACT_COLUMNS,
    FACT_SELECT,
    fact_from_row,
    fact_values,
    history_from_row,
)
from dfip_db.rls import current_rls

_FACT_UPDATE = (
    "UPDATE fact_campaign_day SET "
    + ", ".join(f"{name} = %s" for name in FACT_COLUMNS if name != "first_seen_at")
    + " WHERE client_id = %s AND campaign_id = %s AND variation_id_key = %s AND day = %s"
)
_FACT_PERSIST_STATEMENT_TIMEOUT = "120s"
FACT_RUN_PAGE_SIZE = 1000
FACT_RUN_PAGE_STATEMENT_TIMEOUT = "60s"
_FACT_RUN_TIMEOUT_SQL = f"SET LOCAL statement_timeout = '{FACT_RUN_PAGE_STATEMENT_TIMEOUT}'"
_FACT_RUN_FIRST_PAGE_SQL = f"""
SELECT {FACT_SELECT} FROM fact_campaign_day
WHERE processing_run_id = %s
ORDER BY client_id, campaign_id, variation_id_key, day
LIMIT %s
"""
_FACT_RUN_NEXT_PAGE_SQL = f"""
SELECT {FACT_SELECT} FROM fact_campaign_day
WHERE processing_run_id = %s
  AND (client_id, campaign_id, variation_id_key, day) > (%s, %s, %s, %s)
ORDER BY client_id, campaign_id, variation_id_key, day
LIMIT %s
"""
_FACT_INSERT_PREFIX = f"INSERT INTO fact_campaign_day ({FACT_SELECT}) VALUES "
_HISTORY_INSERT_PREFIX = (
    "INSERT INTO fact_campaign_day_history ("
    + FACT_SELECT
    + ", superseded_at, superseded_by_run_id) VALUES "
)


class PostgresFactStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _tx(self):
        return transaction(self._pool, current_rls())

    def get(self, key: FactKey) -> FactRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                f"""
                SELECT {FACT_SELECT} FROM fact_campaign_day
                WHERE client_id = %s AND campaign_id = %s AND variation_id_key = %s AND day = %s
                """,
                (key.client_id, key.campaign_id, key.variation_id_key, key.day),
            ).fetchone()
        return fact_from_row(row) if row else None

    def upsert(self, record: FactRecord) -> str:
        return self.upsert_many([record])[0]

    def upsert_many(self, records: Sequence[FactRecord]) -> list[str]:
        """Persist a bounded chunk in one transaction.

        Matches single-row upsert semantics: insert, restate-with-history, or
        last_seen-only. One COMMIT for the whole chunk, not one per fact.
        """
        if not records:
            return []
        try:
            return self._upsert_many_once(records)
        except UniqueViolation:
            return self._upsert_many_once(records)

    def _upsert_many_once(self, records: Sequence[FactRecord]) -> list[str]:
        keys = [record.key for record in records]
        with self._tx() as conn:
            conn.execute(f"SET LOCAL statement_timeout = '{_FACT_PERSIST_STATEMENT_TIMEOUT}'")
            previous_by_key = _load_for_update(conn, keys)
            actions: list[str] = []
            state = dict(previous_by_key)
            inserts: list[FactRecord] = []
            history_rows: list[tuple[FactRecord, str | None]] = []
            restates: list[FactRecord] = []
            touches: list[FactRecord] = []
            now = datetime.now(tz=UTC)
            for record in records:
                previous = state.get(record.key)
                if previous is None:
                    inserts.append(record)
                    state[record.key] = record
                    actions.append("inserted")
                    continue
                carried = record.carry_first_seen(previous)
                if previous.business_values() == carried.business_values():
                    touches.append(carried)
                    state[record.key] = carried
                    actions.append("unchanged")
                    continue
                history_rows.append((previous, record.processing_run_id))
                restates.append(carried)
                state[record.key] = carried
                actions.append("restated")
            _insert_many(conn, _FACT_INSERT_PREFIX, [fact_values(item) for item in inserts])
            _insert_many(
                conn,
                _HISTORY_INSERT_PREFIX,
                [(*fact_values(previous), now, run_id) for previous, run_id in history_rows],
            )
            if restates or touches:
                with conn.pipeline():
                    for carried in restates:
                        update_values = tuple(
                            getattr(carried, name)
                            for name in FACT_COLUMNS
                            if name != "first_seen_at"
                        )
                        conn.execute(
                            _FACT_UPDATE,
                            (
                                *update_values,
                                carried.client_id,
                                carried.campaign_id,
                                carried.variation_id_key,
                                carried.day,
                            ),
                        )
                    for carried in touches:
                        conn.execute(
                            """
                            UPDATE fact_campaign_day
                            SET last_seen_at = %s
                            WHERE client_id = %s AND campaign_id = %s
                              AND variation_id_key = %s AND day = %s
                            """,
                            (
                                carried.last_seen_at,
                                carried.client_id,
                                carried.campaign_id,
                                carried.variation_id_key,
                                carried.day,
                            ),
                        )
        return actions

    def for_batch(self, batch_id: str) -> list[FactRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT {FACT_SELECT} FROM fact_campaign_day WHERE batch_id = %s",
                (batch_id,),
            ).fetchall()
        return [fact_from_row(row) for row in rows]

    def count_for_run(self, processing_run_id: str) -> int:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM fact_campaign_day WHERE processing_run_id = %s",
                (processing_run_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def iter_for_run(
        self, processing_run_id: str, *, page_size: int | None = None
    ) -> Iterator[FactRecord]:
        """Yield facts for one processing_run_id in keyset pages.

        QA and inspectors must see the complete run-scoped working set, not a
        single unbounded fetchall. Each page is its own short transaction.
        """
        size = FACT_RUN_PAGE_SIZE if page_size is None else int(page_size)
        if size < 1:
            raise ValueError("page_size must be >= 1")
        after: tuple[object, object, object, object] | None = None
        while True:
            page = self._fetch_run_page(processing_run_id, after=after, page_size=size)
            if not page:
                return
            last = page[-1]
            for row in page:
                yield fact_from_row(row)
            if len(page) < size:
                return
            after = (
                last["client_id"],
                last["campaign_id"],
                last["variation_id_key"],
                last["day"],
            )

    def _fetch_run_page(
        self,
        processing_run_id: str,
        *,
        after: tuple[object, object, object, object] | None,
        page_size: int,
    ) -> list:
        with self._tx() as conn:
            conn.execute(_FACT_RUN_TIMEOUT_SQL)
            if after is None:
                result = conn.execute(_FACT_RUN_FIRST_PAGE_SQL, (processing_run_id, page_size))
            else:
                result = conn.execute(
                    _FACT_RUN_NEXT_PAGE_SQL,
                    (processing_run_id, *after, page_size),
                )
            return list(result.fetchall())

    def for_run(self, processing_run_id: str) -> list[FactRecord]:
        expected = self.count_for_run(processing_run_id)
        facts = list(self.iter_for_run(processing_run_id))
        if len(facts) != expected:
            raise RuntimeError(
                f"QA fact load incomplete for run {processing_run_id}: "
                f"loaded {len(facts)}, expected {expected}"
            )
        return facts

    def list_current(self) -> list[FactRecord]:
        with self._tx() as conn:
            rows = conn.execute(f"SELECT {FACT_SELECT} FROM fact_campaign_day").fetchall()
        return [fact_from_row(row) for row in rows]

    def list_history(self) -> list[SupersededFact]:
        with self._tx() as conn:
            rows = conn.execute(
                f"""
                SELECT {FACT_SELECT}, superseded_at, superseded_by_run_id
                FROM fact_campaign_day_history
                """
            ).fetchall()
        return [history_from_row(row) for row in rows]

    def list_published_slice(
        self,
        *,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        limit: int,
        offset: int,
        fact_scope: str = "processing_run",
        working_set: bool = False,
    ) -> tuple[list[FactRecord], int]:
        where = [
            "client_id = %s",
            "day IS NOT NULL",
        ]
        params: list[object] = [client_id]
        if fact_scope != "client_current":
            where.insert(1, "processing_run_id = %s")
            params.append(processing_run_id)
        if period_start is not None:
            where.append("day >= %s")
            params.append(period_start)
        if period_end is not None:
            where.append("day <= %s")
            params.append(period_end)
        clause = " AND ".join(where)
        # Superuser store tests have no RLS context and query the table.
        # Bound API requests run as dfip_api; readers cannot SELECT the
        # working-set table, so published reads use the view. Publish-time
        # candidate selection must use live fact_campaign_day.
        if working_set or current_rls() is None:
            table = "fact_campaign_day"
        else:
            table = "published_fact_campaign_day"
        with self._tx() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}",
                params,
            ).fetchone()
            total = int(total_row["n"] if total_row else 0)
            rows = conn.execute(
                f"""
                SELECT {FACT_SELECT} FROM {table}
                WHERE {clause}
                ORDER BY day, campaign_id, variation_id_key, client_id
                LIMIT %s OFFSET %s
                """,
                [*params, limit, offset],
            ).fetchall()
        return [fact_from_row(row) for row in rows], total


def _load_for_update(conn, keys: Sequence[FactKey]) -> dict[FactKey, FactRecord]:
    placeholders = ",".join(["(%s, %s, %s, %s)"] * len(keys))
    params: list[object] = []
    for key in keys:
        params.extend((key.client_id, key.campaign_id, key.variation_id_key, key.day))
    rows = conn.execute(
        f"""
        SELECT {FACT_SELECT} FROM fact_campaign_day
        WHERE (client_id, campaign_id, variation_id_key, day) IN ({placeholders})
        FOR UPDATE
        """,
        params,
    ).fetchall()
    loaded: dict[FactKey, FactRecord] = {}
    for row in rows:
        fact = fact_from_row(row)
        loaded[fact.key] = fact
    return loaded


def _insert_many(conn, sql_prefix: str, rows: Sequence[tuple[object, ...]]) -> None:
    if not rows:
        return
    width = len(rows[0])
    one = "(" + ",".join(["%s"] * width) + ")"
    sql = sql_prefix + ",".join(one for _ in rows)
    params: list[object] = []
    for row in rows:
        params.extend(row)
    conn.execute(sql, params)
