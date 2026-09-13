"""Configuration binding for a P4 run.

Wraps the P2 resolvers. Business rules are not re-implemented here: campaign
first-match-wins, no-trim matching, Q:R template lookup, and Filter Logic 1_2
membership all stay in `dfip_config.resolve`.

Publisher-uploaded Logic/Labels participate only when a CatalogStore overlay
has an **active** version for the client. Historical processing_run ids are
resolved even when that version has since been superseded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from dfip_config.catalog import (
    LABELS_KIND,
    LOGIC_KIND,
    CatalogStore,
    CatalogVersion,
)
from dfip_config.catalog_identity import persistable_catalog_version_id
from dfip_config.rate_cards import RATE_CARD_VERSIONS
from dfip_config.resolve import (
    CampaignResolution,
    TemplateResolution,
    resolve_campaign_label,
    resolve_campaign_label_from_rows,
    resolve_filter_logic_1_group,
    resolve_filter_logic_1_group_from_rows,
    resolve_template_status,
    select_version_for_day,
)
from dfip_config.store import (
    ConfigSnapshot,
    campaign_versions,
    load_campaign_version,
    load_manifest,
    load_template_version,
    template_versions,
)

DEFAULT_LABEL_GROUP_VERSION = "fl1-group-v1"


class ConfigurationBindingError(Exception):
    """No configuration version covers the requested day."""


@dataclass(frozen=True)
class ConfigBundle:
    """One immutable configuration selection used for a whole run."""

    campaign_version_label: str
    template_version_label: str
    rate_card_version_label: str
    label_group_version_label: str
    campaign_label_version_id: str | None
    template_label_version_id: str | None
    rate_card_version_id: str | None
    label_group_version_id: str | None
    binding_day: date
    campaign_rows: tuple[dict[str, Any], ...] | None = None
    label_group_rows: tuple[dict[str, Any], ...] | None = None

    def campaign(self, campaign_name: str | None) -> CampaignResolution:
        if self.campaign_rows is not None:
            return resolve_campaign_label_from_rows(self.campaign_rows, campaign_name)
        return resolve_campaign_label(self.campaign_version_label, campaign_name)

    def template(self, template_name: str | None) -> TemplateResolution:
        return resolve_template_status(self.template_version_label, template_name)

    def filter_logic_1_group(self, filter_logic_1: str | None) -> str | None:
        if self.label_group_rows is not None:
            return resolve_filter_logic_1_group_from_rows(filter_logic_1, self.label_group_rows)
        return resolve_filter_logic_1_group(filter_logic_1, self.label_group_version_label)


VersionWindows = tuple[tuple[str, str | None, str | None], ...]


def _windows(labels: tuple[str, ...], loader: Callable[[str], ConfigSnapshot]) -> VersionWindows:
    return tuple(
        (label, snapshot.effective_from, snapshot.effective_to)
        for label, snapshot in ((label, loader(label)) for label in labels)
    )


def _require(label: str | None, axis: str, day: date) -> str:
    if label is None:
        raise ConfigurationBindingError(f"no {axis} configuration version covers {day.isoformat()}")
    return label


def _packaged_ids() -> dict[str, str]:
    return load_manifest()["ids"]


def packaged_label_for_id(version_id: str | None) -> str | None:
    if not version_id:
        return None
    for label, packaged_id in _packaged_ids().items():
        if packaged_id == version_id:
            return label
    return None


def _overlay_rows(record: CatalogVersion) -> tuple[dict[str, Any], ...]:
    return record.rows


def bind_configuration(
    day: date,
    *,
    catalog: CatalogStore | None = None,
    client_id: str | None = None,
) -> ConfigBundle:
    """Select every configuration axis for `day`. Version windows are half-open.

    An active uploaded Logic/Labels version for `client_id` replaces the
    packaged JSON axis. Templates and rate cards remain packaged date windows.
    Packaged version UUIDs are stored only when they belong to `client_id`.
    """
    ids = _packaged_ids()
    campaign_overlay = catalog.active_for(client_id, LOGIC_KIND) if catalog and client_id else None
    label_overlay = catalog.active_for(client_id, LABELS_KIND) if catalog and client_id else None

    if campaign_overlay is not None:
        campaign_label = campaign_overlay.version_label
        campaign_id = persistable_catalog_version_id(
            client_id,
            campaign_overlay.id,
            owner_client_id=campaign_overlay.client_id,
        )
        campaign_rows = _overlay_rows(campaign_overlay)
    else:
        campaign_label = _require(
            select_version_for_day(_windows(campaign_versions(), load_campaign_version), day),
            "campaign label",
            day,
        )
        campaign_id = persistable_catalog_version_id(client_id, ids.get(campaign_label))
        campaign_rows = None

    template_label = _require(
        select_version_for_day(_windows(template_versions(), load_template_version), day),
        "template label",
        day,
    )
    rate_label = _require(select_version_for_day(RATE_CARD_VERSIONS, day), "rate card", day)

    if label_overlay is not None:
        group_label = label_overlay.version_label
        group_id = persistable_catalog_version_id(
            client_id,
            label_overlay.id,
            owner_client_id=label_overlay.client_id,
        )
        group_rows = _overlay_rows(label_overlay)
    else:
        group_label = DEFAULT_LABEL_GROUP_VERSION
        group_id = persistable_catalog_version_id(client_id, ids.get(group_label))
        group_rows = None

    return ConfigBundle(
        campaign_version_label=campaign_label,
        template_version_label=template_label,
        rate_card_version_label=rate_label,
        label_group_version_label=group_label,
        campaign_label_version_id=campaign_id,
        template_label_version_id=persistable_catalog_version_id(
            client_id, ids.get(template_label)
        ),
        rate_card_version_id=persistable_catalog_version_id(client_id, ids.get(rate_label)),
        label_group_version_id=group_id,
        binding_day=day,
        campaign_rows=campaign_rows,
        label_group_rows=group_rows,
    )


def bind_configuration_for_run(
    day: date,
    *,
    campaign_label_version_id: str | None,
    template_label_version_id: str | None,
    rate_card_version_id: str | None,
    label_group_version_id: str | None,
    catalog: CatalogStore | None = None,
    client_id: str | None = None,
) -> ConfigBundle:
    """Bind using version ids already stored on a processing_run.

    Draft/active/superseded overlays are all loadable by id so a historical
    run keeps the Logic/Labels it originally used.
    """
    ids = _packaged_ids()
    fallback = bind_configuration(day, catalog=None, client_id=None)

    campaign_label = packaged_label_for_id(campaign_label_version_id)
    campaign_id = persistable_catalog_version_id(client_id, campaign_label_version_id)
    campaign_rows = None
    overlay_campaign = (
        catalog.get_for_client(client_id, campaign_label_version_id)
        if catalog and client_id and campaign_label_version_id
        else None
    )
    if overlay_campaign is not None and overlay_campaign.kind == LOGIC_KIND:
        campaign_label = overlay_campaign.version_label
        campaign_id = persistable_catalog_version_id(
            client_id,
            overlay_campaign.id,
            owner_client_id=overlay_campaign.client_id,
        )
        campaign_rows = _overlay_rows(overlay_campaign)
    elif campaign_label is None:
        campaign_label = fallback.campaign_version_label
        if campaign_id is None:
            campaign_id = persistable_catalog_version_id(
                client_id, fallback.campaign_label_version_id
            )

    template_label = packaged_label_for_id(template_label_version_id)
    if template_label is None:
        template_label = fallback.template_version_label
    template_id = persistable_catalog_version_id(client_id, template_label_version_id)
    if template_id is None:
        template_id = persistable_catalog_version_id(client_id, ids.get(template_label))

    rate_label = packaged_label_for_id(rate_card_version_id)
    if rate_label is None:
        rate_label = fallback.rate_card_version_label
    rate_id = persistable_catalog_version_id(client_id, rate_card_version_id)
    if rate_id is None:
        rate_id = persistable_catalog_version_id(client_id, ids.get(rate_label))

    group_label = packaged_label_for_id(label_group_version_id) or DEFAULT_LABEL_GROUP_VERSION
    group_id = persistable_catalog_version_id(client_id, label_group_version_id)
    group_rows = None
    overlay_group = (
        catalog.get_for_client(client_id, label_group_version_id)
        if catalog and client_id and label_group_version_id
        else None
    )
    if overlay_group is not None and overlay_group.kind == LABELS_KIND:
        group_label = overlay_group.version_label
        group_id = persistable_catalog_version_id(
            client_id,
            overlay_group.id,
            owner_client_id=overlay_group.client_id,
        )
        group_rows = _overlay_rows(overlay_group)
    elif group_id is None:
        group_id = persistable_catalog_version_id(client_id, ids.get(group_label))

    if campaign_label is None:
        raise ConfigurationBindingError(
            f"no campaign label configuration version covers {day.isoformat()}"
        )

    return ConfigBundle(
        campaign_version_label=campaign_label,
        template_version_label=template_label,
        rate_card_version_label=rate_label,
        label_group_version_label=group_label,
        campaign_label_version_id=campaign_id,
        template_label_version_id=template_id,
        rate_card_version_id=rate_id,
        label_group_version_id=group_id,
        binding_day=day,
        campaign_rows=campaign_rows,
        label_group_rows=group_rows,
    )
