"""Overview KPI and Dynamic Trends HTTP routes. JWT-scoped published-history aggregates."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from dfip_api.analytics_service import AnalyticsService
from dfip_api.ask_service import AskService
from dfip_api.deps import PrincipalDep, get_principal
from dfip_api.errors import ValidationFailed
from dfip_api.publication_routes import ERROR_RESPONSES
from dfip_api.saved_analysis import SavedAnalysisService
from dfip_api.schemas import (
    AnomalyResponse,
    AskRequest,
    AskResponse,
    DrilldownResponse,
    ExplorerResponse,
    InsightResponse,
    OverviewKpiResponse,
    SavedAnalysisCreate,
    SavedAnalysisListItem,
    SavedAnalysisListResponse,
    SavedAnalysisRecordResponse,
    SavedAnalysisUpdate,
    TrendResponse,
)
from dfip_api.explorer_export import render_explorer_csv
from dfip_api.workspace_export import EXPORT_MEDIA_TYPE, render_workspace_csv

analytics_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)
OptionalUuid = Annotated[UUID | None, Query()]
OptionalDate = Annotated[date | None, Query()]
StringList = Annotated[list[str] | None, Query()]


def get_analytics_service(request: Request) -> AnalyticsService:
    return AnalyticsService(
        request.app.state.publication_store,
        getattr(request.app.state, "client_directory", None),
    )


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


def get_ask_service(request: Request) -> AskService:
    settings = request.app.state.settings
    return AskService(
        get_analytics_service(request),
        getattr(request.app.state, "ask_llm", None),
        max_question_chars=int(getattr(settings, "dfip_ask_max_question_chars", 500)),
        provider=str(getattr(settings, "dfip_ask_provider", "none") or "none"),
    )


AskServiceDep = Annotated[AskService, Depends(get_ask_service)]


def get_saved_service(request: Request) -> SavedAnalysisService:
    return SavedAnalysisService(
        request.app.state.saved_analysis_store,
        getattr(request.app.state, "client_directory", None),
    )


SavedServiceDep = Annotated[SavedAnalysisService, Depends(get_saved_service)]


def _saved_record(record) -> SavedAnalysisRecordResponse:
    return SavedAnalysisRecordResponse(
        id=record.analysis_id,
        title=record.title,
        client_id=record.client_id,
        owner_subject=record.owner_subject,
        state=record.state,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _overview_kwargs(
    *,
    principal,
    client_id: UUID | None,
    period: str | None,
    month_start: date | None,
    day_from: date | None,
    day_to: date | None,
    compare: str | None,
    compare_month_start: date | None,
    compare_from: date | None,
    compare_to: date | None,
    campaign_id: list[str] | None,
    channel: list[str] | None,
    filter_logic_1: list[str] | None,
    filter_logic_1_group: list[str] | None,
) -> dict:
    return {
        "principal": principal,
        "requested_client_id": str(client_id) if client_id else None,
        "period": period,
        "month_start": month_start,
        "day_from": day_from,
        "day_to": day_to,
        "compare": compare,
        "compare_month_start": compare_month_start,
        "compare_from": compare_from,
        "compare_to": compare_to,
        "campaign_ids": campaign_id,
        "channels": channel,
        "filter_logic_1": filter_logic_1,
        "filter_logic_1_group": filter_logic_1_group,
    }


@analytics_router.get(
    "/analytics/overview",
    response_model=OverviewKpiResponse,
    summary="Overview KPI aggregates for the authenticated company",
    description=(
        "Returns the eight D1 hero KPIs for a JWT-scoped published-history "
        "window. Default period is the latest published month with auto "
        "prior-month comparison. Optional allowlisted filters: period grain, "
        "month, date range, comparison, campaign_id, channel, filter_logic_1, "
        "filter_logic_1_group. Query client_id cannot widen a bound token. "
        "Does not return fact rows. Arbitrary field names are rejected."
    ),
    tags=["Analytics"],
)
def get_overview_kpis(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
) -> OverviewKpiResponse:
    return service.overview(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
    )


@analytics_router.get(
    "/analytics/trends",
    response_model=TrendResponse,
    summary="Dynamic trend aggregates for the authenticated company",
    description=(
        "Returns allowlisted D3 trend series for a JWT-scoped published-history "
        "window. Uses the same D2 filters as Overview. Metric, grain, and "
        "breakdown keys are validated against the analytics registry. "
        "Aggregates in PostgreSQL (or the in-memory newest-wins store). "
        "Does not return fact rows. Does not accept arbitrary SQL fields."
    ),
    tags=["Analytics"],
)
def get_overview_trends(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    secondary: Annotated[str | None, Query()] = None,
    grain: Annotated[str | None, Query()] = None,
    breakdown: Annotated[str | None, Query()] = None,
    include_metric: StringList = None,
) -> TrendResponse:
    return service.trends(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        ),
        metric=metric or None,
        secondary=secondary or None,
        grain=grain or None,
        breakdown=breakdown or None,
        include_metric=include_metric or None,
    )


@analytics_router.get(
    "/analytics/drilldown",
    response_model=DrilldownResponse,
    summary="KPI/trend drilldown groups for the authenticated company",
    description=(
        "Returns ranked allowlisted dimension groups for one D1 metric. "
        "Reuses D2 period/filters and optional D3 trend slice. Parent values "
        "are validated against published history. Maximum depth is 3. "
        "Does not return fact rows. Does not accept arbitrary SQL fields."
    ),
    tags=["Analytics"],
)
def get_overview_drilldown(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    origin: Annotated[str | None, Query()] = None,
    parent: StringList = None,
    slice_grain: Annotated[str | None, Query()] = None,
    slice_bucket: OptionalDate = None,
) -> DrilldownResponse:
    return service.drilldown(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        ),
        metric=metric or None,
        dimension=dimension or None,
        origin=origin or None,
        parent=parent,
        slice_grain=slice_grain or None,
        slice_bucket=slice_bucket,
    )


@analytics_router.get(
    "/analytics/explorer",
    response_model=ExplorerResponse,
    summary="Performance Explorer ranking for the authenticated company",
    description=(
        "Returns allowlisted D5 ranking, top/bottom, mover, and contribution "
        "aggregates for one metric and dimension. Reuses D2 period/filters and "
        "D1/D3 metric semantics. Optional secondary dimension is depth 2 only. "
        "Does not return fact rows. Does not accept arbitrary SQL fields."
    ),
    tags=["Analytics"],
)
def get_overview_explorer(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    secondary: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    direction: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    mover: Annotated[str | None, Query()] = None,
    contribution: Annotated[str | None, Query()] = None,
    min_value: Annotated[str | None, Query()] = None,
    min_contribution: Annotated[str | None, Query()] = None,
) -> ExplorerResponse:
    return service.explorer(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        ),
        metric=metric or None,
        dimension=dimension or None,
        secondary=secondary or None,
        mode=mode or None,
        direction=direction or None,
        sort=sort or None,
        limit=limit,
        mover=mover or None,
        contribution=contribution or None,
        min_value=min_value or None,
        min_contribution=min_contribution or None,
    )


@analytics_router.get(
    "/analytics/explorer.csv",
    summary="Export the current Performance Explorer result as CSV",
    description=(
        "CSV of the JWT-scoped Performance Explorer ranking currently on "
        "screen. Reuses GET /analytics/explorer. Does not return workspace "
        "metadata, trend points, or unpublished facts."
    ),
    tags=["Analytics"],
    response_class=Response,
)
def export_explorer_csv(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    secondary: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    direction: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    mover: Annotated[str | None, Query()] = None,
    contribution: Annotated[str | None, Query()] = None,
    min_value: Annotated[str | None, Query()] = None,
    min_contribution: Annotated[str | None, Query()] = None,
) -> Response:
    explorer = service.explorer(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        ),
        metric=metric or None,
        dimension=dimension or None,
        secondary=secondary or None,
        mode=mode or None,
        direction=direction or None,
        sort=sort or None,
        limit=limit,
        mover=mover or None,
        contribution=contribution or None,
        min_value=min_value or None,
        min_contribution=min_contribution or None,
    )
    filename, body = render_explorer_csv(explorer)
    return Response(
        content=body,
        media_type=EXPORT_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@analytics_router.get(
    "/analytics/insights",
    response_model=InsightResponse,
    summary="Deterministic insights for the authenticated company",
    description=(
        "Returns ranked deterministic insights for the current D2 analytical "
        "state. Materiality, drivers, and explanations are calculated from "
        "published-history aggregates. Does not call an LLM. Does not return "
        "fact rows. Does not accept arbitrary SQL fields."
    ),
    tags=["Analytics"],
)
def get_overview_insights(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> InsightResponse:
    return service.insights(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        ),
        metric=metric or None,
        dimension=dimension or None,
        limit=limit,
    )


@analytics_router.get(
    "/analytics/anomalies",
    response_model=AnomalyResponse,
    summary="Deterministic anomaly diagnostics for the authenticated company",
    description=(
        "Returns ranked anomalies for a published month versus the median of "
        "prior published months under the same D2 filters. Requires at least "
        "three prior months. Does not call an LLM. Does not return fact rows. "
        "Does not accept arbitrary SQL fields."
    ),
    tags=["Analytics"],
)
def get_overview_anomalies(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> AnomalyResponse:
    return service.anomalies(
        **_overview_kwargs(
            principal=principal,
            client_id=client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_id=campaign_id,
            channel=channel,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        ),
        metric=metric or None,
        dimension=dimension or None,
        limit=limit,
    )


@analytics_router.post(
    "/analytics/ask",
    response_model=AskResponse,
    summary="Contextual Ask for the authenticated company",
    description=(
        "Maps a natural-language question to an allowlisted D1–D7 analytical "
        "operation, then explains the structured result. The language model "
        "does not execute SQL, invent KPIs, or widen company scope."
    ),
    tags=["Analytics"],
)
def post_overview_ask(
    payload: AskRequest,
    service: AskServiceDep,
    principal: PrincipalDep,
) -> AskResponse:
    return service.ask(principal, payload)


@analytics_router.get(
    "/analytics/saved",
    response_model=SavedAnalysisListResponse,
    summary="List saved analytical states for the authenticated company user",
    description=(
        "Returns saved Overview configurations owned by the current JWT subject "
        "inside the bound company. Does not return fact rows. Unbound tokens "
        "must select a company first. Inactive companies are refused."
    ),
    tags=["Analytics"],
)
def list_saved_analyses(
    service: SavedServiceDep,
    principal: PrincipalDep,
) -> SavedAnalysisListResponse:
    items = service.list(principal)
    return SavedAnalysisListResponse(
        client_id=principal.client_id or "",
        items=[
            SavedAnalysisListItem(
                id=item.analysis_id,
                title=item.title,
                client_id=item.client_id,
                owner_subject=item.owner_subject,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in items
        ],
    )


@analytics_router.post(
    "/analytics/saved",
    response_model=SavedAnalysisRecordResponse,
    summary="Save the current analytical workspace state",
    description=(
        "Stores allowlisted Overview configuration for the JWT subject and "
        "bound company. Rejects client_id in state. Does not snapshot facts."
    ),
    tags=["Analytics"],
)
def create_saved_analysis(
    payload: SavedAnalysisCreate,
    service: SavedServiceDep,
    principal: PrincipalDep,
) -> SavedAnalysisRecordResponse:
    return _saved_record(service.create(principal, title=payload.title, state=payload.state))


@analytics_router.get(
    "/analytics/saved/{analysis_id}",
    response_model=SavedAnalysisRecordResponse,
    summary="Load one saved analytical state",
    description="Owner + company scoped. Missing or cross-tenant ids return 404.",
    tags=["Analytics"],
)
def get_saved_analysis(
    analysis_id: UUID,
    service: SavedServiceDep,
    principal: PrincipalDep,
) -> SavedAnalysisRecordResponse:
    return _saved_record(service.get(principal, str(analysis_id)))


@analytics_router.post(
    "/analytics/saved/{analysis_id}",
    response_model=SavedAnalysisRecordResponse,
    summary="Rename or replace a saved analytical state",
    tags=["Analytics"],
)
def update_saved_analysis(
    analysis_id: UUID,
    payload: SavedAnalysisUpdate,
    service: SavedServiceDep,
    principal: PrincipalDep,
) -> SavedAnalysisRecordResponse:
    return _saved_record(
        service.update(
            principal,
            str(analysis_id),
            title=payload.title,
            state=payload.state,
        )
    )


@analytics_router.delete(
    "/analytics/saved/{analysis_id}",
    status_code=204,
    summary="Delete a saved analytical state",
    tags=["Analytics"],
)
def delete_saved_analysis(
    analysis_id: UUID,
    service: SavedServiceDep,
    principal: PrincipalDep,
) -> None:
    service.delete(principal, str(analysis_id))


@analytics_router.get(
    "/analytics/export.csv",
    summary="Export the current analytical workspace as CSV",
    description=(
        "CSV of the JWT-scoped Overview state currently on screen: period, "
        "comparison, D2 filters, KPIs, explorer ranking, insights, anomalies, "
        "and drill rows when a drill is open. Excel, PDF, and PowerPoint "
        "exports are not implemented. Does not return unpublished facts."
    ),
    tags=["Analytics"],
    response_class=Response,
)
def export_workspace_csv(
    service: AnalyticsServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
    period: Annotated[str | None, Query()] = None,
    month_start: OptionalDate = None,
    day_from: OptionalDate = None,
    day_to: OptionalDate = None,
    compare: Annotated[str | None, Query()] = None,
    compare_month_start: OptionalDate = None,
    compare_from: OptionalDate = None,
    compare_to: OptionalDate = None,
    campaign_id: StringList = None,
    channel: StringList = None,
    filter_logic_1: StringList = None,
    filter_logic_1_group: StringList = None,
    metric: Annotated[str | None, Query()] = None,
    secondary: Annotated[str | None, Query()] = None,
    grain: Annotated[str | None, Query()] = None,
    breakdown: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    explorer_secondary: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    direction: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    mover: Annotated[str | None, Query()] = None,
    min_value: Annotated[str | None, Query()] = None,
    min_contribution: Annotated[str | None, Query()] = None,
    drill_metric: Annotated[str | None, Query()] = None,
    drill_dimension: Annotated[str | None, Query()] = None,
    origin: Annotated[str | None, Query()] = None,
    parent: StringList = None,
    slice_grain: Annotated[str | None, Query()] = None,
    slice_bucket: OptionalDate = None,
    export_format: Annotated[str | None, Query(alias="format")] = "csv",
) -> Response:
    fmt = (export_format or "csv").strip().lower()
    if fmt not in {"csv", "workspace"}:
        raise ValidationFailed("Only CSV export is supported.")
    include_drill = bool(origin or drill_metric or drill_dimension or parent or slice_bucket)
    filename, body = render_workspace_csv(
        service,
        principal,
        requested_client_id=str(client_id) if client_id else None,
        period=period,
        month_start=month_start,
        day_from=day_from,
        day_to=day_to,
        compare=compare,
        compare_month_start=compare_month_start,
        compare_from=compare_from,
        compare_to=compare_to,
        campaign_ids=campaign_id,
        channels=channel,
        filter_logic_1=filter_logic_1,
        filter_logic_1_group=filter_logic_1_group,
        metric=metric or None,
        secondary=secondary or None,
        grain=grain or None,
        breakdown=breakdown or None,
        dimension=dimension or None,
        explorer_secondary=explorer_secondary or None,
        mode=mode or None,
        direction=direction or None,
        sort=sort or None,
        limit=limit,
        mover=mover or None,
        min_value=min_value or None,
        min_contribution=min_contribution or None,
        drill_metric=drill_metric or None,
        drill_dimension=drill_dimension or None,
        origin=origin or None,
        parent=parent,
        slice_grain=slice_grain or None,
        slice_bucket=slice_bucket,
        include_drill=include_drill,
    )
    return Response(
        content=body,
        media_type=EXPORT_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
