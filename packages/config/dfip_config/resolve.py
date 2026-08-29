"""Deterministic configuration resolution.

Reproduces recovered Excel VLOOKUP semantics:
- case-insensitive
- no trimming
- first row_order wins
- missing campaign labels -> None (Excel #N/A -> NULL)
- missing template status -> blank string (IFERROR -> "")
- column I of New Logic is never consulted for Template Status

This is not a row transformation engine and does not compute Total Cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from dfip_config.rate_cards import RATE_CARD_RULES, RateCardRule
from dfip_config.store import (
    ConfigSnapshot,
    load_campaign_version,
    load_label_group_captions,
    load_label_group_version,
    load_template_version,
)


def match_key(value: str | None) -> str | None:
    """Excel-like lookup key: lowercased, not trimmed."""
    if value is None:
        return None
    if value == "":
        return None
    return value.lower()


def _parse_day(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def select_version_for_day(
    versions: tuple[tuple[str, str | None, str | None], ...],
    day: date | datetime | str,
) -> str | None:
    """Pick the version whose [effective_from, effective_to) contains day."""
    target = _parse_day(day)
    for label, start, end in versions:
        if start and target < date.fromisoformat(start):
            continue
        if end and target >= date.fromisoformat(end):
            continue
        return label
    return None


@dataclass(frozen=True)
class CampaignResolution:
    match_status: str
    row_order: int | None
    campaign_name: str | None
    filter_logic_1: str | None
    filter_logic_2: str | None
    amc_status_filter_logic_3: str | None
    amc_device_category_filter_logic_4: str | None
    amc_product_cat_filter_logic_5: str | None
    manual_or_automated: str | None


@dataclass(frozen=True)
class TemplateResolution:
    match_status: str
    row_order: int | None
    template_name: str | None
    template_status: str


@dataclass(frozen=True)
class RateRuleResolution:
    matched: bool
    rule: RateCardRule | None


def _first_match_index(
    rows: tuple[dict[str, Any], ...], key_field: str
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: int(item["row_order"])):
        key = match_key(row.get(key_field))
        if key is None:
            continue
        if key not in index:
            index[key] = row
    return index


@lru_cache(maxsize=None)
def _campaign_index(version_label: str) -> dict[str, dict[str, Any]]:
    return _first_match_index(load_campaign_version(version_label).rows, "campaign_name")


@lru_cache(maxsize=None)
def _template_index(version_label: str) -> dict[str, dict[str, Any]]:
    return _first_match_index(load_template_version(version_label).rows, "template_name")


def _empty_campaign(status: str) -> CampaignResolution:
    return CampaignResolution(
        match_status=status,
        row_order=None,
        campaign_name=None,
        filter_logic_1=None,
        filter_logic_2=None,
        amc_status_filter_logic_3=None,
        amc_device_category_filter_logic_4=None,
        amc_product_cat_filter_logic_5=None,
        manual_or_automated=None,
    )


def campaign_resolution_from_row(row: dict[str, Any]) -> CampaignResolution:
    return CampaignResolution(
        match_status="matched",
        row_order=int(row["row_order"]),
        campaign_name=row.get("campaign_name"),
        filter_logic_1=row.get("filter_logic_1"),
        filter_logic_2=row.get("filter_logic_2"),
        amc_status_filter_logic_3=row.get("amc_status_filter_logic_3"),
        amc_device_category_filter_logic_4=row.get("amc_device_category_filter_logic_4"),
        amc_product_cat_filter_logic_5=row.get("amc_product_cat_filter_logic_5"),
        manual_or_automated=row.get("manual_or_automated"),
    )


def resolve_campaign_label_from_rows(
    rows: tuple[dict[str, Any], ...], campaign_name: str | None
) -> CampaignResolution:
    key = match_key(campaign_name)
    if key is None:
        return _empty_campaign("blank_key")
    row = _first_match_index(rows, "campaign_name").get(key)
    if row is None:
        return _empty_campaign("unmatched")
    return campaign_resolution_from_row(row)


def resolve_campaign_label(version_label: str, campaign_name: str | None) -> CampaignResolution:
    key = match_key(campaign_name)
    if key is None:
        return _empty_campaign("blank_key")
    row = _campaign_index(version_label).get(key)
    if row is None:
        return _empty_campaign("unmatched")
    return campaign_resolution_from_row(row)


def resolve_template_status(version_label: str, template_name: str | None) -> TemplateResolution:
    """Lookup New Logic Q:R. Never uses column I."""
    key = match_key(template_name)
    if key is None:
        return TemplateResolution("blank_key", None, template_name, "")
    row = _template_index(version_label).get(key)
    if row is None:
        return TemplateResolution("unmatched", None, template_name, "")
    status = row.get("template_status")
    return TemplateResolution(
        match_status="matched",
        row_order=int(row["row_order"]),
        template_name=row.get("template_name"),
        template_status="" if status is None else str(status),
    )


def resolve_rate_card_rule(
    version_label: str,
    template_status: str | None,
    channel: str | None,
) -> RateRuleResolution:
    """Return the first matching rate-card rule. Does not multiply Delivered."""
    rules = [rule for rule in RATE_CARD_RULES if rule.version_label == version_label]
    rules = sorted(rules, key=lambda rule: rule.priority)
    for rule in rules:
        candidate = template_status if rule.match_field == "template_status" else channel
        if candidate is None:
            continue
        if rule.match_mode == "equals_ci" and candidate.lower() == rule.match_value.lower():
            return RateRuleResolution(True, rule)
    return RateRuleResolution(False, None)


@lru_cache(maxsize=None)
def _group_index(version_label: str) -> dict[str | None, str | None]:
    """Exact-value membership index. First listed row wins, matching a linear scan."""
    snapshot: ConfigSnapshot = load_label_group_version(version_label)
    index: dict[str | None, str | None] = {}
    for row in snapshot.rows:
        value = row.get("filter_logic_1_value")
        if value not in index:
            index[value] = row.get("group_name")
    return index


def group_index_from_rows(rows: tuple[dict[str, Any], ...]) -> dict[str | None, str | None]:
    """Exact-value membership index. First listed row wins, matching a linear scan."""
    index: dict[str | None, str | None] = {}
    for row in rows:
        value = row.get("filter_logic_1_value")
        if value not in index:
            index[value] = row.get("group_name")
    return index


def resolve_filter_logic_1_group_from_rows(
    filter_logic_1: str | None,
    rows: tuple[dict[str, Any], ...],
) -> str | None:
    index = group_index_from_rows(rows)
    if filter_logic_1 in index:
        return index[filter_logic_1]
    return filter_logic_1


def resolve_filter_logic_1_group(
    filter_logic_1: str | None,
    version_label: str = "fl1-group-v1",
) -> str | None:
    """Map Filter Logic 1 to Filter Logic 1_2. Unknown values pass through.

    Exact string match. Values are not trimmed or cased.
    """
    index = _group_index(version_label)
    if filter_logic_1 in index:
        return index[filter_logic_1]
    return filter_logic_1


def resolve_group_display_name(
    group_name: str | None,
    version_label: str = "fl1-group-v1",
) -> str | None:
    """Map an internal Filter Logic 1_2 key to the FY-2026 client caption.

    Does not change membership. Leftover / unknown keys pass through.
    """
    if group_name is None:
        return None
    for key, caption in load_label_group_captions(version_label):
        if key == group_name:
            return caption
    return group_name
