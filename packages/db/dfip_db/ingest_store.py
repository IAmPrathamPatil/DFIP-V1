"""PostgreSQL IngestStore. P3 pipeline semantics are unchanged."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from dfip_core.ingest.store import (
    SUCCESS_BATCH_STATUSES,
    BatchRecord,
    ProcessingRunRecord,
    RejectedRowRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    batch_from_row,
    processing_run_from_row,
    rejected_from_row,
    source_file_from_row,
    staged_from_row,
)
from dfip_db.rls import current_rls

# Keyset pages over stg_source_row. UNIQUE (batch_id, source_row_number) makes
# source_row_number a complete seek key; ORDER BY also includes id so the
# stream stays stable. Each page is its own short transaction so the transform
# consumer can persist facts without holding a cursor or pool connection.
# 1000 JSONB rows stays well under statement_timeout (~51 round-trips for a
# 50,286-row August batch) without materialising the full working set.
STAGED_ROW_PAGE_SIZE = 1000
STAGED_ROW_PAGE_STATEMENT_TIMEOUT = "60s"
_STAGED_TIMEOUT_SQL = f"SET LOCAL statement_timeout = '{STAGED_ROW_PAGE_STATEMENT_TIMEOUT}'"
_STAGED_FIRST_PAGE_SQL = """
SELECT * FROM stg_source_row
WHERE batch_id = %s
ORDER BY source_row_number ASC, id ASC
LIMIT %s
"""
_STAGED_NEXT_PAGE_SQL = """
SELECT * FROM stg_source_row
WHERE batch_id = %s
  AND source_row_number > %s
