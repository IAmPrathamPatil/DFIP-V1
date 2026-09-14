"""app_user / client_membership lookup and operator client-user insert.

Identity reads assume ``dfip_api`` via ``identity_lookup_bind`` because the
production API LOGIN has no table grants. Client-user inserts use the bound
inspector RLS context (SET LOCAL ROLE dfip_api). Publisher setup inserts use
identity-lookup RLS. Application authorization remains the primary control.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import as_uuid_text
from dfip_db.rls import (
    apply_rls_settings,
    expand_inspector_registry_clients,
    identity_lookup_bind,
    identity_lookup_rls,
    require_inspector_rls,
)


@dataclass(frozen=True)
class MembershipRow:
    client_id: str
    role: str
    code: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class IdentityRecord:
    user_id: str
    subject: str
    is_platform_admin: bool
    memberships: tuple[MembershipRow, ...]
    token_version: int = 1


@dataclass(frozen=True)
class CredentialRecord:
    identity: IdentityRecord
    password_hash: str | None


def _identity_from_rows(rows: Sequence[Mapping[str, Any]]) -> IdentityRecord:
    memberships: list[MembershipRow] = []
    for row in rows:
        if row["client_id"] is not None:
            memberships.append(
                MembershipRow(
                    as_uuid_text(row["client_id"]),
                    row["role"],
                    code=row["client_code"] if "client_code" in row else None,
                    name=row["client_name"] if "client_name" in row else None,
                )
            )
    first = rows[0]
    token_version = first["token_version"]
    return IdentityRecord(
        user_id=as_uuid_text(first["user_id"]),
        subject=first["subject"],
        is_platform_admin=bool(first["is_platform_admin"]),
        memberships=tuple(memberships),
        token_version=int(token_version) if token_version is not None else 1,
    )


def fetch_identity(conn: Connection[object], subject: str) -> IdentityRecord | None:
    rows = conn.execute(
        """
        SELECT
            u.id AS user_id,
            u.subject AS subject,
            u.is_platform_admin AS is_platform_admin,
            u.token_version AS token_version,
            m.client_id AS client_id,
            m.role AS role,
            c.code AS client_code,
            c.name AS client_name
        FROM app_user AS u
        LEFT JOIN client_membership AS m ON m.user_id = u.id
        LEFT JOIN client AS c ON c.id = m.client_id
        WHERE u.subject = %s
        ORDER BY m.client_id
        """,
        (subject,),
    ).fetchall()
    if not rows:
        return None
    return _identity_from_rows(rows)


def fetch_credential(conn: Connection[object], subject: str) -> CredentialRecord | None:
    rows = conn.execute(
        """
        SELECT
            u.id AS user_id,
            u.subject AS subject,
            u.is_platform_admin AS is_platform_admin,
            u.token_version AS token_version,
            u.password_hash AS password_hash,
            m.client_id AS client_id,
            m.role AS role,
            c.code AS client_code,
            c.name AS client_name
        FROM app_user AS u
        LEFT JOIN client_membership AS m ON m.user_id = u.id
        LEFT JOIN client AS c ON c.id = m.client_id
        WHERE u.subject = %s
        ORDER BY m.client_id
        """,
        (subject,),
    ).fetchall()
    if not rows:
        return None
    identity = _identity_from_rows(rows)
    raw_hash = rows[0]["password_hash"]
    password_hash = str(raw_hash) if raw_hash else None
    return CredentialRecord(identity=identity, password_hash=password_hash)


class DuplicateSubjectError(Exception):
    """Raised when app_user.subject is already taken. Do not include secrets."""


class PublisherAlreadyExistsError(Exception):
    """Raised when a publisher/admin identity already exists. No secrets."""


class NoCompaniesForSetupError(Exception):
    """Raised when publisher setup has no client rows to authorize."""


def inspector_exists(conn: Connection[object]) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM app_user AS u
            WHERE u.is_platform_admin
               OR EXISTS (
                    SELECT 1
                    FROM client_membership AS m
                    WHERE m.user_id = u.id
                      AND m.role IN ('publisher', 'admin')
               )
        ) AS present
        """
    ).fetchone()
    return bool(row and row["present"])


