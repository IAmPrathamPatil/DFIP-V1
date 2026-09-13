"""PostgreSQL IngestStore. P3 pipeline semantics are unchanged."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from dfip_core.ingest.store import (
    PROCESSED_BATCH_STATUS,
    RECOVERABLE_BATCH_STATUSES,
    SUCCESS_BATCH_STATUSES,
    BatchRecord,
    ProcessingRunRecord,
    RejectedRowRecord,
    SourceFileRecord,
    StagedRowRecord,
    apply_batch_progress,
)
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    as_uuid_text,
    batch_from_row,
    processing_run_from_row,
    rejected_from_row,
    source_file_from_row,
    staged_from_row,
)
from dfip_db.rls import RlsContext, current_rls

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

    def processed_batch_for_file(self, source_file_id: str) -> BatchRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM batch
                WHERE source_file_id = %s AND status = %s
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (source_file_id, PROCESSED_BATCH_STATUS),
            ).fetchone()
        return batch_from_row(row) if row else None

    def recoverable_batch_for_file(self, source_file_id: str) -> BatchRecord | None:
        statuses = tuple(RECOVERABLE_BATCH_STATUSES)
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM batch
                WHERE source_file_id = %s AND status = ANY(%s)
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (source_file_id, list(statuses)),
            ).fetchone()
        return batch_from_row(row) if row else None

    def list_auto_resume_batches(
        self, *, limit: int = 50, resume_reasons: tuple[str, ...] = ()
    ) -> list[BatchRecord]:
        reasons = list(resume_reasons)
        cap = max(1, min(int(limit), 100))
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM batch
                WHERE cancel_requested = FALSE
                  AND status <> 'cancelled'
                  AND (
                    status = 'received'
                    OR (status = 'staged' AND row_count_staged > 0)
                    OR (
                        status = 'failed'
                        AND (
                            error_summary = ANY(%s)
                            OR EXISTS (
                                SELECT 1 FROM processing_run AS r
                                WHERE r.batch_id = batch.id
                                  AND r.error_summary = ANY(%s)
                            )
                        )
                      )
                  )
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (reasons, reasons, cap),
            ).fetchall()
        return [batch_from_row(row) for row in rows]

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

    def save_batch_progress(
        self,
        batch_id: str,
        *,
        stage: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        now = datetime.now(tz=UTC)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM batch WHERE id = %s FOR UPDATE",
                (batch_id,),
            ).fetchone()
            if row is None:
                return
            loaded = batch_from_row(row)
            if loaded.cancel_requested and stage not in {"cancelling", "cancelled"}:
                from dfip_core.ingest.progress import ProcessingCancelled

                raise ProcessingCancelled(batch_id)
            record = apply_batch_progress(
                loaded,
                stage=stage,
                current=current,
                total=total,
                message=message,
                at=now,
            )
            conn.execute(
                """
                UPDATE batch SET
                    progress_stage = %s,
                    progress_current = %s,
                    progress_total = %s,
                    progress_message = %s,
                    progress_at = %s
                WHERE id = %s
                """,
                (
                    record.progress_stage,
                    record.progress_current,
                    record.progress_total,
                    record.progress_message,
                    record.progress_at,
                    batch_id,
                ),
            )

    def add_staged_row(self, record: StagedRowRecord) -> None:
        self.add_staged_rows((record,))

    def add_staged_rows(self, records: Sequence[StagedRowRecord]) -> None:
        if not records:
            return
        batch_id = records[0].batch_id
        with self._tx() as conn:
            client_row = conn.execute(
                "SELECT client_id FROM batch WHERE id = %s", (batch_id,)
            ).fetchone()
            if client_row is None:
                raise KeyError(f"unknown batch {batch_id}")
            client_id = client_row["client_id"]
            payload = [
                (
                    record.id,
                    record.batch_id,
                    client_id,
                    record.source_row_number,
                    Jsonb(record.raw),
                    record.campaign_id,
                    record.variation_id,
                    record.day,
                )
                for record in records
            ]
            temp = f"_dfip_bulk_stg_{uuid4().hex}"
            conn.execute(
                f"""
                CREATE TEMP TABLE {temp} (
                    id uuid,
                    batch_id uuid,
                    client_id uuid,
                    source_row_number integer,
                    raw jsonb,
                    campaign_id text,
                    variation_id text,
                    day date
                ) ON COMMIT DROP
                """
            )
            with conn.cursor() as cursor:
                with cursor.copy(
                    f"""
                    COPY {temp} (
                        id, batch_id, client_id, source_row_number, raw,
                        campaign_id, variation_id, day
                    ) FROM STDIN
                    """
                ) as copy:
                    for row in payload:
                        copy.write_row(row)
            conn.execute(
                f"""
                INSERT INTO stg_source_row (
                    id, batch_id, client_id, source_row_number, raw,
                    campaign_id, variation_id, day
                )
                SELECT
                    id, batch_id, client_id, source_row_number, raw,
                    campaign_id, variation_id, day
                FROM {temp}
                """
            )
            conn.execute(f"DROP TABLE {temp}")

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
                    campaign_label_version_id = (
                        SELECT v.id FROM campaign_label_version AS v
                        WHERE v.id = %s AND v.client_id = processing_run.client_id
                    ),
                    template_label_version_id = (
                        SELECT v.id FROM template_label_version AS v
                        WHERE v.id = %s AND v.client_id = processing_run.client_id
                    ),
                    rate_card_version_id = (
                        SELECT v.id FROM rate_card_version AS v
                        WHERE v.id = %s AND v.client_id = processing_run.client_id
                    ),
                    label_group_version_id = (
                        SELECT v.id FROM label_group_version AS v
                        WHERE v.id = %s AND v.client_id = processing_run.client_id
                    ),
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
        active = self.active_processing_run_for_batch(batch_id)
        if active is not None:
            return active
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
                    %s,
                    b.id,
                    b.client_id,
                    (
                        SELECT v.id FROM campaign_label_version AS v
                        WHERE v.id = %s AND v.client_id = b.client_id
                    ),
                    (
                        SELECT v.id FROM template_label_version AS v
                        WHERE v.id = %s AND v.client_id = b.client_id
                    ),
                    (
                        SELECT v.id FROM rate_card_version AS v
                        WHERE v.id = %s AND v.client_id = b.client_id
                    ),
                    (
                        SELECT v.id FROM label_group_version AS v
                        WHERE v.id = %s AND v.client_id = b.client_id
                    ),
                    %s,
                    %s,
                    NULL,
                    'pending',
                    NULL
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

    def reset_staging_for_batch(self, batch_id: str) -> None:
        with self._tx() as conn:
            batch = conn.execute("SELECT id FROM batch WHERE id = %s", (batch_id,)).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            conn.execute("DELETE FROM stg_source_row WHERE batch_id = %s", (batch_id,))
            conn.execute("DELETE FROM stg_rejected_row WHERE batch_id = %s", (batch_id,))
            conn.execute(
                """
                UPDATE batch SET
                    status = 'received',
                    row_count_declared = NULL,
                    row_count_staged = 0,
                    row_count_rejected = 0,
                    empty_row_count = 0,
                    observed_day_min = NULL,
                    observed_day_max = NULL,
                    completed_at = NULL,
                    error_summary = NULL,
                    worksheet_name = NULL,
                    header_row = NULL,
                    source_start_column = NULL,
                    progress_stage = NULL,
                    progress_current = NULL,
                    progress_total = NULL,
                    progress_message = NULL,
                    progress_at = NULL
                WHERE id = %s
                """,
                (batch_id,),
            )

    def list_processing_runs_for_batch(self, batch_id: str) -> list[ProcessingRunRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM processing_run
                WHERE batch_id = %s
                ORDER BY started_at DESC, id DESC
                """,
                (batch_id,),
            ).fetchall()
        return [processing_run_from_row(row) for row in rows]

    def request_cancel(self, batch_id: str) -> BatchRecord | None:
        now = datetime.now(tz=UTC)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM batch WHERE id = %s FOR UPDATE",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE batch SET
                    cancel_requested = TRUE,
                    progress_stage = 'cancelling',
                    progress_message = %s,
                    progress_at = %s
                WHERE id = %s
                """,
                ("Cancelling...", now, batch_id),
            )
            updated = conn.execute(
                "SELECT * FROM batch WHERE id = %s",
                (batch_id,),
            ).fetchone()
        return batch_from_row(updated) if updated else None

    def discard_staging(self, batch_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM stg_source_row WHERE batch_id = %s", (batch_id,))
            conn.execute("DELETE FROM stg_rejected_row WHERE batch_id = %s", (batch_id,))

    def delete_batch_record(self, batch_id: str) -> str | None:
        with self._tx() as conn:
            batch = conn.execute(
                "SELECT source_file_id FROM batch WHERE id = %s FOR UPDATE",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            source_id = as_uuid_text(batch["source_file_id"])
            conn.execute("DELETE FROM stg_source_row WHERE batch_id = %s", (batch_id,))
            conn.execute("DELETE FROM stg_rejected_row WHERE batch_id = %s", (batch_id,))
            conn.execute("DELETE FROM processing_run WHERE batch_id = %s", (batch_id,))
            conn.execute("DELETE FROM batch WHERE id = %s", (batch_id,))
            leftover = conn.execute(
                "SELECT 1 FROM batch WHERE source_file_id = %s LIMIT 1",
                (source_id,),
            ).fetchone()
            if leftover is not None:
                return None
            catalog_hit = conn.execute(
                """
                SELECT 1 FROM campaign_label_version WHERE source_file_id = %s
                UNION ALL
                SELECT 1 FROM template_label_version WHERE source_file_id = %s
                LIMIT 1
                """,
                (source_id, source_id),
            ).fetchone()
            if catalog_hit is not None:
                return None
            conn.execute("DELETE FROM source_file WHERE id = %s", (source_id,))
            return source_id

    def fail_abandoned_processing_runs(self, *, reason: str) -> int:
        now = datetime.now(tz=UTC)
        with transaction(
            self._pool,
            rls=RlsContext(
                user_id="dfip-recovery-worker",
                role="admin",
                client_ids=(),
                platform_admin=True,
                subject="dfip-recovery-worker",
            ),
        ) as conn:
            rows = conn.execute(
                """
                UPDATE processing_run
                SET status = 'failed',
                    finished_at = COALESCE(finished_at, %s),
                    error_summary = %s
                WHERE status IN ('pending', 'running')
                RETURNING id
                """,
                (now, reason),
            ).fetchall()
        return len(rows)
