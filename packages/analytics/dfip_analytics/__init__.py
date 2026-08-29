"""Post-aggregation analytics: KPI specification, aggregation, and QA.

Does not rewrite P4 Total Cost / label / derive formulas. Does not compute
KPIs at fact-row grain. Native Web Engage rate columns are not inputs.
"""

from dfip_analytics.aggregate import aggregate_facts
from dfip_analytics.grains import Grain, GrainError
from dfip_analytics.kpis import KPI_SPECS, compute_kpis, spec_by_slug
from dfip_analytics.qa import evaluate_qa

__all__ = [
    "Grain",
    "GrainError",
    "KPI_SPECS",
    "aggregate_facts",
    "compute_kpis",
    "evaluate_qa",
    "spec_by_slug",
]
__version__ = "0.2.0"
