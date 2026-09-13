"""Application-layer company lifecycle (P13F).

Deactivation blocks new live operations. It does not delete data, clear
publication_current, or change RLS / reporting views.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dfip_config.settings import Settings
from dfip_db.client_directory import ClientRecord

from dfip_api.errors import AuthorizationError

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_INACTIVE = "inactive"
ALLOWED_LIFECYCLE = frozenset({LIFECYCLE_ACTIVE, LIFECYCLE_INACTIVE})
INACTIVE_COMPANY_MESSAGE = "This company is inactive."


def lifecycle_status_of(record: ClientRecord | None) -> str:
    if record is None:
        return LIFECYCLE_ACTIVE
    status = (record.lifecycle_status or LIFECYCLE_ACTIVE).strip().lower()
    if status not in ALLOWED_LIFECYCLE:
        return LIFECYCLE_ACTIVE
    return status


def is_company_active(record: ClientRecord | None) -> bool:
    return lifecycle_status_of(record) == LIFECYCLE_ACTIVE


def require_company_active(directory, client_id: str | None) -> None:
    """Refuse live operations against an inactive company.

    Missing directory or unknown client_id is left to the caller (404/422).
    """
    if not client_id or directory is None:
        return
    record = directory.get(client_id)
    if record is None:
        return
    if not is_company_active(record):
        raise AuthorizationError(INACTIVE_COMPANY_MESSAGE)


def purge_eligible_after_for(settings: Settings, now: datetime | None = None) -> datetime | None:
    days = settings.dfip_company_purge_min_age_days
    if days is None:
        return None
    stamp = now or datetime.now(tz=UTC)
    return stamp + timedelta(days=max(0, int(days)))


def is_purge_eligible(record: ClientRecord, *, now: datetime | None = None) -> bool:
    if lifecycle_status_of(record) != LIFECYCLE_INACTIVE:
        return False
    eligible = record.purge_eligible_after
    if eligible is None:
        return False
    stamp = now or datetime.now(tz=UTC)
    if eligible.tzinfo is None:
        eligible = eligible.replace(tzinfo=UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp >= eligible


def is_protected_company(record: ClientRecord | None) -> bool:
    """Packaged catalog owner and the seeded default tenant cannot be deleted."""
    if record is None:
        return False
    from dfip_config.catalog_identity import packaged_catalog_owner_client_id
    from dfip_db.local_demo_guard import DEFAULT_CLIENT_CODE, DEFAULT_CLIENT_ID

    return (
        record.client_id == packaged_catalog_owner_client_id()
        or record.client_id == DEFAULT_CLIENT_ID
        or record.code == DEFAULT_CLIENT_CODE
    )
