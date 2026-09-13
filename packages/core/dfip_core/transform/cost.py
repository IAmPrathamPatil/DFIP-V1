"""Total Cost.

Recovered Excel formula, identical in every supplied production workbook
(only the two rate literals differ between vintages):

    H = IF(C="Utility", AI*<utility>,
        IF(P="SMS",      AI*0.15,
        IF(P="Email",    AI*0.01,
        IF(P="RCS",      AI*0.25,
        IF(P="WhatsApp", AI*<whatsapp>, 0)))))

C = Template Status (New Logic Q:R), P = Channel, AI = Delivered.

So: Template Status Utility outranks Channel, the multiplier is always
Delivered, and an unmatched row costs 0 — never NULL and never an invented
rate. Native Web Engage rate columns are not inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from dfip_config.catalog_identity import packaged_catalog_owner_client_id
from dfip_config.rate_cards import RateCardRule
from dfip_config.resolve import resolve_rate_card_rule

# fact_campaign_day.total_cost is numeric(18, 4).
COST_EXPONENT = Decimal("0.0001")
ZERO_COST = Decimal("0.0000")

# Rate-card rule ids seeded in P1, keyed by (version_label, priority).
RATE_CARD_RULE_IDS: dict[tuple[str, int], str] = {
    ("rate-v1", 1): "a0000000-0000-4000-8000-000000000111",
    ("rate-v1", 2): "a0000000-0000-4000-8000-000000000112",
    ("rate-v1", 3): "a0000000-0000-4000-8000-000000000113",
    ("rate-v1", 4): "a0000000-0000-4000-8000-000000000114",
    ("rate-v1", 5): "a0000000-0000-4000-8000-000000000115",
    ("rate-v2", 1): "a0000000-0000-4000-8000-000000000121",
    ("rate-v2", 2): "a0000000-0000-4000-8000-000000000122",
    ("rate-v2", 3): "a0000000-0000-4000-8000-000000000123",
    ("rate-v2", 4): "a0000000-0000-4000-8000-000000000124",
    ("rate-v2", 5): "a0000000-0000-4000-8000-000000000125",
}


@dataclass(frozen=True)
class CostResolution:
    matched: bool
    rule: RateCardRule | None
    rule_id: str | None
    rate: Decimal | None
    total_cost: Decimal


def rate_card_rule_id(rule: RateCardRule | None) -> str | None:
    if rule is None:
        return None
    return RATE_CARD_RULE_IDS.get((rule.version_label, rule.priority))


def persistable_rate_card_rule_id(client_id: str | None, rule_id: str | None) -> str | None:
    """Store a rate-card rule UUID only when it belongs to ``client_id``.

    Packaged P1 rule rows are owned by the default catalog client. Packaged
    fallback still computes Total Cost; non-default tenants persist NULL
    instead of another tenant's rule identity.
    """
    if not rule_id:
        return None
    if not client_id:
        return rule_id
    packaged = frozenset(RATE_CARD_RULE_IDS.values())
    if rule_id in packaged:
        return rule_id if client_id == packaged_catalog_owner_client_id() else None
    return None


def calculate_total_cost(
    rate_card_version_label: str,
    template_status: str | None,
    channel: str | None,
    delivered: int | None,
) -> CostResolution:
    """Delivered x resolved rate. Unmatched rows cost 0, as the formula does."""
    resolution = resolve_rate_card_rule(rate_card_version_label, template_status, channel)
    if not resolution.matched or resolution.rule is None:
        return CostResolution(False, None, None, None, ZERO_COST)
    rate = Decimal(resolution.rule.rate)
    # Excel multiplies a blank Delivered cell as zero; the branch still matched.
    units = Decimal(0 if delivered is None else delivered)
    total = (units * rate).quantize(COST_EXPONENT, rounding=ROUND_HALF_UP)
    return CostResolution(
        matched=True,
        rule=resolution.rule,
        rule_id=rate_card_rule_id(resolution.rule),
        rate=rate,
        total_cost=total,
    )
