"""Application-user directory used by password sign-in and JWT membership."""

from __future__ import annotations

import threading
from typing import Protocol
from uuid import uuid4

from dfip_db.identity import (
    CredentialRecord,
    DuplicateSubjectError,
    IdentityRecord,
    MembershipRow,
    NoCompaniesForSetupError,
    PublisherAlreadyExistsError,
    fetch_credential_from_pool,
    fetch_identity_from_pool,
    increment_token_version_from_pool,
    insert_client_password_user_from_pool,
    insert_publisher_password_user_from_pool,
    inspector_exists_from_pool,
)
from psycopg_pool import ConnectionPool

from dfip_api.password import hash_password
from dfip_api.roles import ADMIN_ROLES


class IdentityStore(Protocol):
    """Lookup of app_user + memberships. Postgres is the authoritative directory."""

    requires_directory: bool

    def get_by_subject(self, subject: str) -> IdentityRecord | None: ...

    def get_credential(self, subject: str) -> CredentialRecord | None: ...

    def increment_token_version(self, user_id: str) -> int | None: ...

    def create_client_user(
        self,
        *,
        subject: str,
        password: str,
        client_id: str,
        iterations: int,
    ) -> IdentityRecord: ...

    def inspector_exists(self) -> bool: ...

    def create_publisher_user(
        self,
        *,
        subject: str,
        password: str,
        memberships: tuple[MembershipRow, ...],
        iterations: int,
    ) -> IdentityRecord: ...


