"""D9 canonical analytical workspace state.

URL query is the single persistence layer for D1–D8 Overview. Saved analysis
stores this configuration, not fact rows. ``client_id`` is never part of state.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from dfip_analytics.drill import DRILL_DIMENSIONS, DRILL_ORIGINS, MAX_DRILL_DEPTH
from dfip_analytics.explorer import (
    EXPLORER_DIRECTIONS,
    EXPLORER_MODES,
    EXPLORER_MOVERS,
    EXPLORER_SORTS,
    MAX_EXPLORER_LIMIT,
)
from dfip_analytics.filters import ALLOWED_DIMENSIONS, MAX_MULTI_VALUES, FilterValidationError
from dfip_analytics.overview import HERO_KPIS
from dfip_analytics.trends import TREND_DIMENSIONS_BY_KEY, TREND_GRAINS, TREND_METRICS_BY_KEY

HERO_KPI_IDS: frozenset[str] = frozenset(item.id for item in HERO_KPIS)
METRIC_IDS: frozenset[str] = frozenset(TREND_METRICS_BY_KEY) | HERO_KPI_IDS
DRILL_DIMENSION_IDS: frozenset[str] = frozenset(item.key for item in DRILL_DIMENSIONS)
TREND_DIMENSION_IDS: frozenset[str] = frozenset(TREND_DIMENSIONS_BY_KEY)
EXPLORER_DIMENSION_IDS: frozenset[str] = DRILL_DIMENSION_IDS
ASK_SOURCES: tuple[str, ...] = (
    "overview",
    "kpi",
    "trend",
    "explorer",
    "drill",
    "insight",
    "anomaly",
)

WORKSPACE_SINGLE_KEYS: tuple[str, ...] = (
    "period",
    "month_start",
    "day_from",
    "day_to",
    "compare",
    "compare_month_start",
    "compare_from",
    "compare_to",
    "trend_metric",
    "trend_secondary",
    "trend_grain",
    "trend_breakdown",
    "ex_metric",
    "ex_dimension",
    "ex_secondary",
    "ex_mode",
    "ex_dir",
    "ex_limit",
    "ex_sort",
    "ex_mover",
    "ex_min",
    "ex_min_contrib",
    "drill",
    "drill_metric",
    "drill_dimension",
    "drill_slice",
    "drill_slice_grain",
    "kpi",
    "insight",
    "anomaly",
    "ex_row",
    "trend_point",
)

WORKSPACE_MULTI_KEYS: tuple[str, ...] = (
    "campaign_id",
    "channel",
    "filter_logic_1",
    "filter_logic_1_group",
    "drill_parent",
)

FORBIDDEN_STATE_KEYS: frozenset[str] = frozenset(
    {"client_id", "saved", "token", "sql", "query", "share", "share_token"}
)

_DATE_KEYS = frozenset(
    {
        "month_start",
        "day_from",
        "day_to",
        "compare_month_start",
        "compare_from",
        "compare_to",
        "drill_slice",
        "trend_point",
    }
)
_METRIC_KEYS = frozenset({"trend_metric", "trend_secondary", "ex_metric", "drill_metric", "kpi"})
_MAX_STRING = 200
_MAX_ID = 120

ALLOWED_PERIODS = frozenset({"month", "range", "all_history"})
ALLOWED_COMPARE = frozenset({"auto", "none"})
ALLOWED_GRAINS = frozenset(TREND_GRAINS)
ALLOWED_SLICE_GRAINS = frozenset({"day", "week", "month"})


class WorkspaceStateError(FilterValidationError):
    """422-class workspace state error. Message is safe for clients."""


def _as_text(value: object, *, field: str, maximum: int = _MAX_STRING) -> str:
    if value is None:
        raise WorkspaceStateError(f"{field} is required.")
    text = str(value).strip()
    if not text:
        raise WorkspaceStateError(f"{field} is required.")
    if len(text) > maximum:
        raise WorkspaceStateError(f"{field} is too long.")
    if "\x00" in text:
        raise WorkspaceStateError(f"{field} is invalid.")
    return text


def _parse_date(value: object, *, field: str) -> str:
    text = _as_text(value, field=field, maximum=10)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise WorkspaceStateError(f"{field} must be an ISO date.") from exc
    return text


def _parse_string_list(value: object, *, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise WorkspaceStateError(f"{field} is invalid.")
    if len(items) > MAX_MULTI_VALUES:
        raise WorkspaceStateError(f"{field} has too many values.")
    parsed: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _as_text(item, field=field, maximum=_MAX_STRING)
        if text in seen:
            continue
        seen.add(text)
        parsed.append(text)
    return parsed


def parse_workspace_state(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate allowlisted workspace configuration. Never accepts SQL or client_id."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise WorkspaceStateError("Workspace state is invalid.")
    unknown = [str(key) for key in raw if key not in WORKSPACE_SINGLE_KEYS + WORKSPACE_MULTI_KEYS]
    forbidden = [key for key in unknown if key in FORBIDDEN_STATE_KEYS]
    if forbidden:
        raise WorkspaceStateError("Workspace state cannot include company identity or secrets.")
    if unknown:
        raise WorkspaceStateError("Workspace state contains unsupported fields.")

    state: dict[str, Any] = {}
    for key in WORKSPACE_SINGLE_KEYS:
        if key not in raw or raw[key] in (None, ""):
            continue
        if key in _DATE_KEYS:
            state[key] = _parse_date(raw[key], field=key)
            continue
        if key == "period":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in ALLOWED_PERIODS:
                raise WorkspaceStateError("period is not supported.")
            state[key] = value
            continue
        if key == "compare":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in ALLOWED_COMPARE:
                raise WorkspaceStateError("compare is not supported.")
            state[key] = value
            continue
        if key == "trend_grain":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in ALLOWED_GRAINS:
                raise WorkspaceStateError("trend_grain is not supported.")
            state[key] = value
            continue
        if key == "drill_slice_grain":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in ALLOWED_SLICE_GRAINS:
                raise WorkspaceStateError("drill_slice_grain is not supported.")
            state[key] = value
            continue
        if key in _METRIC_KEYS:
            value = _as_text(raw[key], field=key, maximum=80)
            if value not in METRIC_IDS:
                raise WorkspaceStateError(f"{key} is not a supported metric.")
            state[key] = value
            continue
        if key == "trend_breakdown":
            value = _as_text(raw[key], field=key, maximum=80)
            if value not in TREND_DIMENSION_IDS:
                raise WorkspaceStateError("trend_breakdown is not supported.")
            state[key] = value
            continue
        if key in {"ex_dimension", "ex_secondary", "drill_dimension"}:
            value = _as_text(raw[key], field=key, maximum=80)
            allowed = EXPLORER_DIMENSION_IDS if key != "drill_dimension" else DRILL_DIMENSION_IDS
            if key == "ex_secondary" and value not in EXPLORER_DIMENSION_IDS:
                raise WorkspaceStateError("ex_secondary is not supported.")
            if value not in allowed:
                raise WorkspaceStateError(f"{key} is not supported.")
            state[key] = value
            continue
        if key == "ex_mode":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in EXPLORER_MODES:
                raise WorkspaceStateError("ex_mode is not supported.")
            state[key] = value
            continue
        if key == "ex_dir":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in EXPLORER_DIRECTIONS:
                raise WorkspaceStateError("ex_dir is not supported.")
            state[key] = value
            continue
        if key == "ex_sort":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in EXPLORER_SORTS:
                raise WorkspaceStateError("ex_sort is not supported.")
            state[key] = value
            continue
        if key == "ex_mover":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in EXPLORER_MOVERS:
                raise WorkspaceStateError("ex_mover is not supported.")
            state[key] = value
            continue
        if key == "ex_limit":
            text = _as_text(raw[key], field=key, maximum=8)
            try:
                limit = int(text)
            except ValueError as exc:
                raise WorkspaceStateError("ex_limit is invalid.") from exc
            if limit < 1 or limit > MAX_EXPLORER_LIMIT:
                raise WorkspaceStateError("ex_limit is not supported.")
            state[key] = str(limit)
            continue
        if key == "drill":
            value = _as_text(raw[key], field=key, maximum=20)
            if value not in DRILL_ORIGINS:
                raise WorkspaceStateError("drill origin is not supported.")
            state[key] = value
            continue
        if key in {"ex_min", "ex_min_contrib"}:
            state[key] = _as_text(raw[key], field=key, maximum=40)
            continue
        if key in {"insight", "anomaly", "ex_row"}:
            state[key] = _as_text(raw[key], field=key, maximum=_MAX_ID)
            continue
        state[key] = _as_text(raw[key], field=key)

    for key in WORKSPACE_MULTI_KEYS:
        if key not in raw:
            continue
        values = _parse_string_list(raw[key], field=key)
        if key == "drill_parent" and len(values) > MAX_DRILL_DEPTH:
            raise WorkspaceStateError("drill path exceeds maximum depth.")
        if key != "drill_parent" and key not in ALLOWED_DIMENSIONS:
            raise WorkspaceStateError(f"{key} is not supported.")
        if values:
            state[key] = values
    if state.get("compare") == "none":
        state.pop("compare_month_start", None)
        state.pop("compare_from", None)
        state.pop("compare_to", None)
    return state


def copy_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    parsed = parse_workspace_state(state or {})
    copied: dict[str, Any] = {}
    for key, value in parsed.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


def with_kpi(state: Mapping[str, Any] | None, metric: str) -> dict[str, Any]:
    next_state = copy_state(state)
    next_state["kpi"] = metric
    return parse_workspace_state(next_state)


def with_trend_metric(state: Mapping[str, Any] | None, metric: str) -> dict[str, Any]:
    next_state = with_kpi(state, metric)
    if metric != "total_cost":
        next_state["trend_metric"] = metric
    else:
        next_state.pop("trend_metric", None)
    return parse_workspace_state(next_state)


def with_explorer_from_trend(state: Mapping[str, Any] | None) -> dict[str, Any]:
    next_state = copy_state(state)
    metric = next_state.get("trend_metric")
    if metric and metric != "total_cost":
        next_state["ex_metric"] = metric
    elif metric == "total_cost":
        next_state.pop("ex_metric", None)
    breakdown = next_state.get("trend_breakdown")
    if breakdown:
        next_state["ex_dimension"] = breakdown
    return parse_workspace_state(next_state)


def with_drill(
    state: Mapping[str, Any] | None,
    *,
    origin: str,
    metric: str,
    dimension: str = "campaign_id",
    parents: list[str] | None = None,
    slice_bucket: str | None = None,
    slice_grain: str | None = None,
) -> dict[str, Any]:
    next_state = copy_state(state)
    for key in (
        "drill",
        "drill_metric",
        "drill_dimension",
        "drill_slice",
        "drill_slice_grain",
        "drill_parent",
    ):
        next_state.pop(key, None)
    next_state["drill"] = origin
    if metric != "total_cost":
        next_state["drill_metric"] = metric
    if dimension != "campaign_id":
        next_state["drill_dimension"] = dimension
    if slice_bucket:
        next_state["drill_slice"] = slice_bucket
        if slice_grain and slice_grain != "day":
            next_state["drill_slice_grain"] = slice_grain
        next_state["trend_point"] = slice_bucket
    if parents:
        next_state["drill_parent"] = list(parents)
    return parse_workspace_state(next_state)


def with_insight(state: Mapping[str, Any] | None, insight_id: str) -> dict[str, Any]:
    next_state = copy_state(state)
    next_state["insight"] = insight_id
    return parse_workspace_state(next_state)


def with_anomaly(state: Mapping[str, Any] | None, anomaly_id: str) -> dict[str, Any]:
    next_state = copy_state(state)
    next_state["anomaly"] = anomaly_id
    return parse_workspace_state(next_state)


def ask_focus(state: Mapping[str, Any] | None, source: str) -> dict[str, Any]:
    """Map workspace state onto D8 Ask focus. Does not invent operations."""
    parsed = copy_state(state)
    if source not in ASK_SOURCES:
        raise WorkspaceStateError("Ask source is not supported.")
    focus: dict[str, Any] = {}
    if source == "kpi":
        metric = parsed.get("kpi") or parsed.get("trend_metric") or parsed.get("ex_metric")
        if metric:
            focus["metric"] = metric
    elif source == "trend":
        if parsed.get("trend_metric"):
            focus["metric"] = parsed["trend_metric"]
            focus["trend_metric"] = parsed["trend_metric"]
        if parsed.get("trend_secondary"):
            focus["trend_secondary"] = parsed["trend_secondary"]
        if parsed.get("trend_grain"):
            focus["trend_grain"] = parsed["trend_grain"]
        if parsed.get("trend_breakdown"):
            focus["trend_breakdown"] = parsed["trend_breakdown"]
    elif source == "explorer":
        metric = parsed.get("ex_metric") or parsed.get("kpi")
        if metric:
            focus["metric"] = metric
            focus["explorer_metric"] = parsed.get("ex_metric") or metric
        if parsed.get("ex_dimension"):
            focus["dimension"] = parsed["ex_dimension"]
            focus["explorer_dimension"] = parsed["ex_dimension"]
        if parsed.get("ex_mode"):
            focus["explorer_mode"] = parsed["ex_mode"]
    elif source == "drill":
        origin = parsed.get("drill")
        metric = parsed.get("drill_metric") or parsed.get("kpi")
        if origin:
            focus["drill_origin"] = origin
        if metric:
            focus["metric"] = metric
            focus["drill_metric"] = metric
        if parsed.get("drill_dimension"):
            focus["dimension"] = parsed["drill_dimension"]
            focus["drill_dimension"] = parsed["drill_dimension"]
        if parsed.get("drill_parent"):
            focus["drill_parents"] = list(parsed["drill_parent"])
    elif source == "insight":
        if parsed.get("insight"):
            focus["insight_id"] = parsed["insight"]
        if parsed.get("kpi"):
            focus["metric"] = parsed["kpi"]
    elif source == "anomaly":
        if parsed.get("anomaly"):
            focus["anomaly_id"] = parsed["anomaly"]
        if parsed.get("kpi"):
            focus["metric"] = parsed["kpi"]
    else:
        metric = parsed.get("kpi") or parsed.get("trend_metric") or parsed.get("ex_metric")
        if metric:
            focus["metric"] = metric
    return focus


def canonical_workspace_keys() -> tuple[str, ...]:
    return WORKSPACE_SINGLE_KEYS + WORKSPACE_MULTI_KEYS
