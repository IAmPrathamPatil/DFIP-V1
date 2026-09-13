"""D5 Performance Explorer ranking rules.

Reuses D3 metric specs and D4 drill dimensions. Formulas stay in kpis.py.
Secondary breakdown is one extra allowlisted dimension (depth 2), not a pivot cube.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from dfip_analytics.drill import (
    DEFAULT_DRILL_DIMENSION,
    DRILL_DIMENSIONS,
    DrillParent,
    next_dimensions,
    parse_drill_dimension,
)
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.trends import TrendDimensionSpec, TrendMetricSpec, parse_trend_metric

EXPLORER_MODES = ("ranking", "top", "bottom", "movers")
EXPLORER_SORTS = ("value", "delta", "delta_pct", "contribution")
EXPLORER_DIRECTIONS = ("desc", "asc")
EXPLORER_MOVERS = ("up", "down")
EXPLORER_CONTRIBUTIONS = ("auto", "share", "none")
DEFAULT_EXPLORER_MODE = "ranking"
DEFAULT_EXPLORER_SORT = "value"
DEFAULT_EXPLORER_LIMIT = 25
DEFAULT_TOP_LIMIT = 10
MAX_EXPLORER_LIMIT = 50
MAX_EXPLORER_DEPTH = 2
EXPLORER_DIMENSIONS = DRILL_DIMENSIONS
EXPLORER_DIMENSIONS_BY_KEY = {item.key: item for item in EXPLORER_DIMENSIONS}


def explorer_metric(raw: str | None) -> TrendMetricSpec:
    return parse_trend_metric(raw, role="primary")


def parse_explorer_dimension(raw: str | None) -> TrendDimensionSpec:
    return parse_drill_dimension(raw or DEFAULT_DRILL_DIMENSION)


def parse_explorer_secondary(raw: str | None) -> TrendDimensionSpec | None:
    if raw is None or str(raw).strip() == "":
        return None
    return parse_drill_dimension(raw)


def parse_explorer_mode(raw: str | None) -> str:
    mode = (raw or DEFAULT_EXPLORER_MODE).strip().lower()
    if mode not in EXPLORER_MODES:
        raise FilterValidationError("Unknown explorer ranking mode.")
    return mode


def parse_explorer_sort(raw: str | None, *, mode: str = DEFAULT_EXPLORER_MODE) -> str:
    if raw is None or str(raw).strip() == "":
        return "delta" if mode == "movers" else DEFAULT_EXPLORER_SORT
    sort = str(raw).strip().lower()
    if sort not in EXPLORER_SORTS:
        raise FilterValidationError("Unknown explorer sort.")
    return sort


def parse_explorer_direction(raw: str | None, *, mode: str, mover: str = "up") -> str:
    if raw is None or str(raw).strip() == "":
        if mode == "bottom" or (mode == "movers" and mover == "down"):
            return "asc"
        return "desc"
    direction = str(raw).strip().lower()
    if direction not in EXPLORER_DIRECTIONS:
        raise FilterValidationError("Sort direction must be asc or desc.")
    return direction


def parse_explorer_mover(raw: str | None) -> str:
    side = (raw or "up").strip().lower()
    if side not in EXPLORER_MOVERS:
        raise FilterValidationError("Mover side must be up or down.")
    return side


def parse_explorer_contribution(raw: str | None) -> str:
    mode = (raw or "auto").strip().lower()
    if mode not in EXPLORER_CONTRIBUTIONS:
        raise FilterValidationError("Contribution mode must be auto, share, or none.")
    return mode


def parse_explorer_limit(raw: int | str | None, *, mode: str) -> int:
    if raw is None or str(raw).strip() == "":
        return DEFAULT_TOP_LIMIT if mode in {"top", "bottom", "movers"} else DEFAULT_EXPLORER_LIMIT
    try:
        limit = int(str(raw).strip())
    except ValueError as exc:
        raise FilterValidationError("Explorer limit must be a positive integer.") from exc
    if limit < 1 or limit > MAX_EXPLORER_LIMIT:
        raise FilterValidationError(f"Explorer limit must be between 1 and {MAX_EXPLORER_LIMIT}.")
    return limit


def parse_threshold(raw: str | None, *, label: str) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise FilterValidationError(f"{label} must be a number.") from exc
    if value < 0:
        raise FilterValidationError(f"{label} cannot be negative.")
    return value


def contribution_supported(metric: TrendMetricSpec) -> bool:
    """Share-of-total is valid for additive measures only. Ratios are not shares."""
    return bool(metric.additive)


def validate_explorer_selection(
    metric: TrendMetricSpec,
    dimension: TrendDimensionSpec,
    secondary: TrendDimensionSpec | None,
    *,
    mode: str,
    sort: str,
    contribution: str,
    comparison_available: bool,
    min_contribution: Decimal | None,
) -> None:
    if dimension.key != "day" and dimension.key not in metric.dimensions:
        raise FilterValidationError(f"{metric.label} cannot be broken down by {dimension.label}.")
    if secondary is not None:
        if secondary.key == dimension.key:
            raise FilterValidationError(
                "Secondary dimension must differ from the primary dimension."
            )
        if secondary.key != "day" and secondary.key not in metric.dimensions:
            raise FilterValidationError(
                f"{metric.label} cannot be broken down by {secondary.label}."
            )
        if dimension.key == "day":
            raise FilterValidationError(
                "Day cannot have a secondary breakdown. It is a terminal grouping."
            )
    if contribution == "share" and not contribution_supported(metric):
        raise FilterValidationError(f"Share-of-total contribution is not valid for {metric.label}.")
    if min_contribution is not None and not contribution_supported(metric):
        raise FilterValidationError(f"A contribution threshold is not valid for {metric.label}.")
    if sort == "contribution" and not contribution_supported(metric):
        raise FilterValidationError(f"{metric.label} does not support contribution sorting.")
    if mode == "movers" and not comparison_available:
        raise FilterValidationError("Movers require a valid comparison period.")


def explorer_drill_target(
    dimension: TrendDimensionSpec,
    secondary: TrendDimensionSpec | None,
    *,
    key: str,
    parent_key: str | None,
) -> tuple[str | None, tuple[str, ...]]:
    """D4-compatible next dimension and parent tokens for one explorer row."""
    if secondary is not None:
        parent = DrillParent(dimension.key, parent_key or "")
        nxt = next_dimensions((parent,), secondary)
        if not nxt:
            return None, ()
        return nxt[0], (f"{dimension.key}:{parent_key or ''}", f"{secondary.key}:{key}")
    nxt = next_dimensions((), dimension)
    if not nxt:
        return None, ()
    return nxt[0], (f"{dimension.key}:{key}",)


def default_limit_for(mode: str) -> int:
    return DEFAULT_TOP_LIMIT if mode in {"top", "bottom", "movers"} else DEFAULT_EXPLORER_LIMIT