class InMemoryIdentityStore:
    """Test/local directory. Unknown subjects keep claim-based JWT behavior."""

    requires_directory = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_subject: dict[str, CredentialRecord] = {}
        self._by_user_id: dict[str, str] = {}

    def put_password_user(
        self,
        *,
        subject: str,
        password: str | None,
        iterations: int = 1000,
        memberships: tuple[MembershipRow, ...] = (),
        is_platform_admin: bool = False,
        user_id: str | None = None,
        token_version: int = 1,
    ) -> str:
        resolved_id = user_id or str(uuid4())
        password_hash = hash_password(password, iterations) if password else None
        identity = IdentityRecord(
            user_id=resolved_id,
            subject=subject,
            is_platform_admin=is_platform_admin,
            memberships=memberships,
            token_version=token_version,
        )
        record = CredentialRecord(identity=identity, password_hash=password_hash)
        with self._lock:
            self._by_subject[subject] = record
            self._by_user_id[resolved_id] = subject
        return resolved_id

    def create_client_user(
        self,
        *,
        subject: str,
        password: str,
        client_id: str,
        iterations: int,
    ) -> IdentityRecord:
        with self._lock:
            if subject in self._by_subject:
                raise DuplicateSubjectError("username is already in use.")
        self.put_password_user(
            subject=subject,
            password=password,
            iterations=iterations,
            memberships=(MembershipRow(client_id, "client"),),
        )
        identity = self.get_by_subject(subject)
        if identity is None:
            raise RuntimeError("client user insert returned no identity.")
        return identity

    def inspector_exists(self) -> bool:
        with self._lock:
            for record in self._by_subject.values():
                if record.identity.is_platform_admin:
                    return True
                if any(item.role in ADMIN_ROLES for item in record.identity.memberships):
                    return True
        return False

    def create_publisher_user(
        self,
        *,
        subject: str,
        password: str,
        memberships: tuple[MembershipRow, ...],
        iterations: int,
    ) -> IdentityRecord:
        if self.inspector_exists():
            raise PublisherAlreadyExistsError("A publisher account already exists.")
        if not memberships:
            raise NoCompaniesForSetupError("No companies exist.")
        with self._lock:
            if subject in self._by_subject:
                raise DuplicateSubjectError("username is already in use.")
        self.put_password_user(
            subject=subject,
            password=password,
            iterations=iterations,
            memberships=memberships,
        )
        identity = self.get_by_subject(subject)
        if identity is None:
            raise RuntimeError("publisher user insert returned no identity.")
        return identity

    def get_by_subject(self, subject: str) -> IdentityRecord | None:
        record = self.get_credential(subject)
        return None if record is None else record.identity

    def get_credential(self, subject: str) -> CredentialRecord | None:
        with self._lock:
            return self._by_subject.get(subject)

    def increment_token_version(self, user_id: str) -> int | None:
        with self._lock:
            subject = self._by_user_id.get(user_id)
            if subject is None:
                return None
            current = self._by_subject[subject]
            nxt = current.identity.token_version + 1
            identity = IdentityRecord(
                user_id=current.identity.user_id,
                subject=current.identity.subject,
                is_platform_admin=current.identity.is_platform_admin,
                memberships=current.identity.memberships,
                token_version=nxt,
            )
            self._by_subject[subject] = CredentialRecord(
                identity=identity, password_hash=current.password_hash
            )
            return nxt

    def identities(self) -> tuple[IdentityRecord, ...]:
        with self._lock:
            return tuple(record.identity for record in self._by_subject.values())

    def apply_client_name(self, client_id: str, name: str) -> None:
        with self._lock:
            for subject, current in list(self._by_subject.items()):
                memberships = tuple(
                    MembershipRow(
                        item.client_id,
                        item.role,
                        code=item.code,
                        name=name if item.client_id == client_id else item.name,
                    )
                    for item in current.identity.memberships
                )
                identity = IdentityRecord(
                    user_id=current.identity.user_id,
                    subject=current.identity.subject,
                    is_platform_admin=current.identity.is_platform_admin,
                    memberships=memberships,
                    token_version=current.identity.token_version,
                )
                self._by_subject[subject] = CredentialRecord(
                    identity=identity, password_hash=current.password_hash
                )

    def add_inspector_membership(self, user_id: str, membership: MembershipRow) -> None:
        with self._lock:
            subject = self._by_user_id.get(user_id)
            if subject is None:
                return
            current = self._by_subject[subject]
            if any(item.client_id == membership.client_id for item in current.identity.memberships):
                return
            identity = IdentityRecord(
                user_id=current.identity.user_id,
                subject=current.identity.subject,
                is_platform_admin=current.identity.is_platform_admin,
                memberships=(*current.identity.memberships, membership),
                token_version=current.identity.token_version,
            )
            self._by_subject[subject] = CredentialRecord(
                identity=identity, password_hash=current.password_hash
            )

    def drop_company(self, client_id: str) -> None:
        with self._lock:
            for subject, current in list(self._by_subject.items()):
                remaining = tuple(
                    item for item in current.identity.memberships if item.client_id != client_id
                )
                if not remaining and not current.identity.is_platform_admin:
                    self._by_subject.pop(subject, None)
                    self._by_user_id.pop(current.identity.user_id, None)
                    continue
                identity = IdentityRecord(
                    user_id=current.identity.user_id,
                    subject=current.identity.subject,
                    is_platform_admin=current.identity.is_platform_admin,
                    memberships=remaining,
                    token_version=current.identity.token_version,
                )
                self._by_subject[subject] = CredentialRecord(
                    identity=identity, password_hash=current.password_hash
                )


class PostgresIdentityStore:
    """app_user directory when a database pool is attached. Fail closed."""

    requires_directory = True

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get_by_subject(self, subject: str) -> IdentityRecord | None:
        return fetch_identity_from_pool(self._pool, subject)

    def get_credential(self, subject: str) -> CredentialRecord | None:
        return fetch_credential_from_pool(self._pool, subject)

    def increment_token_version(self, user_id: str) -> int | None:
        return increment_token_version_from_pool(self._pool, user_id)

    def create_client_user(
        self,
        *,
        subject: str,
        password: str,
        client_id: str,
        iterations: int,
    ) -> IdentityRecord:
        return insert_client_password_user_from_pool(
            self._pool,
            subject=subject,
            password_hash=hash_password(password, iterations),
            client_id=client_id,
        )

    def inspector_exists(self) -> bool:
        return inspector_exists_from_pool(self._pool)

    def create_publisher_user(
        self,
        *,
        subject: str,
        password: str,
        memberships: tuple[MembershipRow, ...],
        iterations: int,
    ) -> IdentityRecord:
        return insert_publisher_password_user_from_pool(
            self._pool,
            subject=subject,
            password_hash=hash_password(password, iterations),
            memberships=memberships,
        )
