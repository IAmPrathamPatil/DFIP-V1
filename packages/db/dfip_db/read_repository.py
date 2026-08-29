"""SQL-backed read repository. Pagination remains limit/offset with max 200 at the API."""

from __future__ import annotations

from datetime import date
from typing import Any

from dfip_api.errors import NotFoundError
from dfip_core.ingest.store import (
    BatchRecord,
    ProcessingRunRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import SupersededFact
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    FACT_SELECT,
    batch_from_row,
    fact_from_row,
    history_from_row,
    processing_run_from_row,
    source_file_from_row,
    staged_from_row,
)
from dfip_db.rls import current_rls


class PostgresReadRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _tx(self):
        return transaction(self._pool, current_rls())

    def list_source_files(
        self,
        *,
        client_id: str | None,
        client_ids: tuple[str, ...] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SourceFileRecord], int]:
        where, params = _client_clause("client_id", client_id, client_ids)
        return self._page(
            "source_file",
            where,
            params,
            "uploaded_at ASC, id ASC",
            source_file_from_row,
            limit,
            offset,
        )

    def get_source_file(self, source_file_id: str) -> SourceFileRecord:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM source_file WHERE id = %s", (source_file_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("Source file not found.")
        return source_file_from_row(row)

    def list_batches(
        self,
        *,
        source_file_id: str | None,
        client_id: str | None,
        client_ids: tuple[str, ...] | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BatchRecord], int]:
        where, params = _client_clause("client_id", client_id, client_ids)
        if source_file_id is not None:
            where.append("source_file_id = %s")
            params.append(source_file_id)
        if status is not None:
            where.append("status = %s")
            params.append(status)
        return self._page(
            "batch", where, params, "created_at ASC, id ASC", batch_from_row, limit, offset
        )

    def get_batch(self, batch_id: str) -> BatchRecord:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM batch WHERE id = %s", (batch_id,)).fetchone()
        if row is None:
            raise NotFoundError("Batch not found.")
        return batch_from_row(row)

    def list_processing_runs(
        self,
        *,
        batch_id: str | None,
        client_ids: tuple[str, ...] | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProcessingRunRecord], int]:
        where, params = _client_clause("client_id", None, client_ids)
        if batch_id is not None:
            where.append("batch_id = %s")
            params.append(batch_id)
        if status is not None:
            where.append("status = %s")
            params.append(status)
        return self._page(
            "processing_run",
            where,
            params,
            "started_at ASC, id ASC",
            processing_run_from_row,
            limit,
            offset,
        )

    def get_processing_run(self, processing_run_id: str) -> ProcessingRunRecord:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM processing_run WHERE id = %s", (processing_run_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("Processing run not found.")
        return processing_run_from_row(row)

    def list_staged_rows(
        self,
        *,
        batch_id: str,
        source_row_number: int | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[StagedRowRecord], int]:
        self.get_batch(batch_id)
        where = ["batch_id = %s"]
        params: list[Any] = [batch_id]
        if source_row_number is not None:
            where.append("source_row_number = %s")
            params.append(source_row_number)
        if campaign_id is not None:
            where.append("campaign_id = %s")
            params.append(campaign_id)
        if variation_id is not None:
            where.append("variation_id = %s")
            params.append(variation_id)
        if day is not None:
            where.append("day = %s")
            params.append(day)
        return self._page(
            "stg_source_row",
            where,
            params,
            "source_row_number ASC, id ASC",
            staged_from_row,
            limit,
            offset,
        )

    def list_facts(
        self,
        *,
        client_id: str | None,
        client_ids: tuple[str, ...] | None,
        batch_id: str | None,
        processing_run_id: str | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        where, params = _fact_filters(
            client_id=client_id,
            client_ids=client_ids,
            batch_id=batch_id,
            processing_run_id=processing_run_id,
            campaign_id=campaign_id,
            variation_id=variation_id,
            day=day,
        )
        return self._page(
            "fact_campaign_day",
            where,
            params,
            "day ASC, campaign_id ASC, variation_id_key ASC, client_id ASC",
            fact_from_row,
            limit,
            offset,
            columns=FACT_SELECT,
        )

    def list_fact_history(
        self,
        *,
        client_id: str | None,
        client_ids: tuple[str, ...] | None,
        batch_id: str | None,
        processing_run_id: str | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SupersededFact], int]:
        where, params = _fact_filters(
            client_id=client_id,
            client_ids=client_ids,
            batch_id=batch_id,
            processing_run_id=processing_run_id,
            campaign_id=campaign_id,
            variation_id=variation_id,
            day=day,
        )
        return self._page(
            "fact_campaign_day_history",
            where,
            params,
            "superseded_at ASC, day ASC, campaign_id ASC, variation_id_key ASC, client_id ASC",
            history_from_row,
            limit,
            offset,
            columns=f"{FACT_SELECT}, superseded_at, superseded_by_run_id",
        )

    def _page(
        self,
        table: str,
        where: list[str],
        params: list[Any],
        order: str,
        mapper,
        limit: int,
        offset: int,
        *,
        columns: str = "*",
    ) -> tuple[list[Any], int]:
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._tx() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} {clause}",
                params,
            ).fetchone()
            total = int(total_row["n"] if total_row else 0)
            rows = conn.execute(
                f"""
                SELECT {columns} FROM {table}
                {clause}
                ORDER BY {order}
                LIMIT %s OFFSET %s
                """,
                [*params, limit, offset],
            ).fetchall()
        return [mapper(row) for row in rows], total


def _client_clause(
    column: str, client_id: str | None, client_ids: tuple[str, ...] | None
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if client_ids is not None:
        if not client_ids:
            where.append("FALSE")
            return where, params
        where.append(f"{column} = ANY(%s)")
        params.append(list(client_ids))
    if client_id is not None:
        where.append(f"{column} = %s")
        params.append(client_id)
    return where, params


def _fact_filters(
    *,
    client_id: str | None,
    client_ids: tuple[str, ...] | None,
    batch_id: str | None,
    processing_run_id: str | None,
    campaign_id: str | None,
    variation_id: str | None,
    day: date | None,
) -> tuple[list[str], list[Any]]:
    where, params = _client_clause("client_id", client_id, client_ids)
    if batch_id is not None:
        where.append("batch_id = %s")
        params.append(batch_id)
    if processing_run_id is not None:
        where.append("processing_run_id = %s")
        params.append(processing_run_id)
    if campaign_id is not None:
        where.append("campaign_id = %s")
        params.append(campaign_id)
    if variation_id is not None:
        where.append("variation_id = %s")
        params.append(variation_id)
    if day is not None:
        where.append("day = %s")
        params.append(day)
    return where, params
