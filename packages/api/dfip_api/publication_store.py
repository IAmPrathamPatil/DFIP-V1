"""In-memory `publication` / `publication_current` (P1 shape).

Mirrors the P1 tables so P7 can be exercised without a live PostgreSQL.
Does not open a database. This is not RLS.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import uuid4

from dfip_core.transform.fact import FactKey, FactRecord

from dfip_api.errors import PersistenceUnavailableError

FACT_SCOPE_PROCESSING_RUN = "processing_run"
FACT_SCOPE_CLIENT_CURRENT = "client_current"
ALLOWED_FACT_SCOPES = frozenset({FACT_SCOPE_PROCESSING_RUN, FACT_SCOPE_CLIENT_CURRENT})
SNAPSHOT_STATUS_NONE = "none"
SNAPSHOT_STATUS_COMPLETE = "complete"
SNAPSHOT_CHUNK_SIZE = 500


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class PublicationRecord:
    """One `publication` row."""

    id: str
    client_id: str
    processing_run_id: str
    period_start: date | None
    period_end: date | None
    published_at: datetime
    published_by: str | None
    notes: str | None
    fact_scope: str = FACT_SCOPE_PROCESSING_RUN
    snapshot_status: str = SNAPSHOT_STATUS_NONE
    snapshot_row_count: int | None = None


@dataclass(frozen=True)
class PublicationCurrentRecord:
    """One `publication_current` row. Exactly one pointer per client."""

    client_id: str
    publication_id: str
    updated_at: datetime


@dataclass
class InMemoryPublicationStore:
    """Process-local stand-in for publication / publication_current."""

    publications: dict[str, PublicationRecord] = field(default_factory=dict)
    current: dict[str, PublicationCurrentRecord] = field(default_factory=dict)
    snapshots: dict[str, list[FactRecord]] = field(default_factory=dict)

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
        """Insert a publication, its snapshot, and replace the current pointer.

        Prior publication rows remain in ``publications`` as history.
        The pointer is written only after the snapshot list is complete.
        """
        rows = _unique_snapshot_rows(snapshot_facts or ())
        now = _now()
        record = PublicationRecord(
            id=_new_id(),
            client_id=client_id,
            processing_run_id=processing_run_id,
            period_start=period_start,
            period_end=period_end,
            published_at=now,
            published_by=published_by,
            notes=notes,
            fact_scope=fact_scope,
            snapshot_status=SNAPSHOT_STATUS_COMPLETE,
            snapshot_row_count=len(rows),
        )
        pointer = PublicationCurrentRecord(
            client_id=client_id,
            publication_id=record.id,
            updated_at=now,
        )
        copied: list[FactRecord] = []
        for offset in range(0, len(rows), SNAPSHOT_CHUNK_SIZE):
            copied.extend(rows[offset : offset + SNAPSHOT_CHUNK_SIZE])
        if len(copied) != len(rows):
            raise RuntimeError("Publication snapshot was incomplete.")
        self.snapshots[record.id] = copied
        self.publications[record.id] = record
        self.current[client_id] = pointer
        return record, pointer

    def get(self, publication_id: str) -> PublicationRecord | None:
        return self.publications.get(publication_id)

    def get_current(
        self, client_id: str
    ) -> tuple[PublicationRecord | None, PublicationCurrentRecord | None]:
        pointer = self.current.get(client_id)
        if pointer is None:
            return None, None
        record = self.publications.get(pointer.publication_id)
        return record, pointer

    def list_for_client(self, client_id: str) -> list[PublicationRecord]:
        rows = [item for item in self.publications.values() if item.client_id == client_id]
        rows.sort(key=lambda item: (item.published_at, item.id))
        return rows

    def list_snapshot(
        self,
        publication_id: str,
        *,
        client_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        records = [
            item for item in self.snapshots.get(publication_id, []) if item.client_id == client_id
        ]
        records.sort(
            key=lambda item: (item.day, item.campaign_id, item.variation_id_key, item.client_id)
        )
        return records[offset : offset + limit], len(records)


def _unique_snapshot_rows(facts: Sequence[FactRecord]) -> list[FactRecord]:
    seen: set[FactKey] = set()
    rows: list[FactRecord] = []
    for fact in facts:
        if fact.key in seen:
            raise PersistenceUnavailableError("Publication snapshot contains duplicate grains.")
        seen.add(fact.key)
        rows.append(fact)
    return rows
