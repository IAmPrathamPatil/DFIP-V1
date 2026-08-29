"""Explicit request/response models for the P5 read-only API.

Field names follow the existing P1/P3/P4 records. Decimal values are serialized
as strings. NULL and empty string are distinct and are never rewritten.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
PublicationFactScope = Literal["processing_run", "client_current"]


class PaginationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int
    offset: int
    total: int


class HealthDatabase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    status: Literal["not_checked"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    application: Literal["dfip-api"]
    environment: str
    database: HealthDatabase


class SessionResponse(BaseModel):
    """Authenticated identity foundation for later authorization (P6)."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    auth_mode: str
    role: str
    client_id: str | None


class LoginRequest(BaseModel):
    """POST /auth/login body. Username maps to app_user.subject."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    client_id: UUID | None = None


class LoginResponse(BaseModel):
    """Issued access JWT plus the session principal. Never log access_token."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"]
    expires_in: int
    session: SessionResponse


class SourceFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file_id: str
    client_id: str
    original_filename: str
    sha256: str
    byte_size: int
    source_kind: str
    uploaded_at: datetime


class BatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    source_file_id: str
    client_id: str
    status: str
    worksheet_name: str | None
    header_row: int | None
    source_start_column: str | None
    row_count_declared: int | None
    row_count_staged: int
    row_count_rejected: int
    empty_row_count: int
    observed_day_min: date | None
    observed_day_max: date | None
    created_at: datetime
    completed_at: datetime | None
    error_summary: str | None


class ProcessingRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_run_id: str
    batch_id: str
    status: str
    campaign_label_version_id: str | None
    template_label_version_id: str | None
    rate_card_version_id: str | None
    label_group_version_id: str | None
    engine_version: str | None
    started_at: datetime
    finished_at: datetime | None
    qa_verdict: str | None
    error_summary: str | None = None
    progress_at: datetime | None = None


class StagedRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged_row_id: str
    batch_id: str
    source_row_number: int
    campaign_id: str | None
    variation_id: str | None
    day: date | None
    raw: dict[str, Any]


class FactResponse(BaseModel):
    """One `fact_campaign_day` row. Decimal fields are JSON strings."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    campaign_id: str
    variation_id: str | None
    variation_id_key: str
    day: date
    month_start: date | None
    month_label: str | None
    campaign_name: str | None
    variation_name: str | None
    channel: str | None
    type_of_campaign: str | None
    start_date: datetime | None
    template_name_whatsapp: str | None
    sent: int | None
    failed: int | None
    delivered: int | None
    unique_impressions: int | None
    unique_clicks: int | None
    unique_conversions: int | None
    unique_impression_through_conversions: int | None
    unique_click_through_conversions: int | None
    revenue_inr: str | None
    impression_through_revenue_inr: str | None
    click_through_revenue_inr: str | None
    filter_logic_1: str | None
    filter_logic_2: str | None
    template_status: str | None
    amc_status_filter_logic_3: str | None
    amc_device_category_filter_logic_4: str | None
    amc_product_cat_filter_logic_5: str | None
    manual_or_automated: str | None
    total_cost: str | None
    hhh: str | None
    filter_logic_1_group: str | None
    label_match_status: str | None
    template_match_status: str | None
    rate_card_rule_id: str | None
    processing_run_id: str | None
    batch_id: str | None
    campaign_label_version_id: str | None
    template_label_version_id: str | None
    rate_card_version_id: str | None
    label_group_version_id: str | None
    first_seen_at: datetime
    last_seen_at: datetime


class FactHistoryResponse(FactResponse):
    """One `fact_campaign_day_history` row. Same columns as P1 plus supersede."""

    superseded_at: datetime
    superseded_by_run_id: str | None


class SourceFilePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SourceFileResponse]
    pagination: PaginationMeta


class BatchPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchResponse]
    pagination: PaginationMeta


class ProcessingRunPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProcessingRunResponse]
    pagination: PaginationMeta


class QaFindingResponse(BaseModel):
    """One inspector-only qa_finding row. Not a published fact."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    client_id: str
    processing_run_id: str
    batch_id: str | None
    rule_id: str
    severity: str
    status: str
    entity_type: str
    entity_key: str
    message: str
    diagnostics: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QaFindingSummary(BaseModel):
    """Run-level QA finding counts. Derived from persisted qa_finding rows."""

    model_config = ConfigDict(extra="forbid")

    total: int
    error: int
    warning: int
    info: int
    by_rule: dict[str, int]


class QaFindingPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[QaFindingResponse]
    pagination: PaginationMeta
    summary: QaFindingSummary


class StagedRowPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StagedRowResponse]
    pagination: PaginationMeta


class FactPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FactResponse]
    pagination: PaginationMeta


class FactHistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FactHistoryResponse]
    pagination: PaginationMeta


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


class PaginationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    offset: int = Field(default=0, ge=0)


class PublicationCreateRequest(BaseModel):
    """POST /publications body. P1 publication columns plus required keys."""

    model_config = ConfigDict(extra="forbid")

    client_id: UUID
    processing_run_id: UUID
    period_start: date | None = None
    period_end: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    fact_scope: PublicationFactScope = "processing_run"


class PublicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: str
    client_id: str
    processing_run_id: str
    period_start: date | None
    period_end: date | None
    published_at: datetime
    published_by: str | None
    notes: str | None
    fact_scope: PublicationFactScope
    snapshot_status: str = "none"
    snapshot_row_count: int | None = None


class PublicationPage(BaseModel):
    """GET /publications. Prior rows remain after the current pointer moves."""

    model_config = ConfigDict(extra="forbid")

    items: list[PublicationResponse]
    pagination: PaginationMeta


class PublicationCurrentPointer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str
    publication_id: str
    updated_at: datetime


class PublicationStateResponse(BaseModel):
    """Created publication plus the current pointer for that client."""

    model_config = ConfigDict(extra="forbid")

    publication: PublicationResponse
    current: PublicationCurrentPointer


class PublicationCurrentResponse(BaseModel):
    """GET /publications/current. Both fields are null when nothing is published."""

    model_config = ConfigDict(extra="forbid")

    publication: PublicationResponse | None = None
    current: PublicationCurrentPointer | None = None


class UploadRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_row_number: int | None
    reason_code: str
    reason_detail: str | None


class UploadTransformSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transformed: int
    inserted: int
    restated: int
    unchanged: int
    rejected: int
    status: str


class ReprocessResponse(BaseModel):
    """POST /batches/{batch_id}/process. Never publishes."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    processing_run: ProcessingRunResponse
    logic_version_label: str | None = None
    labels_version_label: str | None = None
    published: Literal[False] = False
    accepted: bool = True
    transform: UploadTransformSummary | None = None


class UploadResponse(BaseModel):
    """POST /uploads result. Publication is never performed by this endpoint."""

    model_config = ConfigDict(extra="forbid")

    source_file_id: str
    original_filename: str
    sha256: str
    byte_size: int
    client_id: str
    replayed: bool
    published: Literal[False] = False
    batch: BatchResponse
    processing_run: ProcessingRunResponse | None = None
    transform: UploadTransformSummary | None = None
    rejections: list[UploadRejection] = Field(default_factory=list)
    accepted: bool = True


class UploadGroupResponse(BaseModel):
    """POST /uploads result when more than one workbook is accepted.

    Each item is a normal single-file ``UploadResponse``. Files are processed
    independently through the existing ingest/transform pipeline. Publication
    is never performed by this endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str
    file_count: int
    replayed: bool
    published: Literal[False] = False
    accepted: bool = True
    duration_ms: int | None = None
    staged_row_count: int = 0
    fact_count: int = 0
    items: list[UploadResponse]


class CatalogValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int | None = None
    code: str
    detail: str


class CatalogVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    client_id: str
    kind: Literal["logic", "labels"]
    version_label: str
    status: Literal["draft", "active", "superseded"]
    origin: Literal["uploaded", "packaged"]
    row_count: int
    distinct_key_count: int
    duplicate_key_count: int
    created_at: datetime | None = None
    created_by: str | None = None
    source_filename: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    validation_status: Literal["valid", "invalid", "packaged"] = "valid"


class CatalogImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: CatalogVersionResponse
    validation_status: Literal["valid"] = "valid"
    errors: list[CatalogValidationIssue] = Field(default_factory=list)


class CatalogListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["logic", "labels"]
    client_id: str
    processing_active: CatalogVersionResponse | None = None
    packaged_fallback: CatalogVersionResponse
    items: list[CatalogVersionResponse]
