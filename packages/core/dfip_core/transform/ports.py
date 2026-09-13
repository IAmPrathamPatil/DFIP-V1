"""Store protocol for P4 fact persistence.

Adapters must reproduce InMemoryFactStore upsert semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from dfip_core.transform.fact import FactKey, FactRecord
from dfip_core.transform.store import SupersededFact


class FactStore(Protocol):
    def get(self, key: FactKey) -> FactRecord | None: ...

    def upsert(self, record: FactRecord) -> str: ...

    def upsert_many(self, records: Sequence[FactRecord]) -> list[str]: ...

    def for_batch(self, batch_id: str) -> list[FactRecord]: ...

    def for_run(self, processing_run_id: str) -> list[FactRecord]: ...

    def list_current(self) -> list[FactRecord]: ...

    def list_history(self) -> list[SupersededFact]: ...

    def revert_run(self, processing_run_id: str) -> None: ...

    def list_published_slice(
        self,
        *,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        limit: int,
        offset: int,
        fact_scope: str = "processing_run",
        working_set: bool = False,
    ) -> tuple[list[FactRecord], int]: ...
