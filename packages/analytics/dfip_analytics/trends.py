"""D3 Dynamic Trends: registry and time-bucket helpers.

Metric formulas stay in kpis.py / HERO_KPIS. This module only declares
which of those metrics may be trended, at which grains, and with which
allowlisted breakdowns. Week buckets are ISO-8601 (Monday start).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from dfip_core.transform.derive import month_label
from dfip_core.transform.derive import month_start as month_of

from dfip_analytics.filters import ALLOWED_DIMENSIONS, DIMENSION_COLUMNS, FilterValidationError
from dfip_analytics.kpis import ADDITIVE_MEASURES
from dfip_analytics.overview import HERO_KPIS, HeroKpi

TrendGrain = Literal["day", "week", "month"]
TREND_GRAINS: tuple[TrendGrain, ...] = ("day", "week", "month")
DEFAULT_TREND_METRIC = "total_cost"
DEFAULT_TREND_GRAIN: TrendGrain = "day"
MAX_BREAKDOWN_SERIES = 8
MAX_INCLUDE_METRICS = 8
OTHER_SERIES_KEY = "__other__"
TOTAL_SERIES_KEY = "total"
MAX_SERIES_ROWS = 20000
REASON_COMPARISON_WITH_BREAKDOWN = "comparison_not_shown_with_breakdown"

# Postgres date_trunc('week') is Monday-based, matching ISO-8601.
BUCKET_SQL: dict[str, str] = {
    "day": "day",
    "week": "(date_trunc('week', day::timestamp))::date",
    "month": "(date_trunc('month', day::timestamp))::date",
}


@dataclass(frozen=True)
class TrendMetricSpec:
    """Trend projection of a D1 hero KPI. Formula source is HERO_KPIS + compute_kpis."""

    key: str
    label: str
    kind: str
    source: str
    measure: str | None
    kpi_slug: str | None
    definition: str
    numerator: str | None
    denominator: str | None
    additive: bool
    grains: tuple[TrendGrain, ...]
    dimensions: tuple[str, ...]
    primary: bool
    secondary: bool
    format: str


@dataclass(frozen=True)
class TrendDimensionSpec:
    key: str
    label: str
    column: str
    grouping: bool
    filtered_by_d2: bool


def _from_hero(hero: HeroKpi) -> TrendMetricSpec:
    additive = hero.source == "measure"
    return TrendMetricSpec(
        key=hero.id,
        label=hero.label,
        kind=hero.kind,
        source=hero.source,
        measure=hero.measure,
        kpi_slug=hero.kpi_slug,
        definition=hero.definition,
        numerator=hero.numerator,
        denominator=hero.denominator,
        additive=additive,
        grains=TREND_GRAINS,
        dimensions=ALLOWED_DIMENSIONS,
        primary=True,
        secondary=True,
        format=hero.kind,
    )


TREND_METRICS: tuple[TrendMetricSpec, ...] = tuple(_from_hero(hero) for hero in HERO_KPIS)
TREND_METRICS_BY_KEY: dict[str, TrendMetricSpec] = {item.key: item for item in TREND_METRICS}

TREND_DIMENSIONS: tuple[TrendDimensionSpec, ...] = (
    TrendDimensionSpec("campaign_id", "Campaign", "campaign_id", True, True),
    TrendDimensionSpec("channel", "Channel", "channel", True, True),
    TrendDimensionSpec("filter_logic_1", "Filter Logic 1", "filter_logic_1", True, True),
    TrendDimensionSpec(
        "filter_logic_1_group", "Filter Logic 1 group", "filter_logic_1_group", True, True
    ),
)
TREND_DIMENSIONS_BY_KEY: dict[str, TrendDimensionSpec] = {
    item.key: item for item in TREND_DIMENSIONS
}


def iso_week_start(day: date) -> date:
    """Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.weekday())


def bucket_start(day: date, grain: str) -> date:
    if grain == "day":
        return day
    if grain == "week":
        return iso_week_start(day)
    if grain == "month":
        return month_of(day)
    raise FilterValidationError("Time grain must be day, week, or month.")


def bucket_label(bucket: date, grain: str) -> str:
    if grain == "day":
        return bucket.isoformat()
    if grain == "week":
        iso_year, iso_week, _iso_day = bucket.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return month_label(bucket)


def iter_buckets(day_from: date, day_to_exclusive: date, grain: str) -> list[date]:
    if day_from >= day_to_exclusive:
        return []
    first = bucket_start(day_from, grain)
    last = bucket_start(day_to_exclusive - timedelta(days=1), grain)
    out = [first]
    cursor = first
    while cursor < last:
        if grain == "day":
            cursor = cursor + timedelta(days=1)
        elif grain == "week":
            cursor = cursor + timedelta(days=7)
        else:
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = date(year, month, 1)
        out.append(cursor)
    return out


def parse_trend_grain(raw: str | None) -> TrendGrain:
    grain = (raw or DEFAULT_TREND_GRAIN).strip().lower()
    if grain not in TREND_GRAINS:
        raise FilterValidationError("Time grain must be day, week, or month.")
    return grain  # type: ignore[return-value]


def parse_trend_metric(raw: str | None, *, role: str) -> TrendMetricSpec:
    key = (raw or DEFAULT_TREND_METRIC).strip()
    spec = TREND_METRICS_BY_KEY.get(key)
    if spec is None:
        raise FilterValidationError(f"Unknown {role} metric.")
    if role == "primary" and not spec.primary:
        raise FilterValidationError(f"{spec.label} cannot be a primary trend metric.")
    if role == "secondary" and not spec.secondary:
        raise FilterValidationError(f"{spec.label} cannot be a secondary trend metric.")
    return spec


