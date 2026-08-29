"""Read-only service: maps existing P3/P4 records onto API schemas.

No P2 resolver or P4 transform logic lives here.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from dfip_core.ingest.store import (
    BatchRecord,
    ProcessingRunRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import SupersededFact

from dfip_api.errors import NotFoundError
from dfip_api.ports import ReadRepository
from dfip_api.qa_store import QaFindingStore
from dfip_api.schemas import (
    BatchPage,
    BatchResponse,
    FactHistoryPage,
    FactHistoryResponse,
    FactPage,
    FactResponse,
    PaginationMeta,
    ProcessingRunPage,
    ProcessingRunResponse,
    QaFindingPage,
    QaFindingResponse,
    QaFindingSummary,
    SourceFilePage,
    SourceFileResponse,
    StagedRowPage,
    StagedRowResponse,
)


def decimal_to_api(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def json_safe(value: Any) -> Any:
    """Encode staging payload values for JSON without rewriting NULL or ``""``."""
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    raise TypeError("unsupported staging value type")


def source_file_to_response(record: SourceFileRecord) -> SourceFileResponse:
    return SourceFileResponse(
        source_file_id=record.id,
        client_id=record.client_id,
        original_filename=record.original_filename,
        sha256=record.sha256,
        byte_size=record.byte_size,
        source_kind=record.source_kind,
        uploaded_at=record.uploaded_at,
    )


def batch_to_response(record: BatchRecord) -> BatchResponse:
    return BatchResponse(
        batch_id=record.id,
        source_file_id=record.source_file_id,
        client_id=record.client_id,
        status=record.status,
        worksheet_name=record.worksheet_name,
        header_row=record.header_row,
        source_start_column=record.source_start_column,
        row_count_declared=record.row_count_declared,
        row_count_staged=record.row_count_staged,
        row_count_rejected=record.row_count_rejected,
        empty_row_count=record.empty_row_count,
        observed_day_min=record.observed_day_min,
        observed_day_max=record.observed_day_max,
        created_at=record.created_at,
        completed_at=record.completed_at,
        error_summary=record.error_summary,
    )


def processing_run_to_response(record: ProcessingRunRecord) -> ProcessingRunResponse:
    return ProcessingRunResponse(
        processing_run_id=record.id,
        batch_id=record.batch_id,
        status=record.status,
        campaign_label_version_id=record.campaign_label_version_id,
        template_label_version_id=record.template_label_version_id,
        rate_card_version_id=record.rate_card_version_id,
        label_group_version_id=record.label_group_version_id,
        engine_version=record.engine_version,
        started_at=record.started_at,
        finished_at=record.finished_at,
        qa_verdict=record.qa_verdict,
        error_summary=record.error_summary,
        progress_at=record.progress_at,
    )


def _qa_finding_summary(rows: list[dict[str, Any]]) -> QaFindingSummary:
    error = warning = info = 0
    by_rule: dict[str, int] = {}
    for row in rows:
        severity = row.get("severity")
        if severity == "error":
            error += 1
        elif severity == "warning":
            warning += 1
        elif severity == "info":
            info += 1
        rule_id = str(row.get("rule_id") or "")
        if rule_id:
            by_rule[rule_id] = by_rule.get(rule_id, 0) + 1
    return QaFindingSummary(
        total=len(rows),
        error=error,
        warning=warning,
        info=info,
        by_rule=by_rule,
    )


def qa_finding_to_response(row: dict[str, Any]) -> QaFindingResponse:
    diagnostics = row.get("diagnostics")
    if diagnostics is None:
        diagnostics = {}
    elif not isinstance(diagnostics, dict):
        diagnostics = dict(diagnostics)
    return QaFindingResponse(
        finding_id=str(row["finding_id"]),
        client_id=str(row["client_id"]),
        processing_run_id=str(row["processing_run_id"]),
        batch_id=None if row.get("batch_id") is None else str(row["batch_id"]),
        rule_id=str(row["rule_id"]),
        severity=str(row["severity"]),
        status=str(row["status"]),
        entity_type=str(row["entity_type"]),
        entity_key=str(row["entity_key"]),
        message=str(row["message"]),
        diagnostics=diagnostics,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def staged_row_to_response(record: StagedRowRecord) -> StagedRowResponse:
    return StagedRowResponse(
        staged_row_id=record.id,
        batch_id=record.batch_id,
        source_row_number=record.source_row_number,
        campaign_id=record.campaign_id,
        variation_id=record.variation_id,
        day=record.day,
        raw=json_safe(record.raw),
    )


def fact_to_response(record: FactRecord) -> FactResponse:
    return FactResponse(
        client_id=record.client_id,
        campaign_id=record.campaign_id,
        variation_id=record.variation_id,
        variation_id_key=record.variation_id_key,
        day=record.day,
        month_start=record.month_start,
        month_label=record.month_label,
        campaign_name=record.campaign_name,
        variation_name=record.variation_name,
        channel=record.channel,
        type_of_campaign=record.type_of_campaign,
        start_date=record.start_date,
        template_name_whatsapp=record.template_name_whatsapp,
        sent=record.sent,
        failed=record.failed,
        delivered=record.delivered,
        unique_impressions=record.unique_impressions,
        unique_clicks=record.unique_clicks,
        unique_conversions=record.unique_conversions,
        unique_impression_through_conversions=record.unique_impression_through_conversions,
        unique_click_through_conversions=record.unique_click_through_conversions,
        revenue_inr=decimal_to_api(record.revenue_inr),
        impression_through_revenue_inr=decimal_to_api(record.impression_through_revenue_inr),
        click_through_revenue_inr=decimal_to_api(record.click_through_revenue_inr),
        filter_logic_1=record.filter_logic_1,
        filter_logic_2=record.filter_logic_2,
        template_status=record.template_status,
        amc_status_filter_logic_3=record.amc_status_filter_logic_3,
        amc_device_category_filter_logic_4=record.amc_device_category_filter_logic_4,
        amc_product_cat_filter_logic_5=record.amc_product_cat_filter_logic_5,
        manual_or_automated=record.manual_or_automated,
        total_cost=decimal_to_api(record.total_cost),
        hhh=record.hhh,
        filter_logic_1_group=record.filter_logic_1_group,
        label_match_status=record.label_match_status,
        template_match_status=record.template_match_status,
        rate_card_rule_id=record.rate_card_rule_id,
        processing_run_id=record.processing_run_id,
        batch_id=record.batch_id,
        campaign_label_version_id=record.campaign_label_version_id,
        template_label_version_id=record.template_label_version_id,
        rate_card_version_id=record.rate_card_version_id,
        label_group_version_id=record.label_group_version_id,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
    )


def history_to_response(record: SupersededFact) -> FactHistoryResponse:
    base = fact_to_response(record.fact)
    return FactHistoryResponse(
        **base.model_dump(),
        superseded_at=record.superseded_at,
        superseded_by_run_id=record.superseded_by_run_id,
    )


class ReadService:
    def __init__(self, repository: ReadRepository) -> None:
        self._repository = repository

    def list_source_files(
        self,
        *,
        client_id: str | None,
        limit: int,
        offset: int,
        client_ids: tuple[str, ...] | None = None,
    ) -> SourceFilePage:
        items, total = self._repository.list_source_files(
            client_id=client_id, client_ids=client_ids, limit=limit, offset=offset
        )
        return SourceFilePage(
            items=[source_file_to_response(item) for item in items],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def get_source_file(
        self, source_file_id: str, *, client_ids: tuple[str, ...] | None = None
    ) -> SourceFileResponse:
        record = self._repository.get_source_file(source_file_id)
        if client_ids is not None and record.client_id not in client_ids:
            raise NotFoundError("Source file not found.")
        return source_file_to_response(record)

    def list_batches(
        self,
        *,
        source_file_id: str | None,
        client_id: str | None,
        status: str | None,
        limit: int,
        offset: int,
        client_ids: tuple[str, ...] | None = None,
    ) -> BatchPage:
        items, total = self._repository.list_batches(
            source_file_id=source_file_id,
            client_id=client_id,
            client_ids=client_ids,
            status=status,
            limit=limit,
            offset=offset,
        )
        return BatchPage(
            items=[batch_to_response(item) for item in items],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def get_batch(
        self, batch_id: str, *, client_ids: tuple[str, ...] | None = None
    ) -> BatchResponse:
        record = self._repository.get_batch(batch_id)
        if client_ids is not None and record.client_id not in client_ids:
            raise NotFoundError("Batch not found.")
        return batch_to_response(record)

    def list_processing_runs(
        self,
        *,
        batch_id: str | None,
        status: str | None,
        limit: int,
        offset: int,
        client_ids: tuple[str, ...] | None = None,
    ) -> ProcessingRunPage:
        items, total = self._repository.list_processing_runs(
            batch_id=batch_id, client_ids=client_ids, status=status, limit=limit, offset=offset
        )
        return ProcessingRunPage(
            items=[processing_run_to_response(item) for item in items],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def get_processing_run(
        self, processing_run_id: str, *, client_ids: tuple[str, ...] | None = None
    ) -> ProcessingRunResponse:
        record = self._repository.get_processing_run(processing_run_id)
        if client_ids is not None:
            client_id = record.client_id
            if client_id is None:
                client_id = self._repository.get_batch(record.batch_id).client_id
            if client_id not in client_ids:
                raise NotFoundError("Processing run not found.")
        return processing_run_to_response(record)

    def list_qa_findings(
        self,
        *,
        processing_run_id: str,
        qa_store: QaFindingStore,
        client_ids: tuple[str, ...] | None = None,
        limit: int,
        offset: int,
    ) -> QaFindingPage:
        self.get_processing_run(processing_run_id, client_ids=client_ids)
        rows = qa_store.list_for_run(processing_run_id)
        if client_ids is not None:
            allowed = set(client_ids)
            rows = [item for item in rows if item.get("client_id") in allowed]
        total = len(rows)
        page = rows[offset : offset + limit]
        return QaFindingPage(
            items=[qa_finding_to_response(item) for item in page],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
            summary=_qa_finding_summary(rows),
        )

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
        client_ids: tuple[str, ...] | None = None,
    ) -> StagedRowPage:
        if client_ids is not None:
            batch = self._repository.get_batch(batch_id)
            if batch.client_id not in client_ids:
                raise NotFoundError("Batch not found.")
        items, total = self._repository.list_staged_rows(
            batch_id=batch_id,
            source_row_number=source_row_number,
            campaign_id=campaign_id,
            variation_id=variation_id,
            day=day,
            limit=limit,
            offset=offset,
        )
        return StagedRowPage(
            items=[staged_row_to_response(item) for item in items],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def list_facts(
        self,
        *,
        client_id: str | None,
        batch_id: str | None,
        processing_run_id: str | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
        client_ids: tuple[str, ...] | None = None,
    ) -> FactPage:
        items, total = self._repository.list_facts(
            client_id=client_id,
            client_ids=client_ids,
            batch_id=batch_id,
            processing_run_id=processing_run_id,
            campaign_id=campaign_id,
            variation_id=variation_id,
            day=day,
            limit=limit,
            offset=offset,
        )
        return FactPage(
            items=[fact_to_response(item) for item in items],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def list_fact_history(
        self,
        *,
        client_id: str | None,
        batch_id: str | None,
        processing_run_id: str | None,
        campaign_id: str | None,
        variation_id: str | None,
        day: date | None,
        limit: int,
        offset: int,
        client_ids: tuple[str, ...] | None = None,
    ) -> FactHistoryPage:
        items, total = self._repository.list_fact_history(
            client_id=client_id,
            client_ids=client_ids,
            batch_id=batch_id,
            processing_run_id=processing_run_id,
            campaign_id=campaign_id,
            variation_id=variation_id,
            day=day,
            limit=limit,
            offset=offset,
        )
        return FactHistoryPage(
            items=[history_to_response(item) for item in items],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )
