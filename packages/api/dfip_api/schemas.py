"""Explicit request/response models for the P5 read-only API.

Field names follow the existing P1/P3/P4 records. Decimal values are serialized
as strings. NULL and empty string are distinct and are never rewritten.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
# Excel Refresh All pages this endpoint sequentially. 200-row pages made a
# ~230k-row company ~1,150 HTTP round trips. 250,000 fits current cumulative
# tenants in one request so Power Query does one Json.Document. Other JSON
# list routes stay at MAX_PAGE_LIMIT = 200.
HISTORY_FACTS_MAX_PAGE_LIMIT = 250000
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


class ReadyDatabase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]


class ReadyStorage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable", "not_configured"]


class ReadyBackup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["configured", "not_configured"]
    manifest_created_at: str | None = None
    manifest_age_seconds: int | None = None


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    application: Literal["dfip-api"]
    database: ReadyDatabase
    storage: ReadyStorage
    worker: Literal["idle", "busy"]
    backup: ReadyBackup


class SessionClient(BaseModel):
    """One authorized membership. Names come from the client directory when present."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    role: str
    code: str | None = None
    name: str | None = None
    lifecycle_status: str = "active"
    deactivated_at: datetime | None = None
    purge_eligible_after: datetime | None = None


class SessionResponse(BaseModel):
    """Authenticated identity foundation for later authorization (P6)."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    auth_mode: str
    role: str
    client_id: str | None
    clients: list[SessionClient] = Field(default_factory=list)


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


class SelectClientRequest(BaseModel):
    """POST /auth/select-client body. Must be an inspector membership of the caller."""

    model_config = ConfigDict(extra="forbid")

    client_id: UUID


class PublisherSetupStatusResponse(BaseModel):
    """GET /auth/setup-status. True only when no publisher/admin identity exists."""

    model_config = ConfigDict(extra="forbid")

    publisher_setup_required: bool


class PublisherSetupRequest(BaseModel):
    """POST /auth/setup-publisher. One-time operator-chosen publisher login."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    confirm_password: str = Field(min_length=12, max_length=1024)

    @model_validator(mode="after")
    def passwords_match(self) -> PublisherSetupRequest:
        if self.password != self.confirm_password:
            raise ValueError("password confirmation does not match.")
        return self


class PublisherSetupResponse(BaseModel):
    """Created publisher identity. Password is never returned."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    role: Literal["publisher"]


class ClientResponse(BaseModel):
    """One company in the publisher registry. client_id is immutable."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    code: str
    name: str
    lifecycle_status: Literal["active", "inactive"] = "active"
    deactivated_at: datetime | None = None
    purge_eligible_after: datetime | None = None


class ClientPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ClientResponse]


class ClientRenameRequest(BaseModel):
    """POST /clients/{client_id}/rename body. Display name only."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ClientCreateRequest(BaseModel):
    """POST /clients body. Creates a new tenant; does not rename."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ClientUserCreateRequest(BaseModel):
    """POST /clients/{client_id}/users. Operator-chosen client login only."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    confirm_password: str = Field(min_length=12, max_length=1024)
    role: Literal["client"] = "client"

    @model_validator(mode="after")
    def passwords_match(self) -> ClientUserCreateRequest:
        if self.password != self.confirm_password:
            raise ValueError("password confirmation does not match.")
        return self


class ClientUserResponse(BaseModel):
    """Created client login. Password is never returned."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    client_id: str
    role: Literal["client"]


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
    stage: str | None = None
    stuck: bool = False
    progress_at: datetime | None = None
    started_at: datetime | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    progress_message: str | None = None
    progress_percent: int | None = None


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
    stage: str | None = None
    stuck: bool = False


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
    """One `fact_campaign_day` row. Decimal fields are JSON strings.

    Field order matches FACT_VALUE_FIELDS / the client workbook header contract
    so JSON object keys stay aligned with queryTableFields after Refresh All.
    """

    model_config = ConfigDict(extra="forbid")

    filter_logic_1: str | None
    filter_logic_2: str | None
    template_status: str | None
    amc_status_filter_logic_3: str | None
    amc_device_category_filter_logic_4: str | None
    amc_product_cat_filter_logic_5: str | None
    manual_or_automated: str | None
    total_cost: str | None
    hhh: str | None
    month_label: str | None
    day: date
    campaign_name: str | None
    campaign_id: str
    variation_name: str | None
    variation_id: str | None
    channel: str | None
    type_of_campaign: str | None
    start_date: datetime | None
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
    template_name_whatsapp: str | None
    client_id: str
    variation_id_key: str
    month_start: date | None
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


FACT_TABLE_COLUMNS: tuple[str, ...] = tuple(FactResponse.model_fields)


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


class FactTablePage(BaseModel):
    """Compact history/facts page for Excel Refresh All.

    Same tenant snapshot as FactPage. Columns are FACT_TABLE_COLUMNS.
    Rows are positional values, so JSON keys are not repeated per fact.
    """

    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[list[Any]]
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


class HistoryFactsPaginationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=HISTORY_FACTS_MAX_PAGE_LIMIT)
    offset: int = Field(default=0, ge=0)


class ClientDeleteResponse(BaseModel):
    """DELETE /clients/{client_id}. Idempotent: already-absent is still deleted."""

    model_config = ConfigDict(extra="forbid")

    deleted: bool = True
    client_id: str
    already_absent: bool = False


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


class PublicationProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_run_id: str
    client_id: str
    stage: str
    status: str
    message: str
    current_count: int | None = None
    total_count: int | None = None
    progress_percent: int | None = None
    publication_id: str | None = None
    error_summary: str | None = None


class BatchDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    deleted: bool


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


class OverviewPeriod(BaseModel):
    """Inclusive published-history window. Month grain uses `month_start` from Day."""

    model_config = ConfigDict(extra="forbid")

    grain: Literal["day", "week", "month", "range", "all_history"] = "month"
    month_start: date | None = None
    month_label: str | None = None
    day_min: date | None = None
    day_max: date | None = None
    grain_row_count: int = 0


class OverviewComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    reason: str | None = None
    grain: Literal["day", "week", "month", "range", "all_history"] | None = None
    month_start: date | None = None
    month_label: str | None = None
    day_min: date | None = None
    day_max: date | None = None
    grain_row_count: int | None = None


class OverviewFilterOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str


class OverviewMonthOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month_start: date
    month_label: str


class OverviewAppliedState(BaseModel):
    """Resolved analytical state after validation and stale-value drops."""

    model_config = ConfigDict(extra="forbid")

    is_default: bool
    period: Literal["month", "range", "all_history"]
    compare: Literal["auto", "none"]
    month_start: date | None = None
    day_from: date | None = None
    day_to: date | None = None
    compare_month_start: date | None = None
    campaign_ids: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    filter_logic_1: list[str] = Field(default_factory=list)
    filter_logic_1_group: list[str] = Field(default_factory=list)


class OverviewFilterOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    months: list[OverviewMonthOption] = Field(default_factory=list)
    published_day_min: date | None = None
    published_day_max: date | None = None
    campaigns: list[OverviewFilterOption] = Field(default_factory=list)
    channels: list[OverviewFilterOption] = Field(default_factory=list)
    filter_logic_1: list[OverviewFilterOption] = Field(default_factory=list)
    filter_logic_1_group: list[OverviewFilterOption] = Field(default_factory=list)


class OverviewKpiCard(BaseModel):
    """One D1 hero KPI. Money/rate/ROAS values are JSON strings like FactResponse."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    kind: Literal["money", "count", "rate", "roas"]
    definition: str
    value: str | int | None
    prior_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    numerator: str | int | None = None
    denominator: str | int | None = None


class OverviewKpiResponse(BaseModel):
    """GET /analytics/overview. JWT-scoped published-history aggregates."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    has_published_history: bool
    period: OverviewPeriod | None = None
    comparison: OverviewComparison
    kpis: list[OverviewKpiCard]
    applied: OverviewAppliedState | None = None
    options: OverviewFilterOptions = Field(default_factory=OverviewFilterOptions)
    dropped_filters: dict[str, list[str]] = Field(default_factory=dict)


