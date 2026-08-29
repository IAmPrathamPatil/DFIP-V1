"""PostgreSQL publication / publication_current store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import (
    FACT_SCOPE_CLIENT_CURRENT,
    FACT_SCOPE_PROCESSING_RUN,
    SNAPSHOT_CHUNK_SIZE,
    SNAPSHOT_STATUS_COMPLETE,
    SNAPSHOT_STATUS_NONE,
    PublicationCurrentRecord,
    PublicationRecord,
)
from dfip_core.transform.fact import FactRecord
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    FACT_COLUMNS,
    FACT_SELECT,
    as_datetime,
    as_optional_date,
    as_uuid_text,
    fact_from_row,
    fact_values,
)
from dfip_db.rls import current_rls

_SNAPSHOT_INSERT_PREFIX = f"INSERT INTO publication_fact (publication_id, {FACT_SELECT}) VALUES "
_SNAPSHOT_VALUE_WIDTH = 1 + len(FACT_COLUMNS)


def _publication_from_row(row: dict[str, Any]) -> PublicationRecord:
    snapshot_count = row.get("snapshot_row_count")
    return PublicationRecord(
        id=as_uuid_text(row["id"]),
        client_id=as_uuid_text(row["client_id"]),
        processing_run_id=as_uuid_text(row["processing_run_id"]),
        period_start=as_optional_date(row["period_start"]),
        period_end=as_optional_date(row["period_end"]),
        published_at=as_datetime(row["published_at"]),
        published_by=row["published_by"],
        notes=row["notes"],
        fact_scope=row.get("fact_scope") or FACT_SCOPE_PROCESSING_RUN,
        snapshot_status=row.get("snapshot_status") or SNAPSHOT_STATUS_NONE,
        snapshot_row_count=None if snapshot_count is None else int(snapshot_count),
    )


def _current_from_row(row: dict[str, Any]) -> PublicationCurrentRecord:
    return PublicationCurrentRecord(
        client_id=as_uuid_text(row["client_id"]),
        publication_id=as_uuid_text(row["publication_id"]),
        updated_at=as_datetime(row["updated_at"]),
    )


class PostgresPublicationStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _tx(self):
        return transaction(self._pool, current_rls())

    def create(
        self,
        *,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        published_by: str | None,
        notes: str | None,
        fact_scope: str = FACT_SCOPE_PROCESSING_RUN,
        snapshot_facts: Sequence[FactRecord] | None = None,
    ) -> tuple[PublicationRecord, PublicationCurrentRecord]:
        now = datetime.now(tz=UTC)
        new_id = str(uuid4())
        with self._tx() as conn:
            if snapshot_facts is None:
                expected = _count_live_facts(
                    conn,
                    client_id=client_id,
                    processing_run_id=processing_run_id,
                    period_start=period_start,
                    period_end=period_end,
                    fact_scope=fact_scope,
                )
            else:
                expected = len(snapshot_facts)
            publication_row = conn.execute(
                """
                INSERT INTO publication (
                    id, client_id, processing_run_id, period_start, period_end,
                    published_at, published_by, notes, fact_scope,
                    snapshot_status, snapshot_row_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    new_id,
                    client_id,
                    processing_run_id,
                    period_start,
                    period_end,
                    now,
                    published_by,
                    notes,
                    fact_scope,
                    SNAPSHOT_STATUS_COMPLETE,
                    expected,
                ),
            ).fetchone()
            if snapshot_facts is None:
                written = _copy_live_facts(
                    conn,
                    publication_id=new_id,
                    client_id=client_id,
                    processing_run_id=processing_run_id,
                    period_start=period_start,
                    period_end=period_end,
                    fact_scope=fact_scope,
                )
            else:
                written = _insert_snapshot_chunks(conn, new_id, snapshot_facts)
            stored = conn.execute(
                "SELECT COUNT(*) AS n FROM publication_fact WHERE publication_id = %s",
                (new_id,),
            ).fetchone()
            actual = int(stored["n"] if stored else 0)
            if actual != written or actual != expected:
                raise PersistenceUnavailableError("Publication snapshot was incomplete.")
            pointer_row = conn.execute(
                """
                INSERT INTO publication_current (client_id, publication_id, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE
                SET publication_id = EXCLUDED.publication_id,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
                """,
                (client_id, new_id, now),
            ).fetchone()
        assert publication_row is not None
        assert pointer_row is not None
        return _publication_from_row(publication_row), _current_from_row(pointer_row)

    def get(self, publication_id: str) -> PublicationRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM publication WHERE id = %s",
                (publication_id,),
            ).fetchone()
        if row is None:
            return None
        return _publication_from_row(row)

    def get_current(
        self, client_id: str
    ) -> tuple[PublicationRecord | None, PublicationCurrentRecord | None]:
        with self._tx() as conn:
            pointer_row = conn.execute(
                "SELECT * FROM publication_current WHERE client_id = %s",
                (client_id,),
            ).fetchone()
            if pointer_row is None:
                return None, None
            publication_row = conn.execute(
                "SELECT * FROM publication WHERE id = %s",
                (pointer_row["publication_id"],),
            ).fetchone()
        if publication_row is None:
            return None, _current_from_row(pointer_row)
        return _publication_from_row(publication_row), _current_from_row(pointer_row)

    def list_for_client(self, client_id: str) -> list[PublicationRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM publication
                WHERE client_id = %s
                ORDER BY published_at ASC, id ASC
                """,
                (client_id,),
            ).fetchall()
        return [_publication_from_row(row) for row in rows]

    def list_snapshot(
        self,
        publication_id: str,
        *,
        client_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        with self._tx() as conn:
            total_row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM publication_fact
                WHERE publication_id = %s AND client_id = %s
                """,
                (publication_id, client_id),
            ).fetchone()
            total = int(total_row["n"] if total_row else 0)
            rows = conn.execute(
                f"""
                SELECT {FACT_SELECT} FROM publication_fact
                WHERE publication_id = %s AND client_id = %s
                ORDER BY day, campaign_id, variation_id_key, client_id
                LIMIT %s OFFSET %s
                """,
                (publication_id, client_id, limit, offset),
            ).fetchall()
        return [fact_from_row(row) for row in rows], total


