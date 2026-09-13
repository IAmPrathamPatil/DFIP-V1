"""P3 ingestion pipeline: workbook → source_file → batch → stg_source_row.

Does not apply campaign labels, template status, rate cards, cost, or KPIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from dfip_config.catalog import LABELS_KIND, LOGIC_KIND, CatalogStore
from dfip_core.transform.engine import ENGINE_VERSION
from dfip_config.catalog_identity import persistable_catalog_version_id
from dfip_config.rate_cards import RATE_CARD_VERSIONS
from dfip_config.resolve import select_version_for_day
from dfip_config.store import (
    campaign_versions,
    load_campaign_version,
    load_manifest,
    load_template_version,
    template_versions,
)

from dfip_core.ingest.headers import HeaderContractError, expected_source_headers
from dfip_core.ingest.ports import IngestStore
from dfip_core.ingest.progress import (
    STAGE_FAILED,
    STAGE_INGESTING,
    STAGE_STAGING,
    ProcessingCancelled,
    ProgressCallback,
    WorkbookCapacityError,
    emit_progress,
)
from dfip_core.ingest.reader import (
    iter_source_rows_from_workbook,
    open_source_workbook,
    sha256_file,
    worksheet_data_row_hint,
)
from dfip_core.ingest.store import (
    BatchRecord,
    InMemoryIngestStore,
    ProcessingRunRecord,
    SourceFileRecord,
    StagedRowRecord,
)

DEFAULT_CLIENT_ID = "a0000000-0000-4000-8000-000000000001"
STAGING_CHUNK_SIZE = 5000


def _version_windows(
    labels: tuple[str, ...], loader: Callable[[str], Any]
) -> tuple[tuple[str, str | None, str | None], ...]:
    windows: list[tuple[str, str | None, str | None]] = []
    for label in labels:
        snapshot = loader(label)
        windows.append((label, snapshot.effective_from, snapshot.effective_to))
    return tuple(windows)


@dataclass(frozen=True)
class VersionBinding:
    campaign_label_version_id: str | None
    template_label_version_id: str | None
    rate_card_version_id: str | None
    label_group_version_id: str | None
    campaign_version_label: str | None
    template_version_label: str | None
    rate_card_version_label: str | None
    label_group_version_label: str | None
    binding_day: date | None


@dataclass(frozen=True)
class IngestResult:
    source_file: SourceFileRecord
    batch: BatchRecord
    processing_run: ProcessingRunRecord | None
    version_binding: VersionBinding | None
    replayed: bool
    staged_count: int
    rejected_count: int
    empty_row_count: int


def discover_source_workbooks(directory: Path) -> tuple[Path, ...]:
    root = Path(directory)
    return tuple(sorted(path for path in root.glob("*.xlsx") if path.is_file()))


def bind_versions_for_day(
    day: date | None,
    *,
    catalog: CatalogStore | None = None,
    client_id: str | None = None,
) -> VersionBinding:
    """Select P2 version ids for a processing_run. Does not resolve labels.

    An active uploaded Logic/Labels version for ``client_id`` replaces the
    packaged JSON axis. Draft versions are never selected. Packaged manifest
    UUIDs are stored only when they belong to ``client_id``.
    """
    manifest = load_manifest()
    ids: dict[str, str] = manifest["ids"]
    group_label = "fl1-group-v1"
    campaign_overlay = catalog.active_for(client_id, LOGIC_KIND) if catalog and client_id else None
    label_overlay = catalog.active_for(client_id, LABELS_KIND) if catalog and client_id else None
    if day is None:
        campaign_id = (
            persistable_catalog_version_id(
                client_id,
                campaign_overlay.id,
                owner_client_id=campaign_overlay.client_id,
            )
            if campaign_overlay is not None
            else None
        )
        group_id = (
            persistable_catalog_version_id(
                client_id,
                label_overlay.id,
                owner_client_id=label_overlay.client_id,
            )
            if label_overlay is not None
            else persistable_catalog_version_id(client_id, ids.get(group_label))
        )
        return VersionBinding(
            campaign_id,
            None,
            None,
            group_id,
            campaign_overlay.version_label if campaign_overlay else None,
            None,
            None,
            label_overlay.version_label if label_overlay else group_label,
            None,
        )
    if campaign_overlay is not None:
        campaign_label = campaign_overlay.version_label
        campaign_id = persistable_catalog_version_id(
            client_id,
            campaign_overlay.id,
            owner_client_id=campaign_overlay.client_id,
        )
    else:
        campaign_windows = _version_windows(campaign_versions(), load_campaign_version)
        campaign_label = select_version_for_day(campaign_windows, day)
        campaign_id = persistable_catalog_version_id(
            client_id, ids.get(campaign_label) if campaign_label else None
        )
    template_windows = _version_windows(template_versions(), load_template_version)
    template_label = select_version_for_day(template_windows, day)
    rate_label = select_version_for_day(RATE_CARD_VERSIONS, day)
    if label_overlay is not None:
        group_label = label_overlay.version_label
        group_id = persistable_catalog_version_id(
            client_id,
            label_overlay.id,
            owner_client_id=label_overlay.client_id,
        )
    else:
        group_id = persistable_catalog_version_id(client_id, ids.get(group_label))
    return VersionBinding(
        campaign_id,
        persistable_catalog_version_id(
            client_id, ids.get(template_label) if template_label else None
        ),
        persistable_catalog_version_id(client_id, ids.get(rate_label) if rate_label else None),
        group_id,
        campaign_label,
        template_label,
        rate_label,
        group_label,
        day,
    )


def _replay_successful_batch(
    store: IngestStore,
    source_file: SourceFileRecord,
    *,
    catalog: CatalogStore | None,
    client_id: str,
) -> IngestResult | None:
    existing_batch = store.successful_batch_for_file(source_file.id)
    if existing_batch is None:
        return None
    run = store.processing_run_for_batch(existing_batch.id)
    binding = bind_versions_for_day(
        existing_batch.observed_day_min, catalog=catalog, client_id=client_id
    )
    return IngestResult(
        source_file=source_file,
        batch=existing_batch,
        processing_run=run,
        version_binding=binding,
        replayed=True,
        staged_count=existing_batch.row_count_staged,
        rejected_count=existing_batch.row_count_rejected,
        empty_row_count=existing_batch.empty_row_count,
    )


def _fail_header_ingest(
    store: IngestStore,
    *,
    path: Path,
    digest: str,
    byte_size: int,
    client_id: str,
    reserved_batch: BatchRecord | None,
    exc: HeaderContractError,
    on_progress: ProgressCallback | None,
) -> IngestResult:
    if reserved_batch is None:
        source_file = store.register_source_file(
            client_id=client_id,
            sha256=digest,
            original_filename=path.name,
            byte_size=byte_size,
            source_kind="legacy_workbook",
        )
        batch = store.create_batch(source_file_id=source_file.id, client_id=client_id)
    else:
        source_file = store.get_source_file(reserved_batch.source_file_id)
        if source_file is None:
            source_file = store.register_source_file(
                client_id=client_id,
                sha256=digest,
                original_filename=path.name,
                byte_size=byte_size,
                source_kind="legacy_workbook",
            )
        batch = store.get_batch(reserved_batch.id) or reserved_batch
    store.add_rejected_row(
        batch_id=batch.id,
        source_row_number=None,
        raw=None,
        reason_code=exc.reason_code,
        reason_detail=exc.detail,
    )
    batch.status = "failed"
    batch.row_count_rejected = 1
    batch.error_summary = str(exc)
    batch.completed_at = batch.created_at
    batch.progress_stage = STAGE_FAILED
    store.save_batch(batch)
    emit_progress(on_progress, STAGE_FAILED, None, None, str(exc))
    return IngestResult(source_file, batch, None, None, False, 0, 1, 0)


def ingest_workbook(
    path: Path,
    store: IngestStore | None = None,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    force: bool = False,
    engine_version: str = ENGINE_VERSION,
    catalog: CatalogStore | None = None,
    reserved_batch: BatchRecord | None = None,
    on_progress: ProgressCallback | None = None,
    max_facts: int | None = None,
) -> IngestResult:
    """Register a workbook and stage K:BO source rows plus approved extras. No P4 transform.

    ``reserved_batch`` is an already-created ``received`` batch used by HTTP
    upload so callers can return a pollable id before inspect/staging.
    """
    path = Path(path)
    store = store or InMemoryIngestStore()
    digest = sha256_file(path)
    byte_size = path.stat().st_size

    if reserved_batch is None:
        existing_file = store.get_source_file_by_sha256(digest, client_id)
        if existing_file is not None and not force:
            replayed = _replay_successful_batch(
                store, existing_file, catalog=catalog, client_id=client_id
            )
            if replayed is not None:
                return replayed

    emit_progress(on_progress, STAGE_INGESTING, None, None, "Reading workbook...")
    try:
        workbook_cm = open_source_workbook(path)
        layout, workbook = workbook_cm.__enter__()
    except HeaderContractError as exc:
        return _fail_header_ingest(
            store,
            path=path,
            digest=digest,
            byte_size=byte_size,
            client_id=client_id,
            reserved_batch=reserved_batch,
            exc=exc,
            on_progress=on_progress,
        )

    try:
        source_kind = layout.match.source_kind
        if reserved_batch is None:
            source_file = store.register_source_file(
                client_id=client_id,
                sha256=digest,
                original_filename=path.name,
                byte_size=byte_size,
                source_kind=source_kind,
            )
            if not force:
                replayed = _replay_successful_batch(
                    store, source_file, catalog=catalog, client_id=client_id
                )
                if replayed is not None:
                    return replayed
            batch = store.create_batch(
                source_file_id=source_file.id,
                client_id=client_id,
                worksheet_name=layout.worksheet_name,
                header_row=layout.match.header_row,
                source_start_column=layout.match.start_column_letter,
            )
        else:
            source_file = store.get_source_file(reserved_batch.source_file_id)
            if source_file is None:
                source_file = store.register_source_file(
                    client_id=client_id,
                    sha256=digest,
                    original_filename=path.name,
                    byte_size=byte_size,
                    source_kind=source_kind,
                )
            batch = store.get_batch(reserved_batch.id) or reserved_batch
            batch.worksheet_name = layout.worksheet_name
            batch.header_row = layout.match.header_row
            batch.source_start_column = layout.match.start_column_letter
            store.save_batch(batch)
            if source_file.source_kind != source_kind:
                source_file.source_kind = source_kind

        row_hint = worksheet_data_row_hint(workbook, layout)
        if row_hint:
            emit_progress(
                on_progress,
                STAGE_INGESTING,
                None,
                row_hint,
                f"{row_hint} rows discovered",
            )
        else:
            emit_progress(on_progress, STAGE_INGESTING, None, None, "Reading workbook...")

        staged = 0
        rejected = 0
        empty = 0
        days: list[date] = []
        pending: list[StagedRowRecord] = []
        headers = expected_source_headers()
        extra_headers = layout.match.extra_headers

        def flush_pending() -> None:
            if not pending:
                return
            store.add_staged_rows(pending)
            pending.clear()

        emit_progress(on_progress, STAGE_STAGING, 0, row_hint, "Staging rows...")
        try:
            for row in iter_source_rows_from_workbook(workbook, layout):
                if row.is_empty:
                    empty += 1
                    continue
                extras = tuple(key for key in row.raw if key not in headers)
                if tuple(row.raw)[:57] != headers or extras != extra_headers:
                    store.add_rejected_row(
                        batch_id=batch.id,
                        source_row_number=row.source_row_number,
                        raw=row.raw,
                        reason_code="IMPOSSIBLE_ROW",
                        reason_detail=(
                            "source payload is not the locked 57-header mapping plus approved extras"
                        ),
                    )
                    rejected += 1
                    continue
                fact_count = staged + rejected + 1
                if max_facts is not None and max_facts > 0 and fact_count > max_facts:
                    flush_pending()
                    raise WorkbookCapacityError(max_facts, fact_count)
                pending.append(
                    StagedRowRecord(
                        id=str(uuid4()),
                        batch_id=batch.id,
                        source_row_number=row.source_row_number,
                        raw=row.raw,
                        campaign_id=row.campaign_id,
                        variation_id=row.variation_id,
                        day=row.day,
                    )
                )
                staged += 1
                if row.day is not None:
                    days.append(row.day)
                if len(pending) >= STAGING_CHUNK_SIZE:
                    emit_progress(
                        on_progress,
                        STAGE_STAGING,
                        staged,
                        row_hint,
                        "Staging rows...",
                    )
                    flush_pending()
            emit_progress(
                on_progress,
                STAGE_STAGING,
                staged,
                row_hint,
                "Staging rows...",
            )
            flush_pending()
        except ProcessingCancelled:
            raise
        except WorkbookCapacityError as exc:
            batch.status = "failed"
            batch.row_count_staged = staged
            batch.row_count_rejected = rejected
            batch.empty_row_count = empty
            batch.row_count_declared = staged + rejected
            batch.error_summary = str(exc)
            batch.completed_at = datetime.now(tz=UTC)
            batch.progress_stage = STAGE_FAILED
            store.save_batch(batch)
            emit_progress(on_progress, STAGE_FAILED, staged + rejected, max_facts, str(exc))
            return IngestResult(source_file, batch, None, None, False, staged, rejected, empty)

        total_seen = staged + rejected + empty
        emit_progress(on_progress, STAGE_STAGING, staged, total_seen or staged, "Staging rows...")
        batch.row_count_staged = staged
        batch.row_count_rejected = rejected
        batch.empty_row_count = empty
        batch.row_count_declared = staged + rejected
        if days:
            batch.observed_day_min = min(days)
            batch.observed_day_max = max(days)
        batch.status = "staged"
        if staged == 0 and rejected > 0:
            batch.status = "failed"
            batch.error_summary = "all data rows were rejected"
        batch.completed_at = datetime.now(tz=UTC)
        store.save_batch(batch)

        binding = bind_versions_for_day(
            batch.observed_day_min, catalog=catalog, client_id=client_id
        )
        run = None
        if batch.status == "staged":
            run = store.create_processing_run(
                batch_id=batch.id,
                campaign_label_version_id=binding.campaign_label_version_id,
                template_label_version_id=binding.template_label_version_id,
                rate_card_version_id=binding.rate_card_version_id,
                label_group_version_id=binding.label_group_version_id,
                engine_version=engine_version,
            )
        return IngestResult(
            source_file=source_file,
            batch=batch,
            processing_run=run,
            version_binding=binding,
            replayed=False,
            staged_count=staged,
            rejected_count=rejected,
            empty_row_count=empty,
        )
    finally:
        workbook_cm.__exit__(None, None, None)
