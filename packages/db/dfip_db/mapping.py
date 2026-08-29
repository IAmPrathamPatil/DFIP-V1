"""Map PostgreSQL rows onto V1 record types without changing semantics."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from dfip_core.ingest.store import (
    BatchRecord,
    ProcessingRunRecord,
    RejectedRowRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import SupersededFact

FACT_COLUMNS: tuple[str, ...] = (
    "client_id",
    "campaign_id",
    "variation_id",
    "variation_id_key",
    "day",
    "month_start",
    "month_label",
    "campaign_name",
    "variation_name",
    "channel",
    "type_of_campaign",
    "start_date",
    "template_name_whatsapp",
    "sent",
    "failed",
    "delivered",
    "unique_impressions",
    "unique_clicks",
    "unique_conversions",
    "unique_impression_through_conversions",
    "unique_click_through_conversions",
    "revenue_inr",
    "impression_through_revenue_inr",
    "click_through_revenue_inr",
    "filter_logic_1",
    "filter_logic_2",
    "template_status",
    "amc_status_filter_logic_3",
    "amc_device_category_filter_logic_4",
    "amc_product_cat_filter_logic_5",
    "manual_or_automated",
    "total_cost",
    "hhh",
    "filter_logic_1_group",
    "label_match_status",
    "template_match_status",
    "rate_card_rule_id",
    "processing_run_id",
    "batch_id",
    "campaign_label_version_id",
    "template_label_version_id",
    "rate_card_version_id",
    "label_group_version_id",
    "first_seen_at",
    "last_seen_at",
)

FACT_SELECT = ", ".join(FACT_COLUMNS)


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value
    return str(value)


def as_uuid_text(value: Any) -> str:
    text = as_text(value)
    if text is None:
        raise ValueError("expected uuid")
    return text


def as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    raise TypeError("expected datetime")


def as_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return as_datetime(value)


def as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError("expected date")


def as_optional_date(value: Any) -> date | None:
    if value is None:
        return None
    return as_date(value)


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def source_file_from_row(row: dict[str, Any]) -> SourceFileRecord:
    sha = row["sha256"]
    if isinstance(sha, str):
        sha = sha.rstrip()
    return SourceFileRecord(
        id=as_uuid_text(row["id"]),
        client_id=as_uuid_text(row["client_id"]),
        sha256=str(sha),
        original_filename=row["original_filename"],
        byte_size=int(row["byte_size"] or 0),
        source_kind=row["source_kind"],
        uploaded_at=as_datetime(row["uploaded_at"]),
        storage_uri=row.get("storage_uri"),
    )


def batch_from_row(row: dict[str, Any]) -> BatchRecord:
    return BatchRecord(
        id=as_uuid_text(row["id"]),
        source_file_id=as_uuid_text(row["source_file_id"]),
        client_id=as_uuid_text(row["client_id"]),
        status=row["status"],
        row_count_declared=as_int(row["row_count_declared"]),
        row_count_staged=int(row["row_count_staged"] or 0),
        row_count_rejected=int(row["row_count_rejected"] or 0),
        observed_day_min=as_optional_date(row["observed_day_min"]),
        observed_day_max=as_optional_date(row["observed_day_max"]),
        created_at=as_datetime(row["created_at"]),
        completed_at=as_optional_datetime(row["completed_at"]),
        error_summary=row["error_summary"],
        worksheet_name=row.get("worksheet_name"),
        header_row=as_int(row.get("header_row")),
        source_start_column=row.get("source_start_column"),
        empty_row_count=int(row["empty_row_count"] or 0),
    )


def staged_from_row(row: dict[str, Any]) -> StagedRowRecord:
    raw = row["raw"]
    if not isinstance(raw, dict):
        raw = dict(raw or {})
    return StagedRowRecord(
        id=as_uuid_text(row["id"]),
        batch_id=as_uuid_text(row["batch_id"]),
        source_row_number=int(row["source_row_number"]),
        raw=raw,
        campaign_id=row["campaign_id"],
        variation_id=row["variation_id"],
        day=as_optional_date(row["day"]),
    )


def rejected_from_row(row: dict[str, Any]) -> RejectedRowRecord:
    raw = row["raw"]
    if raw is not None and not isinstance(raw, dict):
        raw = dict(raw)
    return RejectedRowRecord(
        id=as_uuid_text(row["id"]),
        batch_id=as_uuid_text(row["batch_id"]),
        source_row_number=as_int(row["source_row_number"]),
        raw=raw,
        reason_code=row["reason_code"],
        reason_detail=row["reason_detail"],
        rejected_at=as_datetime(row["rejected_at"]),
    )


def processing_run_from_row(row: dict[str, Any]) -> ProcessingRunRecord:
    client_id = row.get("client_id")
    return ProcessingRunRecord(
        id=as_uuid_text(row["id"]),
        batch_id=as_uuid_text(row["batch_id"]),
        campaign_label_version_id=as_text(row["campaign_label_version_id"]),
        template_label_version_id=as_text(row["template_label_version_id"]),
        rate_card_version_id=as_text(row["rate_card_version_id"]),
        label_group_version_id=as_text(row["label_group_version_id"]),
        engine_version=row["engine_version"],
        started_at=as_datetime(row["started_at"]),
        finished_at=as_optional_datetime(row["finished_at"]),
        status=row["status"],
        qa_verdict=row["qa_verdict"],
        client_id=as_text(client_id),
        error_summary=row.get("error_summary"),
        progress_at=as_optional_datetime(row.get("progress_at")),
    )


def fact_from_row(row: dict[str, Any]) -> FactRecord:
    return FactRecord(
        client_id=as_uuid_text(row["client_id"]),
        campaign_id=row["campaign_id"],
        variation_id=row["variation_id"],
        variation_id_key=row["variation_id_key"],
        day=as_date(row["day"]),
        month_start=as_optional_date(row["month_start"]),
        month_label=row["month_label"],
        campaign_name=row["campaign_name"],
        variation_name=row["variation_name"],
        channel=row["channel"],
        type_of_campaign=row["type_of_campaign"],
        start_date=as_optional_datetime(row["start_date"]),
        template_name_whatsapp=row["template_name_whatsapp"],
        sent=as_int(row["sent"]),
        failed=as_int(row["failed"]),
        delivered=as_int(row["delivered"]),
        unique_impressions=as_int(row["unique_impressions"]),
        unique_clicks=as_int(row["unique_clicks"]),
        unique_conversions=as_int(row["unique_conversions"]),
        unique_impression_through_conversions=as_int(row["unique_impression_through_conversions"]),
        unique_click_through_conversions=as_int(row["unique_click_through_conversions"]),
        revenue_inr=as_decimal(row["revenue_inr"]),
        impression_through_revenue_inr=as_decimal(row["impression_through_revenue_inr"]),
        click_through_revenue_inr=as_decimal(row["click_through_revenue_inr"]),
        filter_logic_1=row["filter_logic_1"],
        filter_logic_2=row["filter_logic_2"],
        template_status=row["template_status"],
        amc_status_filter_logic_3=row["amc_status_filter_logic_3"],
        amc_device_category_filter_logic_4=row["amc_device_category_filter_logic_4"],
        amc_product_cat_filter_logic_5=row["amc_product_cat_filter_logic_5"],
        manual_or_automated=row["manual_or_automated"],
        total_cost=as_decimal(row["total_cost"]),
        hhh=row["hhh"],
        filter_logic_1_group=row["filter_logic_1_group"],
        label_match_status=row["label_match_status"],
        template_match_status=row["template_match_status"],
        rate_card_rule_id=as_text(row["rate_card_rule_id"]),
        processing_run_id=as_text(row["processing_run_id"]),
        batch_id=as_text(row["batch_id"]),
        campaign_label_version_id=as_text(row["campaign_label_version_id"]),
        template_label_version_id=as_text(row["template_label_version_id"]),
        rate_card_version_id=as_text(row["rate_card_version_id"]),
        label_group_version_id=as_text(row["label_group_version_id"]),
        first_seen_at=as_datetime(row["first_seen_at"]),
        last_seen_at=as_datetime(row["last_seen_at"]),
    )


def fact_values(record: FactRecord) -> tuple[Any, ...]:
    return tuple(getattr(record, name) for name in FACT_COLUMNS)


def history_from_row(row: dict[str, Any]) -> SupersededFact:
    return SupersededFact(
        fact=fact_from_row(row),
        superseded_at=as_datetime(row["superseded_at"]),
        superseded_by_run_id=as_text(row["superseded_by_run_id"]),
    )
