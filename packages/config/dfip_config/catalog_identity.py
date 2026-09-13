"""Tenant ownership for persisted catalog version UUIDs.

Stored catalog version IDs must belong to the run's client_id. Packaged JSON
fallback must not masquerade as a different tenant's catalog version.

Packaged manifest IDs are owned by ``manifest.client_id`` (the default client).
Overlay version IDs are owned by ``CatalogVersion.client_id``.
"""

from __future__ import annotations

from dfip_config.store import load_manifest


def packaged_catalog_owner_client_id() -> str:
    return str(load_manifest()["client_id"])


def packaged_catalog_version_ids() -> frozenset[str]:
    return frozenset(str(value) for value in load_manifest()["ids"].values())


def persistable_catalog_version_id(
    client_id: str | None,
    version_id: str | None,
    *,
    owner_client_id: str | None = None,
) -> str | None:
    """Return ``version_id`` only when it is owned by ``client_id``.

    When ``client_id`` is omitted, packaged identifiers are left unchanged so
    default-client ingest helpers keep existing behavior. Foreign packaged
    UUIDs and unowned overlay IDs become ``None``.
    """
    if not version_id:
        return None
    if owner_client_id is not None:
        if not client_id or owner_client_id == client_id:
            return version_id
        return None
    if not client_id:
        return version_id
    if version_id in packaged_catalog_version_ids():
        return version_id if client_id == packaged_catalog_owner_client_id() else None
    return None


def strip_foreign_packaged_catalog_version_id(
    client_id: str | None, version_id: str | None
) -> str | None:
    """Drop packaged default-client UUIDs when the tenant is not that owner.

    Unknown (overlay) UUIDs are left unchanged. PostgreSQL insert still
    requires ``campaign_label_version.client_id = batch.client_id``.
    """
    if not version_id or not client_id:
        return version_id
    if (
        version_id in packaged_catalog_version_ids()
        and client_id != packaged_catalog_owner_client_id()
    ):
        return None
    return version_id
