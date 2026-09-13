"""P4 transformation engine.

Converts staged Web Engage rows into `fact_campaign_day` records:

    stg_source_row -> extract -> campaign labels -> template Q:R
                   -> Filter Logic 1_2 -> rate card -> Total Cost -> fact

Rows are processed one at a time so a run never has to hold a whole batch in
memory. Configuration is bound once per distinct day and the P2 lookup indexes
are built once per version, so no row re-parses a JSON snapshot.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from dfip_config.catalog import CatalogStore

from dfip_core.ingest.ports import IngestStore
from dfip_core.ingest.progress import (
    STAGE_PROCESSING,
    ProcessingCancelled,
    ProgressCallback,
    emit_progress,
)
from dfip_core.ingest.store import ProcessingRunRecord, StagedRowRecord
from dfip_core.transform import derive
from dfip_core.transform.cost import calculate_total_cost, persistable_rate_card_rule_id
from dfip_core.transform.extract import RowValidationError, extract_source_fields
from dfip_core.transform.fact import FactKey, FactRecord
from dfip_core.transform.labels import (
    ConfigBundle,
    ConfigurationBindingError,
    bind_configuration,
    bind_configuration_for_run,
)
from dfip_core.transform.ports import FactStore

ENGINE_VERSION = "0.4.0"

# One COMMIT per chunk, not per fact. COPY removes the 65535-parameter cap, so
# 5000 rows is one persist transaction (~100 commits for a 500,000-row batch)
# while still emitting real processing progress.
FACT_PERSIST_CHUNK_SIZE = 5000
STAGED_TRANSFORM_PAGE_SIZE = 5000


@dataclass(frozen=True)
class RowRejection:
    source_row_number: int
    reason_code: str
    reason_detail: str


@dataclass(frozen=True)
class RowOutcome:
    """Exactly one of `fact` / `rejection` is set."""

    source_row_number: int
    fact: FactRecord | None
    rejection: RowRejection | None


@dataclass
class TransformResult:
    processing_run: ProcessingRunRecord
    transformed: int = 0
    inserted: int = 0
    restated: int = 0
    unchanged: int = 0
    rejected: int = 0
    rejections: list[RowRejection] = field(default_factory=list)
    version_labels: dict[str, set[str]] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.processing_run.status == "succeeded"


class ConfigBinder:
    """Per-day configuration selection, memoised for the life of a run."""

    def __init__(
        self,
        *,
        catalog: CatalogStore | None = None,
        client_id: str | None = None,
        processing_run: ProcessingRunRecord | None = None,
    ) -> None:
        self._cache: dict[date, ConfigBundle] = {}
        self._catalog = catalog
        self._client_id = client_id
        self._processing_run = processing_run

    def for_day(self, day: date) -> ConfigBundle:
        bundle = self._cache.get(day)
        if bundle is None:
            if self._processing_run is not None:
                bundle = bind_configuration_for_run(
                    day,
                    campaign_label_version_id=self._processing_run.campaign_label_version_id,
                    template_label_version_id=self._processing_run.template_label_version_id,
                    rate_card_version_id=self._processing_run.rate_card_version_id,
                    label_group_version_id=self._processing_run.label_group_version_id,
                    catalog=self._catalog,
                    client_id=self._client_id,
                )
            else:
                bundle = bind_configuration(day, catalog=self._catalog, client_id=self._client_id)
            self._cache[day] = bundle
        return bundle

    @property
    def bundles(self) -> tuple[ConfigBundle, ...]:
        return tuple(self._cache.values())


def transform_row(
    staged: StagedRowRecord,
    binder: ConfigBinder,
    *,
    client_id: str,
    batch_id: str,
    processing_run_id: str,
    now: datetime | None = None,
) -> RowOutcome:
    """Turn one staged row into a fact record or a deterministic rejection."""
    row_number = staged.source_row_number
    try:
        fields = extract_source_fields(staged.raw)
    except RowValidationError as exc:
        return RowOutcome(row_number, None, RowRejection(row_number, exc.reason_code, exc.detail))

    try:
        bundle = binder.for_day(fields.day)
    except ConfigurationBindingError as exc:
        return RowOutcome(
            row_number,
            None,
            RowRejection(row_number, "NO_CONFIG_VERSION", str(exc)),
        )

    campaign = bundle.campaign(fields.campaign_name)
    template = bundle.template(fields.template_name_whatsapp)
    group = bundle.filter_logic_1_group(campaign.filter_logic_1)
    cost = calculate_total_cost(
        bundle.rate_card_version_label,
        template.template_status,
        fields.channel,
        fields.integers["delivered"],
    )

    observed_at = now or datetime.now(tz=UTC)
    fact = FactRecord(
        client_id=client_id,
        campaign_id=fields.campaign_id,
        variation_id=fields.variation_id,
        variation_id_key=derive.variation_id_key(fields.variation_id),
        day=fields.day,
        month_start=derive.month_start(fields.day),
        month_label=derive.month_label(fields.day),
        campaign_name=fields.campaign_name,
        variation_name=fields.variation_name,
        channel=fields.channel,
        type_of_campaign=fields.type_of_campaign,
        start_date=fields.start_date,
        template_name_whatsapp=fields.template_name_whatsapp,
        **fields.integers,
        **fields.decimals,
        filter_logic_1=campaign.filter_logic_1,
        filter_logic_2=campaign.filter_logic_2,
        template_status=template.template_status,
        amc_status_filter_logic_3=campaign.amc_status_filter_logic_3,
        amc_device_category_filter_logic_4=campaign.amc_device_category_filter_logic_4,
        amc_product_cat_filter_logic_5=campaign.amc_product_cat_filter_logic_5,
        manual_or_automated=campaign.manual_or_automated,
        total_cost=cost.total_cost,
        hhh=derive.hhh(fields.start_date),
        filter_logic_1_group=group,
        label_match_status=campaign.match_status,
        template_match_status=template.match_status,
        rate_card_rule_id=persistable_rate_card_rule_id(client_id, cost.rule_id),
        processing_run_id=processing_run_id,
        batch_id=batch_id,
        campaign_label_version_id=bundle.campaign_label_version_id,
        template_label_version_id=bundle.template_label_version_id,
        rate_card_version_id=bundle.rate_card_version_id,
        label_group_version_id=bundle.label_group_version_id,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
    )
    return RowOutcome(row_number, fact, None)


def transform_rows(
    rows: Iterable[StagedRowRecord],
    binder: ConfigBinder,
    *,
    client_id: str,
    batch_id: str,
    processing_run_id: str,
    now: datetime | None = None,
) -> Iterator[RowOutcome]:
    """Stream outcomes. Never materialises the batch."""
    for staged in rows:
        yield transform_row(
            staged,
            binder,
            client_id=client_id,
            batch_id=batch_id,
            processing_run_id=processing_run_id,
            now=now,
        )


def _iter_staged(ingest_store: IngestStore, batch_id: str) -> Iterator[StagedRowRecord]:
    """Prefer larger keyset pages when the store supports them."""
    iterator = ingest_store.iter_staged_for_batch
    try:
        return iterator(batch_id, page_size=STAGED_TRANSFORM_PAGE_SIZE)
    except TypeError:
        return iterator(batch_id)


def run_transformation(
    ingest_store: IngestStore,
    fact_store: FactStore,
    batch_id: str,
    *,
    processing_run_id: str,
    now: datetime | None = None,
    catalog: CatalogStore | None = None,
    persist_rejections: bool = True,
    persist_chunk_size: int = FACT_PERSIST_CHUNK_SIZE,
    on_progress: ProgressCallback | None = None,
) -> TransformResult:
    """Transform a staged batch into the explicitly selected processing_run."""
    batch = ingest_store.get_batch(batch_id)
    if batch is None:
        raise KeyError(f"unknown batch {batch_id}")
    run = ingest_store.get_processing_run(processing_run_id)
    if run is None:
        raise ValueError(f"unknown processing_run {processing_run_id}")
    if run.batch_id != batch_id:
        raise ValueError(f"processing_run {processing_run_id} does not belong to batch {batch_id}")

    chunk_size = persist_chunk_size if persist_chunk_size > 0 else FACT_PERSIST_CHUNK_SIZE
    run.status = "running"
    run.finished_at = None
    run.progress_at = datetime.now(tz=UTC)
    ingest_store.save_processing_run(run)
    binder = ConfigBinder(catalog=catalog, client_id=batch.client_id, processing_run=run)
    result = TransformResult(processing_run=run)
    seen: set[FactKey] = set()
    pending: list[FactRecord] = []
    total = batch.row_count_staged if batch.row_count_staged else None
    seen_rows = 0
    emit_progress(on_progress, STAGE_PROCESSING, 0, total, "Transforming campaign/day facts...")

    def flush_pending() -> None:
        if not pending:
            return
        actions = fact_store.upsert_many(pending)
        for action in actions:
            if action == "inserted":
                result.inserted += 1
            elif action == "restated":
                result.restated += 1
            else:
                result.unchanged += 1
            result.transformed += 1
        pending.clear()
        run.progress_at = datetime.now(tz=UTC)
        emit_progress(
            on_progress,
            STAGE_PROCESSING,
            seen_rows,
            total,
            "Transforming campaign/day facts...",
        )

    try:
        for outcome in transform_rows(
            _iter_staged(ingest_store, batch_id),
            binder,
            client_id=batch.client_id,
            batch_id=batch_id,
            processing_run_id=run.id,
            now=now,
        ):
            seen_rows += 1
            if outcome.rejection is not None:
                _record_rejection(
                    ingest_store,
                    batch_id,
                    outcome.rejection,
                    result,
                    persist=persist_rejections,
                )
                continue
            fact = outcome.fact
            assert fact is not None
            if fact.key in seen:
                # Never overwrite an earlier row of the same run: keep the first and
                # record the collision so nothing is silently dropped.
                _record_rejection(
                    ingest_store,
                    batch_id,
                    RowRejection(
                        outcome.source_row_number,
                        "DUPLICATE_GRAIN_KEY",
                        f"{fact.campaign_id}/{fact.variation_id_key}/{fact.day.isoformat()} "
                        "already produced a fact in this run",
                    ),
                    result,
                    persist=persist_rejections,
                )
                continue
            seen.add(fact.key)
            pending.append(fact)
            if len(pending) >= chunk_size:
                flush_pending()

        flush_pending()
        emit_progress(
            on_progress,
            STAGE_PROCESSING,
            seen_rows,
            total if total is not None else seen_rows,
            "Transforming campaign/day facts...",
        )
        result.version_labels = _version_labels(binder)
        run.finished_at = datetime.now(tz=UTC)
        run.status = "succeeded" if result.transformed > 0 or result.rejected == 0 else "failed"
        run.error_summary = None
        ingest_store.save_processing_run(run)
        if run.status == "succeeded":
            batch.status = "processed"
            ingest_store.save_batch(batch)
        return result
    except ProcessingCancelled:
        raise
    except Exception as exc:
        run.status = "failed"
        run.finished_at = datetime.now(tz=UTC)
        run.error_summary = f"{type(exc).__name__} during fact persistence"
        ingest_store.save_processing_run(run)
        raise


def _record_rejection(
    ingest_store: IngestStore,
    batch_id: str,
    rejection: RowRejection,
    result: TransformResult,
    *,
    persist: bool = True,
) -> None:
    if persist:
        ingest_store.add_rejected_row(
            batch_id=batch_id,
            source_row_number=rejection.source_row_number,
            raw=None,
            reason_code=rejection.reason_code,
            reason_detail=rejection.reason_detail,
        )
    result.rejections.append(rejection)
    result.rejected += 1


def _version_labels(binder: ConfigBinder) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {
        "campaign": set(),
        "template": set(),
        "rate_card": set(),
        "label_group": set(),
    }
    for bundle in binder.bundles:
        labels["campaign"].add(bundle.campaign_version_label)
        labels["template"].add(bundle.template_version_label)
        labels["rate_card"].add(bundle.rate_card_version_label)
        labels["label_group"].add(bundle.label_group_version_label)
    return labels