class TrendMetricInfo(BaseModel):
    """Registry projection of one D1 hero KPI used as a trend series."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    kind: Literal["money", "count", "rate", "roas"]
    additive: bool
    definition: str


class TrendPoint(BaseModel):
    """One time-bucket aggregate. Ratios are computed after SUM. Missing buckets are null."""

    model_config = ConfigDict(extra="forbid")

    bucket: date
    bucket_label: str
    value: str | int | None
    secondary_value: str | int | None = None
    comparison_value: str | int | None = None
    comparison_secondary_value: str | int | None = None
    numerator: str | int | None = None
    denominator: str | int | None = None
    grain_row_count: int = 0
    values: dict[str, str | int | None] | None = None
    bucket_share: str | None = None

    @model_serializer(mode="wrap")
    def _omit_null_values(self, serializer):
        data = serializer(self)
        if isinstance(data, dict) and data.get("values") is None:
            data.pop("values", None)
        if isinstance(data, dict) and data.get("bucket_share") is None:
            data.pop("bucket_share", None)
        return data


class TrendSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    points: list[TrendPoint]


class TrendSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    secondary: str | None = None
    grain: Literal["day", "week", "month"]
    breakdown: str | None = None
    dual_axis: bool = False
    chart: Literal["line", "bar"] = "line"


class TrendResponse(BaseModel):
    """GET /analytics/trends. Same D2 scope as Overview; aggregated buckets only."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    has_published_history: bool
    period: OverviewPeriod | None = None
    comparison: OverviewComparison
    comparison_shown: bool = False
    comparison_omitted_reason: str | None = None
    selection: TrendSelection | None = None
    metric: TrendMetricInfo | None = None
    secondary: TrendMetricInfo | None = None
    truncated: bool = False
    truncated_message: str | None = None
    empty: bool = True
    series: list[TrendSeries] = Field(default_factory=list)
    applied: OverviewAppliedState | None = None
    dropped_filters: dict[str, list[str]] = Field(default_factory=dict)
    metrics: list[TrendMetricInfo] = Field(default_factory=list)
    dimensions: list[OverviewFilterOption] = Field(default_factory=list)
    grains: list[str] = Field(default_factory=list)


class DrillParentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    value: str
    label: str


class DrillRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int | None = None
    key: str
    label: str
    value: str | int | None
    prior_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    contribution_pct: str | None = None
    numerator: str | int | None = None
    denominator: str | int | None = None
    grain_row_count: int = 0
    drillable: bool = False


class DrillSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Literal["kpi", "trend"]
    metric: str
    dimension: str
    depth: int
    max_depth: int
    slice_grain: Literal["day", "week", "month"] | None = None
    slice_bucket: date | None = None
    parents: list[DrillParentItem] = Field(default_factory=list)
    next_dimensions: list[str] = Field(default_factory=list)
    can_go_deeper: bool = False


class DrilldownResponse(BaseModel):
    """GET /analytics/drilldown. JWT-scoped ranked groups for one allowlisted dimension."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    has_published_history: bool
    period: OverviewPeriod | None = None
    comparison: OverviewComparison
    comparison_shown: bool = False
    selection: DrillSelection | None = None
    metric: TrendMetricInfo | None = None
    truncated: bool = False
    truncated_message: str | None = None
    empty: bool = True
    result_count: int = 0
    rows: list[DrillRow] = Field(default_factory=list)
    applied: OverviewAppliedState | None = None
    dropped_filters: dict[str, list[str]] = Field(default_factory=dict)
    dimensions: list[OverviewFilterOption] = Field(default_factory=list)


class ExplorerMetricValues(BaseModel):
    """One canonical D1/D3 metric on an explorer row. Formulas stay in kpis.py."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: str | int | None = None
    prior_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None


class ExplorerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    key: str
    label: str
    parent_key: str | None = None
    parent_label: str | None = None
    value: str | int | None
    prior_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    contribution_pct: str | None = None
    contribution_amount: str | int | None = None
    numerator: str | int | None = None
    denominator: str | int | None = None
    grain_row_count: int = 0
    drillable: bool = False
    drill_dimension: str | None = None
    drill_parents: list[str] = Field(default_factory=list)
    metrics: list[ExplorerMetricValues] = Field(default_factory=list)


class ExplorerSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    dimension: str
    secondary: str | None = None
    mode: Literal["ranking", "top", "bottom", "movers"]
    direction: Literal["asc", "desc"]
    sort: Literal["value", "delta", "delta_pct", "contribution"]
    limit: int
    mover: Literal["up", "down"] | None = None
    contribution: Literal["auto", "share", "none"]
    contribution_supported: bool
    min_value: str | None = None
    min_contribution: str | None = None
    max_depth: int
    truncated: bool = False


