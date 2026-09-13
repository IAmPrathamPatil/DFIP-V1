"""In-memory `fact_campaign_day` / `fact_campaign_day_history`.

Mirrors the P1 tables so P4 can be exercised without a live PostgreSQL. The
upsert semantics here are the ones a SQL implementation must reproduce.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from dfip_core.transform.fact import FactKey, FactRecord


@dataclass(frozen=True)
class SupersededFact:
    """One `fact_campaign_day_history` row."""

    fact: FactRecord
    superseded_at: datetime
    superseded_by_run_id: str | None


@dataclass
class InMemoryFactStore:
    facts: dict[FactKey, FactRecord] = field(default_factory=dict)
    history: list[SupersededFact] = field(default_factory=list)

    def get(self, key: FactKey) -> FactRecord | None:
        return self.facts.get(key)

    def upsert(self, record: FactRecord) -> str:
        """Write a fact, archiving any prior version first.

        Returns 'inserted', 'restated', or 'unchanged'. A prior fact is never
        overwritten in place: it is copied to history and stamped with the run
        that superseded it.
        """
        previous = self.facts.get(record.key)
        if previous is None:
            self.facts[record.key] = record
            return "inserted"

        carried = record.carry_first_seen(previous)
        if previous.business_values() == carried.business_values():
            self.facts[record.key] = replace(carried, last_seen_at=record.last_seen_at)
            return "unchanged"

        self.history.append(
            SupersededFact(
                fact=previous,
                superseded_at=datetime.now(tz=UTC),
                superseded_by_run_id=record.processing_run_id,
            )
        )
        self.facts[record.key] = carried
        return "restated"

    def upsert_many(self, records: Sequence[FactRecord]) -> list[str]:
        return [self.upsert(record) for record in records]

    def for_batch(self, batch_id: str) -> list[FactRecord]:
        return [fact for fact in self.facts.values() if fact.batch_id == batch_id]

    def for_run(self, processing_run_id: str) -> list[FactRecord]:
        return [fact for fact in self.facts.values() if fact.processing_run_id == processing_run_id]

    def list_current(self) -> list[FactRecord]:
        return list(self.facts.values())

    def list_history(self) -> list[SupersededFact]:
        return list(self.history)

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
    ) -> tuple[list[FactRecord], int]:
        del working_set
        run_scoped = fact_scope != "client_current"
        records = [
            item
            for item in self.facts.values()
            if item.client_id == client_id
            and (not run_scoped or item.processing_run_id == processing_run_id)
            and item.day is not None
            and (period_start is None or item.day >= period_start)
            and (period_end is None or item.day <= period_end)
        ]
        records.sort(
            key=lambda item: (item.day, item.campaign_id, item.variation_id_key, item.client_id)
        )
        return records[offset : offset + limit], len(records)

    def revert_run(self, processing_run_id: str) -> None:
        """Restore restated grains and drop facts first seen in this run."""
        latest: dict[FactKey, SupersededFact] = {}
        remaining: list[SupersededFact] = []
        for item in self.history:
            if item.superseded_by_run_id != processing_run_id:
                remaining.append(item)
                continue
            previous = latest.get(item.fact.key)
            if previous is None or item.superseded_at >= previous.superseded_at:
                latest[item.fact.key] = item
        for item in latest.values():
            self.facts[item.fact.key] = item.fact
        self.history = remaining
        self.facts = {
            key: fact
            for key, fact in self.facts.items()
            if fact.processing_run_id != processing_run_id
        }

    def purge_client(self, client_id: str) -> None:
        self.facts = {key: fact for key, fact in self.facts.items() if fact.client_id != client_id}
        self.history = [item for item in self.history if item.fact.client_id != client_id]
