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
        on_progress=None,
    ) -> tuple[PublicationRecord, PublicationCurrentRecord]: ...

    def has_publication_for_runs(self, run_ids: Sequence[str]) -> bool: ...

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

    def list_published_history(
        self,
        client_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]: ...

    def copy_published_history_csv(self, client_id: str, *, max_rows: int) -> bytes: ...

    def list_published_month_starts(self, client_id: str) -> list[date]: ...

    def published_day_bounds(self, client_id: str) -> tuple[date | None, date | None]: ...

    def sum_published_history_month(
        self, client_id: str, month_start: date
    ) -> tuple[dict[str, object], int, date | None, date | None]: ...

    def sum_published_history(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> tuple[dict[str, object], int, date | None, date | None]: ...

    def list_published_filter_values(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        dimension: str,
    ) -> list[tuple[str, str]]: ...

    def list_published_history_series(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        grain: str,
        breakdown: str | None = None,
        rank_measure: str | None = None,
        limit_series: int | None = None,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> tuple[list[tuple[date, str | None, str | None, dict[str, object], int]], int]: ...

    def list_published_history_groups(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        dimension: str,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> list[tuple[str, str, dict[str, object], int]]: ...

    def list_published_history_pairs(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        primary: str,
        secondary: str,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> list[tuple[str, str, str, str, dict[str, object], int]]: ...


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
