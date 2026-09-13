"""D4 drilldown path rules. Reuses D3 metric/dimension registries.

Day is a terminal time grouping, not a D2 filter field. Maximum depth is 3.
Formulas stay in kpis.py. Parent values are allowlisted dimension equals-filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from dfip_analytics.filters import (
    DIMENSION_COLUMNS,
    DimensionFilters,
    FilterValidationError,
    PeriodWindow,
    add_calendar_month,
)
from dfip_analytics.trends import (
    OTHER_SERIES_KEY,
    TREND_DIMENSIONS,
    TrendDimensionSpec,
    TrendMetricSpec,
    bucket_start,
    iter_buckets,
    parse_trend_grain,
    parse_trend_metric,
)

MAX_DRILL_DEPTH = 3
MAX_DRILL_ROWS = 50
DEFAULT_DRILL_DIMENSION = "campaign_id"
DRILL_ORIGINS = ("kpi", "trend")
DRILL_ORDER: tuple[str, ...] = (
    "campaign_id",
    "channel",
    "filter_logic_1",
    "filter_logic_1_group",
    "day",
)
PARENT_FILTER_FIELD = {
    "campaign_id": "campaign_ids",
    "channel": "channels",
    "filter_logic_1": "filter_logic_1",
    "filter_logic_1_group": "filter_logic_1_group",
}

DAY_DIMENSION = TrendDimensionSpec("day", "Day", "day", True, False)
DRILL_DIMENSIONS: tuple[TrendDimensionSpec, ...] = TREND_DIMENSIONS + (DAY_DIMENSION,)
DRILL_DIMENSIONS_BY_KEY: dict[str, TrendDimensionSpec] = {
    item.key: item for item in DRILL_DIMENSIONS
}


@dataclass(frozen=True)
class DrillParent:
    dimension: str
    value: str
    label: str | None = None


def parse_drill_origin(raw: str | None) -> str:
    origin = (raw or "kpi").strip().lower()
    if origin not in DRILL_ORIGINS:
        raise FilterValidationError("Drill origin must be kpi or trend.")
    return origin


def parse_drill_dimension(raw: str | None) -> TrendDimensionSpec:
    key = (raw or DEFAULT_DRILL_DIMENSION).strip()
    spec = DRILL_DIMENSIONS_BY_KEY.get(key)
    if spec is None:
        raise FilterValidationError("Unknown drill dimension.")
    if not spec.grouping:
        raise FilterValidationError(f"{spec.label} cannot be used as a drill dimension.")
    return spec


def parse_parent_token(raw: str) -> DrillParent:
    text = str(raw)
    if ":" not in text:
        raise FilterValidationError("Drill parent must be dimension:value.")
    dimension, value = text.split(":", 1)
    spec = DRILL_DIMENSIONS_BY_KEY.get(dimension.strip())
    if spec is None:
        raise FilterValidationError("Unknown drill parent dimension.")
    if spec.key == "day":
        raise FilterValidationError("Day cannot be a drill parent. It is a terminal grouping.")
    if spec.key not in PARENT_FILTER_FIELD:
        raise FilterValidationError("Unknown drill parent dimension.")
    return DrillParent(dimension=spec.key, value=value)


def parse_parents(raw: list[str] | None) -> tuple[DrillParent, ...]:
    if not raw:
        return ()
    parents = tuple(parse_parent_token(item) for item in raw)
    if len(parents) > MAX_DRILL_DEPTH - 1:
        raise FilterValidationError(f"Drill depth cannot exceed {MAX_DRILL_DEPTH}.")
    seen: set[str] = set()
    for parent in parents:
        if parent.dimension in seen:
            raise FilterValidationError("A drill path cannot repeat a dimension.")
        if parent.value == OTHER_SERIES_KEY:
            raise FilterValidationError("Other is not a drillable parent.")
        seen.add(parent.dimension)
    return parents


def validate_drill_selection(
    metric: TrendMetricSpec,
    dimension: TrendDimensionSpec,
    parents: tuple[DrillParent, ...],
) -> None:
    if dimension.key != "day" and dimension.key not in metric.dimensions:
        raise FilterValidationError(f"{metric.label} cannot be broken down by {dimension.label}.")
    if dimension.key in {item.dimension for item in parents}:
        raise FilterValidationError("A drill path cannot repeat a dimension.")
    depth = len(parents) + 1
    if depth > MAX_DRILL_DEPTH:
        raise FilterValidationError(f"Drill depth cannot exceed {MAX_DRILL_DEPTH}.")
    if dimension.key == "day" and depth > MAX_DRILL_DEPTH:
        raise FilterValidationError(f"Drill depth cannot exceed {MAX_DRILL_DEPTH}.")


def next_dimensions(
    parents: tuple[DrillParent, ...], current: TrendDimensionSpec
) -> tuple[str, ...]:
    used = {item.dimension for item in parents} | {current.key}
    if current.key == "day" or len(parents) + 1 >= MAX_DRILL_DEPTH:
        return ()
    return tuple(key for key in DRILL_ORDER if key not in used)


def apply_parents(filters: DimensionFilters, parents: tuple[DrillParent, ...]) -> DimensionFilters:
    data = {
        "campaign_ids": filters.campaign_ids,
        "channels": filters.channels,
        "filter_logic_1": filters.filter_logic_1,
        "filter_logic_1_group": filters.filter_logic_1_group,
    }
    for parent in parents:
        field = PARENT_FILTER_FIELD[parent.dimension]
        existing: tuple[str, ...] = data[field]
        if existing and parent.value not in existing:
            raise FilterValidationError("Parent value is not in the current filter scope.")
        data[field] = (parent.value,)
    return DimensionFilters(**data)


def bucket_end_exclusive(bucket: date, grain: str) -> date:
    if grain == "day":
        return bucket + timedelta(days=1)
    if grain == "week":
        return bucket + timedelta(days=7)
    if grain == "month":
        return add_calendar_month(bucket)
    raise FilterValidationError("Time grain must be day, week, or month.")


def intersect_slice(window: PeriodWindow, grain: str, bucket: date) -> PeriodWindow:
    start = max(bucket, window.day_from)
    end = min(bucket_end_exclusive(bucket, grain), window.day_to_exclusive)
    if start >= end:
        raise FilterValidationError("Selected trend slice is not in the current period.")
    return PeriodWindow(
        grain="range",
        day_from=start,
        day_to_exclusive=end,
        month_start=None,
        month_label=None,
    )


def resolve_slice_windows(
    window: PeriodWindow,
    comparison_window: PeriodWindow | None,
    *,
    origin: str,
    slice_grain: str | None,
    slice_bucket: date | None,
) -> tuple[PeriodWindow, PeriodWindow | None]:
    if origin != "trend":
        return window, comparison_window
    if slice_grain is None or slice_bucket is None:
        raise FilterValidationError("A trend drill requires slice_grain and slice_bucket.")
    grain = parse_trend_grain(slice_grain)
    aligned = bucket_start(slice_bucket, grain)
    current_buckets = iter_buckets(window.day_from, window.day_to_exclusive, grain)
    if aligned not in current_buckets:
        raise FilterValidationError("Selected trend slice is not in the current period.")
    current = intersect_slice(window, grain, aligned)
    if comparison_window is None:
        return current, None
    compare_buckets = iter_buckets(
        comparison_window.day_from, comparison_window.day_to_exclusive, grain
    )
    index = current_buckets.index(aligned)
    if index >= len(compare_buckets):
        return current, None
    return current, intersect_slice(comparison_window, grain, compare_buckets[index])


def drill_metric(raw: str | None) -> TrendMetricSpec:
    return parse_trend_metric(raw, role="primary")


def column_for_dimension(dimension: str) -> str:
    if dimension == "day":
        return "day"
    column = DIMENSION_COLUMNS.get(dimension)
    if column is None:
        raise FilterValidationError("Unknown drill dimension.")
    return column
