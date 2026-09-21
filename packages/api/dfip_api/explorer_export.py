"""Serialize the current Performance Explorer result as a client-readable CSV.

Numbers come from GET /analytics/explorer. This module does not re-aggregate
facts or invent metrics.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from dfip_analytics.drill import DRILL_DIMENSIONS_BY_KEY
from dfip_analytics.trends import TREND_METRICS
from dfip_api.published_download import CSV_MEDIA_TYPE
from dfip_api.schemas import ExplorerResponse

EXPORT_MEDIA_TYPE = CSV_MEDIA_TYPE
NA = "n/a"
EXPLORER_CSV_FILENAME = "dfip-explorer.csv"

METRIC_DELTA_HEADERS: dict[str, str] = {
    "total_cost": "Δ Cost",
    "revenue_inr": "Δ Revenue",
    "overall_roas": "Δ ROAS",
    "delivered": "Δ Delivered",
    "unique_clicks": "Δ Unique Clicks",
    "unique_conversions": "Δ Unique Conversions",
    "delivery_rate": "Δ Delivery Rate",
    "ctr_del_to_clicks": "Δ CTR",
}

WORKSPACE_METADATA_HEADERS = frozenset(
    {
        "section",
        "key",
        "label",
        "value",
        "extra",
        "trend_point",
        "kpi",
        "context",
        "explorer_metric",
        "explorer_row",
    }
)


def render_explorer_csv(explorer: ExplorerResponse) -> tuple[str, bytes]:
    headers = explorer_csv_headers(explorer)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in explorer.rows or []:
        writer.writerow(_row_values(explorer, row))
    return EXPLORER_CSV_FILENAME, buffer.getvalue().encode("utf-8")


def explorer_csv_headers(explorer: ExplorerResponse) -> list[str]:
    selection = explorer.selection
    dimension_key = selection.dimension if selection is not None else "campaign_id"
    secondary_key = selection.secondary if selection is not None else None
    headers = ["Rank", _dimension_label(dimension_key, explorer)]
    if secondary_key:
        headers.append(_dimension_label(secondary_key, explorer))
    for spec in _metric_catalog(explorer):
        headers.append(spec.label)
        headers.append(_delta_header(spec))
    headers.extend(["Comparison", "Delta", "Delta %"])
    if selection is not None and selection.contribution_supported:
        headers.append("Share")
    return headers


def _metric_catalog(explorer: ExplorerResponse) -> list[Any]:
    catalog = list(explorer.metrics or [])
    if catalog:
        return catalog
    return list(TREND_METRICS)


def _dimension_label(key: str, explorer: ExplorerResponse) -> str:
    for item in explorer.dimensions or []:
        if item.value == key:
            return item.label
    spec = DRILL_DIMENSIONS_BY_KEY.get(key)
    return spec.label if spec is not None else key


def _delta_header(spec: Any) -> str:
    key = getattr(spec, "key", "")
    return METRIC_DELTA_HEADERS.get(key) or f"Δ {getattr(spec, 'label', key)}"


def _row_values(explorer: ExplorerResponse, row: Any) -> list[str]:
    selection = explorer.selection
    secondary = selection.secondary if selection is not None else None
    comparison_available = bool(explorer.comparison and explorer.comparison.available)
    ranking_key = selection.metric if selection is not None else None
    metric_map = {item.key: item for item in getattr(row, "metrics", None) or []}
    values = [str(row.rank), _display_label(row.parent_label if secondary else row.label, row.parent_key if secondary else row.key)]
    if secondary:
        values.append(_display_label(row.label, row.key))
    for spec in _metric_catalog(explorer):
        cell = metric_map.get(spec.key)
        if cell is None and spec.key == ranking_key:
            values.append(_fmt(row.value))
            values.append(_compared(row.delta, comparison_available))
            continue
        values.append(_fmt(None if cell is None else cell.value))
        values.append(_compared(None if cell is None else cell.delta, comparison_available))
    values.append(_compared(row.prior_value, comparison_available))
    values.append(_compared(row.delta, comparison_available))
    values.append(_compared(row.delta_pct, comparison_available))
    if selection is not None and selection.contribution_supported:
        values.append(_fmt(row.contribution_pct) if row.contribution_pct is not None else NA)
    return values


def _display_label(label: str | None, fallback: str | None) -> str:
    text = (label or "").strip()
    if text:
        return text
    alt = (fallback or "").strip()
    return alt if alt else "(blank)"


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return NA
    return str(value)


def _compared(value: Any, available: bool) -> str:
    if not available:
        return NA
    return _fmt(value)