def _insert_snapshot_chunks(conn, publication_id: str, facts: Sequence[FactRecord]) -> int:
    rows = list(facts)
    if not rows:
        return 0
    try:
        for offset in range(0, len(rows), SNAPSHOT_CHUNK_SIZE):
            chunk = rows[offset : offset + SNAPSHOT_CHUNK_SIZE]
            placeholders = ",".join(
                ["(" + ",".join(["%s"] * _SNAPSHOT_VALUE_WIDTH) + ")"] * len(chunk)
            )
            params: list[object] = []
            for fact in chunk:
                params.append(publication_id)
                params.extend(fact_values(fact))
            conn.execute(_SNAPSHOT_INSERT_PREFIX + placeholders, params)
    except UniqueViolation as exc:
        raise PersistenceUnavailableError(
            "Publication snapshot contains duplicate grains."
        ) from exc
    return len(rows)


def _live_fact_filter(
    *,
    client_id: str,
    processing_run_id: str,
    period_start: date | None,
    period_end: date | None,
    fact_scope: str,
) -> tuple[str, list[object]]:
    where = ["client_id = %s", "day IS NOT NULL"]
    params: list[object] = [client_id]
    if fact_scope != FACT_SCOPE_CLIENT_CURRENT:
        where.append("processing_run_id = %s")
        params.append(processing_run_id)
    if period_start is not None:
        where.append("day >= %s")
        params.append(period_start)
    if period_end is not None:
        where.append("day <= %s")
        params.append(period_end)
    return " AND ".join(where), params


def _count_live_facts(
    conn,
    *,
    client_id: str,
    processing_run_id: str,
    period_start: date | None,
    period_end: date | None,
    fact_scope: str,
) -> int:
    clause, params = _live_fact_filter(
        client_id=client_id,
        processing_run_id=processing_run_id,
        period_start=period_start,
        period_end=period_end,
        fact_scope=fact_scope,
    )
    counted = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_campaign_day WHERE {clause}",
        params,
    ).fetchone()
    return int(counted["n"] if counted else 0)


def _copy_live_facts(
    conn,
    *,
    publication_id: str,
    client_id: str,
    processing_run_id: str,
    period_start: date | None,
    period_end: date | None,
    fact_scope: str,
) -> int:
    clause, params = _live_fact_filter(
        client_id=client_id,
        processing_run_id=processing_run_id,
        period_start=period_start,
        period_end=period_end,
        fact_scope=fact_scope,
    )
    conn.execute(
        f"""
        INSERT INTO publication_fact (publication_id, {FACT_SELECT})
        SELECT %s, {FACT_SELECT}
        FROM fact_campaign_day
        WHERE {clause}
        """,
        [publication_id, *params],
    )
    counted = conn.execute(
        "SELECT COUNT(*) AS n FROM publication_fact WHERE publication_id = %s",
        (publication_id,),
    ).fetchone()
    return int(counted["n"] if counted else 0)