ORDER BY source_row_number ASC, id ASC
LIMIT %s
"""


class PostgresIngestStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _tx(self):
        return transaction(self._pool, current_rls())

    def get_source_file_by_sha256(
        self, sha256: str, client_id: str | None = None
    ) -> SourceFileRecord | None:
        with self._tx() as conn:
            if client_id is None:
                row = conn.execute(
                    "SELECT * FROM source_file"
                    " WHERE rtrim(sha256) = %s ORDER BY uploaded_at, id LIMIT 1",
                    (sha256,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM source_file WHERE client_id = %s AND rtrim(sha256) = %s",
                    (client_id, sha256),
                ).fetchone()
        return source_file_from_row(row) if row else None

    def get_source_file(self, source_file_id: str) -> SourceFileRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM source_file WHERE id = %s", (source_file_id,)
            ).fetchone()
        return source_file_from_row(row) if row else None

    def register_source_file(
        self,
        *,
        client_id: str,
        sha256: str,
        original_filename: str,
        byte_size: int,
        source_kind: str,
    ) -> SourceFileRecord:
        new_id = str(uuid4())
        uploaded_at = datetime.now(tz=UTC)
        with self._tx() as conn:
            row = conn.execute(
                """
                INSERT INTO source_file (
                    id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id, sha256) DO NOTHING
                RETURNING *
                """,
                (new_id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM source_file WHERE client_id = %s AND rtrim(sha256) = %s",
                    (client_id, sha256),
                ).fetchone()
        if row is None:
            raise RuntimeError("source_file register failed")
        return source_file_from_row(row)

    def set_storage_uri(self, source_file_id: str, storage_uri: str) -> SourceFileRecord:
        with self._tx() as conn:
            row = conn.execute(
                """
                UPDATE source_file
                SET storage_uri = %s
                WHERE id = %s
                RETURNING *
                """,
                (storage_uri, source_file_id),
            ).fetchone()
        if row is None:
            raise KeyError(source_file_id)
        return source_file_from_row(row)

    def successful_batch_for_file(self, source_file_id: str) -> BatchRecord | None:
        statuses = tuple(SUCCESS_BATCH_STATUSES)
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM batch
                WHERE source_file_id = %s AND status = ANY(%s)
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (source_file_id, list(statuses)),
            ).fetchone()
        return batch_from_row(row) if row else None

    def create_batch(
        self,
        *,
        source_file_id: str,
        client_id: str,
        worksheet_name: str | None = None,
        header_row: int | None = None,
        source_start_column: str | None = None,
    ) -> BatchRecord:
        created = datetime.now(tz=UTC)
        new_id = str(uuid4())
        with self._tx() as conn:
            row = conn.execute(
                """
                INSERT INTO batch (
                    id, source_file_id, client_id, status, row_count_declared,
                    row_count_staged, row_count_rejected, observed_day_min,
                    observed_day_max, created_at, completed_at,
                    error_summary, worksheet_name, header_row, source_start_column, empty_row_count
                )
                VALUES (
                    %s, %s, %s, 'received', NULL, 0, 0, NULL, NULL, %s, NULL,
                    NULL, %s, %s, %s, 0
                )
                RETURNING *
                """,
                (
                    new_id,
                    source_file_id,
                    client_id,
                    created,
                    worksheet_name,
                    header_row,
                    source_start_column,
                ),
            ).fetchone()
        assert row is not None
        return batch_from_row(row)

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM batch WHERE id = %s", (batch_id,)).fetchone()
        return batch_from_row(row) if row else None

    def save_batch(self, record: BatchRecord) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE batch SET
                    source_file_id = %s,
                    client_id = %s,
                    status = %s,
                    row_count_declared = %s,
                    row_count_staged = %s,
                    row_count_rejected = %s,
                    observed_day_min = %s,
                    observed_day_max = %s,
                    created_at = %s,
                    completed_at = %s,
                    error_summary = %s,
                    worksheet_name = %s,
                    header_row = %s,
                    source_start_column = %s,
                    empty_row_count = %s
                WHERE id = %s
                """,
                (
                    record.source_file_id,
                    record.client_id,
                    record.status,
                    record.row_count_declared,
                    record.row_count_staged,
                    record.row_count_rejected,
                    record.observed_day_min,
                    record.observed_day_max,
                    record.created_at,
                    record.completed_at,
                    record.error_summary,
                    record.worksheet_name,
                    record.header_row,
                    record.source_start_column,
                    record.empty_row_count,
                    record.id,
                ),
            )

    def add_staged_row(self, record: StagedRowRecord) -> None:
        with self._tx() as conn:
            client_id = conn.execute(
                "SELECT client_id FROM batch WHERE id = %s", (record.batch_id,)
            ).fetchone()
            if client_id is None:
                raise KeyError(f"unknown batch {record.batch_id}")
            conn.execute(
                """
                INSERT INTO stg_source_row (
                    id, batch_id, client_id, source_row_number, raw, campaign_id, variation_id, day
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    record.batch_id,
                    client_id["client_id"],
                    record.source_row_number,
                    Jsonb(record.raw),
                    record.campaign_id,
                    record.variation_id,
                    record.day,
                ),
            )

    def add_rejected_row(
        self,
        *,
        batch_id: str,
        source_row_number: int | None,
        raw: dict[str, Any] | None,
        reason_code: str,
        reason_detail: str | None,
    ) -> RejectedRowRecord:
        new_id = str(uuid4())
        rejected_at = datetime.now(tz=UTC)
        with self._tx() as conn:
            client_row = conn.execute(
                "SELECT client_id FROM batch WHERE id = %s", (batch_id,)
            ).fetchone()
            if client_row is None:
                raise KeyError(f"unknown batch {batch_id}")
            row = conn.execute(
                """
                INSERT INTO stg_rejected_row (
                    id, batch_id, client_id, source_row_number, raw, reason_code, reason_detail,
                    rejected_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    new_id,
                    batch_id,
                    client_row["client_id"],
                    source_row_number,
                    Jsonb(raw) if raw is not None else None,
                    reason_code,
                    reason_detail,
                    rejected_at,
                ),
            ).fetchone()
        assert row is not None
        return rejected_from_row(row)

    def staged_for_batch(self, batch_id: str) -> list[StagedRowRecord]:
        return list(self.iter_staged_for_batch(batch_id))

    def iter_staged_for_batch(
        self, batch_id: str, *, page_size: int | None = None
    ) -> Iterator[StagedRowRecord]:
        """Yield staged rows in keyset pages; never fetchall the whole batch."""
        size = STAGED_ROW_PAGE_SIZE if page_size is None else int(page_size)
        if size < 1:
            raise ValueError("page_size must be >= 1")
        after_source_row_number: int | None = None
        while True:
            page = self._fetch_staged_page(
                batch_id,
                after_source_row_number=after_source_row_number,
                page_size=size,
            )
            if not page:
                return
            page_len = len(page)
            last_number = int(page[-1]["source_row_number"])
            for row in page:
                yield staged_from_row(row)
            if page_len < size:
                return
            after_source_row_number = last_number

    def _fetch_staged_page(
        self,
        batch_id: str,
        *,
        after_source_row_number: int | None,
        page_size: int,
    ) -> list[Any]:
        with self._tx() as conn:
            conn.execute(_STAGED_TIMEOUT_SQL)
            if after_source_row_number is None:
                result = conn.execute(_STAGED_FIRST_PAGE_SQL, (batch_id, page_size))
            else:
                result = conn.execute(
                    _STAGED_NEXT_PAGE_SQL,
                    (batch_id, after_source_row_number, page_size),
                )
            return list(result.fetchall())

    def rejected_for_batch(self, batch_id: str) -> list[RejectedRowRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM stg_rejected_row
                WHERE batch_id = %s
                ORDER BY rejected_at ASC, id ASC
                """,
                (batch_id,),
            ).fetchall()
        return [rejected_from_row(row) for row in rows]

    def processing_run_for_batch(self, batch_id: str) -> ProcessingRunRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM processing_run
                WHERE batch_id = %s
                ORDER BY started_at ASC, id ASC
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
        return processing_run_from_row(row) if row else None

    def get_processing_run(self, processing_run_id: str) -> ProcessingRunRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM processing_run WHERE id = %s", (processing_run_id,)
            ).fetchone()
        return processing_run_from_row(row) if row else None

    def active_processing_run_for_batch(self, batch_id: str) -> ProcessingRunRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM processing_run
                WHERE batch_id = %s AND status IN ('pending', 'running')
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
        return processing_run_from_row(row) if row else None

    def save_processing_run(self, record: ProcessingRunRecord) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE processing_run SET
                    batch_id = %s,
                    campaign_label_version_id = %s,
                    template_label_version_id = %s,
                    rate_card_version_id = %s,
                    label_group_version_id = %s,
                    engine_version = %s,
                    started_at = %s,
                    finished_at = %s,
                    status = %s,
                    qa_verdict = %s,
                    error_summary = %s,
                    progress_at = %s
                WHERE id = %s
                """,
                (
                    record.batch_id,
                    record.campaign_label_version_id,
                    record.template_label_version_id,
                    record.rate_card_version_id,
                    record.label_group_version_id,
                    record.engine_version,
                    record.started_at,
                    record.finished_at,
                    record.status,
                    record.qa_verdict,
                    record.error_summary,
                    record.progress_at,
                    record.id,
                ),
            )

    def create_processing_run(
        self,
        *,
        batch_id: str,
        campaign_label_version_id: str | None,
        template_label_version_id: str | None,
        rate_card_version_id: str | None,
        label_group_version_id: str | None,
        engine_version: str | None,
    ) -> ProcessingRunRecord:
        existing = self.processing_run_for_batch(batch_id)
        if existing is not None:
            return existing
        return self.add_processing_run(
            batch_id=batch_id,
            campaign_label_version_id=campaign_label_version_id,
            template_label_version_id=template_label_version_id,
            rate_card_version_id=rate_card_version_id,
            label_group_version_id=label_group_version_id,
            engine_version=engine_version,
        )

    def add_processing_run(
        self,
        *,
        batch_id: str,
        campaign_label_version_id: str | None,
        template_label_version_id: str | None,
        rate_card_version_id: str | None,
        label_group_version_id: str | None,
        engine_version: str | None,
    ) -> ProcessingRunRecord:
        new_id = str(uuid4())
        started = datetime.now(tz=UTC)
        with self._tx() as conn:
            row = conn.execute(
                """
                INSERT INTO processing_run (
                    id, batch_id, client_id, campaign_label_version_id, template_label_version_id,
                    rate_card_version_id, label_group_version_id, engine_version, started_at,
                    finished_at, status, qa_verdict
                )
                SELECT
                    %s, b.id, b.client_id, %s, %s, %s, %s, %s, %s, NULL, 'pending', NULL
                FROM batch AS b
                WHERE b.id = %s
                RETURNING *
                """,
                (
                    new_id,
                    campaign_label_version_id,
                    template_label_version_id,
                    rate_card_version_id,
                    label_group_version_id,
                    engine_version,
                    started,
                    batch_id,
                ),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown batch {batch_id}")
        return processing_run_from_row(row)
