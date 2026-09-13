"""D1 Overview hero KPIs. Reuses client-namespace formulas from kpis.py.

Does not invent metrics. Does not average daily ratios. Dashboard D2+
filters and charts are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from dfip_core.transform.derive import month_label

HeroKind = Literal["money", "count", "rate", "roas"]
HeroSource = Literal["measure", "client_kpi"]


@dataclass(frozen=True)
class HeroKpi:
    """One Overview card. Formulas come from ADDITIVE_MEASURES or client KPIs."""

    id: str
    label: str
    kind: HeroKind
    source: HeroSource
    measure: str | None
    kpi_slug: str | None
    definition: str
    numerator: str | None = None
    denominator: str | None = None


HERO_KPIS: tuple[HeroKpi, ...] = (
    HeroKpi(
        "total_cost",
        "Total Cost",
        "money",
        "measure",
        "total_cost",
        None,
        "SUM of Total Cost (Delivered × rate card). Unmatched cost is 0.0000.",
    ),
    HeroKpi(
        "revenue_inr",
        "Revenue",
        "money",
        "measure",
        "revenue_inr",
        None,
        "SUM of Revenue (INR).",
    ),
    HeroKpi(
        "overall_roas",
        "Overall ROAS",
        "roas",
        "client_kpi",
        None,
        "overall_roas",
        "Revenue (INR) / Total Cost after SUM. Divide by zero is n/a, never 0.",
        "revenue_inr",
        "total_cost",
    ),
    HeroKpi(
        "delivered",
        "Delivered",
        "count",
        "measure",
        "delivered",
        None,
        "SUM of Delivered.",
    ),
    HeroKpi(
        "unique_clicks",
        "Unique Clicks",
        "count",
        "measure",
        "unique_clicks",
        None,
        "SUM of Unique Clicks. Source name is unique; reporting SUMs the column.",
    ),
    HeroKpi(
        "unique_conversions",
        "Unique Conversions",
        "count",
        "measure",
        "unique_conversions",
        None,
        "SUM of Unique Conversions.",
    ),
    HeroKpi(
        "delivery_rate",
        "Delivery Rate",
        "rate",
        "client_kpi",
        None,
        "delivery_rate",
        "Delivered / Sent after SUM. Divide by zero is n/a, never 0.",
        "delivered",
        "sent",
    ),
    HeroKpi(
        "ctr_del_to_clicks",
        "CTR (Delivered → Clicks)",
        "rate",
        "client_kpi",
        None,
        "ctr_del_to_clicks",
        "Unique Clicks / Delivered after SUM. Not clicks / impressions.",
        "unique_clicks",
        "delivered",
    ),
)

REASON_NO_HISTORY = "no_published_history"
REASON_NO_PRIOR = "no_prior_published_month"


def select_overview_months(month_starts: list[date]) -> tuple[date | None, date | None]:
    """Latest published month and the immediately preceding published month.

    Gaps are allowed: Oct vs Jun is valid when Jul–Sep were never published.
    Calendar-only prior months with no history are not invented.
    """
    unique = sorted({item for item in month_starts if item is not None})
    if not unique:
        return None, None
    current = unique[-1]
    prior = unique[-2] if len(unique) > 1 else None
    return current, prior


def overview_month_label(month: date | None) -> str | None:
    if month is None:
        return None
    return month_label(month)