def parse_breakdown(raw: str | None) -> TrendDimensionSpec | None:
    if raw is None or str(raw).strip() == "":
        return None
    key = str(raw).strip()
    spec = TREND_DIMENSIONS_BY_KEY.get(key)
    if spec is None:
        raise FilterValidationError("Unknown breakdown dimension.")
    if not spec.grouping:
        raise FilterValidationError(f"{spec.label} cannot be used as a trend breakdown.")
    return spec


def validate_trend_pair(primary: TrendMetricSpec, secondary: TrendMetricSpec | None) -> None:
    if secondary is None:
        return
    if secondary.key == primary.key:
        raise FilterValidationError("Secondary metric must differ from the primary metric.")
    if secondary.key not in TREND_METRICS_BY_KEY or primary.key not in TREND_METRICS_BY_KEY:
        raise FilterValidationError("Metric pair is not supported.")


def parse_include_metrics(raw: list[str] | None) -> tuple[TrendMetricSpec, ...]:
    """Allowlisted extra metrics for one company-total trends response. Dedupes, caps at 8."""
    if not raw:
        return ()
    seen: set[str] = set()
    specs: list[TrendMetricSpec] = []
    for item in raw:
        key = str(item or "").strip()
        if not key:
            continue
        spec = TREND_METRICS_BY_KEY.get(key)
        if spec is None:
            raise FilterValidationError("Unknown include_metric.")
        if key in seen:
            continue
        seen.add(key)
        specs.append(spec)
    if len(specs) > MAX_INCLUDE_METRICS:
        raise FilterValidationError("At most 8 include_metric values are allowed.")
    return tuple(specs)


def validate_include_metrics(
    include: tuple[TrendMetricSpec, ...],
    secondary: TrendMetricSpec | None,
    breakdown: TrendDimensionSpec | None,
) -> None:
    if not include:
        return
    if breakdown is not None:
        raise FilterValidationError("include_metric cannot be combined with a breakdown.")
    if secondary is not None:
        raise FilterValidationError("include_metric cannot be combined with a secondary metric.")


def validate_trend_selection(
    primary: TrendMetricSpec,
    secondary: TrendMetricSpec | None,
    grain: str,
    breakdown: TrendDimensionSpec | None,
    include: tuple[TrendMetricSpec, ...] = (),
) -> None:
    validate_trend_pair(primary, secondary)
    validate_include_metrics(include, secondary, breakdown)
    if grain not in primary.grains:
        raise FilterValidationError(f"{primary.label} cannot be trended by {grain}.")
    if secondary is not None and grain not in secondary.grains:
        raise FilterValidationError(f"{secondary.label} cannot be trended by {grain}.")
    if breakdown is None:
        return
    if breakdown.key not in primary.dimensions:
        raise FilterValidationError(f"{primary.label} cannot be broken down by {breakdown.label}.")
    if secondary is not None:
        raise FilterValidationError("A secondary metric cannot be combined with a breakdown.")


GENERATED_TREND_KEYS = frozenset({"metric", "secondary", "grain", "breakdown", "compare"})
_NONE_SELECTION = frozenset({"", "none", "null"})
_OMIT_COMPARE = frozenset({"", "omit", "null"})


def parse_generated_trend_selection(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a bounded NL trend selection against existing D3 parsers."""
    if not isinstance(raw, dict):
        raise FilterValidationError("Trend selection is invalid.")
    extra = set(raw) - GENERATED_TREND_KEYS
    if extra:
        raise FilterValidationError("Trend selection contains unsupported fields.")
    metric_raw = raw.get("metric")
    if metric_raw is None or str(metric_raw).strip() == "":
        raise FilterValidationError("Unknown primary metric.")
    primary = parse_trend_metric(str(metric_raw), role="primary")
    secondary_raw = raw.get("secondary")
    secondary = None
    if secondary_raw is not None and str(secondary_raw).strip().lower() not in _NONE_SELECTION:
        secondary = parse_trend_metric(str(secondary_raw), role="secondary")
    grain = parse_trend_grain(raw.get("grain"))
    breakdown_raw = raw.get("breakdown")
    breakdown = None
    if breakdown_raw is not None and str(breakdown_raw).strip().lower() not in _NONE_SELECTION:
        breakdown = parse_breakdown(str(breakdown_raw))
    compare_raw = raw.get("compare")
    compare: str | None = None
    if compare_raw is not None and str(compare_raw).strip().lower() not in _OMIT_COMPARE:
        token = str(compare_raw).strip().lower()
        if token != "none":
            raise FilterValidationError("Comparison must be omit or none.")
        compare = "none"
    validate_trend_selection(primary, secondary, grain, breakdown)
    return {
        "metric": primary.key,
        "secondary": secondary.key if secondary else None,
        "grain": grain,
        "breakdown": breakdown.key if breakdown else None,
        "compare": compare,
    }


def dual_axis(primary: TrendMetricSpec, secondary: TrendMetricSpec | None) -> bool:
    if secondary is None:
        return False
    return primary.kind != secondary.kind


def rank_measure_name(spec: TrendMetricSpec) -> str:
    """Additive volume used to cap breakdown series. Ratios rank by denominator."""
    if spec.additive and spec.measure in ADDITIVE_MEASURES:
        return spec.measure
    if spec.denominator in ADDITIVE_MEASURES:
        return spec.denominator
    return "total_cost"


def breakdown_column(dimension: str) -> str:
    column = DIMENSION_COLUMNS.get(dimension)
    if column is None:
        raise FilterValidationError("Unknown breakdown dimension.")
    return column
