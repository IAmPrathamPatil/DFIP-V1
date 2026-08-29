"""Application-user directory used by password sign-in and JWT membership."""

from __future__ import annotations

import threading
from typing import Protocol
from uuid import uuid4

from dfip_db.identity import (
    CredentialRecord,
    IdentityRecord,
    MembershipRow,
    fetch_credential_from_pool,
    fetch_identity_from_pool,
    increment_token_version_from_pool,
)
from psycopg_pool import ConnectionPool

from dfip_api.password import hash_password


class IdentityStore(Protocol):
    """Lookup of app_user + memberships. Postgres is the authoritative directory."""

    requires_directory: bool

    def get_by_subject(self, subject: str) -> IdentityRecord | None: ...

    def get_credential(self, subject: str) -> CredentialRecord | None: ...

    def increment_token_version(self, user_id: str) -> int | None: ...


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
