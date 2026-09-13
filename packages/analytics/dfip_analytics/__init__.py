"""Post-aggregation analytics: KPI specification, aggregation, and QA.

Does not rewrite P4 Total Cost / label / derive formulas. Does not compute
KPIs at fact-row grain. Native Web Engage rate columns are not inputs.
"""

from dfip_analytics.aggregate import aggregate_facts
from dfip_analytics.anomalies import ANOMALY_KINDS, MIN_BASELINE_MONTHS
from dfip_analytics.ask import ASK_INTENTS, ASK_SOURCES
from dfip_analytics.drill import DRILL_DIMENSIONS, MAX_DRILL_DEPTH
from dfip_analytics.explorer import EXPLORER_DIMENSIONS, MAX_EXPLORER_DEPTH, MAX_EXPLORER_LIMIT
from dfip_analytics.filters import (
    ALLOWED_DIMENSIONS,
    FilterValidationError,
    normalize_multi,
    resolve_comparison,
    resolve_period,
)
from dfip_analytics.grains import Grain, GrainError
from dfip_analytics.insights import (
    DRIVER_DIMENSIONS,
    INSIGHT_CATEGORIES,
    MATERIAL_PCT,
    MAX_INSIGHTS,
)
from dfip_analytics.kpis import KPI_SPECS, compute_kpis, spec_by_slug
from dfip_analytics.overview import HERO_KPIS, select_overview_months
from dfip_analytics.qa import evaluate_qa
from dfip_analytics.trends import (
    TREND_DIMENSIONS,
    TREND_GRAINS,
    TREND_METRICS,
    bucket_start,
    iso_week_start,
)
from dfip_analytics.workspace import (
    ASK_SOURCES as WORKSPACE_ASK_SOURCES,
)
from dfip_analytics.workspace import (
    METRIC_IDS,
    WORKSPACE_MULTI_KEYS,
    WORKSPACE_SINGLE_KEYS,
    parse_workspace_state,
)

__all__ = [
    "Grain",
    "GrainError",
    "HERO_KPIS",
    "KPI_SPECS",
    "TREND_DIMENSIONS",
    "TREND_GRAINS",
    "TREND_METRICS",
    "DRILL_DIMENSIONS",
    "MAX_DRILL_DEPTH",
    "EXPLORER_DIMENSIONS",
    "MAX_EXPLORER_DEPTH",
    "MAX_EXPLORER_LIMIT",
    "DRIVER_DIMENSIONS",
    "INSIGHT_CATEGORIES",
    "MAX_INSIGHTS",
    "MATERIAL_PCT",
    "ANOMALY_KINDS",
    "MIN_BASELINE_MONTHS",
    "ASK_INTENTS",
    "ASK_SOURCES",
    "WORKSPACE_ASK_SOURCES",
    "METRIC_IDS",
    "WORKSPACE_MULTI_KEYS",
    "WORKSPACE_SINGLE_KEYS",
    "parse_workspace_state",
    "aggregate_facts",
    "bucket_start",
    "compute_kpis",
    "evaluate_qa",
    "iso_week_start",
    "select_overview_months",
    "spec_by_slug",
    "ALLOWED_DIMENSIONS",
    "FilterValidationError",
    "normalize_multi",
    "resolve_comparison",
    "resolve_period",
]
__version__ = "0.2.0"
