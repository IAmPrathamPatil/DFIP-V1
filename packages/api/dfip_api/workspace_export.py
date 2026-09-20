"""Render the current Overview analytical state as CSV.

CSV is the D9 export. Excel workbooks already exist on Reports. PDF and
PowerPoint-style decks are deferred. Numbers come from D1–D7 contracts.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from dfip_api.analytics_service import AnalyticsService
from dfip_api.auth import Principal
from dfip_api.errors import AuthorizationError, ValidationFailed
from dfip_api.published_download import CSV_MEDIA_TYPE
from dfip_api.schemas import OverviewKpiResponse

EXPORT_MEDIA_TYPE = CSV_MEDIA_TYPE


def render_workspace_csv(
    service: AnalyticsService,
    principal: Principal,
    *,
    requested_client_id: str | None,
    period: str | None = None,
    month_start: date | None = None,
    day_from: date | None = None,
    day_to: date | None = None,
    compare: str | None = None,
    compare_month_start: date | None = None,
    compare_from: date | None = None,
    compare_to: date | None = None,
    campaign_ids: list[str] | None = None,
    channels: list[str] | None = None,
    filter_logic_1: list[str] | None = None,
    filter_logic_1_group: list[str] | None = None,
    metric: str | None = None,
    secondary: str | None = None,
    grain: str | None = None,
    breakdown: str | None = None,
    dimension: str | None = None,
    explorer_secondary: str | None = None,
    mode: str | None = None,
    direction: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    mover: str | None = None,
    min_value: str | None = None,
    min_contribution: str | None = None,
    drill_metric: str | None = None,
    drill_dimension: str | None = None,
    origin: str | None = None,
    parent: list[str] | None = None,
    slice_grain: str | None = None,
    slice_bucket: date | None = None,
    include_drill: bool = False,
) -> tuple[str, bytes]:
    overview_kwargs = {
        "principal": principal,
        "requested_client_id": requested_client_id,
        "period": period,
        "month_start": month_start,
        "day_from": day_from,
        "day_to": day_to,
        "compare": compare,
        "compare_month_start": compare_month_start,
        "compare_from": compare_from,
        "compare_to": compare_to,
        "campaign_ids": campaign_ids,
        "channels": channels,
        "filter_logic_1": filter_logic_1,
        "filter_logic_1_group": filter_logic_1_group,
    }
    overview = service.overview(**overview_kwargs)
    explorer = _optional(
        lambda: service.explorer(
            **overview_kwargs,
            metric=metric,
            dimension=dimension,
            secondary=explorer_secondary,
            mode=mode,
            direction=direction,
            sort=sort,
            limit=limit,
            mover=mover,
            min_value=min_value,
            min_contribution=min_contribution,
        )
    )
    trend = _optional(
        lambda: service.trends(
            **overview_kwargs,
            metric=metric,
            secondary=secondary,
            grain=grain,
            breakdown=breakdown,
        )
    )
    insights = _optional(lambda: service.insights(**overview_kwargs))
    anomalies = _optional(lambda: service.anomalies(**overview_kwargs))
    drill = None
    if include_drill:
        drill = _optional(
            lambda: service.drilldown(
                **overview_kwargs,
                metric=drill_metric,
                dimension=drill_dimension,
                origin=origin,
                parent=parent,
                slice_grain=slice_grain,
                slice_bucket=slice_bucket,
            )
        )
    body = _csv_bytes(overview, trend, explorer, insights, anomalies, drill)
    period_label = ""
    if overview.period and overview.period.month_start:
        period_label = f"-{overview.period.month_start.isoformat()}"
    filename = f"dfip-analysis{period_label}.csv"
    return filename, body


def _optional(factory):
    try:
        return factory()
    except ValidationFailed:
        return None
    except AuthorizationError:
        raise


def _csv_bytes(
    overview: OverviewKpiResponse,
    trend: Any,
    explorer: Any,
    insights: Any,
    anomalies: Any,
    drill: Any,
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "key", "label", "value", "comparison", "extra"])
    writer.writerow(
        [
            "context",
            "company",
            overview.company_name or "",
            overview.client_id,
            "",
            "published_history" if overview.has_published_history else "no_published_history",
        ]
    )
    period = overview.period
    if period is not None:
        writer.writerow(
            [
                "context",
                "period",
                period.month_label or period.grain,
                period.month_start.isoformat() if period.month_start else "",
                f"{period.day_min or ''}..{period.day_max or ''}",
                period.grain,
            ]
        )
    comparison = overview.comparison
    writer.writerow(
        [
            "context",
            "comparison",
            comparison.month_label or comparison.reason or "",
            comparison.month_start.isoformat() if comparison.month_start else "",
            "available" if comparison.available else "unavailable",
            comparison.reason or "",
        ]
    )
    applied = overview.applied
    if applied is not None:
        writer.writerow(["context", "compare_mode", "", applied.compare, "", applied.period])
        for key, values in (
            ("campaign_id", applied.campaign_ids),
            ("channel", applied.channels),
            ("filter_logic_1", applied.filter_logic_1),
            ("filter_logic_1_group", applied.filter_logic_1_group),
        ):
            if values:
                writer.writerow(["filter", key, "", "|".join(values), "", str(len(values))])
    for kpi in overview.kpis:
        writer.writerow(
            [
                "kpi",
                kpi.id,
                kpi.label,
                "" if kpi.value is None else str(kpi.value),
                "" if kpi.prior_value is None else str(kpi.prior_value),
                "" if kpi.delta_pct is None else str(kpi.delta_pct),
            ]
        )
    if trend is not None:
        selection = getattr(trend, "selection", None)
        writer.writerow(
            [
                "trend",
                "selection",
                getattr(selection, "grain", "") if selection else "",
                getattr(selection, "metric", "") if selection else "",
                getattr(selection, "breakdown", "") if selection else "",
                getattr(selection, "secondary", "") if selection else "",
            ]
        )
        for series in getattr(trend, "series", []) or []:
            for point in series.points[:400]:
                writer.writerow(
                    [
                        "trend_point",
                        series.key,
                        point.bucket_label,
                        "" if point.value is None else str(point.value),
                        "" if point.comparison_value is None else str(point.comparison_value),
                        point.bucket.isoformat() if point.bucket else "",
                    ]
                )
    if explorer is not None and getattr(explorer, "rows", None):
        selection = getattr(explorer, "selection", None)
        writer.writerow(
            [
                "explorer",
                "selection",
                getattr(selection, "dimension", "") if selection else "",
                getattr(selection, "metric", "") if selection else "",
                getattr(selection, "mode", "") if selection else "",
                getattr(selection, "sort", "") if selection else "",
            ]
        )
        if selection is not None:
            writer.writerow(
                [
                    "explorer",
                    "grouping",
                    getattr(selection, "dimension", "") or "",
                    getattr(selection, "secondary", None) or "none",
                    getattr(selection, "mover", None) or "",
                    str(getattr(selection, "limit", "") or ""),
                ]
            )
        for row in explorer.rows:
            writer.writerow(
                [
                    "explorer_row",
                    row.key,
                    row.label if not row.parent_label else f"{row.parent_label} → {row.label}",
                    "" if row.value is None else str(row.value),
                    "" if row.prior_value is None else str(row.prior_value),
                    "" if row.delta_pct is None else str(row.delta_pct),
                ]
            )
            for metric in getattr(row, "metrics", None) or []:
                writer.writerow(
                    [
                        "explorer_metric",
                        f"{row.key}|{metric.key}",
                        metric.key,
                        "" if metric.value is None else str(metric.value),
                        "" if metric.prior_value is None else str(metric.prior_value),
                        "" if metric.delta_pct is None else str(metric.delta_pct),
                    ]
                )
    if insights is not None:
        for item in getattr(insights, "insights", []) or []:
            writer.writerow(
                [
                    "insight",
                    item.insight_id,
                    item.headline,
                    "" if item.current_value is None else str(item.current_value),
                    "" if item.prior_value is None else str(item.prior_value),
                    item.category,
                ]
            )
        if getattr(insights, "empty", False):
            writer.writerow(
                ["insight", "empty", insights.empty_reason or "no_material_insights", "", "", ""]
            )
    if anomalies is not None:
        for item in getattr(anomalies, "anomalies", []) or []:
            writer.writerow(
                [
                    "anomaly",
                    item.anomaly_id,
                    item.headline,
                    "" if item.current_value is None else str(item.current_value),
                    "" if item.baseline_value is None else str(item.baseline_value),
                    item.kind,
                ]
            )
        if getattr(anomalies, "empty", False):
            writer.writerow(
                ["anomaly", "empty", anomalies.empty_reason or "no_anomalies", "", "", ""]
            )
    if drill is not None and getattr(drill, "rows", None):
        for row in drill.rows:
            writer.writerow(
                [
                    "drill_row",
                    row.key,
                    row.label,
                    "" if row.value is None else str(row.value),
                    "" if row.prior_value is None else str(row.prior_value),
                    "" if row.delta_pct is None else str(row.delta_pct),
                ]
            )
    return buffer.getvalue().encode("utf-8")