class ExplorerResponse(BaseModel):
    """GET /analytics/explorer. JWT-scoped ranked/contribution aggregates. No fact rows."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    has_published_history: bool
    period: OverviewPeriod | None = None
    comparison: OverviewComparison
    comparison_shown: bool = False
    selection: ExplorerSelection | None = None
    metric: TrendMetricInfo | None = None
    truncated: bool = False
    truncated_message: str | None = None
    empty: bool = True
    result_count: int = 0
    rows: list[ExplorerRow] = Field(default_factory=list)
    applied: OverviewAppliedState | None = None
    dropped_filters: dict[str, list[str]] = Field(default_factory=dict)
    metrics: list[TrendMetricInfo] = Field(default_factory=list)
    dimensions: list[OverviewFilterOption] = Field(default_factory=list)
    modes: list[OverviewFilterOption] = Field(default_factory=list)


class InsightDriver(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    dimension_label: str
    key: str
    label: str
    current_value: str | int | None = None
    prior_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    contribution_pct: str | None = None
    grain_row_count: int = 0
    drillable: bool = False
    drill_dimension: str | None = None
    drill_parents: list[str] = Field(default_factory=list)


class InsightEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grain_row_count: int = 0
    comparison_grain_row_count: int = 0
    group_count: int | None = None
    same_sign_group_count: int | None = None
    materiality_pct: str
    materiality_abs: str
    driver_share_threshold: str | None = None
    higher_is_better: bool
    additive: bool
    contribution_valid: bool
    related_metric: str | None = None
    related_current_value: str | int | None = None
    related_prior_value: str | int | None = None
    related_delta: str | int | None = None
    related_delta_pct: str | None = None


class InsightItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str
    category: Literal[
        "material_change",
        "dominant_driver",
        "positive_signal",
        "negative_signal",
        "relationship",
    ]
    headline: str
    explanation: str
    metric: str
    metric_label: str
    direction: Literal["up", "down", "neutral"] = "neutral"
    magnitude: str | int | None = None
    kind: Literal["money", "count", "rate", "roas"]
    current_value: str | int | None = None
    prior_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    dimension: str | None = None
    driver_key: str | None = None
    driver_label: str | None = None
    driver_contribution_pct: str | None = None
    threshold: str
    rank: int
    score: str
    period: OverviewPeriod | None = None
    comparison: OverviewComparison | None = None
    drivers: list[InsightDriver] = Field(default_factory=list)
    evidence: InsightEvidence
    related_metric: str | None = None


class InsightThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_pct: str
    material_high_pct: str
    abs_floor_money: str
    abs_floor_count: str
    abs_floor_rate: str
    abs_floor_roas: str
    driver_share: str
    multi_driver_each: str
    multi_driver_together: str
    min_driver_groups: str
    min_denominator: str
    max_insights: str
    ranking: str


class InsightResponse(BaseModel):
    """GET /analytics/insights. JWT-scoped deterministic insight aggregates. No fact rows."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    has_published_history: bool
    period: OverviewPeriod | None = None
    comparison: OverviewComparison
    grain: Literal["day", "week", "month"] = "month"
    comparison_shown: bool = False
    empty: bool = True
    empty_reason: str | None = None
    result_count: int = 0
    insights: list[InsightItem] = Field(default_factory=list)
    applied: OverviewAppliedState | None = None
    dropped_filters: dict[str, list[str]] = Field(default_factory=dict)
    thresholds: InsightThresholds
    metrics: list[TrendMetricInfo] = Field(default_factory=list)
    dimensions: list[OverviewFilterOption] = Field(default_factory=list)
    categories: list[OverviewFilterOption] = Field(default_factory=list)