def increment_token_version(conn: Connection[object], user_id: str) -> int | None:
    row = conn.execute(
        """
        UPDATE app_user
        SET token_version = token_version + 1,
            updated_at = now()
        WHERE id = %s
        RETURNING token_version
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["token_version"])


def insert_client_password_user(
    conn: Connection[object],
    *,
    subject: str,
    password_hash: str,
    client_id: str,
) -> IdentityRecord:
    """Insert one client-role membership. Does not log or return the password."""
    now = datetime.now(tz=UTC)
    user_id = str(uuid4())
    try:
        conn.execute(
            """
            INSERT INTO app_user (
                id, subject, is_platform_admin, password_hash, token_version,
                created_at, updated_at
            )
            VALUES (%s, %s, false, %s, 1, %s, %s)
            """,
            (user_id, subject, password_hash, now, now),
        )
        conn.execute(
            """
            INSERT INTO client_membership (user_id, client_id, role, created_at, updated_at)
            VALUES (%s, %s, 'client', %s, %s)
            """,
            (user_id, client_id, now, now),
        )
    except UniqueViolation as exc:
        raise DuplicateSubjectError("username is already in use.") from exc
    identity = fetch_identity(conn, subject)
    if identity is None:
        raise RuntimeError("client user insert returned no identity.")
    return identity


def insert_publisher_password_user(
    conn: Connection[object],
    *,
    subject: str,
    password_hash: str,
    memberships: Sequence[MembershipRow],
) -> IdentityRecord:
    """Insert the one V1 publisher with inspector memberships. Hash only."""
    if inspector_exists(conn):
        raise PublisherAlreadyExistsError("A publisher account already exists.")
    if not memberships:
        raise NoCompaniesForSetupError("No companies exist.")
    now = datetime.now(tz=UTC)
    user_id = str(uuid4())
    try:
        conn.execute(
            """
            INSERT INTO app_user (
                id, subject, is_platform_admin, password_hash, token_version,
                created_at, updated_at
            )
            VALUES (%s, %s, false, %s, 1, %s, %s)
            """,
            (user_id, subject, password_hash, now, now),
        )
        for item in memberships:
            conn.execute(
                """
                INSERT INTO client_membership (
                    user_id, client_id, role, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, item.client_id, item.role, now, now),
            )
    except UniqueViolation as exc:
        raise DuplicateSubjectError("username is already in use.") from exc
    identity = fetch_identity(conn, subject)
    if identity is None:
        raise RuntimeError("publisher user insert returned no identity.")
    return identity


def _scope_identity_client_join(conn: Connection[object], identity: IdentityRecord) -> None:
    """Restrict FORCE RLS on ``client`` to this subject's memberships.

    The first lookup only needs SET ROLE for ``app_user``. The join onto
    ``client`` uses ``dfip_member_client``, so empty GUCs hide company
    names. Membership ids come from the already-loaded identity row, not
    from platform-admin recovery.
    """
    apply_rls_settings(
        conn,
        identity_lookup_rls(
            client_ids=tuple(item.client_id for item in identity.memberships),
            platform_admin=identity.is_platform_admin,
            role="admin" if identity.is_platform_admin else "client",
            user_id=identity.user_id,
            subject=identity.subject,
        ),
    )


def fetch_identity_from_pool(pool: ConnectionPool, subject: str) -> IdentityRecord | None:
    with identity_lookup_bind() as rls:
        with transaction(pool, rls=rls) as conn:
            identity = fetch_identity(conn, subject)
            if identity is None:
                return None
            _scope_identity_client_join(conn, identity)
            return fetch_identity(conn, subject) or identity


def fetch_credential_from_pool(pool: ConnectionPool, subject: str) -> CredentialRecord | None:
    with identity_lookup_bind() as rls:
        with transaction(pool, rls=rls) as conn:
            record = fetch_credential(conn, subject)
            if record is None:
                return None
            _scope_identity_client_join(conn, record.identity)
            return fetch_credential(conn, subject) or record


def increment_token_version_from_pool(pool: ConnectionPool, user_id: str) -> int | None:
    with identity_lookup_bind() as rls:
        with transaction(pool, rls=rls) as conn:
            return increment_token_version(conn, user_id)


def inspector_exists_from_pool(pool: ConnectionPool) -> bool:
    with identity_lookup_bind() as rls:
        with transaction(pool, rls=rls) as conn:
            return inspector_exists(conn)


def insert_client_password_user_from_pool(
    pool: ConnectionPool,
    *,
    subject: str,
    password_hash: str,
    client_id: str,
) -> IdentityRecord:
    ctx = require_inspector_rls()
    with transaction(pool, rls=ctx) as conn:
        authorized = expand_inspector_registry_clients(conn, (client_id,))
        if client_id not in authorized:
            raise PermissionError("Inspector membership is required to provision a client user.")
        return insert_client_password_user(
            conn, subject=subject, password_hash=password_hash, client_id=client_id
        )


def insert_publisher_password_user_from_pool(
    pool: ConnectionPool,
    *,
    subject: str,
    password_hash: str,
    memberships: Sequence[MembershipRow],
) -> IdentityRecord:
    client_ids = tuple(item.client_id for item in memberships)
    with identity_lookup_bind(role="publisher", client_ids=client_ids) as rls:
        with transaction(pool, rls=rls) as conn:
            return insert_publisher_password_user(
                conn, subject=subject, password_hash=password_hash, memberships=memberships
            )
