"""P1 rate-card configuration mirrored for resolvers.

Values were verified against Web-Engage Raw!H2 in all six production workbooks
and against the P1 SQL seed. P2 does not change them.
"""

from __future__ import annotations

from typing import NamedTuple


class RateCardRule(NamedTuple):
    version_label: str
    priority: int
    match_field: str
    match_value: str
    match_mode: str
    rate: str
    applies_to_measure: str


RATE_CARD_RULES: tuple[RateCardRule, ...] = (
    RateCardRule("rate-v1", 1, "template_status", "Utility", "equals_ci", "0.120000", "delivered"),
    RateCardRule("rate-v1", 2, "channel", "SMS", "equals_ci", "0.150000", "delivered"),
    RateCardRule("rate-v1", 3, "channel", "Email", "equals_ci", "0.010000", "delivered"),
    RateCardRule("rate-v1", 4, "channel", "RCS", "equals_ci", "0.250000", "delivered"),
    RateCardRule("rate-v1", 5, "channel", "WhatsApp", "equals_ci", "0.830000", "delivered"),
    RateCardRule("rate-v2", 1, "template_status", "Utility", "equals_ci", "0.115000", "delivered"),
    RateCardRule("rate-v2", 2, "channel", "SMS", "equals_ci", "0.150000", "delivered"),
    RateCardRule("rate-v2", 3, "channel", "Email", "equals_ci", "0.010000", "delivered"),
    RateCardRule("rate-v2", 4, "channel", "RCS", "equals_ci", "0.250000", "delivered"),
    RateCardRule("rate-v2", 5, "channel", "WhatsApp", "equals_ci", "0.785000", "delivered"),
)

RATE_CARD_VERSIONS: tuple[tuple[str, str | None, str | None], ...] = (
    ("rate-v1", "2025-04-01", "2025-08-01"),
    ("rate-v2", "2025-08-01", None),
)
