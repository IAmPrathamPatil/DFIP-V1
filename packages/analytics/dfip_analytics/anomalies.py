"""D7 deterministic anomaly / diagnostic rules. Not AI.

An anomaly is a published-month observation that is unusual versus the
median of prior published months under the same D2 filters. Ordinary
period-to-period movement belongs to D6 and is not labeled an anomaly
just because it is large versus one comparison month.

History coverage on local published grain is short (most tenants have two
months; one tenant has four). Rules that need long seasonal models or
parametric fits are not implemented. Day-of-week intra-month models are
not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from dfip_analytics.divide import RATE_SCALE, ZERO, as_decimal, safe_divide, safe_subtract
from dfip_analytics.explorer import MAX_EXPLORER_LIMIT
from dfip_analytics.insights import (
    DRIVER_SHARE,
    MAX_DRIVERS_EVIDENCE,
    MIN_DRIVER_GROUPS,
    MIN_DENOMINATOR,
    parse_insight_dimension,
    parse_insight_limit,
    parse_insight_metric,
)
from dfip_analytics.kpis import compute_kpis
from dfip_analytics.trends import (
    OTHER_SERIES_KEY,
    TREND_DIMENSIONS,
    TREND_DIMENSIONS_BY_KEY,
    TREND_METRICS,
    TrendDimensionSpec,
    TrendMetricSpec,
)

AnomalyKind = Literal["spike", "drop", "rolling_deviation"]

ANOMALY_KINDS: tuple[AnomalyKind, ...] = ("spike", "drop", "rolling_deviation")
DEFAULT_DRIVER_DIMENSIONS: tuple[str, ...] = ("campaign_id", "channel")
DRIVER_DIMENSIONS = TREND_DIMENSIONS
DRIVER_DIMENSIONS_BY_KEY = TREND_DIMENSIONS_BY_KEY

MIN_BASELINE_MONTHS = 3
MAX_BASELINE_MONTHS = 12
ROBUST_Z_THRESHOLD = Decimal("3.5")
MAD_SCALE = Decimal("0.6745")
MIN_MAD_RATIO = Decimal("0.05")
PCT_VS_MEDIAN = Decimal("0.50")
MAX_ANOMALIES = 12
DEFAULT_ANOMALY_LIMIT = 8
SERIES_GROUP_LIMIT = MAX_EXPLORER_LIMIT

ABS_FLOOR = {
    "money": Decimal("5.0000"),
    "count": Decimal("5"),
    "rate": Decimal("0.050000"),
    "roas": Decimal("0.2500"),
}
NON_Z_ABS = {
    "money": Decimal("5.0000"),
    "count": Decimal("5"),
    "rate": Decimal("0.100000"),
    "roas": Decimal("0.5000"),
}

REASON_NO_HISTORY = "no_published_history"
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_PERIOD_NOT_MONTH = "period_not_month"
REASON_EMPTY_PERIOD = "empty_period"
REASON_NO_ANOMALIES = "no_anomalies"

SEVERITY_HIGH = Decimal("75")
SEVERITY_MEDIUM = Decimal("50")


@dataclass(frozen=True)
class MonthObservation:
    month_start: date
    measures: dict[str, object]
    count: int


@dataclass(frozen=True)
class GroupMonth:
    key: str
    label: str
    month_start: date
    measures: dict[str, object]
    count: int


@dataclass(frozen=True)
class AnomalyDriver:
    dimension: str
    dimension_label: str
    key: str
    label: str
    current: object
    baseline: object
    delta: Decimal | None
    delta_pct: Decimal | None
    contribution: Decimal | None
    count: int


@dataclass
class AnomalyDraft:
    kind: AnomalyKind
    direction: Literal["spike", "drop"]
    metric: TrendMetricSpec
    current: object
    baseline: Decimal
    baseline_method: str
    baseline_months: tuple[date, ...]
    delta: Decimal
    delta_pct: Decimal | None
    robust_z: Decimal | None
    new_extreme: bool
    mad: Decimal | None
    mad_ok: bool
    threshold: str
    severity_score: Decimal
    severity: Literal["low", "medium", "high"]
    headline: str
    explanation: str
    anomaly_id: str
    dimension: TrendDimensionSpec | None
    drivers: list[AnomalyDriver]
    current_count: int
    baseline_counts: tuple[int, ...]


def parse_anomaly_metric(raw: str | None) -> TrendMetricSpec | None:
    return parse_insight_metric(raw)


def parse_anomaly_dimension(raw: str | None) -> TrendDimensionSpec | None:
    return parse_insight_dimension(raw)


def parse_anomaly_limit(raw: int | str | None) -> int:
    return parse_insight_limit(raw)


def _numeric(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return as_decimal(value)


def _pick_metric(spec: TrendMetricSpec, measures: dict, kpis: dict):
    if spec.source == "measure":
        return measures.get(spec.measure)
    return kpis.get(spec.kpi_slug)


def _denominator_ok(spec: TrendMetricSpec, measures: dict) -> bool:
    if not spec.denominator:
        return True
    value = _numeric(measures.get(spec.denominator))
    if value is None or value == ZERO:
        return False
    if spec.kind == "rate" and value < MIN_DENOMINATOR:
        return False
    return True


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _mad(values: list[Decimal], median: Decimal) -> Decimal:
    return _median([abs(item - median) for item in values]) or ZERO


def _metric_delta(spec: TrendMetricSpec, current: object, prior: object) -> Decimal | None:
    change = safe_subtract(current, prior)
    if change is None:
        return None
    if spec.kind == "count":
        return Decimal(int(change))
    return change


def _pct(current: object, prior: object) -> Decimal | None:
    change = safe_subtract(current, prior)
    return safe_divide(change, prior, scale=RATE_SCALE)


def abs_floor_for(spec: TrendMetricSpec) -> Decimal:
    return ABS_FLOOR[spec.kind]


def non_z_abs_for(spec: TrendMetricSpec) -> Decimal:
    return NON_Z_ABS[spec.kind]


def select_baseline_months(published: list[date], current: date) -> list[date]:
    prior = [item for item in published if item < current]
    return prior[-MAX_BASELINE_MONTHS:]


def _observe(spec: TrendMetricSpec, item: MonthObservation) -> Decimal | None:
    if not _denominator_ok(spec, item.measures):
        return None
    kpis = compute_kpis(item.measures, namespace="client")
    return _numeric(_pick_metric(spec, item.measures, kpis))


def _severity(
    *, z: Decimal | None, pct: Decimal | None, delta: Decimal, median: Decimal, extreme: bool
) -> tuple[Decimal, Literal["low", "medium", "high"]]:
    z_part = Decimal("0")
    if z is not None:
        z_part = min(abs(z) / ROBUST_Z_THRESHOLD, Decimal("3")) * Decimal("40")
    pct_part = Decimal("0")
    if pct is not None:
        pct_part = min(abs(pct) / PCT_VS_MEDIAN, Decimal("3")) * Decimal("30")
    baseline = abs(median) if median != ZERO else Decimal("1")
    abs_part = min(abs(delta) / baseline, Decimal("3")) * Decimal("10")
    extreme_part = Decimal("15") if extreme else Decimal("0")
    score = min(Decimal("100"), z_part + pct_part + abs_part + extreme_part)
    if score >= SEVERITY_HIGH:
        label: Literal["low", "medium", "high"] = "high"
    elif score >= SEVERITY_MEDIUM:
        label = "medium"
    else:
        label = "low"
    return score, label


def _format_pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{(abs(value) * Decimal('100')).quantize(Decimal('0.1'))}%"


def _format_value(spec: TrendMetricSpec, value: object) -> str:
    number = _numeric(value)
    if number is None:
        return "n/a"
    if spec.kind == "count":
        return str(int(number))
    if spec.kind == "rate":
        return f"{(number * Decimal('100')).quantize(Decimal('0.1'))}%"
    return format(number, "f")


def classify_anomaly(
    spec: TrendMetricSpec,
    current: Decimal,
    baseline_values: list[Decimal],
) -> (
    tuple[
        AnomalyKind,
        Literal["spike", "drop"],
        Decimal,
        Decimal | None,
        Decimal | None,
        bool,
        Decimal,
        bool,
        str,
    ]
    | None
):
    median = _median(baseline_values)
    if median is None:
        return None
    delta = current - median
    if spec.kind == "count":
        delta = Decimal(int(delta))
    pct = _pct(current, median)
    floor = abs_floor_for(spec)
    if abs(delta) < floor:
        return None
    mad = _mad(baseline_values, median)
    mad_floor = max(floor, abs(median) * MIN_MAD_RATIO)
    mad_ok = mad >= mad_floor
    lo, hi = min(baseline_values), max(baseline_values)
    new_extreme = current < lo or current > hi
    direction: Literal["spike", "drop"] = "spike" if current > median else "drop"
    z: Decimal | None = None
    if mad_ok and mad != ZERO:
        z = (MAD_SCALE * (current - median) / mad).quantize(Decimal("0.0001"))
    if mad_ok and z is not None and abs(z) >= ROBUST_Z_THRESHOLD:
        threshold = (
            f"rolling_z>={ROBUST_Z_THRESHOLD};abs>={floor};"
            f"min_baseline={MIN_BASELINE_MONTHS};mad_ratio>={MIN_MAD_RATIO}"
        )
        return "rolling_deviation", direction, median, pct, z, new_extreme, mad, mad_ok, threshold
    rel_ok = True
    if spec.kind != "rate":
        rel_ok = pct is not None and abs(pct) >= PCT_VS_MEDIAN
    abs_ok = abs(delta) >= non_z_abs_for(spec)
    if new_extreme and rel_ok and abs_ok:
        threshold = (
            f"period_vs_median;new_extreme;abs_pct>={PCT_VS_MEDIAN};"
            f"abs>={non_z_abs_for(spec)};min_baseline={MIN_BASELINE_MONTHS}"
        )
        if spec.kind == "rate":
            threshold = (
                f"period_vs_median;new_extreme;abs_pp>={non_z_abs_for(spec)};"
                f"min_baseline={MIN_BASELINE_MONTHS}"
            )
        return direction, direction, median, pct, z, new_extreme, mad, mad_ok, threshold
    return None


def _driver_zero(spec: TrendMetricSpec):
    if spec.kind == "count":
        return 0
    if spec.kind == "money":
        return Decimal("0.0000")
    return ZERO


def build_group_drivers(
    spec: TrendMetricSpec,
    *,
    dimension: TrendDimensionSpec,
    rows: list[GroupMonth],
    baseline_months: list[date],
    current_month: date,
    total_delta: Decimal,
) -> list[AnomalyDriver]:
    if not spec.additive or total_delta == ZERO:
        return []
    by_key: dict[str, dict[date, GroupMonth]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        if row.key == OTHER_SERIES_KEY:
            continue
        by_key.setdefault(row.key, {})[row.month_start] = row
        labels[row.key] = row.label
    if len(by_key) < MIN_DRIVER_GROUPS:
        return []
    zero = _driver_zero(spec)
    drivers: list[AnomalyDriver] = []
    for key, months in by_key.items():
        current_row = months.get(current_month)
        baseline_vals: list[Decimal] = []
        for month in baseline_months:
            item = months.get(month)
            if item is None:
                baseline_vals.append(_numeric(zero) or ZERO)
                continue
            value = _observe(spec, MonthObservation(month, item.measures, item.count))
            baseline_vals.append(value if value is not None else (_numeric(zero) or ZERO))
        group_median = _median(baseline_vals)
        current_value = (
            _observe(spec, MonthObservation(current_month, current_row.measures, current_row.count))
            if current_row is not None
            else (_numeric(zero) or ZERO)
        )
        if group_median is None or current_value is None:
            continue
        delta = _metric_delta(spec, current_value, group_median)
        contribution = (
            safe_divide(delta, total_delta, scale=RATE_SCALE) if delta is not None else None
        )
        if delta is None or contribution is None or (delta > 0) != (total_delta > 0):
            continue
        drivers.append(
            AnomalyDriver(
                dimension=dimension.key,
                dimension_label=dimension.label,
                key=key,
                label=labels.get(key) or key or "(blank)",
                current=current_value if current_row is not None else zero,
                baseline=group_median,
                delta=delta,
                delta_pct=_pct(current_value, group_median),
                contribution=contribution,
                count=current_row.count if current_row is not None else 0,
            )
        )
    drivers.sort(
        key=lambda item: (abs(item.contribution or ZERO), abs(item.delta or ZERO), item.key),
        reverse=True,
    )
    top = drivers[:MAX_DRIVERS_EVIDENCE]
    if not top:
        return []
    if abs(top[0].contribution or ZERO) < DRIVER_SHARE:
        return []
    return top


def _headline(
    spec: TrendMetricSpec, kind: AnomalyKind, direction: str, month: date, pct: Decimal | None
) -> str:
    verb = "spike" if direction == "spike" else "drop"
    if kind == "rolling_deviation":
        return f"{spec.label} in {month.isoformat()[:7]} is an unusual {verb} versus the prior-month median."
    return f"{spec.label} in {month.isoformat()[:7]} is a {verb} versus the prior-month median ({_format_pct(pct)})."


def _explanation(
    spec: TrendMetricSpec,
    kind: AnomalyKind,
    direction: str,
    month: date,
    current: Decimal,
    median: Decimal,
    pct: Decimal | None,
    z: Decimal | None,
    months: list[date],
    new_extreme: bool,
    mad_ok: bool,
    drivers: list[AnomalyDriver],
) -> str:
    labels = ", ".join(item.strftime("%Y-%m") for item in months)
    extreme = " It is outside the min/max of those baseline months." if new_extreme else ""
    z_text = (
        f" Robust z versus MAD is {z}."
        if z is not None and mad_ok
        else " MAD was too small for a rolling z-score, so the period-vs-median extreme rule was used."
    )
    driver_text = ""
    if drivers:
        top = drivers[0]
        share = _format_pct(top.contribution)
        driver_text = f" {top.dimension_label} {top.label} accounted for {share} of the observed-versus-median change."
    return (
        f"{spec.label} observed {_format_value(spec, current)} in {month.strftime('%Y-%m')} versus a "
        f"median of {_format_value(spec, median)} across {len(months)} prior published months ({labels}). "
        f"This {direction} is not a single-period comparison.{extreme}{z_text}{driver_text} "
        f"This is a deterministic baseline diagnostic, not a causal claim and not an LLM explanation."
    )


def build_anomalies(
    *,
    current: MonthObservation,
    baseline: list[MonthObservation],
    grouped: dict[str, list[GroupMonth]],
    metrics: tuple[TrendMetricSpec, ...] = TREND_METRICS,
    limit: int = DEFAULT_ANOMALY_LIMIT,
) -> list[AnomalyDraft]:
    if len(baseline) < MIN_BASELINE_MONTHS:
        return []
    baseline_months = [item.month_start for item in baseline]
    drafts: list[AnomalyDraft] = []
    for spec in metrics:
        current_value = _observe(spec, current)
        baseline_values: list[Decimal] = []
        kept: list[MonthObservation] = []
        for item in baseline:
            value = _observe(spec, item)
            if value is None:
                continue
            baseline_values.append(value)
            kept.append(item)
        if current_value is None or len(baseline_values) < MIN_BASELINE_MONTHS:
            continue
        classified = classify_anomaly(spec, current_value, baseline_values)
        if classified is None:
            continue
        kind, direction, median, pct, z, new_extreme, mad, mad_ok, threshold = classified
        delta = current_value - median
        if spec.kind == "count":
            delta = Decimal(int(delta))
        score, severity = _severity(z=z, pct=pct, delta=delta, median=median, extreme=new_extreme)
        drivers: list[AnomalyDriver] = []
        chosen_dim: TrendDimensionSpec | None = None
        if spec.additive:
            best: list[AnomalyDriver] = []
            best_dim: TrendDimensionSpec | None = None
            best_share = Decimal("0")
            for dim_key, rows in grouped.items():
                dim = DRIVER_DIMENSIONS_BY_KEY[dim_key]
                candidates = build_group_drivers(
                    spec,
                    dimension=dim,
                    rows=rows,
                    baseline_months=[item.month_start for item in kept],
                    current_month=current.month_start,
                    total_delta=delta,
                )
                share = abs(candidates[0].contribution or ZERO) if candidates else Decimal("0")
                if candidates and share > best_share:
                    best = candidates
                    best_dim = dim
                    best_share = share
            drivers = best
            chosen_dim = best_dim
        drafts.append(
            AnomalyDraft(
                kind=kind,
                direction=direction,
                metric=spec,
                current=current_value,
                baseline=median,
                baseline_method="median",
                baseline_months=tuple(item.month_start for item in kept),
                delta=delta,
                delta_pct=pct,
                robust_z=z,
                new_extreme=new_extreme,
                mad=mad,
                mad_ok=mad_ok,
                threshold=threshold,
                severity_score=score,
                severity=severity,
                headline=_headline(spec, kind, direction, current.month_start, pct),
                explanation=_explanation(
                    spec,
                    kind,
                    direction,
                    current.month_start,
                    current_value,
                    median,
                    pct,
                    z,
                    [item.month_start for item in kept],
                    new_extreme,
                    mad_ok,
                    drivers,
                ),
                anomaly_id=f"{kind}:{spec.key}",
                dimension=chosen_dim,
                drivers=drivers,
                current_count=current.count,
                baseline_counts=tuple(item.count for item in kept),
            )
        )
    drafts.sort(
        key=lambda item: (
            -item.severity_score,
            -(abs(item.robust_z) if item.robust_z is not None else ZERO),
            -(abs(item.delta_pct) if item.delta_pct is not None else ZERO),
            item.metric.key,
            item.anomaly_id,
        )
    )
    return drafts[:limit]


def threshold_catalog() -> dict[str, str]:
    return {
        "min_baseline_months": str(MIN_BASELINE_MONTHS),
        "max_baseline_months": str(MAX_BASELINE_MONTHS),
        "robust_z": str(ROBUST_Z_THRESHOLD),
        "mad_scale": str(MAD_SCALE),
        "min_mad_ratio": str(MIN_MAD_RATIO),
        "pct_vs_median": str(PCT_VS_MEDIAN),
        "abs_floor_money": str(ABS_FLOOR["money"]),
        "abs_floor_count": str(ABS_FLOOR["count"]),
        "abs_floor_rate": str(ABS_FLOOR["rate"]),
        "abs_floor_roas": str(ABS_FLOOR["roas"]),
        "non_z_abs_rate": str(NON_Z_ABS["rate"]),
        "driver_share": str(DRIVER_SHARE),
        "max_anomalies": str(MAX_ANOMALIES),
        "ranking": (
            "severity_score = min(100, min(|z|/3.5, 3)*40 + min(|delta_pct|/0.50, 3)*30 "
            "+ min(|delta|/max(|median|,1), 3)*10 + (15 if new_extreme else 0)); "
            "high>=75, medium>=50, else low. Sort severity desc, |z|, |delta_pct|, metric."
        ),
        "coverage_note": (
            "Requires 3 prior published months after filters. Rolling z is used only when "
            "MAD >= max(abs_floor, 5% of |median|). Otherwise spike/drop require a new "
            "min/max extreme and 50% (or 10pp for rates) versus the median. Intra-month "
            "day models and seasonal ARIMA are not supported."
        ),
    }
