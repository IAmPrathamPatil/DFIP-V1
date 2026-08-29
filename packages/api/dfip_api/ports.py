"""API persistence protocols. In-memory and PostgreSQL adapters implement these."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from dfip_core.ingest.store import (
    BatchRecord,
    ProcessingRunRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import SupersededFact

from dfip_api.publication_store import PublicationCurrentRecord, PublicationRecord


class PublicationStore(Protocol):
    def create(
        self,
        *,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        published_by: str | None,
        notes: str | None,
        fact_scope: str = "processing_run",
        snapshot_facts: Sequence[FactRecord] | None = None,
    ) -> tuple[PublicationRecord, PublicationCurrentRecord]: ...

    def get(self, publication_id: str) -> PublicationRecord | None: ...

    def get_current(
        self, client_id: str
    ) -> tuple[PublicationRecord | None, PublicationCurrentRecord | None]: ...

    def list_for_client(self, client_id: str) -> list[PublicationRecord]: ...

    def list_snapshot(
        self,
        publication_id: str,
        *,
        client_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]: ...


class ReadRepository(Protocol):
    def list_source_files(
        self, *, client_id: str | None, client_ids: tuple[str, ...] | None, limit: int, offset: int
    ) -> tuple[list[SourceFileRecord], int]: ...

    def get_source_file(self, source_file_id: str) -> SourceFileRecord: ...

    def list_batches(
        self,
        *,
        source_file_id: str | None,
        client_id: str | None,
        client_ids: tuple[str, ...] | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BatchRecord], int]: ...

    def get_batch(self, batch_id: str) -> BatchRecord: ...

    def list_processing_runs(
        self,
        *,
        batch_id: str | None,
        client_ids: tuple[str, ...] | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProcessingRunRecord], int]: ...

    def get_processing_run(self, processing_run_id: str) -> ProcessingRunRecord: ...

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
    ) -> tuple[list[StagedRowRecord], int]: ...

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
    ) -> tuple[list[FactRecord], int]: ...

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
    ) -> tuple[list[SupersededFact], int]: ...
