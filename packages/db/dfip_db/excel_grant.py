"""Postgres persistence for Excel workbook grants.

Uses ``api_transaction`` so production LOGIN SET LOCAL ROLE dfip_api.
The table has no FORCE RLS. Application authorization still checks JWT
client_id and membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from psycopg_pool import ConnectionPool

from dfip_db.connection import api_transaction
from dfip_db.mapping import as_uuid_text


@dataclass(frozen=True)
class ExcelWorkbookGrant:
    grant_id: str
    user_id: str
    client_id: str
    jti: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


def _row(row: dict[str, object]) -> ExcelWorkbookGrant:
    revoked = row["revoked_at"]
    used = row["last_used_at"]
    return ExcelWorkbookGrant(
        grant_id=as_uuid_text(row["id"]),
        user_id=as_uuid_text(row["user_id"]),
        client_id=as_uuid_text(row["client_id"]),
        jti=as_uuid_text(row["jti"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
        expires_at=row["expires_at"],  # type: ignore[arg-type]
        revoked_at=revoked if isinstance(revoked, datetime) else None,
        last_used_at=used if isinstance(used, datetime) else None,
    )


def insert_grant(
    pool: ConnectionPool,
    *,
    user_id: str,
    client_id: str,
    jti: str,
    expires_at: datetime,
) -> ExcelWorkbookGrant:
    now = datetime.now(tz=UTC)
    grant_id = str(uuid4())
    with api_transaction(pool) as conn:
        row = conn.execute(
            """
            INSERT INTO excel_workbook_grant (
                id, user_id, client_id, jti, created_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, user_id, client_id, jti, created_at, expires_at,
                      revoked_at, last_used_at
            """,
            (grant_id, user_id, client_id, jti, now, expires_at),
        ).fetchone()
    assert row is not None
    return _row(row)


def fetch_active_grant(pool: ConnectionPool, jti: str) -> ExcelWorkbookGrant | None:
    now = datetime.now(tz=UTC)
    with api_transaction(pool) as conn:
        row = conn.execute(
            """
            SELECT id, user_id, client_id, jti, created_at, expires_at,
                   revoked_at, last_used_at
            FROM excel_workbook_grant
            WHERE jti = %s
              AND revoked_at IS NULL
              AND expires_at > %s
            """,
            (jti, now),
        ).fetchone()
    if row is None:
        return None
    return _row(row)


def touch_grant(pool: ConnectionPool, jti: str) -> None:
    now = datetime.now(tz=UTC)
    with api_transaction(pool) as conn:
        conn.execute(
            """
            UPDATE excel_workbook_grant
            SET last_used_at = %s
            WHERE jti = %s AND revoked_at IS NULL
            """,
            (now, jti),
        )


def revoke_grants_for_user(pool: ConnectionPool, user_id: str) -> int:
    now = datetime.now(tz=UTC)
    with api_transaction(pool) as conn:
        result = conn.execute(
            """
            UPDATE excel_workbook_grant
            SET revoked_at = %s
            WHERE user_id = %s AND revoked_at IS NULL
            """,
            (now, user_id),
        )
    return int(result.rowcount or 0)
