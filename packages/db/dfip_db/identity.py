"""app_user / client_membership lookup. Not a user-management product."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import as_uuid_text


@dataclass(frozen=True)
class MembershipRow:
    client_id: str
    role: str


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
            memberships.append(MembershipRow(as_uuid_text(row["client_id"]), row["role"]))
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
            m.role AS role
        FROM app_user AS u
        LEFT JOIN client_membership AS m ON m.user_id = u.id
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
            m.role AS role
        FROM app_user AS u
        LEFT JOIN client_membership AS m ON m.user_id = u.id
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


def fetch_identity_from_pool(pool: ConnectionPool, subject: str) -> IdentityRecord | None:
    with transaction(pool, rls=None) as conn:
        return fetch_identity(conn, subject)


def fetch_credential_from_pool(pool: ConnectionPool, subject: str) -> CredentialRecord | None:
    with transaction(pool, rls=None) as conn:
        return fetch_credential(conn, subject)


def increment_token_version_from_pool(pool: ConnectionPool, user_id: str) -> int | None:
    with transaction(pool, rls=None) as conn:
        return increment_token_version(conn, user_id)
