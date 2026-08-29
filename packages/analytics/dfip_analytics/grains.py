"""Reporting grains for post-aggregation KPI evaluation.

Native fact grain is (client_id, campaign_id, variation_id_key, day).
Product / ASIN is not a source grain and is not supported.
"""

from __future__ import annotations

from enum import Enum

from dfip_core.transform.fact import FactRecord


class GrainError(ValueError):
    """The requested reporting grain is not supported by the fact model."""


class Grain(str, Enum):
    OVERALL = "overall"
    CLIENT = "client"
    DATE = "date"
    CAMPAIGN = "campaign"
    VARIATION = "variation"
    CLIENT_DATE = "client_date"
    CLIENT_CAMPAIGN_DATE = "client_campaign_date"
    CLIENT_CAMPAIGN_VARIATION_DATE = "client_campaign_variation_date"


# Drill path for Power BI: Client → Date → Campaign → Variation → Day.
DRILL_PATH: tuple[Grain, ...] = (
    Grain.CLIENT,
    Grain.DATE,
    Grain.CAMPAIGN,
    Grain.VARIATION,
    Grain.CLIENT_CAMPAIGN_VARIATION_DATE,
)

UNSUPPORTED_GRAIN_NAMES: frozenset[str] = frozenset(
    {
        "product",
        "asin",
        "product_asin",
        "client_product_date",
        "client_asin_date",
    }
)


def parse_grain(name: str) -> Grain:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    if key in UNSUPPORTED_GRAIN_NAMES:
        raise GrainError(
            "Product/ASIN is not a fact grain. Use campaign attributes such as "
            "amc_product_cat_filter_logic_5 as a slicer."
        )
    try:
        return Grain(key)
    except ValueError as exc:
        raise GrainError(f"Unknown reporting grain {name!r}") from exc


def grain_key(fact: FactRecord, grain: Grain) -> tuple[object, ...]:
    """Return the grouping tuple for one fact at the requested grain."""
    if grain is Grain.OVERALL:
        return ()
    if grain is Grain.CLIENT:
        return (fact.client_id,)
    if grain is Grain.DATE:
        return (fact.day,)
    if grain is Grain.CAMPAIGN:
        return (fact.client_id, fact.campaign_id)
    if grain is Grain.VARIATION:
        return (fact.client_id, fact.campaign_id, fact.variation_id_key)
    if grain is Grain.CLIENT_DATE:
        return (fact.client_id, fact.day)
    if grain is Grain.CLIENT_CAMPAIGN_DATE:
        return (fact.client_id, fact.campaign_id, fact.day)
    if grain is Grain.CLIENT_CAMPAIGN_VARIATION_DATE:
        return (fact.client_id, fact.campaign_id, fact.variation_id_key, fact.day)
    raise GrainError(f"Unsupported grain {grain}")
