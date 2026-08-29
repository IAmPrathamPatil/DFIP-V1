"""Store protocol for P3 ingest persistence.

In-memory and PostgreSQL adapters share this surface. P3 pipeline semantics
are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from dfip_core.ingest.store import (
    BatchRecord,
    ProcessingRunRecord,
    RejectedRowRecord,
    SourceFileRecord,
    StagedRowRecord,
)


class IngestStore(Protocol):
    def get_source_file_by_sha256(
        self, sha256: str, client_id: str | None = None
    ) -> SourceFileRecord | None: ...

    def get_source_file(self, source_file_id: str) -> SourceFileRecord | None: ...

    def register_source_file(
        self,
        *,
        client_id: str,
        sha256: str,
        original_filename: str,
        byte_size: int,
        source_kind: str,
    ) -> SourceFileRecord: ...

    def set_storage_uri(self, source_file_id: str, storage_uri: str) -> SourceFileRecord: ...

    def successful_batch_for_file(self, source_file_id: str) -> BatchRecord | None: ...

    def create_batch(
        self,
        *,
        source_file_id: str,
        client_id: str,
        worksheet_name: str | None = None,
        header_row: int | None = None,
        source_start_column: str | None = None,
    ) -> BatchRecord: ...

    def get_batch(self, batch_id: str) -> BatchRecord | None: ...

    def save_batch(self, record: BatchRecord) -> None: ...

    def add_staged_row(self, record: StagedRowRecord) -> None: ...

    def add_rejected_row(
        self,
        *,
        batch_id: str,
        source_row_number: int | None,
        raw: dict[str, Any] | None,
        reason_code: str,
        reason_detail: str | None,
    ) -> RejectedRowRecord: ...

    def staged_for_batch(self, batch_id: str) -> list[StagedRowRecord]: ...

    def iter_staged_for_batch(self, batch_id: str) -> Iterator[StagedRowRecord]: ...

    def rejected_for_batch(self, batch_id: str) -> list[RejectedRowRecord]: ...

    def processing_run_for_batch(self, batch_id: str) -> ProcessingRunRecord | None: ...

    def get_processing_run(self, processing_run_id: str) -> ProcessingRunRecord | None: ...

    def active_processing_run_for_batch(self, batch_id: str) -> ProcessingRunRecord | None: ...

    def save_processing_run(self, record: ProcessingRunRecord) -> None: ...

    def create_processing_run(
        self,
        *,
        batch_id: str,
        campaign_label_version_id: str | None,
        template_label_version_id: str | None,
        rate_card_version_id: str | None,
        label_group_version_id: str | None,
        engine_version: str | None,
    ) -> ProcessingRunRecord: ...

    def add_processing_run(
        self,
        *,
        batch_id: str,
        campaign_label_version_id: str | None,
        template_label_version_id: str | None,
        rate_card_version_id: str | None,
        label_group_version_id: str | None,
        engine_version: str | None,
    ) -> ProcessingRunRecord: ...
