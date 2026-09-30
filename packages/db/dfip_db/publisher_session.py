"""Postgres persistence for the single-active Publisher session lease.

One unrevoked row per ``app_user`` is guaranteed by the
``publisher_session_one_active`` partial unique index. Registration also takes
a transaction-scoped advisory lock derived from ``user_id`` so two concurrent
registrations for the same publisher are serialized instead of racing: the
second one observes the first row, revokes it, and inserts its own. The final
state is exactly one active session, deterministically the later writer.

Uses ``api_transaction`` so production LOGIN SET LOCAL ROLE dfip_api. The table
has no FORCE RLS (same pattern as app_user / excel_workbook_grant). The session
id is an opaque token; it contains no user information.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg_pool import ConnectionPool

from dfip_db.connection import api_transaction

_COLUMNS = """
    id, user_id, created_at, last_seen_at, expires_at, revoked_at, user_agent
"""


@dataclass(frozen=True)
class PublisherSession:
    session_id: str
    user_id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    user_agent: str | None

    def is_active_at(self, moment: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > moment


def advisory_lock_key(user_id: str) -> int:
    """Stable signed 64-bit lock key for one app_user.

    Derived from the uuid text, not from ``hash()``, so it is identical across
    processes and hosts. Collisions between two different users only serialize
    unrelated registrations; they cannot merge their rows.
    """
    try:
        raw = UUID(str(user_id)).bytes[:8]
    except ValueError as exc:
        raise ValueError("Publisher session user_id must be a uuid.") from exc
    return int.from_bytes(raw, "big", signed=True)


def _row(row: dict[str, object]) -> PublisherSession:
    revoked = row["revoked_at"]
    agent = row["user_agent"]
    return PublisherSession(
        session_id=str(row["id"]),
        user_id=str(row["user_id"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
        last_seen_at=row["last_seen_at"],  # type: ignore[arg-type]
        expires_at=row["expires_at"],  # type: ignore[arg-type]
        revoked_at=revoked if isinstance(revoked, datetime) else None,
        user_agent=str(agent) if agent else None,
    )


def register_session(
    pool: ConnectionPool,
    *,
    user_id: str,
    session_id: str,
    lease_seconds: int,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> PublisherSession:
    """Revoke any prior session for this user, then insert the new one.

    The advisory lock is transaction scoped, so it is released by the commit or
    rollback that ``api_transaction`` performs. Concurrent registrations for the
    same publisher therefore end with exactly one active session.
    """
    started = now if now is not None else datetime.now(tz=UTC)
    expires_at = started + timedelta(seconds=max(1, int(lease_seconds)))
    with api_transaction(pool) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (advisory_lock_key(user_id),))
        conn.execute(
            """
            UPDATE publisher_session
            SET revoked_at = %s
            WHERE user_id = %s AND revoked_at IS NULL
            """,
            (started, user_id),
        )
        row = conn.execute(
            f"""
            INSERT INTO publisher_session (
                id, user_id, created_at, last_seen_at, expires_at, user_agent
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (session_id, user_id, started, started, expires_at, user_agent),
        ).fetchone()
    assert row is not None
    return _row(row)


def fetch_session(pool: ConnectionPool, session_id: str) -> PublisherSession | None:
    with api_transaction(pool) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM publisher_session WHERE id = %s",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return _row(row)


def fetch_active_session(
    pool: ConnectionPool, session_id: str, *, now: datetime | None = None
) -> PublisherSession | None:
    moment = now if now is not None else datetime.now(tz=UTC)
    with api_transaction(pool) as conn:
        row = conn.execute(
            f"""
            SELECT {_COLUMNS}
            FROM publisher_session
            WHERE id = %s AND revoked_at IS NULL AND expires_at > %s
            """,
            (session_id, moment),
        ).fetchone()
    if row is None:
        return None
    return _row(row)


def renew_session(
    pool: ConnectionPool,
    *,
    session_id: str,
    user_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> PublisherSession | None:
    """Extend the lease. Returns None when the session is not the live one.

    The ``id``, ``user_id`` and ``revoked_at`` predicates are the whole check:
    a replaced or foreign session id never matches, so a heartbeat can never
    resurrect it and never extends someone else's lease.
    """
    moment = now if now is not None else datetime.now(tz=UTC)
    with api_transaction(pool) as conn:
        row = conn.execute(
            """
            UPDATE publisher_session
            SET last_seen_at = %s,
                expires_at = %s
            WHERE id = %s
              AND user_id = %s
              AND revoked_at IS NULL
              AND expires_at > %s
            RETURNING id, user_id, created_at, last_seen_at, expires_at,
                      revoked_at, user_agent
            """,
            (
                moment,
                moment + timedelta(seconds=max(1, int(lease_seconds))),
                session_id,
                user_id,
                moment,
            ),
        ).fetchone()
    if row is None:
        return None
    return _row(row)


def revoke_active_session_for_user(pool: ConnectionPool, user_id: str) -> int:
    """Revoke the caller's own active session. Always scoped by user_id.

    There is deliberately no revoke-by-session-id helper: revoking a session
    without an ownership predicate would let any authenticated caller cancel
    another publisher's session.
    """
    now = datetime.now(tz=UTC)
    with api_transaction(pool) as conn:
        result = conn.execute(
            """
            UPDATE publisher_session
            SET revoked_at = %s
            WHERE user_id = %s AND revoked_at IS NULL
            """,
            (now, user_id),
        )
    return int(result.rowcount or 0)
