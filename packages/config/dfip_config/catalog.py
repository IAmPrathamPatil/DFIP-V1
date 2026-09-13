"""Publisher-uploaded Logic and Labels catalogs.

Packaged JSON under ``dfip_config.data`` remains the default processing
fallback. Uploaded versions live in this store, use the existing
``draft`` / ``active`` / ``superseded`` statuses, and never overwrite an
active version on import.

Processing selects a client's **active uploaded** version when one exists.
Historical ``processing_run`` rows keep the version ids they originally bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

CatalogKind = Literal["logic", "labels"]
CatalogStatus = Literal["draft", "active", "superseded"]

UPLOAD_NOTES = "v2c-publisher-upload"
LOGIC_KIND: CatalogKind = "logic"
LABELS_KIND: CatalogKind = "labels"


@dataclass(frozen=True)
class CatalogVersion:
    id: str
    client_id: str
    kind: CatalogKind
    version_label: str
    status: CatalogStatus
    row_count: int
    distinct_key_count: int
    duplicate_key_count: int
    created_at: datetime
    created_by: str | None
    notes: str | None
    source_filename: str | None
    rows: tuple[dict[str, Any], ...] = ()
    effective_from: str | None = None
    effective_to: str | None = None


class CatalogStore(Protocol):
    def add_version(self, record: CatalogVersion) -> CatalogVersion: ...

    def get(self, version_id: str) -> CatalogVersion | None: ...

    def get_for_client(self, client_id: str, version_id: str) -> CatalogVersion | None: ...

    def list_for_client(self, client_id: str, kind: CatalogKind) -> tuple[CatalogVersion, ...]: ...

    def save(self, record: CatalogVersion) -> CatalogVersion: ...

    def active_for(self, client_id: str, kind: CatalogKind) -> CatalogVersion | None: ...

    def activate(
        self,
        client_id: str,
        version_id: str,
        *,
        activated_by: str | None = None,
    ) -> CatalogVersion: ...

    def deactivate(self, client_id: str, version_id: str) -> CatalogVersion: ...


@dataclass
class InMemoryCatalogStore:
    """Process-local catalog overlay. Seeded packaged JSON is not stored here."""

    versions: dict[str, CatalogVersion] = field(default_factory=dict)

    def add_version(self, record: CatalogVersion) -> CatalogVersion:
        if record.id in self.versions:
            raise ValueError(f"catalog version {record.id} already exists")
        self.versions[record.id] = record
        return record

    def get(self, version_id: str) -> CatalogVersion | None:
        return self.versions.get(version_id)

    def get_for_client(self, client_id: str, version_id: str) -> CatalogVersion | None:
        record = self.versions.get(version_id)
        if record is None or record.client_id != client_id:
            return None
        return record

    def list_for_client(self, client_id: str, kind: CatalogKind) -> tuple[CatalogVersion, ...]:
        items = [
            item
            for item in self.versions.values()
            if item.client_id == client_id and item.kind == kind
        ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return tuple(items)

    def save(self, record: CatalogVersion) -> CatalogVersion:
        self.versions[record.id] = record
        return record

    def active_for(self, client_id: str, kind: CatalogKind) -> CatalogVersion | None:
        active = [
            item
            for item in self.versions.values()
            if item.client_id == client_id and item.kind == kind and item.status == "active"
        ]
        if not active:
            return None
        active.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return active[0]

    def activate(
        self,
        client_id: str,
        version_id: str,
        *,
        activated_by: str | None = None,
    ) -> CatalogVersion:
        current = self.get_for_client(client_id, version_id)
        if current is None:
            raise KeyError(version_id)
        if current.status != "draft":
            raise ValueError("only a draft version can be activated")
        if not current.rows:
            raise ValueError("empty catalog versions cannot be activated")
        now = datetime.now(tz=UTC).date().isoformat()
        for item in tuple(self.versions.values()):
            if (
                item.client_id == client_id
                and item.kind == current.kind
                and item.status == "active"
            ):
                self.versions[item.id] = replace(item, status="superseded", effective_to=now)
        activated = replace(
            current,
            status="active",
            effective_from=current.effective_from or now,
            effective_to=None,
            created_by=current.created_by or activated_by,
        )
        self.versions[activated.id] = activated
        return activated

    def deactivate(self, client_id: str, version_id: str) -> CatalogVersion:
        current = self.get_for_client(client_id, version_id)
        if current is None:
            raise KeyError(version_id)
        if current.status != "active":
            raise ValueError("only an active version can be deactivated")
        now = datetime.now(tz=UTC).date().isoformat()
        updated = replace(current, status="superseded", effective_to=now)
        self.versions[updated.id] = updated
        return updated

    def purge_client(self, client_id: str) -> None:
        self.versions = {
            key: item for key, item in self.versions.items() if item.client_id != client_id
        }


def new_version_label(kind: CatalogKind, when: datetime | None = None) -> str:
    stamp = (when or datetime.now(tz=UTC)).strftime("%Y%m%dT%H%M%SZ")
    prefix = "logic" if kind == LOGIC_KIND else "labels"
    return f"{prefix}-{stamp}-{uuid4().hex[:8]}"
