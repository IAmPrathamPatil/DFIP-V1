"""In-memory P1 staging model. Does not connect to PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _new_id() -> str:
    return str(uuid4())


@dataclass
class SourceFileRecord:
    id: str
    client_id: str
    sha256: str
    original_filename: str
    byte_size: int
    source_kind: str
    uploaded_at: datetime
    storage_uri: str | None = None


@dataclass
class BatchRecord:
    id: str
    source_file_id: str
    client_id: str
    status: str
    row_count_declared: int | None
    row_count_staged: int
    row_count_rejected: int
    observed_day_min: date | None
    observed_day_max: date | None
    created_at: datetime
    completed_at: datetime | None
    error_summary: str | None
    worksheet_name: str | None
    header_row: int | None
    source_start_column: str | None
    empty_row_count: int


@dataclass
class StagedRowRecord:
    id: str
    batch_id: str
    source_row_number: int
    raw: dict[str, Any]
    campaign_id: str | None
    variation_id: str | None
    day: date | None


@dataclass
class RejectedRowRecord:
    id: str
    batch_id: str
    source_row_number: int | None
    raw: dict[str, Any] | None
    reason_code: str
    reason_detail: str | None
    rejected_at: datetime


@dataclass
class ProcessingRunRecord:
    id: str
    batch_id: str
    campaign_label_version_id: str | None
    template_label_version_id: str | None
    rate_card_version_id: str | None
    label_group_version_id: str | None
    engine_version: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    qa_verdict: str | None
    client_id: str | None = None
    error_summary: str | None = None
    progress_at: datetime | None = None


SUCCESS_BATCH_STATUSES = frozenset({"staged", "validated", "processed"})


@dataclass
class InMemoryIngestStore:
    """Process-local stand-in for source_file / batch / stg_* / processing_run."""

    source_files: dict[str, SourceFileRecord] = field(default_factory=dict)
    source_files_by_id: dict[str, SourceFileRecord] = field(default_factory=dict)
    batches: dict[str, BatchRecord] = field(default_factory=dict)
    staged_rows: list[StagedRowRecord] = field(default_factory=list)
    rejected_rows: list[RejectedRowRecord] = field(default_factory=list)
    processing_runs: dict[str, ProcessingRunRecord] = field(default_factory=dict)

    def get_source_file_by_sha256(
        self, sha256: str, client_id: str | None = None
    ) -> SourceFileRecord | None:
        del client_id
        return self.source_files.get(sha256)

    def get_source_file(self, source_file_id: str) -> SourceFileRecord | None:
        return self.source_files_by_id.get(source_file_id)

    def register_source_file(
        self,
        *,
        client_id: str,
        sha256: str,
        original_filename: str,
        byte_size: int,
        source_kind: str,
    ) -> SourceFileRecord:
        existing = self.source_files.get(sha256)
        if existing is not None:
            return existing
        record = SourceFileRecord(
            id=_new_id(),
            client_id=client_id,
            sha256=sha256,
            original_filename=original_filename,
            byte_size=byte_size,
            source_kind=source_kind,
            uploaded_at=_now(),
        )
        self.source_files[sha256] = record
        self.source_files_by_id[record.id] = record
        return record

    def set_storage_uri(self, source_file_id: str, storage_uri: str) -> SourceFileRecord:
        record = self.source_files_by_id.get(source_file_id)
        if record is None:
            raise KeyError(source_file_id)
        record.storage_uri = storage_uri
        self.source_files[record.sha256] = record
        return record

    def successful_batch_for_file(self, source_file_id: str) -> BatchRecord | None:
        matches = [
            batch
            for batch in self.batches.values()
            if batch.source_file_id == source_file_id and batch.status in SUCCESS_BATCH_STATUSES
        ]
        if not matches:
            return None
        return min(matches, key=lambda batch: batch.created_at)

    def create_batch(
        self,
        *,
        source_file_id: str,
        client_id: str,
        worksheet_name: str | None = None,
        header_row: int | None = None,
        source_start_column: str | None = None,
    ) -> BatchRecord:
        record = BatchRecord(
            id=_new_id(),
            source_file_id=source_file_id,
            client_id=client_id,
            status="received",
            row_count_declared=None,
            row_count_staged=0,
            row_count_rejected=0,
            observed_day_min=None,
            observed_day_max=None,
            created_at=_now(),
            completed_at=None,
            error_summary=None,
            worksheet_name=worksheet_name,
            header_row=header_row,
            source_start_column=source_start_column,
            empty_row_count=0,
        )
        self.batches[record.id] = record
        return record

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        return self.batches.get(batch_id)

    def save_batch(self, record: BatchRecord) -> None:
        self.batches[record.id] = record

    def add_staged_row(self, record: StagedRowRecord) -> None:
        self.staged_rows.append(record)

    def add_rejected_row(
        self,
        *,
        batch_id: str,
        source_row_number: int | None,
        raw: dict[str, Any] | None,
        reason_code: str,
        reason_detail: str | None,
    ) -> RejectedRowRecord:
        record = RejectedRowRecord(
            id=_new_id(),
            batch_id=batch_id,
            source_row_number=source_row_number,
            raw=raw,
            reason_code=reason_code,
            reason_detail=reason_detail,
            rejected_at=_now(),
        )
        self.rejected_rows.append(record)
        return record

    def staged_for_batch(self, batch_id: str) -> list[StagedRowRecord]:
        return sorted(
            (row for row in self.staged_rows if row.batch_id == batch_id),
            key=lambda row: (row.source_row_number, row.id),
        )

    def iter_staged_for_batch(self, batch_id: str) -> Iterator[StagedRowRecord]:
        """Yield this batch in source_row_number, id order (Postgres keyset order)."""
        yield from self.staged_for_batch(batch_id)

    def rejected_for_batch(self, batch_id: str) -> list[RejectedRowRecord]:
        return [row for row in self.rejected_rows if row.batch_id == batch_id]

    def processing_run_for_batch(self, batch_id: str) -> ProcessingRunRecord | None:
        matches = [run for run in self.processing_runs.values() if run.batch_id == batch_id]
        if not matches:
            return None
        return min(matches, key=lambda run: run.started_at)

    def get_processing_run(self, processing_run_id: str) -> ProcessingRunRecord | None:
        return self.processing_runs.get(processing_run_id)

    def active_processing_run_for_batch(self, batch_id: str) -> ProcessingRunRecord | None:
        matches = [
            run
            for run in self.processing_runs.values()
            if run.batch_id == batch_id and run.status in {"pending", "running"}
        ]
        if not matches:
            return None
        return max(matches, key=lambda run: run.started_at)

    def save_processing_run(self, record: ProcessingRunRecord) -> None:
        self.processing_runs[record.id] = record

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
        batch = self.batches.get(batch_id)
        record = ProcessingRunRecord(
            id=_new_id(),
            batch_id=batch_id,
            campaign_label_version_id=campaign_label_version_id,
            template_label_version_id=template_label_version_id,
            rate_card_version_id=rate_card_version_id,
            label_group_version_id=label_group_version_id,
            engine_version=engine_version,
            started_at=_now(),
            finished_at=None,
            status="pending",
            qa_verdict=None,
            client_id=batch.client_id if batch is not None else None,
            error_summary=None,
            progress_at=None,
        )
        self.processing_runs[record.id] = record
        return record
