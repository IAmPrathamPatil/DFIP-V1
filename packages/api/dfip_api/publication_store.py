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
        on_progress=None,
    ) -> tuple[PublicationRecord, PublicationCurrentRecord]:
        """Insert a publication, its snapshot, and replace the current pointer.

        Prior publication rows remain in ``publications`` as history.
        The pointer is written only after the snapshot list is complete.
        """
        rows = _unique_snapshot_rows(snapshot_facts or ())
        if on_progress is not None:
            on_progress("creating", "Creating publication")
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
        if on_progress is not None:
            on_progress("history", "Updating historical serving data")
            on_progress("finalizing", "Finalizing")
        self.current[client_id] = pointer
        return record, pointer

    def has_publication_for_runs(self, run_ids: Sequence[str]) -> bool:
        wanted = set(run_ids)
        return any(item.processing_run_id in wanted for item in self.publications.values())

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

    def list_published_history(
        self,
        client_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        """Union complete snapshots for one client; newest publication wins per grain."""
        publications = [
            item
            for item in reversed(list(self.publications.values()))
            if item.client_id == client_id and item.snapshot_status == SNAPSHOT_STATUS_COMPLETE
        ]
        merged: dict[FactKey, FactRecord] = {}
        for publication in publications:
            for fact in self.snapshots.get(publication.id, []):
                if fact.client_id != client_id:
                    continue
                if fact.key not in merged:
                    merged[fact.key] = fact
        records = list(merged.values())
        records.sort(
            key=lambda item: (item.day, item.campaign_id, item.variation_id_key, item.client_id)
        )
        return records[offset : offset + limit], len(records)

    def copy_published_history_csv(self, client_id: str, *, max_rows: int) -> bytes:
        from dfip_api.published_download import render_history_facts_csv

        records, _total = self.list_published_history(client_id, limit=max_rows, offset=0)
        return render_history_facts_csv(records)

    def list_published_month_starts(self, client_id: str) -> list[date]:
        from dfip_core.transform.derive import month_start as month_of

        months: set[date] = set()
        for fact in self._history_facts(client_id):
            months.add(fact.month_start or month_of(fact.day))
        return sorted(months)

    def published_day_bounds(self, client_id: str) -> tuple[date | None, date | None]:
        days = [fact.day for fact in self._history_facts(client_id)]
        if not days:
            return None, None
        return min(days), max(days)

    def sum_published_history_month(
        self, client_id: str, month_start: date
    ) -> tuple[dict[str, object], int, date | None, date | None]:
        from dfip_analytics.filters import add_calendar_month
        from dfip_core.transform.derive import month_start as month_of

        start = month_of(month_start)
        return self.sum_published_history(
            client_id,
            day_from=start,
            day_to_exclusive=add_calendar_month(start),
        )

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
    ) -> tuple[dict[str, object], int, date | None, date | None]:
        from dfip_analytics.aggregate import sum_additive_measures
        from dfip_analytics.filters import matches_text_filter

        facts = [
            fact
            for fact in self._history_facts(client_id)
            if day_from <= fact.day < day_to_exclusive
            and matches_text_filter(fact.campaign_id, campaign_ids)
            and matches_text_filter(fact.channel, channels)
            and matches_text_filter(fact.filter_logic_1, filter_logic_1)
            and matches_text_filter(fact.filter_logic_1_group, filter_logic_1_group)
        ]
        days = [fact.day for fact in facts]
        return (
            dict(sum_additive_measures(facts)),
            len(facts),
            min(days) if days else None,
            max(days) if days else None,
        )

    def list_published_filter_values(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        dimension: str,
    ) -> list[tuple[str, str]]:
        from dfip_analytics.filters import ALLOWED_DIMENSIONS

        if dimension not in ALLOWED_DIMENSIONS:
            return []
        found: dict[str, str] = {}
        for fact in self._history_facts(client_id):
            if not (day_from <= fact.day < day_to_exclusive):
                continue
            if dimension == "campaign_id":
                value = fact.campaign_id
                label = fact.campaign_name or fact.campaign_id
            elif dimension == "channel":
                value = fact.channel or ""
                label = value or "(blank)"
            elif dimension == "filter_logic_1":
                value = fact.filter_logic_1 or ""
                label = value or "(blank)"
            else:
                value = fact.filter_logic_1_group or ""
                label = value or "(blank)"
            if value not in found:
                found[value] = label
        return sorted(found.items(), key=lambda item: (item[1].lower(), item[0]))

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
    ) -> tuple[list[tuple[date, str | None, str | None, dict[str, object], int]], int]:
        from collections import defaultdict
        from decimal import Decimal

        from dfip_analytics.aggregate import sum_additive_measures
        from dfip_analytics.filters import ALLOWED_DIMENSIONS, matches_text_filter
        from dfip_analytics.kpis import ADDITIVE_MEASURES
        from dfip_analytics.trends import (
            MAX_BREAKDOWN_SERIES,
            OTHER_SERIES_KEY,
            bucket_start,
        )

        facts = [
            fact
            for fact in self._history_facts(client_id)
            if day_from <= fact.day < day_to_exclusive
            and matches_text_filter(fact.campaign_id, campaign_ids)
            and matches_text_filter(fact.channel, channels)
            and matches_text_filter(fact.filter_logic_1, filter_logic_1)
            and matches_text_filter(fact.filter_logic_1_group, filter_logic_1_group)
        ]
        if not breakdown:
            grouped: dict[date, list[FactRecord]] = defaultdict(list)
            for fact in facts:
                grouped[bucket_start(fact.day, grain)].append(fact)
            rows = [
                (bucket, None, None, dict(sum_additive_measures(group)), len(group))
                for bucket, group in sorted(grouped.items())
            ]
            return rows, 1 if rows else 0

        if breakdown not in ALLOWED_DIMENSIONS:
            return [], 0
        cap = limit_series if limit_series and limit_series > 0 else MAX_BREAKDOWN_SERIES
        rank_name = rank_measure if rank_measure in ADDITIVE_MEASURES else "total_cost"

        def dim_parts(fact: FactRecord) -> tuple[str, str]:
            if breakdown == "campaign_id":
                value = fact.campaign_id or ""
                label = fact.campaign_name or value or "(blank)"
                return value, label
            raw = getattr(fact, breakdown) or ""
            return raw, raw or "(blank)"

        by_dim: dict[str, list[FactRecord]] = defaultdict(list)
        labels: dict[str, str] = {}
        for fact in facts:
            value, label = dim_parts(fact)
            by_dim[value].append(fact)
            labels[value] = label
        distinct = len(by_dim)
        ranked = sorted(
            by_dim.keys(),
            key=lambda key: (
                -(sum_additive_measures(by_dim[key]).get(rank_name) or Decimal("0")),
                labels.get(key, key).lower(),
                key,
            ),
        )
        keep = set(ranked[:cap])
        grouped_bd: dict[tuple[date, str], list[FactRecord]] = defaultdict(list)
        grouped_labels: dict[str, str] = {}
        for fact in facts:
            value, label = dim_parts(fact)
            series_key = value if value in keep else OTHER_SERIES_KEY
            series_label = label if value in keep else "Other"
            grouped_bd[(bucket_start(fact.day, grain), series_key)].append(fact)
            grouped_labels[series_key] = series_label
        rows = [
            (
                bucket,
                dim_value,
                grouped_labels.get(dim_value),
                dict(sum_additive_measures(group)),
                len(group),
            )
            for (bucket, dim_value), group in sorted(grouped_bd.items())
        ]
        return rows, distinct

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
    ) -> list[tuple[str, str, dict[str, object], int]]:
        from collections import defaultdict

        from dfip_analytics.aggregate import sum_additive_measures
        from dfip_analytics.filters import matches_text_filter
        from dfip_analytics.drill import DRILL_DIMENSIONS_BY_KEY

        if dimension not in DRILL_DIMENSIONS_BY_KEY:
            return []
        facts = [
            fact
            for fact in self._history_facts(client_id)
            if day_from <= fact.day < day_to_exclusive
            and matches_text_filter(fact.campaign_id, campaign_ids)
            and matches_text_filter(fact.channel, channels)
            and matches_text_filter(fact.filter_logic_1, filter_logic_1)
            and matches_text_filter(fact.filter_logic_1_group, filter_logic_1_group)
        ]
        grouped: dict[str, list] = defaultdict(list)
        labels: dict[str, str] = {}
        for fact in facts:
            if dimension == "day":
                value = fact.day.isoformat()
                label = value
            elif dimension == "campaign_id":
                value = fact.campaign_id or ""
                label = fact.campaign_name or value or "(blank)"
            else:
                value = getattr(fact, dimension) or ""
                label = value or "(blank)"
            grouped[value].append(fact)
            labels[value] = label
        return [
            (value, labels[value], dict(sum_additive_measures(group)), len(group))
            for value, group in grouped.items()
        ]

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
    ) -> list[tuple[str, str, str, str, dict[str, object], int]]:
        from collections import defaultdict

        from dfip_analytics.aggregate import sum_additive_measures
        from dfip_analytics.drill import DRILL_DIMENSIONS_BY_KEY
        from dfip_analytics.filters import matches_text_filter

        if primary not in DRILL_DIMENSIONS_BY_KEY or secondary not in DRILL_DIMENSIONS_BY_KEY:
            return []
        facts = [
            fact
            for fact in self._history_facts(client_id)
            if day_from <= fact.day < day_to_exclusive
            and matches_text_filter(fact.campaign_id, campaign_ids)
            and matches_text_filter(fact.channel, channels)
            and matches_text_filter(fact.filter_logic_1, filter_logic_1)
            and matches_text_filter(fact.filter_logic_1_group, filter_logic_1_group)
        ]
        grouped: dict[tuple[str, str], list] = defaultdict(list)
        labels: dict[tuple[str, str], tuple[str, str]] = {}
        for fact in facts:
            primary_parts = _dimension_parts(fact, primary)
            secondary_parts = _dimension_parts(fact, secondary)
            if primary_parts is None or secondary_parts is None:
                continue
            pvalue, plabel = primary_parts
            svalue, slabel = secondary_parts
            key = (pvalue, svalue)
            grouped[key].append(fact)
            labels[key] = (plabel, slabel)
        return [
            (
                pvalue,
                labels[(pvalue, svalue)][0],
                svalue,
                labels[(pvalue, svalue)][1],
                dict(sum_additive_measures(group)),
                len(group),
            )
            for (pvalue, svalue), group in grouped.items()
        ]

    def _history_facts(self, client_id: str) -> list[FactRecord]:
        records, _total = self.list_published_history(client_id, limit=10_000_000, offset=0)
        return records

    def purge_client(self, client_id: str) -> None:
        drop_ids = [item.id for item in self.publications.values() if item.client_id == client_id]
        for publication_id in drop_ids:
            self.publications.pop(publication_id, None)
            self.snapshots.pop(publication_id, None)
        self.current.pop(client_id, None)


def _dimension_parts(fact: FactRecord, dimension: str) -> tuple[str, str] | None:
    if dimension == "day":
        value = fact.day.isoformat()
        return value, value
    if dimension == "campaign_id":
        value = fact.campaign_id or ""
        return value, fact.campaign_name or value or "(blank)"
    if not hasattr(fact, dimension):
        return None
    value = getattr(fact, dimension) or ""
    return value, value or "(blank)"


def _unique_snapshot_rows(facts: Sequence[FactRecord]) -> list[FactRecord]:
    seen: set[FactKey] = set()
    rows: list[FactRecord] = []
    for fact in facts:
        if fact.key in seen:
            raise PersistenceUnavailableError("Publication snapshot contains duplicate grains.")
        seen.add(fact.key)
        rows.append(fact)
    return rows
