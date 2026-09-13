"""Authorized-company directory used by the publisher registry and rename."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from dfip_db.client_directory import (
    ClientRecord,
    fetch_client_from_pool,
    insert_client_for_inspector_from_pool,
    list_all_clients_from_pool,
    list_clients_from_pool,
    set_client_lifecycle_from_pool,
    update_client_name_from_pool,
)
from dfip_db.identity import IdentityRecord, MembershipRow
from psycopg_pool import ConnectionPool

from dfip_api.auth import Principal
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.roles import ADMIN_ROLES, can_inspect
from dfip_api.schemas import SessionClient


class ClientDirectory(Protocol):
    def get(self, client_id: str) -> ClientRecord | None: ...

    def list(self, client_ids: Sequence[str]) -> tuple[ClientRecord, ...]: ...

    def list_all(self) -> tuple[ClientRecord, ...]: ...

    def rename(self, client_id: str, name: str) -> ClientRecord | None: ...

    def create(self, *, name: str, owner_user_id: str, owner_role: str) -> ClientRecord: ...

    def set_lifecycle(
        self,
        client_id: str,
        *,
        lifecycle_status: str,
        deactivated_at: datetime | None,
        purge_eligible_after: datetime | None,
    ) -> ClientRecord | None: ...


class InMemoryClientDirectory:
    """Test/local client rows. Rename updates name only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, ClientRecord] = {}

    def ensure(
        self,
        client_id: str,
        *,
        code: str | None = None,
        name: str | None = None,
    ) -> ClientRecord:
        with self._lock:
            existing = self._by_id.get(client_id)
            if existing is not None:
                return existing
            record = ClientRecord(
                client_id=client_id,
                code=code or client_id,
                name=name or code or client_id,
            )
            self._by_id[client_id] = record
            return record

    def get(self, client_id: str) -> ClientRecord | None:
        with self._lock:
            return self._by_id.get(client_id)

    def list(self, client_ids: Sequence[str]) -> tuple[ClientRecord, ...]:
        with self._lock:
            found = [self._by_id[item] for item in client_ids if item in self._by_id]
        found.sort(key=lambda row: (row.name.lower(), row.client_id))
        return tuple(found)

    def list_all(self) -> tuple[ClientRecord, ...]:
        with self._lock:
            found = list(self._by_id.values())
        found.sort(key=lambda row: (row.name.lower(), row.client_id))
        return tuple(found)

    def rename(self, client_id: str, name: str) -> ClientRecord | None:
        with self._lock:
            existing = self._by_id.get(client_id)
            if existing is None:
                return None
            updated = ClientRecord(
                client_id=existing.client_id,
                code=existing.code,
                name=name,
                lifecycle_status=existing.lifecycle_status,
                deactivated_at=existing.deactivated_at,
                purge_eligible_after=existing.purge_eligible_after,
            )
            self._by_id[client_id] = updated
            return updated

    def create(self, *, name: str, owner_user_id: str, owner_role: str) -> ClientRecord:
        del owner_user_id, owner_role
        with self._lock:
            client_id = str(uuid4())
            while client_id in self._by_id:
                client_id = str(uuid4())
            record = ClientRecord(client_id=client_id, code=client_id, name=name)
            self._by_id[client_id] = record
            return record

    def set_lifecycle(
        self,
        client_id: str,
        *,
        lifecycle_status: str,
        deactivated_at: datetime | None,
        purge_eligible_after: datetime | None,
    ) -> ClientRecord | None:
        with self._lock:
            existing = self._by_id.get(client_id)
            if existing is None:
                return None
            updated = ClientRecord(
                client_id=existing.client_id,
                code=existing.code,
                name=existing.name,
                lifecycle_status=lifecycle_status,
                deactivated_at=deactivated_at,
                purge_eligible_after=purge_eligible_after,
            )
            self._by_id[client_id] = updated
            return updated

    def delete(self, client_id: str) -> bool:
        with self._lock:
            return self._by_id.pop(client_id, None) is not None


class PostgresClientDirectory:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get(self, client_id: str) -> ClientRecord | None:
        return fetch_client_from_pool(self._pool, client_id)

    def list(self, client_ids: Sequence[str]) -> tuple[ClientRecord, ...]:
        return list_clients_from_pool(self._pool, client_ids)

    def list_all(self) -> tuple[ClientRecord, ...]:
        return list_all_clients_from_pool(self._pool)

    def rename(self, client_id: str, name: str) -> ClientRecord | None:
        return update_client_name_from_pool(self._pool, client_id, name)

    def create(self, *, name: str, owner_user_id: str, owner_role: str) -> ClientRecord:
        return insert_client_for_inspector_from_pool(
            self._pool,
            name=name,
            owner_user_id=owner_user_id,
            owner_role=owner_role,
        )

    def set_lifecycle(
        self,
        client_id: str,
        *,
        lifecycle_status: str,
        deactivated_at: datetime | None,
        purge_eligible_after: datetime | None,
    ) -> ClientRecord | None:
        return set_client_lifecycle_from_pool(
            self._pool,
            client_id,
            lifecycle_status=lifecycle_status,
            deactivated_at=deactivated_at,
            purge_eligible_after=purge_eligible_after,
        )


def inspector_directory_ids(
    principal: Principal, identity: IdentityRecord | None
) -> tuple[str, ...]:
    """Inspector memberships from the directory, not the bound JWT filter.

    Operational routes still use JWT client_id precedence. Company display
    names are membership-scoped directory attributes.
    """
    if not can_inspect(principal.role):
        return ()
    if identity is not None:
        return tuple(item.client_id for item in identity.memberships if item.role in ADMIN_ROLES)
    if principal.membership_client_ids:
        return principal.membership_client_ids
    if principal.client_id:
        return (principal.client_id,)
    return ()


def overlay_session_clients(
    clients: list[SessionClient],
    directory: ClientDirectory | None,
) -> list[SessionClient]:
    if directory is None or not clients:
        return clients
    records = {
        item.client_id: item for item in directory.list(tuple(row.client_id for row in clients))
    }
    overlaid: list[SessionClient] = []
    for row in clients:
        record = records.get(row.client_id)
        overlaid.append(
            SessionClient(
                client_id=row.client_id,
                role=row.role,
                code=record.code if record is not None else row.code,
                name=record.name if record is not None else row.name,
                lifecycle_status=(record.lifecycle_status if record is not None else "active"),
                deactivated_at=record.deactivated_at if record is not None else None,
                purge_eligible_after=(record.purge_eligible_after if record is not None else None),
            )
        )
    return overlaid


def seed_from_identity(directory: InMemoryClientDirectory, store: InMemoryIdentityStore) -> None:
    for identity in store.identities():
        for item in identity.memberships:
            directory.ensure(item.client_id, code=item.code, name=item.name)


def apply_name_to_identity_store(store, client_id: str, name: str) -> None:
    if not isinstance(store, InMemoryIdentityStore):
        return
    store.apply_client_name(client_id, name)


def grant_created_company(store, *, user_id: str, record: ClientRecord, role: str) -> None:
    if not isinstance(store, InMemoryIdentityStore):
        return
    store.add_inspector_membership(
        user_id,
        MembershipRow(record.client_id, role, code=record.code, name=record.name),
    )
