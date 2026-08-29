"""Thin read adapter over the existing P3 ingest and P4 fact stores.

This module does not implement ingestion, transformation, cost, or labels.
It does not open PostgreSQL. Live-database access is not implemented.
"""

from __future__ import annotations

from datetime import date
from typing import TypeVar

from dfip_core.ingest.store import (
    BatchRecord,
    InMemoryIngestStore,
    ProcessingRunRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import InMemoryFactStore, SupersededFact

from dfip_api.errors import NotFoundError

T = TypeVar("T")


def _paginate(items: list[T], *, limit: int, offset: int) -> tuple[list[T], int]:
    return items[offset : offset + limit], len(items)


class InMemoryReadRepository:
    """Read-only view of process-local P3/P4 stores."""

    def __init__(
        self,
        ingest_store: InMemoryIngestStore,
        fact_store: InMemoryFactStore,
    ) -> None:
        self._ingest = ingest_store
        self._facts = fact_store

    def list_source_files(
        self,
        *,
        client_id: str | None,
        client_ids: tuple[str, ...] | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[SourceFileRecord], int]:
        records = list(self._ingest.source_files_by_id.values())
        records = [
            item for item in records if _client_allowed(item.client_id, client_id, client_ids)
        ]
        records.sort(key=lambda item: (item.uploaded_at, item.id))
        return _paginate(records, limit=limit, offset=offset)

    def get_source_file(self, source_file_id: str) -> SourceFileRecord:
        record = self._ingest.get_source_file(source_file_id)
        if record is None:
            raise NotFoundError("Source file not found.")
        return record

    def list_batches(
        self,
        *,
        source_file_id: str | None,
        client_id: str | None,
        client_ids: tuple[str, ...] | None = None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BatchRecord], int]:
        records = list(self._ingest.batches.values())
        if source_file_id is not None:
            records = [item for item in records if item.source_file_id == source_file_id]
        records = [
            item for item in records if _client_allowed(item.client_id, client_id, client_ids)
        ]
        if status is not None:
            records = [item for item in records if item.status == status]
        records.sort(key=lambda item: (item.created_at, item.id))
        return _paginate(records, limit=limit, offset=offset)

    def get_batch(self, batch_id: str) -> BatchRecord:
        record = self._ingest.get_batch(batch_id)
        if record is None:
            raise NotFoundError("Batch not found.")
        return record

    def list_processing_runs(
        self,
        *,
        batch_id: str | None,
        client_ids: tuple[str, ...] | None = None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProcessingRunRecord], int]:
        records = list(self._ingest.processing_runs.values())
        if batch_id is not None:
            records = [item for item in records if item.batch_id == batch_id]
        if client_ids is not None:
            allowed = set(client_ids)
            kept: list[ProcessingRunRecord] = []
            for item in records:
                batch = self._ingest.get_batch(item.batch_id)
                if batch is not None and batch.client_id in allowed:
                    kept.append(item)
            records = kept
        if status is not None:
            records = [item for item in records if item.status == status]
        records.sort(key=lambda item: (item.started_at, item.id))
        return _paginate(records, limit=limit, offset=offset)

    def get_processing_run(self, processing_run_id: str) -> ProcessingRunRecord:
        record = self._ingest.get_processing_run(processing_run_id)
        if record is None:
            raise NotFoundError("Processing run not found.")
        return record

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
        if self._ingest.get_batch(batch_id) is None:
            raise NotFoundError("Batch not found.")
        records = self._ingest.staged_for_batch(batch_id)
        if source_row_number is not None:
            records = [item for item in records if item.source_row_number == source_row_number]
        if campaign_id is not None:
            records = [item for item in records if item.campaign_id == campaign_id]
        if variation_id is not None:
            records = [item for item in records if item.variation_id == variation_id]
        if day is not None:
            records = [item for item in records if item.day == day]
        records.sort(key=lambda item: (item.source_row_number, item.id))
        return _paginate(records, limit=limit, offset=offset)

    def list_facts(
        self,
        *,
        client_id: str | None,
        client_ids: tuple[str, ...] | None = None,
        batch_id: str | None,
        processing_run_id: str | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        records = self._facts.list_current()
        records = _filter_facts(
            records,
            client_id=client_id,
            client_ids=client_ids,
            batch_id=batch_id,
            processing_run_id=processing_run_id,
            campaign_id=campaign_id,
            variation_id=variation_id,
            day=day,
        )
        records.sort(
            key=lambda item: (item.day, item.campaign_id, item.variation_id_key, item.client_id)
        )
        return _paginate(records, limit=limit, offset=offset)

    def list_fact_history(
        self,
        *,
        client_id: str | None,
        client_ids: tuple[str, ...] | None = None,
        batch_id: str | None,
        processing_run_id: str | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SupersededFact], int]:
        records = self._facts.list_history()
        matched: list[SupersededFact] = []
        for item in records:
            filtered = _filter_facts(
                [item.fact],
                client_id=client_id,
                client_ids=client_ids,
                batch_id=batch_id,
                processing_run_id=processing_run_id,
                campaign_id=campaign_id,
                variation_id=variation_id,
                day=day,
            )
            if filtered:
                matched.append(item)
        matched.sort(
            key=lambda item: (
                item.superseded_at,
                item.fact.day,
                item.fact.campaign_id,
                item.fact.variation_id_key,
                item.fact.client_id,
            )
        )
        return _paginate(matched, limit=limit, offset=offset)


def _client_allowed(
    record_client_id: str, client_id: str | None, client_ids: tuple[str, ...] | None
) -> bool:
    if client_ids is not None and record_client_id not in client_ids:
        return False
    if client_id is not None:
        return record_client_id == client_id
    return True


def _filter_facts(
    records: list[FactRecord],
    *,
    client_id: str | None,
    client_ids: tuple[str, ...] | None = None,
    batch_id: str | None,
    processing_run_id: str | None,
    campaign_id: str | None,
    variation_id: str | None,
    day: date | None,
) -> list[FactRecord]:
    records = [item for item in records if _client_allowed(item.client_id, client_id, client_ids)]
    if batch_id is not None:
        records = [item for item in records if item.batch_id == batch_id]
    if processing_run_id is not None:
        records = [item for item in records if item.processing_run_id == processing_run_id]
    if campaign_id is not None:
        records = [item for item in records if item.campaign_id == campaign_id]
    if variation_id is not None:
        records = [item for item in records if item.variation_id == variation_id]
    if day is not None:
        records = [item for item in records if item.day == day]
    return records