class AnomalyDriver(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    dimension_label: str
    key: str
    label: str
    current_value: str | int | None = None
    baseline_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    contribution_pct: str | None = None
    grain_row_count: int = 0
    drillable: bool = False
    drill_dimension: str | None = None
    drill_parents: list[str] = Field(default_factory=list)


class AnomalyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_method: str
    baseline_months: list[date] = Field(default_factory=list)
    baseline_observation_count: int
    grain_row_count: int = 0
    robust_z: str | None = None
    mad: str | None = None
    mad_usable: bool = False
    new_extreme: bool = False
    higher_is_better: bool
    additive: bool
    contribution_valid: bool


class AnomalyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anomaly_id: str
    kind: Literal["spike", "drop", "rolling_deviation"]
    direction: Literal["spike", "drop"]
    headline: str
    explanation: str
    metric: str
    metric_label: str
    magnitude: str | int | None = None
    value_kind: Literal["money", "count", "rate", "roas"]
    current_value: str | int | None = None
    baseline_value: str | int | None = None
    delta: str | int | None = None
    delta_pct: str | None = None
    severity: Literal["low", "medium", "high"]
    severity_score: str
    dimension: str | None = None
    affected_key: str | None = None
    affected_label: str | None = None
    threshold: str
    rank: int
    period: OverviewPeriod | None = None
    drivers: list[AnomalyDriver] = Field(default_factory=list)
    evidence: AnomalyEvidence


class AnomalyThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_baseline_months: str
    max_baseline_months: str
    robust_z: str
    mad_scale: str
    min_mad_ratio: str
    pct_vs_median: str
    abs_floor_money: str
    abs_floor_count: str
    abs_floor_rate: str
    abs_floor_roas: str
    non_z_abs_rate: str
    driver_share: str
    max_anomalies: str
    ranking: str
    coverage_note: str


class AnomalyBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grain: Literal["day", "week", "month"] = "month"
    method: str = "median"
    month_starts: list[date] = Field(default_factory=list)
    observation_count: int = 0
    required_count: int


class AnomalyResponse(BaseModel):
    """GET /analytics/anomalies. JWT-scoped baseline diagnostics. No fact rows."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    has_published_history: bool
    period: OverviewPeriod | None = None
    comparison: OverviewComparison
    grain: Literal["day", "week", "month"] = "month"
    comparison_shown: bool = False
    empty: bool = True
    empty_reason: str | None = None
    result_count: int = 0
    anomalies: list[AnomalyItem] = Field(default_factory=list)
    baseline: AnomalyBaseline | None = None
    applied: OverviewAppliedState | None = None
    dropped_filters: dict[str, list[str]] = Field(default_factory=dict)
    thresholds: AnomalyThresholds
    metrics: list[TrendMetricInfo] = Field(default_factory=list)
    dimensions: list[OverviewFilterOption] = Field(default_factory=list)
    kinds: list[OverviewFilterOption] = Field(default_factory=list)


class AskFilters(BaseModel):
    """D2 filter state for Ask. Company scope still comes from the JWT."""

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = None
    period: str | None = None
    month_start: date | None = None
    day_from: date | None = None
    day_to: date | None = None
    compare: str | None = None
    compare_month_start: date | None = None
    compare_from: date | None = None
    compare_to: date | None = None
    campaign_id: list[str] = Field(default_factory=list)
    channel: list[str] = Field(default_factory=list)
    filter_logic_1: list[str] = Field(default_factory=list)
    filter_logic_1_group: list[str] = Field(default_factory=list)


class AskFocus(BaseModel):
    """Allowlisted Overview object the question is about. Not a SQL payload."""

    model_config = ConfigDict(extra="forbid")

    metric: str | None = None
    dimension: str | None = None
    insight_id: str | None = None
    anomaly_id: str | None = None
    group_key: str | None = None
    group_label: str | None = None
    compare_group_key: str | None = None
    compare_group_label: str | None = None
    trend_metric: str | None = None
    trend_secondary: str | None = None
    trend_grain: str | None = None
    trend_breakdown: str | None = None
    explorer_metric: str | None = None
    explorer_dimension: str | None = None
    explorer_mode: str | None = None
    drill_origin: Literal["kpi", "trend"] | None = None
    drill_metric: str | None = None
    drill_dimension: str | None = None
    drill_parents: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    """POST /analytics/ask. Natural-language question plus approved Overview context."""

    model_config = ConfigDict(extra="forbid")

    question: str
    source: str = "overview"
    filters: AskFilters | None = None
    focus: AskFocus | None = None


class AskTimings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_ms: str
    query_ms: str
    llm_ms: str
    total_ms: str


class AskResponse(BaseModel):
    """Grounded Ask answer. Numbers come from D1–D7 contracts, not the model."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    company_name: str | None = None
    status: Literal["answered", "unsupported", "refused"]
    intent: str
    operation: str | None = None
    source: str
    question: str
    answer: str
    caveats: list[str] = Field(default_factory=list)
    next_action: str | None = None
    empty_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    applied: OverviewAppliedState | None = None
    llm_used: bool = False
    timings: AskTimings


class SavedAnalysisCreate(BaseModel):
    """POST /analytics/saved. Allowlisted workspace configuration, not fact rows."""

    model_config = ConfigDict(extra="forbid")

    title: str
    state: dict[str, Any] = Field(default_factory=dict)


class SavedAnalysisUpdate(BaseModel):
    """POST /analytics/saved/{id}. Rename and/or replace workspace state."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    state: dict[str, Any] | None = None


class SavedAnalysisRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    client_id: str
    owner_subject: str
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SavedAnalysisListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    client_id: str
    owner_subject: str
    created_at: datetime
    updated_at: datetime


class SavedAnalysisListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str
    items: list[SavedAnalysisListItem] = Field(default_factory=list)
