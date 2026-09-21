"""Overview KPI and Dynamic Trends services.

JWT-scoped SUM then client-namespace KPIs. D2 period/dimension state is shared
by D1 cards and D3 trends. Does not scan the working set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from dfip_analytics.anomalies import (
    ANOMALY_KINDS,
    MIN_BASELINE_MONTHS,
    REASON_NO_ANOMALIES,
    REASON_PERIOD_NOT_MONTH,
    SERIES_GROUP_LIMIT,
    AnomalyDraft,
    GroupMonth,
    MonthObservation,
    build_anomalies,
    parse_anomaly_dimension,
    parse_anomaly_limit,
    parse_anomaly_metric,
    select_baseline_months,
)
from dfip_analytics.anomalies import (
    REASON_EMPTY_PERIOD as ANOMALY_EMPTY_PERIOD,
)
from dfip_analytics.anomalies import (
    REASON_INSUFFICIENT_HISTORY as ANOMALY_INSUFFICIENT_HISTORY,
)
from dfip_analytics.anomalies import (
    REASON_NO_HISTORY as ANOMALY_NO_HISTORY,
)
from dfip_analytics.anomalies import (
    threshold_catalog as anomaly_threshold_catalog,
)
from dfip_analytics.divide import RATE_SCALE, as_decimal, safe_divide, safe_subtract
from dfip_analytics.drill import (
    DRILL_DIMENSIONS,
    MAX_DRILL_DEPTH,
    MAX_DRILL_ROWS,
    apply_parents,
    drill_metric,
    next_dimensions,
    parse_drill_dimension,
    parse_drill_origin,
    parse_parents,
    resolve_slice_windows,
    validate_drill_selection,
)
from dfip_analytics.explorer import (
    EXPLORER_DIMENSIONS,
    MAX_EXPLORER_DEPTH,
    contribution_supported,
    explorer_drill_target,
    explorer_metric,
    parse_explorer_contribution,
    parse_explorer_dimension,
    parse_explorer_direction,
    parse_explorer_limit,
    parse_explorer_mode,
    parse_explorer_mover,
    parse_explorer_secondary,
    parse_explorer_sort,
    parse_threshold,
    validate_explorer_selection,
)
from dfip_analytics.filters import (
    REASON_NO_COMPARABLE_RANGE,
    DimensionFilters,
    FilterValidationError,
    PeriodWindow,
    add_calendar_month,
    drop_unknown,
    normalize_multi,
    resolve_comparison,
    resolve_period,
)
from dfip_analytics.insights import (
    DEFAULT_DRIVER_DIMENSIONS,
    DRIVER_DIMENSIONS,
    DRIVER_DIMENSIONS_BY_KEY,
    DRIVER_SHARE,
    INSIGHT_CATEGORIES,
    MATERIAL_PCT,
    REASON_EMPTY_PERIOD,
    REASON_INSUFFICIENT_COMPARISON,
    REASON_INSUFFICIENT_HISTORY,
    REASON_NO_MATERIAL,
    InsightDraft,
    abs_floor_for,
    build_insights,
    higher_is_better,
    parse_insight_dimension,
    parse_insight_limit,
    parse_insight_metric,
    threshold_catalog,
)
from dfip_analytics.kpis import ADDITIVE_MEASURES, INTEGER_MEASURES, compute_kpis
from dfip_analytics.overview import HERO_KPIS, REASON_NO_HISTORY
from dfip_analytics.trends import (
    MAX_BREAKDOWN_SERIES,
    MAX_SERIES_ROWS,
    OTHER_SERIES_KEY,
    REASON_COMPARISON_WITH_BREAKDOWN,
    TOTAL_SERIES_KEY,
    TREND_DIMENSIONS,
    TREND_GRAINS,
    TREND_METRICS,
    TrendDimensionSpec,
    TrendMetricSpec,
    bucket_label,
    dual_axis,
    iter_buckets,
    parse_breakdown,
    parse_include_metrics,
    parse_trend_grain,
    parse_trend_metric,
    rank_measure_name,
    validate_trend_selection,
)
from dfip_core.transform.derive import month_label
from dfip_core.transform.derive import month_start as month_of

from dfip_api.auth import Principal
from dfip_api.errors import AuthorizationError, ValidationFailed
from dfip_api.lifecycle import require_company_active
from dfip_api.publication_service import _resolve_client_id
from dfip_api.schemas import (
    AnomalyBaseline,
    AnomalyDriver,
    AnomalyEvidence,
    AnomalyItem,
    AnomalyResponse,
    AnomalyThresholds,
    DrilldownResponse,
    DrillParentItem,
    DrillRow,
    DrillSelection,
    ExplorerMetricValues,
    ExplorerResponse,
    ExplorerRow,
    ExplorerSelection,
    InsightDriver,
    InsightEvidence,
    InsightItem,
    InsightResponse,
    InsightThresholds,
    OverviewAppliedState,
    OverviewComparison,
    OverviewFilterOption,
    OverviewFilterOptions,
    OverviewKpiCard,
    OverviewKpiResponse,
    OverviewMonthOption,
    OverviewPeriod,
    TrendMetricInfo,
    TrendPoint,
    TrendResponse,
    TrendSelection,
    TrendSeries,
)
from dfip_api.service import decimal_to_api


@dataclass
class _AnalyticsScope:
    client_id: str
    company_name: str | None
    months: list[date]
    published_min: date | None
    published_max: date | None
    window: PeriodWindow | None
    comparison_window: PeriodWindow | None
    compare_mode: str
    comparison_reason: str | None
    filters: DimensionFilters
    dropped: dict[str, list[str]]
    applied: OverviewAppliedState | None
    options: OverviewFilterOptions
    has_history: bool


def _count(value: object) -> int | None:
    if value is None:
        return None
    number = as_decimal(value) if not isinstance(value, Decimal) else value
    if number is None:
        return None
    return int(number)


def _serialize(kind: str, value: object) -> str | int | None:
    if kind == "count":
        return _count(value)
    if value is None or isinstance(value, Decimal):
        return decimal_to_api(value)
    return decimal_to_api(as_decimal(value))


def _delta(kind: str, current: object, prior: object) -> str | int | None:
    if current is None or prior is None:
        return None
    if kind == "count":
        left = _count(current)
        right = _count(prior)
        if left is None or right is None:
            return None
        return left - right
    scale = RATE_SCALE if kind == "rate" else None
    return decimal_to_api(safe_subtract(current, prior, scale=scale))


def _delta_pct(current: object, prior: object) -> str | None:
    change = safe_subtract(current, prior)
    return decimal_to_api(safe_divide(change, prior, scale=RATE_SCALE))


def _magnitude(kind: str, current: object, prior: object) -> str | int | None:
    delta = safe_subtract(current, prior)
    if delta is None:
        return None
    return _serialize(kind, abs(delta))


def _operand(measures: dict, name: str | None) -> str | int | None:
    if not name:
        return None
    return _serialize("count" if name in INTEGER_MEASURES else "money", measures.get(name))


def _window_period(
    window: PeriodWindow, count: int, day_min: date | None, day_max: date | None
) -> OverviewPeriod:
    return OverviewPeriod(
        grain=window.grain,
        month_start=window.month_start,
        month_label=window.month_label,
        day_min=day_min,
        day_max=day_max,
        grain_row_count=count,
    )


def _window_comparison(
    window: PeriodWindow | None,
    *,
    available: bool,
    reason: str | None,
    count: int | None = None,
    day_min: date | None = None,
    day_max: date | None = None,
) -> OverviewComparison:
    if window is None or not available:
        return OverviewComparison(available=False, reason=reason)
    return OverviewComparison(
        available=True,
        reason=None,
        grain=window.grain,
        month_start=window.month_start,
        month_label=window.month_label or "",
        day_min=day_min,
        day_max=day_max,
        grain_row_count=count,
    )


def _bucket_period(window: PeriodWindow, bucket: date, grain: str, count: int) -> OverviewPeriod:
    buckets = iter_buckets(window.day_from, window.day_to_exclusive, grain)
    index = buckets.index(bucket)
    next_bucket = buckets[index + 1] if index + 1 < len(buckets) else window.day_to_exclusive
    return OverviewPeriod(
        grain=grain,  # type: ignore[arg-type]
        month_start=window.month_start,
        month_label=bucket_label(bucket, grain),
        day_min=max(bucket, window.day_from),
        day_max=min(next_bucket - timedelta(days=1), window.inclusive_to),
        grain_row_count=count,
    )


def _bucket_comparison(
    window: PeriodWindow | None, bucket: date, grain: str, count: int
) -> OverviewComparison:
    if window is None:
        return OverviewComparison(available=False, reason=REASON_INSUFFICIENT_COMPARISON)
    bucket_end = bucket + timedelta(days=6 if grain == "week" else 0)
    if bucket_end < window.day_from or bucket >= window.day_to_exclusive:
        return OverviewComparison(available=False, reason=REASON_NO_COMPARABLE_RANGE)
    period = _bucket_period(window, bucket, grain, count)
    return OverviewComparison(
        available=True,
        grain=grain,  # type: ignore[arg-type]
        month_start=window.month_start,
        month_label=period.month_label or "",
        day_min=period.day_min,
        day_max=period.day_max,
        grain_row_count=count,
    )


def _bucket_rows(
    rows: list[tuple[date, str | None, str | None, dict[str, object], int]],
) -> dict[tuple[date, str | None], tuple[str | None, dict[str, object], int]]:
    return {(bucket, key): (label, measures, count) for bucket, key, label, measures, count in rows}


def _bucket_slots(window: PeriodWindow, grain: str) -> dict[int, tuple[str, date]]:
    """Map a bounded series to stable relative slots and ISO-labelled identities."""
    buckets = iter_buckets(window.day_from, window.day_to_exclusive, grain)
    if not buckets:
        return {}
    anchor = buckets[0]
    step = 7 if grain == "week" else 1
    return {
        (bucket - anchor).days // step: (bucket_label(bucket, grain), bucket)
        for bucket in buckets
    }


def _metric_info(spec: TrendMetricSpec) -> TrendMetricInfo:
    return TrendMetricInfo(
        key=spec.key,
        label=spec.label,
        kind=spec.kind,  # type: ignore[arg-type]
        additive=spec.additive,
        definition=spec.definition,
    )


def _serialize_insight(
    draft: InsightDraft,
    *,
    index: int,
    period: OverviewPeriod,
    comparison: OverviewComparison,
) -> InsightItem:
    spec = draft.metric
    kind = spec.kind
    related = draft.related_snapshot
    drivers: list[InsightDriver] = []
    for item in draft.drivers:
        dim = DRIVER_DIMENSIONS_BY_KEY[item.dimension]
        nxt, parents = explorer_drill_target(dim, None, key=item.key, parent_key=None)
        drivers.append(
            InsightDriver(
                dimension=item.dimension,
                dimension_label=item.dimension_label,
                key=item.key,
                label=item.label,
                current_value=_serialize(kind, item.current),
                prior_value=_serialize(kind, item.prior),
                delta=_delta(kind, item.current, item.prior),
                delta_pct=_delta_pct(item.current, item.prior),
                contribution_pct=decimal_to_api(item.contribution),
                grain_row_count=item.count,
                drillable=nxt is not None,
                drill_dimension=nxt,
                drill_parents=list(parents),
            )
        )
    top = drivers[0] if drivers else None
    return InsightItem(
        insight_id=draft.insight_id,
        category=draft.category,
        headline=draft.headline,
        explanation=draft.explanation,
        metric=spec.key,
        metric_label=spec.label,
        direction=(
            "up"
            if draft.snapshot.delta is not None and draft.snapshot.delta > 0
            else "down"
            if draft.snapshot.delta is not None and draft.snapshot.delta < 0
            else "neutral"
        ),
        magnitude=_magnitude(kind, draft.snapshot.current, draft.snapshot.prior),
        kind=kind,  # type: ignore[arg-type]
        current_value=_serialize(kind, draft.snapshot.current),
        prior_value=_serialize(kind, draft.snapshot.prior),
        delta=_delta(kind, draft.snapshot.current, draft.snapshot.prior),
        delta_pct=_delta_pct(draft.snapshot.current, draft.snapshot.prior),
        dimension=draft.dimension.key if draft.dimension else None,
        driver_key=top.key if top else None,
        driver_label=top.label if top else None,
        driver_contribution_pct=top.contribution_pct if top else None,
        threshold=draft.threshold,
        rank=index,
        score=format(draft.score, "f"),
        period=period,
        comparison=comparison,
        drivers=drivers,
        related_metric=draft.related.key if draft.related else None,
        evidence=InsightEvidence(
            grain_row_count=draft.snapshot.current_count,
            comparison_grain_row_count=draft.snapshot.prior_count,
            group_count=draft.group_count,
            same_sign_group_count=draft.same_sign_group_count,
            materiality_pct=str(MATERIAL_PCT),
            materiality_abs=str(abs_floor_for(spec)),
            driver_share_threshold=str(DRIVER_SHARE)
            if draft.category == "dominant_driver"
            else None,
            higher_is_better=higher_is_better(spec),
            additive=spec.additive,
            contribution_valid=bool(spec.additive),
            related_metric=draft.related.key if draft.related else None,
            related_current_value=_serialize(related.spec.kind, related.current)
            if related
            else None,
            related_prior_value=_serialize(related.spec.kind, related.prior) if related else None,
            related_delta=_delta(related.spec.kind, related.current, related.prior)
            if related
            else None,
            related_delta_pct=_delta_pct(related.current, related.prior) if related else None,
        ),
    )


def _serialize_anomaly(
    draft: AnomalyDraft,
    *,
    index: int,
    period: OverviewPeriod,
) -> AnomalyItem:
    spec = draft.metric
    kind = spec.kind
    headline = draft.headline
    explanation = draft.explanation
    if period.grain == "week":
        headline = re.sub(r"in \d{4}-\d{2}", f"in {period.month_label}", headline, count=1)
        explanation = re.sub(
            r"in \d{4}-\d{2}", f"in {period.month_label}", explanation, count=1
        )
    drivers: list[AnomalyDriver] = []
    for item in draft.drivers:
        dim = DRIVER_DIMENSIONS_BY_KEY[item.dimension]
        nxt, parents = explorer_drill_target(dim, None, key=item.key, parent_key=None)
        drivers.append(
            AnomalyDriver(
                dimension=item.dimension,
                dimension_label=item.dimension_label,
                key=item.key,
                label=item.label,
                current_value=_serialize(kind, item.current),
                baseline_value=_serialize(kind, item.baseline),
                delta=_delta(kind, item.current, item.baseline),
                delta_pct=_delta_pct(item.current, item.baseline),
                contribution_pct=decimal_to_api(item.contribution),
                grain_row_count=item.count,
                drillable=nxt is not None,
                drill_dimension=nxt,
                drill_parents=list(parents),
            )
        )
    top = drivers[0] if drivers else None
    return AnomalyItem(
        anomaly_id=draft.anomaly_id,
        kind=draft.kind,
        direction=draft.direction,
        headline=headline,
        explanation=explanation,
        metric=spec.key,
        metric_label=spec.label,
        magnitude=_magnitude(kind, draft.current, draft.baseline),
        value_kind=kind,  # type: ignore[arg-type]
        current_value=_serialize(kind, draft.current),
        baseline_value=_serialize(kind, draft.baseline),
        delta=_delta(kind, draft.current, draft.baseline),
        delta_pct=decimal_to_api(draft.delta_pct),
        severity=draft.severity,
        severity_score=format(draft.severity_score, "f"),
        dimension=draft.dimension.key if draft.dimension else None,
        affected_key=top.key if top else None,
        affected_label=top.label if top else None,
        threshold=draft.threshold,
        rank=index,
        period=period,
        drivers=drivers,
        evidence=AnomalyEvidence(
            baseline_method=draft.baseline_method,
            baseline_months=list(draft.baseline_months),
            baseline_observation_count=len(draft.baseline_months),
            grain_row_count=draft.current_count,
            robust_z=decimal_to_api(draft.robust_z),
            mad=decimal_to_api(draft.mad),
            mad_usable=draft.mad_ok,
            new_extreme=draft.new_extreme,
            higher_is_better=higher_is_better(spec),
            additive=spec.additive,
            contribution_valid=bool(spec.additive),
        ),
    )


def _pick_metric(spec: TrendMetricSpec, measures: dict, kpis: dict):
    if spec.source == "measure":
        return measures.get(spec.measure)
    return kpis.get(spec.kpi_slug)


def _metric_values(
    specs: tuple[TrendMetricSpec, ...],
    measures: dict,
    kpis: dict,
) -> dict[str, str | int | None] | None:
    if not specs:
        return None
    return {
        spec.key: _serialize(spec.kind, _pick_metric(spec, measures, kpis) if measures else None)
        for spec in specs
    }


def _index_series_rows(
    rows: list[tuple[date, str | None, str | None, dict[str, object], int]],
    *,
    breakdown: TrendDimensionSpec | None,
) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for bucket, dim_value, dim_label, measures, count in rows:
        if breakdown is None:
            key = TOTAL_SERIES_KEY
            label = "Total"
        else:
            key = TOTAL_SERIES_KEY if dim_value is None else dim_value
            if key == OTHER_SERIES_KEY:
                label = "Other"
            elif dim_value == "":
                label = dim_label or "(blank)"
            else:
                label = dim_label or dim_value or "(blank)"
        slot = indexed.setdefault(key, {"label": label, "points": {}})
        slot["label"] = label
        slot["points"][bucket] = (measures, count)
    return indexed


def _series_sort_key(item: tuple[str, dict[str, object]]) -> tuple[int, str, str]:
    key, payload = item
    if key == TOTAL_SERIES_KEY:
        return (0, "", key)
    if key == OTHER_SERIES_KEY:
        return (2, "", key)
    label = str(payload.get("label") or key)
    return (1, label.lower(), key)


class AnalyticsService:
    def __init__(self, publication_store, client_directory=None) -> None:
        self._publications = publication_store
        self._clients = client_directory

    def overview(
        self,
        *,
        principal: Principal,
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
    ) -> OverviewKpiResponse:
        scope = self._resolve_scope(
            principal=principal,
            requested_client_id=requested_client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
        if not scope.has_history or scope.window is None:
            return OverviewKpiResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=False,
                period=None,
                comparison=OverviewComparison(available=False, reason=REASON_NO_HISTORY),
                kpis=[],
                applied=None,
                options=scope.options,
            )
        current_measures, current_count, current_min, current_max = self._sum(
            scope.client_id, scope.window, scope.filters
        )
        current_kpis = compute_kpis(current_measures, namespace="client")
        prior_measures = None
        prior_kpis = None
        if scope.comparison_window is not None:
            prior_measures, prior_count, prior_min, prior_max = self._sum(
                scope.client_id, scope.comparison_window, scope.filters
            )
            prior_kpis = compute_kpis(prior_measures, namespace="client")
            comparison = _window_comparison(
                scope.comparison_window,
                available=True,
                reason=None,
                count=prior_count,
                day_min=prior_min,
                day_max=prior_max,
            )
        else:
            comparison = _window_comparison(None, available=False, reason=scope.comparison_reason)
        cards = [
            _card(spec, current_measures, current_kpis, prior_measures, prior_kpis)
            for spec in HERO_KPIS
        ]
        return OverviewKpiResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=_window_period(scope.window, current_count, current_min, current_max),
            comparison=comparison,
            kpis=cards,
            applied=scope.applied,
            options=scope.options,
            dropped_filters=scope.dropped,
        )

    def trends(
        self,
        *,
        principal: Principal,
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
        include_metric: list[str] | None = None,
    ) -> TrendResponse:
        scope = self._resolve_scope(
            principal=principal,
            requested_client_id=requested_client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
        try:
            primary_spec = parse_trend_metric(metric, role="primary")
            secondary_spec = parse_trend_metric(secondary, role="secondary") if secondary else None
            grain_key = parse_trend_grain(grain)
            breakdown_spec = parse_breakdown(breakdown)
            include_specs = parse_include_metrics(include_metric)
            validate_trend_selection(
                primary_spec, secondary_spec, grain_key, breakdown_spec, include_specs
            )
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc

        catalog_metrics = [_metric_info(item) for item in TREND_METRICS]
        catalog_dimensions = [
            OverviewFilterOption(value=item.key, label=item.label) for item in TREND_DIMENSIONS
        ]
        if not scope.has_history or scope.window is None:
            return TrendResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=False,
                period=None,
                comparison=OverviewComparison(available=False, reason=REASON_NO_HISTORY),
                comparison_shown=False,
                comparison_omitted_reason=REASON_NO_HISTORY,
                selection=TrendSelection(
                    metric=primary_spec.key,
                    secondary=secondary_spec.key if secondary_spec else None,
                    grain=grain_key,
                    breakdown=breakdown_spec.key if breakdown_spec else None,
                    dual_axis=dual_axis(primary_spec, secondary_spec),
                    chart="line",
                ),
                metric=_metric_info(primary_spec),
                secondary=_metric_info(secondary_spec) if secondary_spec else None,
                empty=True,
                applied=None,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                grains=list(TREND_GRAINS),
            )

        comparison_shown = scope.comparison_window is not None and breakdown_spec is None
        if breakdown_spec is not None and scope.comparison_window is not None:
            omitted = REASON_COMPARISON_WITH_BREAKDOWN
        elif scope.comparison_window is None:
            omitted = scope.comparison_reason
        else:
            omitted = None

        current_rows, distinct = self._series(
            scope.client_id,
            scope.window,
            scope.filters,
            grain_key,
            breakdown_spec,
            primary_spec,
        )
        if len(current_rows) > MAX_SERIES_ROWS:
            raise ValidationFailed(
                "Trend result is too large. Narrow the period or remove the breakdown."
            )
        compare_rows: list[tuple[date, str | None, str | None, dict[str, object], int]] = []
        prior_count = None
        prior_min = None
        prior_max = None
        if comparison_shown and scope.comparison_window is not None:
            compare_rows, _compare_distinct = self._series(
                scope.client_id,
                scope.comparison_window,
                scope.filters,
                grain_key,
                None,
                primary_spec,
            )
            prior_count = sum(item[4] for item in compare_rows)
            compare_days = [item[0] for item in compare_rows]
            prior_min = min(compare_days) if compare_days else None
            prior_max = max(compare_days) if compare_days else None
        current_count = sum(item[4] for item in current_rows)
        current_days = [item[0] for item in current_rows]
        current_min = min(current_days) if current_days else None
        current_max = max(current_days) if current_days else None
        if scope.comparison_window is not None:
            comparison = _window_comparison(
                scope.comparison_window,
                available=True,
                reason=None,
                count=prior_count,
                day_min=prior_min,
                day_max=prior_max,
            )
        else:
            comparison = _window_comparison(None, available=False, reason=scope.comparison_reason)

        buckets = iter_buckets(scope.window.day_from, scope.window.day_to_exclusive, grain_key)
        compare_buckets = (
            iter_buckets(
                scope.comparison_window.day_from,
                scope.comparison_window.day_to_exclusive,
                grain_key,
            )
            if comparison_shown and scope.comparison_window is not None
            else []
        )
        current_index = _index_series_rows(current_rows, breakdown=breakdown_spec)
        compare_index = _index_series_rows(compare_rows, breakdown=None)
        compare_points = compare_index.get(TOTAL_SERIES_KEY, {}).get("points", {})
        truncated = bool(breakdown_spec and distinct > MAX_BREAKDOWN_SERIES)
        chart = "bar" if breakdown_spec is not None and len(buckets) <= 1 else "line"
        series: list[TrendSeries] = []
        keys = current_index or (
            {TOTAL_SERIES_KEY: {"label": "Total", "points": {}}} if buckets else {}
        )
        for key, payload in sorted(keys.items(), key=_series_sort_key):
            points: list[TrendPoint] = []
            for index, bucket in enumerate(buckets):
                measures, count = payload["points"].get(bucket, ({}, 0))
                kpis = compute_kpis(measures, namespace="client") if measures else {}
                value = _pick_metric(primary_spec, measures, kpis) if measures else None
                secondary_value = (
                    _pick_metric(secondary_spec, measures, kpis)
                    if secondary_spec is not None and measures
                    else None
                )
                comparison_value = None
                comparison_secondary_value = None
                if comparison_shown and index < len(compare_buckets):
                    prior_measures, _prior_n = compare_points.get(compare_buckets[index], ({}, 0))
                    if prior_measures:
                        prior_kpis = compute_kpis(prior_measures, namespace="client")
                        comparison_value = _pick_metric(primary_spec, prior_measures, prior_kpis)
                        if secondary_spec is not None:
                            comparison_secondary_value = _pick_metric(
                                secondary_spec, prior_measures, prior_kpis
                            )
                points.append(
                    TrendPoint(
                        bucket=bucket,
                        bucket_label=bucket_label(bucket, grain_key),
                        value=_serialize(primary_spec.kind, value),
                        secondary_value=(
                            _serialize(secondary_spec.kind, secondary_value)
                            if secondary_spec is not None
                            else None
                        ),
                        comparison_value=_serialize(primary_spec.kind, comparison_value),
                        comparison_secondary_value=(
                            _serialize(secondary_spec.kind, comparison_secondary_value)
                            if secondary_spec is not None
                            else None
                        ),
                        numerator=_operand(measures, primary_spec.numerator) if measures else None,
                        denominator=_operand(measures, primary_spec.denominator)
                        if measures
                        else None,
                        grain_row_count=int(count or 0),
                        values=_metric_values(include_specs, measures, kpis),
                    )
                )
            series.append(
                TrendSeries(key=key, label=str(payload.get("label") or key), points=points)
            )
        return TrendResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=_window_period(scope.window, current_count, current_min, current_max),
            comparison=comparison,
            comparison_shown=comparison_shown,
            comparison_omitted_reason=omitted,
            selection=TrendSelection(
                metric=primary_spec.key,
                secondary=secondary_spec.key if secondary_spec else None,
                grain=grain_key,
                breakdown=breakdown_spec.key if breakdown_spec else None,
                dual_axis=dual_axis(primary_spec, secondary_spec),
                chart=chart,
            ),
            metric=_metric_info(primary_spec),
            secondary=_metric_info(secondary_spec) if secondary_spec else None,
            truncated=truncated,
            truncated_message=(
                f"Showing the {MAX_BREAKDOWN_SERIES} largest series plus Other."
                if truncated
                else None
            ),
            empty=current_count == 0,
            series=series,
            applied=scope.applied,
            dropped_filters=scope.dropped,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            grains=list(TREND_GRAINS),
        )

    def drilldown(
        self,
        *,
        principal: Principal,
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
        dimension: str | None = None,
        origin: str | None = None,
        parent: list[str] | None = None,
        slice_grain: str | None = None,
        slice_bucket: date | None = None,
    ) -> DrilldownResponse:
        scope = self._resolve_scope(
            principal=principal,
            requested_client_id=requested_client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
        catalog = [
            OverviewFilterOption(value=item.key, label=item.label) for item in DRILL_DIMENSIONS
        ]
        try:
            origin_key = parse_drill_origin(origin)
            metric_spec = drill_metric(metric)
            dimension_spec = parse_drill_dimension(dimension)
            parents = parse_parents(parent)
            validate_drill_selection(metric_spec, dimension_spec, parents)
            if origin_key == "trend" and not slice_grain:
                slice_grain = "day"
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        empty = DrilldownResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=False,
            period=None,
            comparison=OverviewComparison(available=False, reason=REASON_NO_HISTORY),
            metric=_metric_info(metric_spec),
            empty=True,
            applied=None,
            dimensions=catalog,
        )
        if not scope.has_history or scope.window is None:
            return empty
        try:
            current_window, compare_window = resolve_slice_windows(
                scope.window,
                scope.comparison_window,
                origin=origin_key,
                slice_grain=slice_grain,
                slice_bucket=slice_bucket,
            )
            scoped_filters = self._validate_parents(
                scope.client_id, current_window, scope.filters, parents
            )
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc

        deeper = next_dimensions(parents, dimension_spec)
        parent_items = [
            DrillParentItem(
                dimension=item.dimension,
                value=item.value,
                label=self._parent_label(scope, item.dimension, item.value),
            )
            for item in parents
        ]
        selection = DrillSelection(
            origin=origin_key,  # type: ignore[arg-type]
            metric=metric_spec.key,
            dimension=dimension_spec.key,
            depth=len(parents) + 1,
            max_depth=MAX_DRILL_DEPTH,
            slice_grain=slice_grain if origin_key == "trend" else None,  # type: ignore[arg-type]
            slice_bucket=slice_bucket if origin_key == "trend" else None,
            parents=parent_items,
            next_dimensions=list(deeper),
            can_go_deeper=bool(deeper),
        )
        current_groups = self._groups(
            scope.client_id, current_window, scoped_filters, dimension_spec.key
        )
        prior_groups = (
            self._groups(scope.client_id, compare_window, scoped_filters, dimension_spec.key)
            if compare_window is not None
            else None
        )
        current_count = sum(item[3] for item in current_groups)
        current_min, current_max = current_window.day_from, current_window.inclusive_to
        if compare_window is not None:
            comparison = _window_comparison(
                compare_window,
                available=True,
                reason=None,
                count=sum(item[3] for item in prior_groups or []),
                day_min=compare_window.day_from,
                day_max=compare_window.inclusive_to,
            )
        elif origin_key == "trend" and scope.comparison_window is not None:
            comparison = _window_comparison(
                None, available=False, reason="comparison_not_available_for_slice"
            )
        else:
            comparison = _window_comparison(None, available=False, reason=scope.comparison_reason)
        rows, truncated = _ranked_rows(
            metric_spec,
            current_groups,
            prior_groups,
            can_go_deeper=bool(deeper) and dimension_spec.key != "day",
        )
        return DrilldownResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=_window_period(current_window, current_count, current_min, current_max),
            comparison=comparison,
            comparison_shown=comparison.available,
            selection=selection,
            metric=_metric_info(metric_spec),
            truncated=truncated,
            truncated_message=(
                f"Showing the {MAX_DRILL_ROWS} largest groups plus Other." if truncated else None
            ),
            empty=current_count == 0,
            result_count=len(current_groups),
            rows=rows,
            applied=scope.applied,
            dropped_filters=scope.dropped,
            dimensions=catalog,
        )

    def explorer(
        self,
        *,
        principal: Principal,
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
        dimension: str | None = None,
        secondary: str | None = None,
        mode: str | None = None,
        direction: str | None = None,
        sort: str | None = None,
        limit: int | str | None = None,
        mover: str | None = None,
        contribution: str | None = None,
        min_value: str | None = None,
        min_contribution: str | None = None,
    ) -> ExplorerResponse:
        scope = self._resolve_scope(
            principal=principal,
            requested_client_id=requested_client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
        catalog_metrics = [_metric_info(item) for item in TREND_METRICS]
        catalog_dimensions = [
            OverviewFilterOption(value=item.key, label=item.label) for item in EXPLORER_DIMENSIONS
        ]
        catalog_modes = [
            OverviewFilterOption(value="ranking", label="Ranking"),
            OverviewFilterOption(value="top", label="Top"),
            OverviewFilterOption(value="bottom", label="Bottom"),
            OverviewFilterOption(value="movers", label="Movers"),
        ]
        try:
            metric_spec = explorer_metric(metric)
            dimension_spec = parse_explorer_dimension(dimension)
            secondary_spec = parse_explorer_secondary(secondary)
            mode_key = parse_explorer_mode(mode)
            mover_key = parse_explorer_mover(mover) if mode_key == "movers" else None
            sort_key = parse_explorer_sort(sort, mode=mode_key)
            direction_key = parse_explorer_direction(
                direction, mode=mode_key, mover=mover_key or "up"
            )
            limit_n = parse_explorer_limit(limit, mode=mode_key)
            contribution_mode = parse_explorer_contribution(contribution)
            min_metric = parse_threshold(min_value, label="Minimum metric value")
            min_share = parse_threshold(min_contribution, label="Minimum contribution")
            comparison_available = scope.comparison_window is not None
            validate_explorer_selection(
                metric_spec,
                dimension_spec,
                secondary_spec,
                mode=mode_key,
                sort=sort_key,
                contribution=contribution_mode,
                comparison_available=comparison_available,
                min_contribution=min_share,
            )
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        if mode_key == "top":
            sort_key = "value"
            direction_key = "desc"
        elif mode_key == "bottom":
            sort_key = "value"
            direction_key = "asc"
        elif mode_key == "movers":
            direction_key = "asc" if mover_key == "down" else "desc"
        share_ok = contribution_supported(metric_spec)
        show_contribution = share_ok and contribution_mode != "none"
        selection = ExplorerSelection(
            metric=metric_spec.key,
            dimension=dimension_spec.key,
            secondary=secondary_spec.key if secondary_spec else None,
            mode=mode_key,  # type: ignore[arg-type]
            direction=direction_key,  # type: ignore[arg-type]
            sort=sort_key,  # type: ignore[arg-type]
            limit=limit_n,
            mover=mover_key,  # type: ignore[arg-type]
            contribution=contribution_mode,  # type: ignore[arg-type]
            contribution_supported=share_ok,
            min_value=str(min_metric) if min_metric is not None else None,
            min_contribution=str(min_share) if min_share is not None else None,
            max_depth=MAX_EXPLORER_DEPTH,
        )
        empty = ExplorerResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=False,
            period=None,
            comparison=OverviewComparison(available=False, reason=REASON_NO_HISTORY),
            selection=selection,
            metric=_metric_info(metric_spec),
            empty=True,
            applied=None,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            modes=catalog_modes,
        )
        if not scope.has_history or scope.window is None:
            return empty
        if secondary_spec is not None:
            current_pairs = self._pairs(
                scope.client_id, scope.window, scope.filters, dimension_spec.key, secondary_spec.key
            )
            prior_pairs = (
                self._pairs(
                    scope.client_id,
                    scope.comparison_window,
                    scope.filters,
                    dimension_spec.key,
                    secondary_spec.key,
                )
                if scope.comparison_window is not None
                else None
            )
            built = _explorer_from_pairs(metric_spec, current_pairs, prior_pairs)
        else:
            current_groups = self._groups(
                scope.client_id, scope.window, scope.filters, dimension_spec.key
            )
            prior_groups = (
                self._groups(
                    scope.client_id, scope.comparison_window, scope.filters, dimension_spec.key
                )
                if scope.comparison_window is not None
                else None
            )
            built = _explorer_from_groups(metric_spec, current_groups, prior_groups)
        totals, total_count, day_min, day_max = self._sum(
            scope.client_id, scope.window, scope.filters
        )
        total_kpis = compute_kpis(totals, namespace="client")
        total_metric = _pick_metric(metric_spec, totals, total_kpis)
        comparison = (
            _window_comparison(
                scope.comparison_window,
                available=True,
                reason=None,
                count=sum(item.prior_count for item in built),
                day_min=scope.comparison_window.day_from,
                day_max=scope.comparison_window.inclusive_to,
            )
            if scope.comparison_window is not None
            else _window_comparison(None, available=False, reason=scope.comparison_reason)
        )
        ranked, result_count, truncated = _rank_explorer_rows(
            metric_spec,
            built,
            total_metric,
            mode=mode_key,
            mover=mover_key,
            sort=sort_key,
            direction=direction_key,
            limit=limit_n,
            min_value=min_metric,
            min_contribution=min_share,
            show_contribution=show_contribution,
            comparison_available=comparison.available,
            dimension=dimension_spec,
            secondary=secondary_spec,
        )
        return ExplorerResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=_window_period(scope.window, total_count, day_min, day_max),
            comparison=comparison,
            comparison_shown=comparison.available,
            selection=selection.model_copy(update={"truncated": truncated}),
            metric=_metric_info(metric_spec),
            truncated=truncated,
            truncated_message=(
                f"Showing {limit_n} of {result_count} groups." if truncated else None
            ),
            empty=result_count == 0,
            result_count=result_count,
            rows=ranked,
            applied=scope.applied,
            dropped_filters=scope.dropped,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            modes=catalog_modes,
        )

    def insights(
        self,
        *,
        principal: Principal,
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
        dimension: str | None = None,
        limit: int | str | None = None,
        grain: str | None = None,
    ) -> InsightResponse:
        scope = self._resolve_scope(
            principal=principal,
            requested_client_id=requested_client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
        catalog_metrics = [_metric_info(item) for item in TREND_METRICS]
        catalog_dimensions = [
            OverviewFilterOption(value=item.key, label=item.label) for item in DRIVER_DIMENSIONS
        ]
        catalog_categories = [
            OverviewFilterOption(value=item, label=item.replace("_", " "))
            for item in INSIGHT_CATEGORIES
        ]
        thresholds = InsightThresholds(**threshold_catalog())
        try:
            metric_spec = parse_insight_metric(metric)
            dimension_spec = parse_insight_dimension(dimension)
            limit_n = parse_insight_limit(limit)
            grain_key = parse_trend_grain(grain or "month")
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        metrics = (metric_spec,) if metric_spec is not None else TREND_METRICS
        driver_keys = (
            (dimension_spec.key,) if dimension_spec is not None else DEFAULT_DRIVER_DIMENSIONS
        )
        empty = InsightResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=False,
            period=None,
            comparison=OverviewComparison(available=False, reason=REASON_NO_HISTORY),
            empty=True,
            empty_reason=REASON_NO_HISTORY,
            applied=None,
            thresholds=thresholds,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            categories=catalog_categories,
        )
        if not scope.has_history or scope.window is None:
            return empty
        if grain_key != "month":
            return self._granular_insights(
                scope=scope,
                grain=grain_key,
                metrics=metrics,
                driver_keys=driver_keys,
                limit=limit_n,
                thresholds=thresholds,
                catalog_metrics=catalog_metrics,
                catalog_dimensions=catalog_dimensions,
                catalog_categories=catalog_categories,
            )
        if scope.comparison_window is None:
            reason = (
                REASON_INSUFFICIENT_COMPARISON
                if scope.comparison_reason == "comparison_disabled"
                else REASON_INSUFFICIENT_HISTORY
            )
            current_measures, current_count, day_min, day_max = self._sum(
                scope.client_id, scope.window, scope.filters
            )
            return InsightResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=_window_period(scope.window, current_count, day_min, day_max),
                comparison=_window_comparison(
                    None, available=False, reason=scope.comparison_reason
                ),
                empty=True,
                empty_reason=reason,
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                categories=catalog_categories,
            )
        current_measures, current_count, day_min, day_max = self._sum(
            scope.client_id, scope.window, scope.filters
        )
        period = _window_period(scope.window, current_count, day_min, day_max)
        prior_measures, prior_count, prior_min, prior_max = self._sum(
            scope.client_id, scope.comparison_window, scope.filters
        )
        comparison = _window_comparison(
            scope.comparison_window,
            available=True,
            reason=None,
            count=prior_count,
            day_min=prior_min,
            day_max=prior_max,
        )
        if current_count == 0:
            return InsightResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=period,
                comparison=comparison,
                comparison_shown=True,
                empty=True,
                empty_reason=REASON_EMPTY_PERIOD,
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                categories=catalog_categories,
            )
        grouped: dict[
            str,
            tuple[
                list[tuple[str, str, dict[str, object], int]],
                list[tuple[str, str, dict[str, object], int]],
            ],
        ] = {}
        for key in driver_keys:
            grouped[key] = (
                self._groups(scope.client_id, scope.window, scope.filters, key),
                self._groups(scope.client_id, scope.comparison_window, scope.filters, key),
            )
        drafts = build_insights(
            current_measures=current_measures,
            prior_measures=prior_measures,
            current_count=current_count,
            prior_count=prior_count,
            grouped=grouped,
            metrics=metrics,
            limit=limit_n,
        )
        items = [
            _serialize_insight(draft, index=index, period=period, comparison=comparison)
            for index, draft in enumerate(drafts, start=1)
        ]
        return InsightResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=period,
            comparison=comparison,
            comparison_shown=True,
            empty=len(items) == 0,
            empty_reason=REASON_NO_MATERIAL if not items else None,
            result_count=len(items),
            insights=items,
            applied=scope.applied,
            dropped_filters=scope.dropped,
            thresholds=thresholds,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            categories=catalog_categories,
        )

    def anomalies(
        self,
        *,
        principal: Principal,
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
        dimension: str | None = None,
        limit: int | str | None = None,
        grain: str | None = None,
    ) -> AnomalyResponse:
        scope = self._resolve_scope(
            principal=principal,
            requested_client_id=requested_client_id,
            period=period,
            month_start=month_start,
            day_from=day_from,
            day_to=day_to,
            compare=compare,
            compare_month_start=compare_month_start,
            compare_from=compare_from,
            compare_to=compare_to,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
        )
        catalog_metrics = [_metric_info(item) for item in TREND_METRICS]
        catalog_dimensions = [
            OverviewFilterOption(value=item.key, label=item.label) for item in DRIVER_DIMENSIONS
        ]
        catalog_kinds = [
            OverviewFilterOption(value=item, label=item.replace("_", " ")) for item in ANOMALY_KINDS
        ]
        thresholds = AnomalyThresholds(**anomaly_threshold_catalog())
        try:
            metric_spec = parse_anomaly_metric(metric)
            dimension_spec = parse_anomaly_dimension(dimension)
            limit_n = parse_anomaly_limit(limit)
            grain_key = parse_trend_grain(grain or "month")
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        metrics = (metric_spec,) if metric_spec is not None else TREND_METRICS
        driver_keys = (
            (dimension_spec.key,) if dimension_spec is not None else DEFAULT_DRIVER_DIMENSIONS
        )
        comparison = (
            _window_comparison(
                scope.comparison_window,
                available=scope.comparison_window is not None,
                reason=scope.comparison_reason,
            )
            if scope.comparison_window is not None
            else _window_comparison(None, available=False, reason=scope.comparison_reason)
        )
        empty = AnomalyResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=False,
            period=None,
            comparison=OverviewComparison(available=False, reason=REASON_NO_HISTORY),
            empty=True,
            empty_reason=ANOMALY_NO_HISTORY,
            thresholds=thresholds,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            kinds=catalog_kinds,
        )
        if not scope.has_history or scope.window is None:
            return empty
        if grain_key != "month":
            return self._granular_anomalies(
                scope=scope,
                grain=grain_key,
                metrics=metrics,
                driver_keys=driver_keys,
                limit=limit_n,
                thresholds=thresholds,
                catalog_metrics=catalog_metrics,
                catalog_dimensions=catalog_dimensions,
                catalog_kinds=catalog_kinds,
            )
        current_month = scope.window.month_start
        if scope.window.grain != "month" or current_month is None:
            current_measures, current_count, day_min, day_max = self._sum(
                scope.client_id, scope.window, scope.filters
            )
            return AnomalyResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=_window_period(scope.window, current_count, day_min, day_max),
                comparison=comparison,
                empty=True,
                empty_reason=REASON_PERIOD_NOT_MONTH,
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                kinds=catalog_kinds,
            )
        prior_months = select_baseline_months(scope.months, current_month)
        baseline_meta = AnomalyBaseline(
            month_starts=prior_months,
            observation_count=len(prior_months),
            required_count=MIN_BASELINE_MONTHS,
        )
        current_measures, current_count, day_min, day_max = self._sum(
            scope.client_id, scope.window, scope.filters
        )
        period = _window_period(scope.window, current_count, day_min, day_max)
        if len(prior_months) < MIN_BASELINE_MONTHS:
            return AnomalyResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=period,
                comparison=comparison,
                comparison_shown=comparison.available,
                empty=True,
                empty_reason=ANOMALY_INSUFFICIENT_HISTORY,
                baseline=baseline_meta,
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                kinds=catalog_kinds,
            )
        if current_count == 0:
            return AnomalyResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=period,
                comparison=comparison,
                comparison_shown=comparison.available,
                empty=True,
                empty_reason=ANOMALY_EMPTY_PERIOD,
                baseline=baseline_meta,
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                kinds=catalog_kinds,
            )
        lookback = PeriodWindow(
            grain="range",
            day_from=prior_months[0],
            day_to_exclusive=add_calendar_month(current_month),
            month_start=None,
            month_label=None,
        )
        total_rows, _series_count = self._history_series(
            scope.client_id, lookback, scope.filters, "month", breakdown=None
        )
        by_month = {
            bucket: MonthObservation(bucket, measures, count)
            for bucket, dim, _label, measures, count in total_rows
            if dim is None
        }
        current_obs = by_month.get(current_month) or MonthObservation(
            current_month, current_measures, current_count
        )
        baseline_obs = [by_month[item] for item in prior_months if item in by_month]
        grouped: dict[str, list[GroupMonth]] = {}
        for key in driver_keys:
            dim_rows, _n = self._history_series(
                scope.client_id,
                lookback,
                scope.filters,
                "month",
                breakdown=key,
                limit_series=SERIES_GROUP_LIMIT,
            )
            grouped[key] = [
                GroupMonth(
                    key=dim or "",
                    label=label or dim or "(blank)",
                    month_start=bucket,
                    measures=measures,
                    count=count,
                )
                for bucket, dim, label, measures, count in dim_rows
                if dim is not None
            ]
        drafts = build_anomalies(
            current=current_obs,
            baseline=baseline_obs,
            grouped=grouped,
            metrics=metrics,
            limit=limit_n,
        )
        items = [
            _serialize_anomaly(draft, index=index, period=period)
            for index, draft in enumerate(drafts, start=1)
        ]
        return AnomalyResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=period,
            comparison=comparison,
            comparison_shown=comparison.available,
            empty=len(items) == 0,
            empty_reason=REASON_NO_ANOMALIES if not items else None,
            result_count=len(items),
            anomalies=items,
            baseline=baseline_meta.model_copy(update={"observation_count": len(baseline_obs)}),
            applied=scope.applied,
            dropped_filters=scope.dropped,
            thresholds=thresholds,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            kinds=catalog_kinds,
        )

    def _granular_insights(
        self,
        *,
        scope: _AnalyticsScope,
        grain: str,
        metrics: tuple[TrendMetricSpec, ...],
        driver_keys: tuple[str, ...],
        limit: int,
        thresholds: InsightThresholds,
        catalog_metrics: list[TrendMetricInfo],
        catalog_dimensions: list[OverviewFilterOption],
        catalog_categories: list[OverviewFilterOption],
    ) -> InsightResponse:
        assert scope.window is not None
        current_rows, _ = self._history_series(
            scope.client_id, scope.window, scope.filters, grain
        )
        current_total = _bucket_rows(current_rows)
        current_slots = _bucket_slots(scope.window, grain)
        current_count, current_min, current_max = self._sum(
            scope.client_id, scope.window, scope.filters
        )[1:]
        if scope.comparison_window is None:
            return InsightResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=_window_period(scope.window, current_count, current_min, current_max),
                comparison=_window_comparison(
                    None, available=False, reason=scope.comparison_reason
                ),
                grain=grain,  # type: ignore[arg-type]
                empty=True,
                empty_reason=(
                    REASON_INSUFFICIENT_COMPARISON
                    if scope.comparison_reason == "comparison_disabled"
                    else REASON_INSUFFICIENT_HISTORY
                ),
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                categories=catalog_categories,
            )

        comparison_rows, _ = self._history_series(
            scope.client_id, scope.comparison_window, scope.filters, grain
        )
        comparison_total = _bucket_rows(comparison_rows)
        comparison_slots = _bucket_slots(scope.comparison_window, grain)
        current_groups = {
            key: _bucket_rows(
                self._history_series(
                    scope.client_id, scope.window, scope.filters, grain, breakdown=key
                )[0]
            )
            for key in driver_keys
        }
        comparison_groups = {
            key: _bucket_rows(
                self._history_series(
                    scope.client_id,
                    scope.comparison_window,
                    scope.filters,
                    grain,
                    breakdown=key,
                )[0]
            )
            for key in driver_keys
        }
        items: list[InsightItem] = []
        for slot, (_current_identity, bucket) in current_slots.items():
            current_payload = current_total.get((bucket, None))
            comparison_ref = comparison_slots.get(slot)
            if current_payload is None or comparison_ref is None:
                continue
            _comparison_identity, comparison_bucket = comparison_ref
            prior_payload = comparison_total.get((comparison_bucket, None))
            if prior_payload is None:
                continue
            current_measures, current_rows_count = current_payload[1], current_payload[2]
            prior_measures, prior_rows_count = prior_payload[1], prior_payload[2]
            grouped = {}
            for key in driver_keys:
                current_groups_for_bucket = [
                    (group_key, label or group_key or "(blank)", measures, count)
                    for (row_bucket, group_key), (label, measures, count) in current_groups[
                        key
                    ].items()
                    if row_bucket == bucket and group_key is not None
                ]
                prior_groups_for_bucket = [
                    (group_key, label or group_key or "(blank)", measures, count)
                    for (row_bucket, group_key), (label, measures, count) in comparison_groups[
                        key
                    ].items()
                    if row_bucket == comparison_bucket and group_key is not None
                ]
                grouped[key] = (current_groups_for_bucket, prior_groups_for_bucket)
            drafts = build_insights(
                current_measures=current_measures,
                prior_measures=prior_measures,
                current_count=current_rows_count,
                prior_count=prior_rows_count,
                grouped=grouped,
                metrics=metrics,
                limit=limit,
            )
            period = _bucket_period(scope.window, bucket, grain, current_rows_count)
            comparison = _bucket_comparison(
                scope.comparison_window, comparison_bucket, grain, prior_rows_count
            )
            for draft in drafts:
                draft.insight_id = f"{draft.insight_id}:{bucket.isoformat()}"
                items.append(
                    _serialize_insight(
                        draft,
                        index=len(items) + 1,
                        period=period,
                        comparison=comparison,
                    )
                )
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        return InsightResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=_window_period(scope.window, current_count, current_min, current_max),
            comparison=_window_comparison(
                scope.comparison_window,
                available=True,
                reason=None,
            ),
            grain=grain,  # type: ignore[arg-type]
            comparison_shown=True,
            empty=not items,
            empty_reason=REASON_NO_MATERIAL if not items else None,
            result_count=len(items),
            insights=items,
            applied=scope.applied,
            dropped_filters=scope.dropped,
            thresholds=thresholds,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            categories=catalog_categories,
        )

    def _granular_anomalies(
        self,
        *,
        scope: _AnalyticsScope,
        grain: str,
        metrics: tuple[TrendMetricSpec, ...],
        driver_keys: tuple[str, ...],
        limit: int,
        thresholds: AnomalyThresholds,
        catalog_metrics: list[TrendMetricInfo],
        catalog_dimensions: list[OverviewFilterOption],
        catalog_kinds: list[OverviewFilterOption],
    ) -> AnomalyResponse:
        assert scope.window is not None
        current_month = scope.window.month_start or month_of(scope.window.day_from)
        prior_months = select_baseline_months(scope.months, current_month)
        baseline = AnomalyBaseline(
            grain=grain,  # type: ignore[arg-type]
            month_starts=prior_months,
            observation_count=len(prior_months),
            required_count=MIN_BASELINE_MONTHS,
        )
        current_rows, _ = self._history_series(
            scope.client_id, scope.window, scope.filters, grain
        )
        current_total = _bucket_rows(current_rows)
        current_slots = _bucket_slots(scope.window, grain)
        current_count, current_min, current_max = self._sum(
            scope.client_id, scope.window, scope.filters
        )[1:]
        comparison = (
            _window_comparison(scope.comparison_window, available=True, reason=None)
            if scope.comparison_window is not None
            else _window_comparison(None, available=False, reason=scope.comparison_reason)
        )
        if len(prior_months) < MIN_BASELINE_MONTHS:
            return AnomalyResponse(
                client_id=scope.client_id,
                company_name=scope.company_name,
                has_published_history=True,
                period=_window_period(scope.window, current_count, current_min, current_max),
                comparison=comparison,
                grain=grain,  # type: ignore[arg-type]
                empty=True,
                empty_reason=ANOMALY_INSUFFICIENT_HISTORY,
                baseline=baseline,
                applied=scope.applied,
                dropped_filters=scope.dropped,
                thresholds=thresholds,
                metrics=catalog_metrics,
                dimensions=catalog_dimensions,
                kinds=catalog_kinds,
            )

        month_windows = {
            month: PeriodWindow(
                grain="month",
                day_from=month,
                day_to_exclusive=add_calendar_month(month),
                month_start=month,
                month_label=month_label(month),
            )
            for month in prior_months
        }
        historical_total = {
            month: _bucket_rows(
                self._history_series(
                    scope.client_id, month_windows[month], scope.filters, grain
                )[0]
            )
            for month in prior_months
        }
        historical_groups = {
            key: {
                month: _bucket_rows(
                    self._history_series(
                        scope.client_id,
                        month_windows[month],
                        scope.filters,
                        grain,
                        breakdown=key,
                        limit_series=SERIES_GROUP_LIMIT,
                    )[0]
                )
                for month in prior_months
            }
            for key in driver_keys
        }
        current_groups = {
            key: _bucket_rows(
                self._history_series(
                    scope.client_id, scope.window, scope.filters, grain, breakdown=key,
                    limit_series=SERIES_GROUP_LIMIT,
                )[0]
            )
            for key in driver_keys
        }
        items: list[AnomalyItem] = []
        for slot, (_current_identity, bucket) in current_slots.items():
            current_payload = current_total.get((bucket, None))
            if current_payload is None:
                continue
            baseline_observations = []
            grouped = {key: [] for key in driver_keys}
            for month in prior_months:
                prior_ref = _bucket_slots(month_windows[month], grain).get(slot)
                if prior_ref is None:
                    continue
                _prior_identity, prior_bucket = prior_ref
                prior_payload = historical_total[month].get((prior_bucket, None))
                if prior_payload is None:
                    continue
                grouped_payload = MonthObservation(
                    month,
                    prior_payload[1],
                    prior_payload[2],
                )
                baseline_observations.append(grouped_payload)
                for key in driver_keys:
                    for (row_bucket, group_key), (label, measures, count) in historical_groups[
                        key
                    ][month].items():
                        if row_bucket == prior_bucket and group_key is not None:
                            grouped[key].append(
                                GroupMonth(
                                    key=group_key,
                                    label=label or group_key or "(blank)",
                                    month_start=month,
                                    measures=measures,
                                    count=count,
                                )
                            )
            for key in driver_keys:
                for (row_bucket, group_key), (label, measures, count) in current_groups[
                    key
                ].items():
                    if row_bucket == bucket and group_key is not None:
                        grouped[key].append(
                            GroupMonth(
                                key=group_key,
                                label=label or group_key or "(blank)",
                                month_start=bucket,
                                measures=measures,
                                count=count,
                            )
                        )
            if len(baseline_observations) < MIN_BASELINE_MONTHS:
                continue
            current_observation = MonthObservation(
                bucket, current_payload[1], current_payload[2]
            )
            drafts = build_anomalies(
                current=current_observation,
                baseline=baseline_observations,
                grouped=grouped,
                metrics=metrics,
                limit=limit,
            )
            period = _bucket_period(scope.window, bucket, grain, current_payload[2])
            for draft in drafts:
                draft.anomaly_id = f"{draft.anomaly_id}:{bucket.isoformat()}"
                items.append(
                    _serialize_anomaly(draft, index=len(items) + 1, period=period)
                )
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        return AnomalyResponse(
            client_id=scope.client_id,
            company_name=scope.company_name,
            has_published_history=True,
            period=_window_period(scope.window, current_count, current_min, current_max),
            comparison=comparison,
            grain=grain,  # type: ignore[arg-type]
            comparison_shown=comparison.available,
            empty=not items,
            empty_reason=REASON_NO_ANOMALIES if not items else None,
            result_count=len(items),
            anomalies=items,
            baseline=baseline.model_copy(update={"observation_count": len(prior_months)}),
            applied=scope.applied,
            dropped_filters=scope.dropped,
            thresholds=thresholds,
            metrics=catalog_metrics,
            dimensions=catalog_dimensions,
            kinds=catalog_kinds,
        )

    def _resolve_scope(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
        period: str | None,
        month_start: date | None,
        day_from: date | None,
        day_to: date | None,
        compare: str | None,
        compare_month_start: date | None,
        compare_from: date | None,
        compare_to: date | None,
        campaign_ids: list[str] | None,
        channels: list[str] | None,
        filter_logic_1: list[str] | None,
        filter_logic_1_group: list[str] | None,
    ) -> _AnalyticsScope:
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            raise AuthorizationError("Not authorized to access this client.")
        require_company_active(self._clients, client_id)
        company = self._clients.get(client_id) if self._clients is not None else None
        company_name = company.name if company is not None else None
        months = self._publications.list_published_month_starts(client_id)
        published_min, published_max = self._publications.published_day_bounds(client_id)
        month_options = [
            OverviewMonthOption(month_start=item, month_label=month_label(item)) for item in months
        ]
        empty_options = OverviewFilterOptions(
            months=month_options,
            published_day_min=published_min,
            published_day_max=published_max,
        )
        if not months or published_min is None or published_max is None:
            return _AnalyticsScope(
                client_id=client_id,
                company_name=company_name,
                months=months,
                published_min=published_min,
                published_max=published_max,
                window=None,
                comparison_window=None,
                compare_mode="auto",
                comparison_reason=REASON_NO_HISTORY,
                filters=DimensionFilters(),
                dropped={},
                applied=None,
                options=empty_options,
                has_history=False,
            )
        try:
            requested_campaigns = normalize_multi(campaign_ids, label="campaign")
            requested_channels = normalize_multi(channels, label="channel")
            requested_fl1 = normalize_multi(filter_logic_1, label="filter_logic_1")
            requested_fl1g = normalize_multi(filter_logic_1_group, label="filter_logic_1_group")
            window, default_period = resolve_period(
                months=months,
                published_min=published_min,
                published_max=published_max,
                period=period,
                month_start=month_start,
                day_from=day_from,
                day_to=day_to,
            )
            comparison_window, compare_mode, comparison_reason = resolve_comparison(
                months=months,
                current=window,
                compare=compare,
                compare_month_start=compare_month_start,
                compare_from=compare_from,
                compare_to=compare_to,
                published_min=published_min,
                published_max=published_max,
            )
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc

        options = self._options(client_id, window, month_options, published_min, published_max)
        allowed_campaigns = {item.value for item in options.campaigns}
        allowed_channels = {item.value for item in options.channels}
        allowed_fl1 = {item.value for item in options.filter_logic_1}
        allowed_fl1g = {item.value for item in options.filter_logic_1_group}
        kept_campaigns, drop_campaigns = drop_unknown(requested_campaigns, allowed_campaigns)
        kept_channels, drop_channels = drop_unknown(requested_channels, allowed_channels)
        kept_fl1, drop_fl1 = drop_unknown(requested_fl1, allowed_fl1)
        kept_fl1g, drop_fl1g = drop_unknown(requested_fl1g, allowed_fl1g)
        filters = DimensionFilters(
            campaign_ids=kept_campaigns,
            channels=kept_channels,
            filter_logic_1=kept_fl1,
            filter_logic_1_group=kept_fl1g,
        )
        dropped = {
            key: list(values)
            for key, values in (
                ("campaign_id", drop_campaigns),
                ("channel", drop_channels),
                ("filter_logic_1", drop_fl1),
                ("filter_logic_1_group", drop_fl1g),
            )
            if values
        }
        is_default = (
            default_period
            and compare_mode == "auto"
            and compare_month_start is None
            and compare_from is None
            and compare_to is None
            and not filters.any()
            and not dropped
        )
        applied = OverviewAppliedState(
            is_default=is_default,
            period=window.grain if window.grain != "all_history" else "all_history",
            compare=compare_mode,
            month_start=window.month_start,
            day_from=window.day_from,
            day_to=window.inclusive_to,
            compare_month_start=comparison_window.month_start if comparison_window else None,
            campaign_ids=list(filters.campaign_ids),
            channels=list(filters.channels),
            filter_logic_1=list(filters.filter_logic_1),
            filter_logic_1_group=list(filters.filter_logic_1_group),
        )
        return _AnalyticsScope(
            client_id=client_id,
            company_name=company_name,
            months=months,
            published_min=published_min,
            published_max=published_max,
            window=window,
            comparison_window=comparison_window,
            compare_mode=compare_mode,
            comparison_reason=comparison_reason,
            filters=filters,
            dropped=dropped,
            applied=applied,
            options=options,
            has_history=True,
        )

    def _options(
        self,
        client_id: str,
        window: PeriodWindow,
        month_options: list[OverviewMonthOption],
        published_min: date | None,
        published_max: date | None,
    ) -> OverviewFilterOptions:
        def values(dimension: str) -> list[OverviewFilterOption]:
            rows = self._publications.list_published_filter_values(
                client_id,
                day_from=window.day_from,
                day_to_exclusive=window.day_to_exclusive,
                dimension=dimension,
            )
            return [OverviewFilterOption(value=value, label=label) for value, label in rows]

        return OverviewFilterOptions(
            months=month_options,
            published_day_min=published_min,
            published_day_max=published_max,
            campaigns=values("campaign_id"),
            channels=values("channel"),
            filter_logic_1=values("filter_logic_1"),
            filter_logic_1_group=values("filter_logic_1_group"),
        )

    def _sum(self, client_id: str, window: PeriodWindow, filters: DimensionFilters):
        return self._publications.sum_published_history(
            client_id,
            day_from=window.day_from,
            day_to_exclusive=window.day_to_exclusive,
            campaign_ids=filters.campaign_ids,
            channels=filters.channels,
            filter_logic_1=filters.filter_logic_1,
            filter_logic_1_group=filters.filter_logic_1_group,
        )

    def _series(
        self,
        client_id: str,
        window: PeriodWindow,
        filters: DimensionFilters,
        grain: str,
        breakdown: TrendDimensionSpec | None,
        primary: TrendMetricSpec,
    ) -> tuple[list[tuple[date, str | None, str | None, dict[str, object], int]], int]:
        return self._publications.list_published_history_series(
            client_id,
            day_from=window.day_from,
            day_to_exclusive=window.day_to_exclusive,
            grain=grain,
            breakdown=breakdown.key if breakdown else None,
            rank_measure=rank_measure_name(primary),
            campaign_ids=filters.campaign_ids,
            channels=filters.channels,
            filter_logic_1=filters.filter_logic_1,
            filter_logic_1_group=filters.filter_logic_1_group,
        )

    def _history_series(
        self,
        client_id: str,
        window: PeriodWindow,
        filters: DimensionFilters,
        grain: str,
        *,
        breakdown: str | None = None,
        limit_series: int | None = None,
    ) -> tuple[list[tuple[date, str | None, str | None, dict[str, object], int]], int]:
        return self._publications.list_published_history_series(
            client_id,
            day_from=window.day_from,
            day_to_exclusive=window.day_to_exclusive,
            grain=grain,
            breakdown=breakdown,
            rank_measure="total_cost",
            limit_series=limit_series,
            campaign_ids=filters.campaign_ids,
            channels=filters.channels,
            filter_logic_1=filters.filter_logic_1,
            filter_logic_1_group=filters.filter_logic_1_group,
        )

    def _groups(
        self,
        client_id: str,
        window: PeriodWindow,
        filters: DimensionFilters,
        dimension: str,
    ) -> list[tuple[str, str, dict[str, object], int]]:
        return self._publications.list_published_history_groups(
            client_id,
            day_from=window.day_from,
            day_to_exclusive=window.day_to_exclusive,
            dimension=dimension,
            campaign_ids=filters.campaign_ids,
            channels=filters.channels,
            filter_logic_1=filters.filter_logic_1,
            filter_logic_1_group=filters.filter_logic_1_group,
        )

    def _pairs(
        self,
        client_id: str,
        window: PeriodWindow,
        filters: DimensionFilters,
        primary: str,
        secondary: str,
    ) -> list[tuple[str, str, str, str, dict[str, object], int]]:
        return self._publications.list_published_history_pairs(
            client_id,
            day_from=window.day_from,
            day_to_exclusive=window.day_to_exclusive,
            primary=primary,
            secondary=secondary,
            campaign_ids=filters.campaign_ids,
            channels=filters.channels,
            filter_logic_1=filters.filter_logic_1,
            filter_logic_1_group=filters.filter_logic_1_group,
        )

    def _validate_parents(
        self,
        client_id: str,
        window: PeriodWindow,
        filters: DimensionFilters,
        parents: tuple,
    ):
        running = filters
        for parent in parents:
            values = {row[0] for row in self._groups(client_id, window, running, parent.dimension)}
            if parent.value not in values:
                raise FilterValidationError("Parent value is not in this period.")
            running = apply_parents(running, (parent,))
        return running

    def _parent_label(self, scope: _AnalyticsScope, dimension: str, value: str) -> str:
        if value == "":
            return "(blank)"
        options = {
            "campaign_id": scope.options.campaigns,
            "channel": scope.options.channels,
            "filter_logic_1": scope.options.filter_logic_1,
            "filter_logic_1_group": scope.options.filter_logic_1_group,
        }.get(dimension, [])
        for item in options:
            if item.value == value:
                return item.label
        return value


def _pick(spec, measures: dict, kpis: dict):
    if spec.source == "measure":
        return measures.get(spec.measure)
    return kpis.get(spec.kpi_slug)


def _card(spec, current_measures, current_kpis, prior_measures, prior_kpis) -> OverviewKpiCard:
    current = _pick(spec, current_measures, current_kpis)
    prior = _pick(spec, prior_measures, prior_kpis) if prior_measures is not None else None
    return OverviewKpiCard(
        id=spec.id,
        label=spec.label,
        kind=spec.kind,
        definition=spec.definition,
        value=_serialize(spec.kind, current),
        prior_value=_serialize(spec.kind, prior) if prior_measures is not None else None,
        delta=_delta(spec.kind, current, prior) if prior_measures is not None else None,
        delta_pct=_delta_pct(current, prior) if prior_measures is not None else None,
        numerator=_operand(current_measures, spec.numerator),
        denominator=_operand(current_measures, spec.denominator),
    )


def _volume(spec: TrendMetricSpec, measures: dict) -> Decimal:
    return as_decimal(measures.get(rank_measure_name(spec))) or Decimal("0")


def _combine_measures(groups: list[dict[str, object]]) -> dict[str, object]:
    totals: dict[str, object] = {}
    for measures in groups:
        for name in ADDITIVE_MEASURES:
            current = as_decimal(totals.get(name)) or Decimal("0")
            extra = as_decimal(measures.get(name)) or Decimal("0")
            totals[name] = current + extra
    return totals


def _ranked_rows(
    spec: TrendMetricSpec,
    current_groups: list[tuple[str, str, dict[str, object], int]],
    prior_groups: list[tuple[str, str, dict[str, object], int]] | None,
    *,
    can_go_deeper: bool,
) -> tuple[list[DrillRow], bool]:
    prior_map = {key: measures for key, _label, measures, _count in prior_groups or []}
    ordered = sorted(
        current_groups,
        key=lambda item: (-_volume(spec, item[2]), item[1].lower(), item[0]),
    )
    total_volume = sum(
        (_volume(spec, measures) for _key, _label, measures, _n in ordered), Decimal("0")
    )
    truncated = len(ordered) > MAX_DRILL_ROWS
    kept = ordered[:MAX_DRILL_ROWS]
    leftover = ordered[MAX_DRILL_ROWS:]
    rows: list[DrillRow] = []
    for index, (key, label, measures, count) in enumerate(kept, start=1):
        kpis = compute_kpis(measures, namespace="client")
        value = _pick_metric(spec, measures, kpis)
        prior_measures = prior_map.get(key)
        prior_kpis = compute_kpis(prior_measures, namespace="client") if prior_measures else None
        prior = _pick_metric(spec, prior_measures, prior_kpis) if prior_measures else None
        rows.append(
            DrillRow(
                rank=index,
                key=key,
                label=label,
                value=_serialize(spec.kind, value),
                prior_value=_serialize(spec.kind, prior) if prior_groups is not None else None,
                delta=_delta(spec.kind, value, prior) if prior_groups is not None else None,
                delta_pct=_delta_pct(value, prior) if prior_groups is not None else None,
                contribution_pct=decimal_to_api(
                    safe_divide(_volume(spec, measures), total_volume, scale=RATE_SCALE)
                ),
                numerator=_operand(measures, spec.numerator),
                denominator=_operand(measures, spec.denominator),
                grain_row_count=count,
                drillable=can_go_deeper and key != OTHER_SERIES_KEY,
            )
        )
    if leftover:
        leftover_measures = _combine_measures([item[2] for item in leftover])
        leftover_count = sum(item[3] for item in leftover)
        leftover_prior = None
        if prior_groups is not None:
            leftover_prior = _combine_measures(
                [prior_map[key] for key, _label, _m, _n in leftover if key in prior_map]
            )
        kpis = compute_kpis(leftover_measures, namespace="client")
        value = _pick_metric(spec, leftover_measures, kpis)
        prior = None
        if leftover_prior:
            prior = _pick_metric(
                spec, leftover_prior, compute_kpis(leftover_prior, namespace="client")
            )
        rows.append(
            DrillRow(
                rank=None,
                key=OTHER_SERIES_KEY,
                label="Other",
                value=_serialize(spec.kind, value),
                prior_value=_serialize(spec.kind, prior) if prior_groups is not None else None,
                delta=_delta(spec.kind, value, prior) if prior_groups is not None else None,
                delta_pct=_delta_pct(value, prior) if prior_groups is not None else None,
                contribution_pct=decimal_to_api(
                    safe_divide(_volume(spec, leftover_measures), total_volume, scale=RATE_SCALE)
                ),
                numerator=_operand(leftover_measures, spec.numerator),
                denominator=_operand(leftover_measures, spec.denominator),
                grain_row_count=leftover_count,
                drillable=False,
            )
        )
    return rows, truncated


@dataclass
class _ExplorerBuiltRow:
    key: str
    label: str
    parent_key: str | None
    parent_label: str | None
    value: object
    prior: object
    count: int
    prior_count: int
    measures: dict[str, object]
    prior_measures: dict[str, object] | None = None


def _numeric(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return as_decimal(value)


def _explorer_from_groups(
    spec: TrendMetricSpec,
    current_groups: list[tuple[str, str, dict[str, object], int]],
    prior_groups: list[tuple[str, str, dict[str, object], int]] | None,
) -> list[_ExplorerBuiltRow]:
    prior_map = {key: (measures, count) for key, _label, measures, count in prior_groups or []}
    rows: list[_ExplorerBuiltRow] = []
    for key, label, measures, count in current_groups:
        kpis = compute_kpis(measures, namespace="client")
        value = _pick_metric(spec, measures, kpis)
        prior_payload = prior_map.get(key)
        prior = None
        prior_count = 0
        if prior_payload is not None:
            prior_measures, prior_count = prior_payload
            prior = _pick_metric(
                spec, prior_measures, compute_kpis(prior_measures, namespace="client")
            )
        rows.append(
            _ExplorerBuiltRow(
                key=key,
                label=label,
                parent_key=None,
                parent_label=None,
                value=value,
                prior=prior if prior_groups is not None else None,
                count=count,
                prior_count=prior_count,
                measures=measures,
                prior_measures=prior_payload[0] if prior_payload is not None else None,
            )
        )
    return rows


def _explorer_from_pairs(
    spec: TrendMetricSpec,
    current_pairs: list[tuple[str, str, str, str, dict[str, object], int]],
    prior_pairs: list[tuple[str, str, str, str, dict[str, object], int]] | None,
) -> list[_ExplorerBuiltRow]:
    prior_map = {
        (pkey, skey): (measures, count)
        for pkey, _plabel, skey, _slabel, measures, count in prior_pairs or []
    }
    rows: list[_ExplorerBuiltRow] = []
    for pkey, plabel, skey, slabel, measures, count in current_pairs:
        kpis = compute_kpis(measures, namespace="client")
        value = _pick_metric(spec, measures, kpis)
        prior_payload = prior_map.get((pkey, skey))
        prior = None
        prior_count = 0
        if prior_payload is not None:
            prior_measures, prior_count = prior_payload
            prior = _pick_metric(
                spec, prior_measures, compute_kpis(prior_measures, namespace="client")
            )
        rows.append(
            _ExplorerBuiltRow(
                key=skey,
                label=slabel,
                parent_key=pkey,
                parent_label=plabel,
                value=value,
                prior=prior if prior_pairs is not None else None,
                count=count,
                prior_count=prior_count,
                measures=measures,
                prior_measures=prior_payload[0] if prior_payload is not None else None,
            )
        )
    return rows


def _explorer_metric_values(
    measures: dict[str, object],
    prior_measures: dict[str, object] | None,
    *,
    comparison_available: bool,
) -> list[ExplorerMetricValues]:
    """Project every D1/D3 catalog metric from already-aggregated group measures."""

    kpis = compute_kpis(measures, namespace="client")
    prior_kpis = (
        compute_kpis(prior_measures, namespace="client") if prior_measures is not None else None
    )
    cells: list[ExplorerMetricValues] = []
    for spec in TREND_METRICS:
        current = _pick_metric(spec, measures, kpis)
        prior = (
            _pick_metric(spec, prior_measures, prior_kpis)
            if comparison_available and prior_measures is not None and prior_kpis is not None
            else None
        )
        cells.append(
            ExplorerMetricValues(
                key=spec.key,
                value=_serialize(spec.kind, current),
                prior_value=_serialize(spec.kind, prior) if comparison_available else None,
                delta=_delta(spec.kind, current, prior) if comparison_available else None,
                delta_pct=_delta_pct(current, prior) if comparison_available else None,
            )
        )
    return cells


def _explorer_sort_tuple(
    spec: TrendMetricSpec,
    item: _ExplorerBuiltRow,
    *,
    sort: str,
    contribution_pct: Decimal | None,
    reverse: bool,
) -> tuple:
    if sort == "delta":
        raw = (
            _numeric(item.value) - _numeric(item.prior)
            if item.value is not None and item.prior is not None
            else None
        )
        if spec.kind == "count" and raw is not None:
            raw = Decimal(int(raw))
    elif sort == "delta_pct":
        raw = safe_divide(
            safe_subtract(item.value, item.prior)
            if item.value is not None and item.prior is not None
            else None,
            item.prior,
            scale=RATE_SCALE,
        )
    elif sort == "contribution":
        raw = contribution_pct
    else:
        raw = _numeric(item.value)
    null_rank = 1 if raw is None else 0
    number = Decimal("0") if raw is None else raw
    ordered = -number if reverse else number
    parent = (item.parent_label or "").lower()
    return (null_rank, ordered, parent, item.label.lower(), item.parent_key or "", item.key)


def _rank_explorer_rows(
    spec: TrendMetricSpec,
    built: list[_ExplorerBuiltRow],
    total_metric: object,
    *,
    mode: str,
    mover: str | None,
    sort: str,
    direction: str,
    limit: int,
    min_value: Decimal | None,
    min_contribution: Decimal | None,
    show_contribution: bool,
    comparison_available: bool,
    dimension,
    secondary,
) -> tuple[list[ExplorerRow], int, bool]:
    prepared: list[tuple[_ExplorerBuiltRow, Decimal | None, Decimal | None]] = []
    total_num = _numeric(total_metric)
    for item in built:
        current_num = _numeric(item.value)
        contrib = None
        if show_contribution and current_num is not None and total_num is not None:
            contrib = safe_divide(current_num, total_num, scale=RATE_SCALE)
        if min_value is not None and (current_num is None or current_num < min_value):
            continue
        if min_contribution is not None and (contrib is None or contrib < min_contribution):
            continue
        if mode == "movers":
            if not comparison_available or item.prior is None or item.value is None:
                continue
            delta_num = current_num - _numeric(item.prior) if current_num is not None else None
            if delta_num is None:
                continue
            if mover == "up" and delta_num <= 0:
                continue
            if mover == "down" and delta_num >= 0:
                continue
        prepared.append((item, contrib, current_num))
    reverse = direction == "desc"
    prepared.sort(
        key=lambda pair: _explorer_sort_tuple(
            spec, pair[0], sort=sort, contribution_pct=pair[1], reverse=reverse
        )
    )
    result_count = len(prepared)
    truncated = result_count > limit
    kept = prepared[:limit]
    rows: list[ExplorerRow] = []
    for index, (item, contrib, _current_num) in enumerate(kept, start=1):
        drill_dimension, drill_parents = explorer_drill_target(
            dimension, secondary, key=item.key, parent_key=item.parent_key
        )
        rows.append(
            ExplorerRow(
                rank=index,
                key=item.key,
                label=item.label,
                parent_key=item.parent_key,
                parent_label=item.parent_label,
                value=_serialize(spec.kind, item.value),
                prior_value=_serialize(spec.kind, item.prior) if comparison_available else None,
                delta=_delta(spec.kind, item.value, item.prior) if comparison_available else None,
                delta_pct=_delta_pct(item.value, item.prior) if comparison_available else None,
                contribution_pct=decimal_to_api(contrib) if show_contribution else None,
                contribution_amount=_serialize(spec.kind, item.value)
                if show_contribution
                else None,
                numerator=_operand(item.measures, spec.numerator),
                denominator=_operand(item.measures, spec.denominator),
                grain_row_count=item.count,
                drillable=bool(drill_dimension),
                drill_dimension=drill_dimension,
                drill_parents=list(drill_parents),
                metrics=_explorer_metric_values(
                    item.measures,
                    item.prior_measures,
                    comparison_available=comparison_available,
                ),
            )
        )
    return rows, result_count, truncated
