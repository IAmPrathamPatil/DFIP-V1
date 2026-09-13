"""PostgreSQL FactStore. Reproduces InMemoryFactStore upsert semantics."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from uuid import uuid4

from dfip_core.transform.fact import FactKey, FactRecord
from dfip_core.transform.store import SupersededFact
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    FACT_COLUMNS,
    FACT_SELECT,
    as_date,
    as_uuid_text,
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
_HISTORY_COLUMNS = FACT_SELECT + ", superseded_at, superseded_by_run_id"
# QA loads the complete run working set. Pages stay keyset-bounded; they run
# inside one RLS transaction so 5000-row fetches do not re-bind identity.
FACT_QA_LOAD_PAGE_SIZE = 5000
FACT_QA_LOAD_STATEMENT_TIMEOUT = "180s"


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

        PostgreSQL rejects COPY FROM on RLS tables, so new rows COPY into a
        TEMP table (no RLS) and INSERT ... SELECT into fact_campaign_day.
        ON CONFLICT DO NOTHING skips existing grains; those rows then follow
        the SELECT FOR UPDATE restatement path.
        """
        if not records:
            return []
        if len({record.key for record in records}) != len(records):
            return self._upsert_lookup_merge(records)
        return self._upsert_insert_first(records)

    def _upsert_insert_first(self, records: Sequence[FactRecord]) -> list[str]:
        with self._tx() as conn:
            conn.execute(f"SET LOCAL statement_timeout = '{_FACT_PERSIST_STATEMENT_TIMEOUT}'")
            inserted = _copy_then_insert(
                conn,
                dest_table="fact_campaign_day",
                columns=FACT_SELECT,
                rows=[fact_values(item) for item in records],
                on_conflict=(
                    "ON CONFLICT (client_id, campaign_id, variation_id_key, day) DO NOTHING"
                ),
                returning="client_id, campaign_id, variation_id_key, day",
            )
            inserted_keys = {_fact_key_from_row(row) for row in inserted}
            if len(inserted_keys) == len(records):
                return ["inserted"] * len(records)
            conflicts = [record for record in records if record.key not in inserted_keys]
            conflict_actions = _merge_existing(conn, conflicts)
            conflict_by_key = {
                record.key: action
                for record, action in zip(conflicts, conflict_actions, strict=True)
            }
            return [
                "inserted" if record.key in inserted_keys else conflict_by_key[record.key]
                for record in records
            ]

    def _upsert_lookup_merge(self, records: Sequence[FactRecord]) -> list[str]:
        """Sequential insert/restate/touch for chunks that repeat a grain."""
        with self._tx() as conn:
            conn.execute(f"SET LOCAL statement_timeout = '{_FACT_PERSIST_STATEMENT_TIMEOUT}'")
            return _merge_existing(conn, records)

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
            return _fetch_run_page_on(conn, processing_run_id, after=after, page_size=page_size)

    def for_run(self, processing_run_id: str) -> list[FactRecord]:
        expected = self.count_for_run(processing_run_id)
        facts = self._load_run_facts(processing_run_id)
        if len(facts) != expected:
            raise RuntimeError(
                f"QA fact load incomplete for run {processing_run_id}: "
                f"loaded {len(facts)}, expected {expected}"
            )
        return facts

    def revert_run(self, processing_run_id: str) -> None:
        """Restore restated grains, then drop facts still owned by this run."""
        pk = ("client_id", "campaign_id", "variation_id_key", "day")
        assignments = ", ".join(
            f"{name} = latest.{name}" for name in FACT_COLUMNS if name not in pk
        )
        with self._tx() as conn:
            conn.execute(
                f"""
                WITH latest AS (
                    SELECT DISTINCT ON (client_id, campaign_id, variation_id_key, day)
                           {FACT_SELECT}
                    FROM fact_campaign_day_history
                    WHERE superseded_by_run_id = %s
                    ORDER BY client_id, campaign_id, variation_id_key, day,
                             superseded_at DESC
                )
                UPDATE fact_campaign_day AS fact
                SET {assignments}
                FROM latest
                WHERE fact.client_id = latest.client_id
                  AND fact.campaign_id = latest.campaign_id
                  AND fact.variation_id_key = latest.variation_id_key
                  AND fact.day = latest.day
                """,
                (processing_run_id,),
            )
            conn.execute(
                "DELETE FROM fact_campaign_day_history WHERE superseded_by_run_id = %s",
                (processing_run_id,),
            )
            conn.execute(
                "DELETE FROM fact_campaign_day WHERE processing_run_id = %s",
                (processing_run_id,),
            )

    def _load_run_facts(self, processing_run_id: str) -> list[FactRecord]:
        """Load the run-scoped working set in one RLS transaction.

        evaluate_qa still receives the complete FactRecord list. Keyset pages
        stay bounded; sharing one transaction avoids re-applying RLS per page.
        """
        facts: list[FactRecord] = []
        after: tuple[object, object, object, object] | None = None
        with self._tx() as conn:
            conn.execute(f"SET LOCAL statement_timeout = '{FACT_QA_LOAD_STATEMENT_TIMEOUT}'")
            while True:
                page = _fetch_run_page_on(
                    conn,
                    processing_run_id,
                    after=after,
                    page_size=FACT_QA_LOAD_PAGE_SIZE,
                )
                if not page:
                    break
                facts.extend(fact_from_row(row) for row in page)
                if len(page) < FACT_QA_LOAD_PAGE_SIZE:
                    break
                last = page[-1]
                after = (
                    last["client_id"],
                    last["campaign_id"],
                    last["variation_id_key"],
                    last["day"],
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


def _fetch_run_page_on(
    conn,
    processing_run_id: str,
    *,
    after: tuple[object, object, object, object] | None,
    page_size: int,
) -> list:
    if after is None:
        result = conn.execute(_FACT_RUN_FIRST_PAGE_SQL, (processing_run_id, page_size))
    else:
        result = conn.execute(
            _FACT_RUN_NEXT_PAGE_SQL,
            (processing_run_id, *after, page_size),
        )
    return list(result.fetchall())


def _load_for_update(conn, keys: Sequence[FactKey]) -> dict[FactKey, FactRecord]:
    if not keys:
        return {}
    unique_keys = list(dict.fromkeys(keys))
    placeholders = ",".join(["(%s, %s, %s, %s)"] * len(unique_keys))
    params: list[object] = []
    for key in unique_keys:
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


def _fact_key_from_row(row: dict) -> FactKey:
    return FactKey(
        as_uuid_text(row["client_id"]),
        str(row["campaign_id"]),
        str(row["variation_id_key"]),
        as_date(row["day"]),
    )


def _merge_existing(conn, records: Sequence[FactRecord]) -> list[str]:
    """Apply insert / restate / last_seen-only in record order."""
    if not records:
        return []
    previous_by_key = _load_for_update(conn, [record.key for record in records])
    actions: list[str] = []
    state = dict(previous_by_key)
    leftover_inserts: list[FactRecord] = []
    history_rows: list[tuple[FactRecord, str | None]] = []
    restates: list[FactRecord] = []
    touches: list[FactRecord] = []
    now = datetime.now(tz=UTC)
    for record in records:
        previous = state.get(record.key)
        if previous is None:
            leftover_inserts.append(record)
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
    if leftover_inserts:
        _copy_then_insert(
            conn,
            dest_table="fact_campaign_day",
            columns=FACT_SELECT,
            rows=[fact_values(item) for item in leftover_inserts],
        )
    if history_rows:
        _copy_then_insert(
            conn,
            dest_table="fact_campaign_day_history",
            columns=_HISTORY_COLUMNS,
            rows=[(*fact_values(previous), now, run_id) for previous, run_id in history_rows],
        )
    if restates or touches:
        with conn.pipeline():
            for carried in restates:
                update_values = tuple(
                    getattr(carried, name) for name in FACT_COLUMNS if name != "first_seen_at"
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


def _copy_then_insert(
    conn,
    *,
    dest_table: str,
    columns: str,
    rows: Sequence[tuple[object, ...]],
    on_conflict: str = "",
    returning: str = "",
) -> list:
    """COPY into a TEMP table, then INSERT ... SELECT into the RLS table.

    PostgreSQL FeatureNotSupported: COPY FROM is not allowed on RLS tables.
    TEMP tables are session-local and are not RLS-protected, so COPY is legal
    there. The INSERT still applies inspector WITH CHECK policies.
    """
    if not rows:
        return []
    temp = f"_dfip_bulk_{uuid4().hex}"
    conn.execute(f"CREATE TEMP TABLE {temp} (LIKE {dest_table} INCLUDING DEFAULTS) ON COMMIT DROP")
    with conn.cursor() as cursor:
        with cursor.copy(f"COPY {temp} ({columns}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
    sql = f"INSERT INTO {dest_table} ({columns}) SELECT {columns} FROM {temp}"
    if on_conflict:
        sql += f" {on_conflict}"
    if returning:
        sql += f" RETURNING {returning}"
    result = conn.execute(sql)
    fetched = list(result.fetchall()) if returning else []
    conn.execute(f"DROP TABLE {temp}")
    return fetched
