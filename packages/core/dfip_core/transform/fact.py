"""The `fact_campaign_day` record and its grain key.

Grain: one row per client per campaign per variation per calendar day, exactly
as locked by the P1 primary key
`(client_id, campaign_id, variation_id_key, day)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, NamedTuple


class FactKey(NamedTuple):
    client_id: str
    campaign_id: str
    variation_id_key: str
    day: date


# Columns excluded from the value comparison used to detect a changed fact.
_LINEAGE_ONLY = frozenset({"first_seen_at", "last_seen_at"})


@dataclass(frozen=True)
class FactRecord:
    client_id: str
    campaign_id: str
    variation_id: str | None
    variation_id_key: str
    day: date

    month_start: date | None = None
    month_label: str | None = None

    campaign_name: str | None = None
    variation_name: str | None = None
    channel: str | None = None
    type_of_campaign: str | None = None
    start_date: datetime | None = None
    template_name_whatsapp: str | None = None

    sent: int | None = None
    failed: int | None = None
    delivered: int | None = None
    unique_impressions: int | None = None
    unique_clicks: int | None = None
    unique_conversions: int | None = None
    unique_impression_through_conversions: int | None = None
    unique_click_through_conversions: int | None = None
    revenue_inr: Decimal | None = None
    impression_through_revenue_inr: Decimal | None = None
    click_through_revenue_inr: Decimal | None = None

    filter_logic_1: str | None = None
    filter_logic_2: str | None = None
    template_status: str | None = None
    amc_status_filter_logic_3: str | None = None
    amc_device_category_filter_logic_4: str | None = None
    amc_product_cat_filter_logic_5: str | None = None
    manual_or_automated: str | None = None

    total_cost: Decimal | None = None
    hhh: str | None = None
    filter_logic_1_group: str | None = None

    label_match_status: str | None = None
    template_match_status: str | None = None

    rate_card_rule_id: str | None = None
    processing_run_id: str | None = None
    batch_id: str | None = None
    campaign_label_version_id: str | None = None
    template_label_version_id: str | None = None
    rate_card_version_id: str | None = None
    label_group_version_id: str | None = None

    first_seen_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def key(self) -> FactKey:
        return FactKey(self.client_id, self.campaign_id, self.variation_id_key, self.day)

    def business_values(self) -> dict[str, Any]:
        """Everything except the observation timestamps."""
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in _LINEAGE_ONLY
        }

    def carry_first_seen(self, previous: FactRecord) -> FactRecord:
        """Preserve the original observation time when a fact is restated."""
        return replace(self, first_seen_at=previous.first_seen_at)
