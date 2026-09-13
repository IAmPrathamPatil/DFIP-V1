"""D6 deterministic Insights. Not AI.

Every insight is a trigger + evidence + calculation + materiality decision +
explanation. Metric formulas stay in kpis.py. Driver breakdowns reuse D5
dimensions. Contribution share is valid for additive metrics only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from dfip_analytics.divide import RATE_SCALE, ZERO, as_decimal, safe_divide, safe_subtract
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.kpis import compute_kpis
from dfip_analytics.trends import (
    TREND_DIMENSIONS,
    TREND_DIMENSIONS_BY_KEY,
    TREND_METRICS,
    TrendDimensionSpec,
    TrendMetricSpec,
    parse_trend_metric,
)

InsightCategory = Literal[
    "material_change",
    "dominant_driver",
    "positive_signal",
    "negative_signal",
    "relationship",
]

INSIGHT_CATEGORIES: tuple[InsightCategory, ...] = (
    "material_change",
    "dominant_driver",
    "positive_signal",
    "negative_signal",
    "relationship",
)
DEFAULT_DRIVER_DIMENSIONS: tuple[str, ...] = ("campaign_id", "channel")
DRIVER_DIMENSIONS = TREND_DIMENSIONS
DRIVER_DIMENSIONS_BY_KEY = TREND_DIMENSIONS_BY_KEY

MATERIAL_PCT = Decimal("0.10")
MATERIAL_HIGH_PCT = Decimal("0.25")
ABS_FLOOR = {
    "money": Decimal("1.0000"),
    "count": Decimal("1"),
    "rate": Decimal("0.010000"),
    "roas": Decimal("0.1000"),
}
DRIVER_SHARE = Decimal("0.40")
MULTI_DRIVER_EACH = Decimal("0.25")
MULTI_DRIVER_TOGETHER = Decimal("0.60")
MIN_DRIVER_GROUPS = 2
MIN_DENOMINATOR = Decimal("1")
MAX_INSIGHTS = 12
DEFAULT_INSIGHT_LIMIT = 8
MAX_DRIVERS_EVIDENCE = 3
PCT_CAP = Decimal("10")
ABS_RATIO_CAP = Decimal("10")

CATEGORY_WEIGHT = {
    "dominant_driver": Decimal("400"),
    "material_change": Decimal("300"),
    "negative_signal": Decimal("250"),
    "positive_signal": Decimal("200"),
    "relationship": Decimal("150"),
}

RELATIONSHIP_PAIRS: tuple[tuple[str, str], ...] = (
    ("revenue_inr", "overall_roas"),
    ("delivered", "delivery_rate"),
)

REASON_INSUFFICIENT_COMPARISON = "insufficient_comparison"
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_MATERIAL = "no_material_insights"
REASON_EMPTY_PERIOD = "empty_period"
REASON_NO_HISTORY = "no_published_history"
REASON_INSUFFICIENT_DENOMINATOR = "insufficient_denominator"

EMPTY_REASONS = (
    REASON_NO_HISTORY,
    REASON_INSUFFICIENT_COMPARISON,
    REASON_INSUFFICIENT_HISTORY,
    REASON_EMPTY_PERIOD,
    REASON_NO_MATERIAL,
)


@dataclass(frozen=True)
class DriverCandidate:
    dimension: str
    dimension_label: str
    key: str
    label: str
    current: object
    prior: object
    delta: Decimal | None
    delta_pct: Decimal | None
    contribution: Decimal | None
    count: int
    prior_count: int


@dataclass
class MetricSnapshot:
    spec: TrendMetricSpec
    current: object
    prior: object
    delta: Decimal | None
    delta_pct: Decimal | None
    current_measures: dict[str, object]
    prior_measures: dict[str, object]
    current_count: int
    prior_count: int
    suppression: str | None = None


@dataclass
class InsightDraft:
    category: InsightCategory
    metric: TrendMetricSpec
    related: TrendMetricSpec | None
    snapshot: MetricSnapshot
    related_snapshot: MetricSnapshot | None
    dimension: TrendDimensionSpec | None
    drivers: list[DriverCandidate]
    threshold: str
    score: Decimal
    headline: str
    explanation: str
    insight_id: str
    group_count: int | None = None
    same_sign_group_count: int | None = None


def parse_insight_metric(raw: str | None) -> TrendMetricSpec | None:
    if raw is None or str(raw).strip() == "":
        return None
    return parse_trend_metric(raw, role="primary")


def parse_insight_dimension(raw: str | None) -> TrendDimensionSpec | None:
    if raw is None or str(raw).strip() == "":
        return None
    key = str(raw).strip()
    if key == "day":
        raise FilterValidationError("Day is not a driver dimension for insights.")
    spec = DRIVER_DIMENSIONS_BY_KEY.get(key)
    if spec is None:
        raise FilterValidationError("Unknown insight driver dimension.")
    return spec


def parse_insight_limit(raw: int | str | None) -> int:
    if raw is None or str(raw).strip() == "":
        return DEFAULT_INSIGHT_LIMIT
    try:
        limit = int(str(raw).strip())
    except ValueError as exc:
        raise FilterValidationError("Insight limit must be a positive integer.") from exc
    if limit < 1 or limit > MAX_INSIGHTS:
        raise FilterValidationError(f"Insight limit must be between 1 and {MAX_INSIGHTS}.")
    return limit


def higher_is_better(spec: TrendMetricSpec) -> bool:
    return spec.key != "total_cost"


def abs_floor_for(spec: TrendMetricSpec) -> Decimal:
    return ABS_FLOOR[spec.kind]


def metric_delta(spec: TrendMetricSpec, current: object, prior: object) -> Decimal | None:
    change = safe_subtract(current, prior)
    if change is None:
        return None
    if spec.kind == "count":
        return Decimal(int(change))
    return change


def metric_delta_pct(current: object, prior: object) -> Decimal | None:
    change = safe_subtract(current, prior)
    return safe_divide(change, prior, scale=RATE_SCALE)


def _additive_zero(spec: TrendMetricSpec):
    if spec.kind == "count":
        return 0
    if spec.kind == "money":
        return Decimal("0.0000")
    return ZERO


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


def _denominator_value(spec: TrendMetricSpec, measures: dict) -> Decimal | None:
    if not spec.denominator:
        return None
    return _numeric(measures.get(spec.denominator))


def denominator_sufficient(spec: TrendMetricSpec, measures: dict) -> bool:
    if not spec.denominator:
        return True
    value = _denominator_value(spec, measures)
    if value is None or value == ZERO:
        return False
    if spec.kind in {"rate", "roas"} and value < MIN_DENOMINATOR and spec.kind == "rate":
        return False
    return True


def snapshot_for(
    spec: TrendMetricSpec,
    current_measures: dict[str, object],
    prior_measures: dict[str, object],
    *,
    current_count: int,
    prior_count: int,
) -> MetricSnapshot:
    current_kpis = compute_kpis(current_measures, namespace="client")
    prior_kpis = compute_kpis(prior_measures, namespace="client")
    current = _pick_metric(spec, current_measures, current_kpis)
    prior = _pick_metric(spec, prior_measures, prior_kpis)
    suppression = None
    if not denominator_sufficient(spec, current_measures) or not denominator_sufficient(
        spec, prior_measures
    ):
        suppression = REASON_INSUFFICIENT_DENOMINATOR
    elif current is None or prior is None:
        suppression = "null_or_missing"
    delta = metric_delta(spec, current, prior) if suppression is None else None
    delta_pct = metric_delta_pct(current, prior) if suppression is None else None
    if suppression is None and (delta is None or delta_pct is None):
        suppression = "null_or_missing"
    return MetricSnapshot(
        spec=spec,
        current=current,
        prior=prior,
        delta=delta,
        delta_pct=delta_pct,
        current_measures=current_measures,
        prior_measures=prior_measures,
        current_count=current_count,
        prior_count=prior_count,
        suppression=suppression,
    )


def is_material(snapshot: MetricSnapshot) -> bool:
    if snapshot.suppression or snapshot.delta is None or snapshot.delta_pct is None:
        return False
    if abs(snapshot.delta_pct) < MATERIAL_PCT:
        return False
    if abs(snapshot.delta) < abs_floor_for(snapshot.spec):
        return False
    return True


def is_high_material(snapshot: MetricSnapshot) -> bool:
    return (
        is_material(snapshot)
        and snapshot.delta_pct is not None
        and abs(snapshot.delta_pct) >= MATERIAL_HIGH_PCT
    )


def signed_category(snapshot: MetricSnapshot) -> InsightCategory:
    assert snapshot.delta is not None
    improved = snapshot.delta > 0 if higher_is_better(snapshot.spec) else snapshot.delta < 0
    if is_high_material(snapshot):
        return "material_change"
    return "positive_signal" if improved else "negative_signal"


def format_pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{(value * Decimal('100')).quantize(Decimal('0.1'))}%"


def format_value(spec: TrendMetricSpec, value: object) -> str:
    number = _numeric(value)
    if number is None:
        return "n/a"
    if spec.kind == "count":
        return str(int(number))
    if spec.kind == "rate":
        return f"{(number * Decimal('100')).quantize(Decimal('0.1'))}%"
    return format(number, "f")


def _direction_word(delta: Decimal) -> str:
    return "increased" if delta > 0 else "declined"


def threshold_label(snapshot: MetricSnapshot, *, driver: bool = False) -> str:
    parts = [
        f"abs_pct>={MATERIAL_PCT}",
        f"abs>={abs_floor_for(snapshot.spec)}",
    ]
    if is_high_material(snapshot):
        parts.append(f"high_pct>={MATERIAL_HIGH_PCT}")
    if driver:
        parts.append(f"driver_share>={DRIVER_SHARE}")
        parts.append(f"min_groups>={MIN_DRIVER_GROUPS}")
    return ";".join(parts)


def insight_score(
    *,
    category: InsightCategory,
    delta: Decimal | None,
    delta_pct: Decimal | None,
    prior: object,
    driver_share: Decimal | None,
) -> Decimal:
    pct_component = Decimal("0")
    if delta_pct is not None:
        pct_component = min(abs(delta_pct), PCT_CAP) * Decimal("10000")
    abs_component = Decimal("0")
    if delta is not None:
        prior_n = _numeric(prior)
        baseline = abs(prior_n) if prior_n is not None and prior_n != ZERO else Decimal("1")
        abs_component = min(abs(delta) / baseline, ABS_RATIO_CAP) * Decimal("100")
    driver_component = (driver_share or Decimal("0")) * Decimal("1000")
    return pct_component + abs_component + driver_component + CATEGORY_WEIGHT[category]


def driver_rows(
    spec: TrendMetricSpec,
    dimension: TrendDimensionSpec,
    current_groups: list[tuple[str, str, dict[str, object], int]],
    prior_groups: list[tuple[str, str, dict[str, object], int]],
    total_delta: Decimal,
) -> tuple[list[DriverCandidate], int]:
    current_map = {key: (label, measures, count) for key, label, measures, count in current_groups}
    prior_map = {key: (label, measures, count) for key, label, measures, count in prior_groups}
    keys = set(current_map) | set(prior_map)
    rows: list[DriverCandidate] = []
    if total_delta == ZERO:
        return rows, len(keys)
    for key in keys:
        current_payload = current_map.get(key)
        prior_payload = prior_map.get(key)
        label = (current_payload or prior_payload)[0]
        current_measures = current_payload[1] if current_payload else {}
        prior_measures = prior_payload[1] if prior_payload else {}
        current_kpis = compute_kpis(current_measures, namespace="client") if current_payload else {}
        prior_kpis = compute_kpis(prior_measures, namespace="client") if prior_payload else {}
        if spec.additive:
            zero = _additive_zero(spec)
            current_value = (
                _pick_metric(spec, current_measures, current_kpis) if current_payload else zero
            )
            prior_value = _pick_metric(spec, prior_measures, prior_kpis) if prior_payload else zero
        else:
            current_value = (
                _pick_metric(spec, current_measures, current_kpis) if current_payload else None
            )
            prior_value = _pick_metric(spec, prior_measures, prior_kpis) if prior_payload else None
        delta = metric_delta(spec, current_value, prior_value)
        contribution = (
            safe_divide(delta, total_delta, scale=RATE_SCALE) if delta is not None else None
        )
        rows.append(
            DriverCandidate(
                dimension=dimension.key,
                dimension_label=dimension.label,
                key=key,
                label=label or key or "(blank)",
                current=current_value,
                prior=prior_value,
                delta=delta,
                delta_pct=metric_delta_pct(current_value, prior_value),
                contribution=contribution,
                count=current_payload[2] if current_payload else 0,
                prior_count=prior_payload[2] if prior_payload else 0,
            )
        )
    rows.sort(
        key=lambda item: (
            abs(item.contribution or ZERO),
            abs(item.delta or ZERO),
            item.key,
        ),
        reverse=True,
    )
    return rows, len(keys)


def _same_sign(rows: list[DriverCandidate], total_delta: Decimal) -> list[DriverCandidate]:
    out: list[DriverCandidate] = []
    for item in rows:
        if item.delta is None or item.contribution is None:
            continue
        if item.delta == ZERO:
            continue
        if (item.delta > 0) == (total_delta > 0):
            out.append(item)
    return out


def _driver_qualifies(spec: TrendMetricSpec, item: DriverCandidate, share: Decimal) -> bool:
    if item.delta is None or abs(item.delta) < abs_floor_for(spec):
        return False
    return item.contribution is not None and abs(item.contribution) >= share


def movement_explanation(snapshot: MetricSnapshot, category: InsightCategory) -> tuple[str, str]:
    spec = snapshot.spec
    assert snapshot.delta is not None and snapshot.delta_pct is not None
    verb = _direction_word(snapshot.delta)
    pct = format_pct(abs(snapshot.delta_pct))
    current = format_value(spec, snapshot.current)
    prior = format_value(spec, snapshot.prior)
    if category == "positive_signal":
        headline = f"{spec.label} improved {pct} vs the comparison period."
        explanation = (
            f"{spec.label} improved {pct} vs the comparison period ({current} vs {prior})."
        )
    elif category == "negative_signal":
        headline = f"{spec.label} deteriorated {pct} vs the comparison period."
        explanation = (
            f"{spec.label} deteriorated {pct} vs the comparison period ({current} vs {prior})."
        )
    else:
        headline = f"{spec.label} {verb} {pct} vs the comparison period."
        explanation = f"{spec.label} {verb} {pct} vs the comparison period ({current} vs {prior})."
    return headline, explanation


def driver_explanation(snapshot: MetricSnapshot, drivers: list[DriverCandidate]) -> tuple[str, str]:
    spec = snapshot.spec
    assert snapshot.delta is not None and snapshot.delta_pct is not None
    verb = _direction_word(snapshot.delta)
    pct = format_pct(abs(snapshot.delta_pct))
    top = drivers[0]
    share = format_pct(abs(top.contribution) if top.contribution is not None else None)
    if len(drivers) >= 2 and _driver_qualifies(spec, drivers[1], MULTI_DRIVER_EACH):
        second = drivers[1]
        second_share = format_pct(
            abs(second.contribution) if second.contribution is not None else None
        )
        headline = (
            f"{spec.label} {verb} {pct}, accounted for by {top.dimension_label} "
            f"{top.label} and {second.label}."
        )
        explanation = (
            f"{spec.label} {verb} {pct} vs the comparison period, accounted for by "
            f"{top.dimension_label} {top.label} ({share} of the observed change) and "
            f"{second.label} ({second_share}). This is contribution to the observed "
            f"change, not a causal claim."
        )
        return headline, explanation
    headline = (
        f"{spec.label} {verb} {pct}, primarily accounted for by {top.dimension_label} {top.label}."
    )
    explanation = (
        f"{spec.label} {verb} {pct} vs the comparison period, primarily accounted for by "
        f"{top.dimension_label} {top.label}, which contributed {share} of the observed change."
    )
    return headline, explanation


def relationship_explanation(left: MetricSnapshot, right: MetricSnapshot) -> tuple[str, str]:
    assert left.delta is not None and right.delta is not None
    assert left.delta_pct is not None and right.delta_pct is not None
    headline = (
        f"{left.spec.label} {_direction_word(left.delta)} {format_pct(abs(left.delta_pct))} "
        f"while {right.spec.label} {_direction_word(right.delta)} "
        f"{format_pct(abs(right.delta_pct))}."
    )
    explanation = (
        f"{left.spec.label} {_direction_word(left.delta)} "
        f"{format_pct(abs(left.delta_pct))} vs the comparison period "
        f"({format_value(left.spec, left.current)} vs {format_value(left.spec, left.prior)}) "
        f"while {right.spec.label} {_direction_word(right.delta)} "
        f"{format_pct(abs(right.delta_pct))} "
        f"({format_value(right.spec, right.current)} vs {format_value(right.spec, right.prior)}). "
        f"This is a mix/efficiency change, not a causal claim."
    )
    return headline, explanation


def build_insights(
    *,
    current_measures: dict[str, object],
    prior_measures: dict[str, object],
    current_count: int,
    prior_count: int,
    grouped: dict[
        str,
        tuple[
            list[tuple[str, str, dict[str, object], int]],
            list[tuple[str, str, dict[str, object], int]],
        ],
    ],
    metrics: tuple[TrendMetricSpec, ...] = TREND_METRICS,
    limit: int = DEFAULT_INSIGHT_LIMIT,
) -> list[InsightDraft]:
    snapshots = {
        spec.key: snapshot_for(
            spec,
            current_measures,
            prior_measures,
            current_count=current_count,
            prior_count=prior_count,
        )
        for spec in metrics
    }
    drafts: list[InsightDraft] = []
    for spec in metrics:
        snap = snapshots[spec.key]
        if not is_material(snap):
            continue
        assert snap.delta is not None and snap.delta_pct is not None
        category = signed_category(snap)
        headline, explanation = movement_explanation(snap, category)
        drafts.append(
            InsightDraft(
                category=category,
                metric=spec,
                related=None,
                snapshot=snap,
                related_snapshot=None,
                dimension=None,
                drivers=[],
                threshold=threshold_label(snap),
                score=insight_score(
                    category=category,
                    delta=snap.delta,
                    delta_pct=snap.delta_pct,
                    prior=snap.prior,
                    driver_share=None,
                ),
                headline=headline,
                explanation=explanation,
                insight_id=f"{category}:{spec.key}",
            )
        )
        if not spec.additive or snap.delta == ZERO:
            continue
        best: tuple[Decimal, TrendDimensionSpec, list[DriverCandidate], int, int] | None = None
        for dim_key, (current_groups, prior_groups) in grouped.items():
            dimension = DRIVER_DIMENSIONS_BY_KEY[dim_key]
            rows, group_count = driver_rows(
                spec, dimension, current_groups, prior_groups, snap.delta
            )
            same = _same_sign(rows, snap.delta)
            if group_count < MIN_DRIVER_GROUPS or not same:
                continue
            top_share = abs(same[0].contribution or ZERO)
            if best is None or top_share > best[0]:
                best = (top_share, dimension, same, group_count, len(same))
        if best is None:
            continue
        _share, dimension, same, group_count, same_count = best
        qualifying = [item for item in same if _driver_qualifies(spec, item, DRIVER_SHARE)]
        multi = (
            len(same) >= 2
            and _driver_qualifies(spec, same[0], MULTI_DRIVER_EACH)
            and _driver_qualifies(spec, same[1], MULTI_DRIVER_EACH)
            and abs((same[0].contribution or ZERO) + (same[1].contribution or ZERO))
            >= MULTI_DRIVER_TOGETHER
        )
        if not qualifying and not multi:
            continue
        shown = same[:MAX_DRIVERS_EVIDENCE]
        headline, explanation = driver_explanation(snap, shown)
        driver_share = abs(shown[0].contribution or ZERO)
        drafts.append(
            InsightDraft(
                category="dominant_driver",
                metric=spec,
                related=None,
                snapshot=snap,
                related_snapshot=None,
                dimension=dimension,
                drivers=shown,
                threshold=threshold_label(snap, driver=True),
                score=insight_score(
                    category="dominant_driver",
                    delta=snap.delta,
                    delta_pct=snap.delta_pct,
                    prior=snap.prior,
                    driver_share=driver_share,
                ),
                headline=headline,
                explanation=explanation,
                insight_id=f"dominant_driver:{spec.key}:{dimension.key}:{shown[0].key}",
                group_count=group_count,
                same_sign_group_count=same_count,
            )
        )
    for left_key, right_key in RELATIONSHIP_PAIRS:
        left = snapshots.get(left_key)
        right = snapshots.get(right_key)
        if left is None or right is None:
            continue
        if not is_material(left) or not is_material(right):
            continue
        assert left.delta is not None and right.delta is not None
        if (left.delta > 0) == (right.delta > 0):
            continue
        headline, explanation = relationship_explanation(left, right)
        drafts.append(
            InsightDraft(
                category="relationship",
                metric=left.spec,
                related=right.spec,
                snapshot=left,
                related_snapshot=right,
                dimension=None,
                drivers=[],
                threshold=f"abs_pct>={MATERIAL_PCT};opposite_signed_material_pair",
                score=insight_score(
                    category="relationship",
                    delta=left.delta,
                    delta_pct=max(abs(left.delta_pct or ZERO), abs(right.delta_pct or ZERO)),
                    prior=left.prior,
                    driver_share=None,
                ),
                headline=headline,
                explanation=explanation,
                insight_id=f"relationship:{left.spec.key}:{right.spec.key}",
            )
        )
    drafts.sort(
        key=lambda item: (
            -item.score,
            -(abs(item.snapshot.delta_pct or ZERO)),
            item.metric.key,
            item.category,
            item.insight_id,
        )
    )
    return drafts[:limit]


def threshold_catalog() -> dict[str, str]:
    return {
        "material_pct": str(MATERIAL_PCT),
        "material_high_pct": str(MATERIAL_HIGH_PCT),
        "abs_floor_money": str(ABS_FLOOR["money"]),
        "abs_floor_count": str(ABS_FLOOR["count"]),
        "abs_floor_rate": str(ABS_FLOOR["rate"]),
        "abs_floor_roas": str(ABS_FLOOR["roas"]),
        "driver_share": str(DRIVER_SHARE),
        "multi_driver_each": str(MULTI_DRIVER_EACH),
        "multi_driver_together": str(MULTI_DRIVER_TOGETHER),
        "min_driver_groups": str(MIN_DRIVER_GROUPS),
        "min_denominator": str(MIN_DENOMINATOR),
        "max_insights": str(MAX_INSIGHTS),
        "ranking": (
            "score = min(|delta_pct|, 10) * 10000 + min(|delta| / max(|prior|, 1), 10) * 100 "
            "+ (driver_share or 0) * 1000 + category_weight; "
            "category_weight: dominant_driver=400, material_change=300, "
            "negative_signal=250, positive_signal=200, relationship=150. "
            "Sort score desc, then |delta_pct| desc, then metric, category, insight_id."
        ),
    }
