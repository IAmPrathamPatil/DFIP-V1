"""Postgres persistence for D9 saved analytical state.

Stores configuration JSON, not published fact rows. Application authorization
still binds owner_subject + JWT client_id. Uses ``transaction(..., rls=None)``
like excel grants: this is identity metadata, not published-fact RLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import as_uuid_text


@dataclass(frozen=True)
class SavedAnalysisRecord:
    analysis_id: str
    owner_subject: str
    user_id: str | None
    client_id: str
    title: str
    state: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def _row(row: dict[str, object]) -> SavedAnalysisRecord:
    user_id = row.get("user_id")
    state = row["state"]
    if not isinstance(state, dict):
        state = {}
    return SavedAnalysisRecord(
        analysis_id=as_uuid_text(row["id"]),
        owner_subject=str(row["owner_subject"]),
        user_id=as_uuid_text(user_id) if user_id is not None else None,
        client_id=as_uuid_text(row["client_id"]),
        title=str(row["title"]),
        state=state,
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )


_SELECT_COLS = "id, owner_subject, user_id, client_id, title, state, created_at, updated_at"
_SELECT = f"SELECT {_SELECT_COLS} FROM analytics_saved_analysis"


def insert_saved_analysis(
    pool: ConnectionPool,
    *,
    owner_subject: str,
    user_id: str | None,
    client_id: str,
    title: str,
    state: dict[str, Any],
) -> SavedAnalysisRecord:
    now = datetime.now(tz=UTC)
    analysis_id = str(uuid4())
    with transaction(pool, rls=None) as conn:
        row = conn.execute(
            f"""
            INSERT INTO analytics_saved_analysis (
                id, owner_subject, user_id, client_id, title, state, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_SELECT_COLS}
            """,
            (analysis_id, owner_subject, user_id, client_id, title, Json(state), now, now),
        ).fetchone()
    assert row is not None
    return _row(row)


def list_saved_analyses(
    pool: ConnectionPool,
    *,
    owner_subject: str,
    client_id: str,
) -> tuple[SavedAnalysisRecord, ...]:
    with transaction(pool, rls=None) as conn:
        rows = conn.execute(
            _SELECT
            + """
            WHERE owner_subject = %s AND client_id = %s
            ORDER BY updated_at DESC, id DESC
            """,
            (owner_subject, client_id),
        ).fetchall()
    return tuple(_row(row) for row in rows)


def count_saved_analyses(
    pool: ConnectionPool,
    *,
    owner_subject: str,
    client_id: str,
) -> int:
    with transaction(pool, rls=None) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM analytics_saved_analysis
            WHERE owner_subject = %s AND client_id = %s
            """,
            (owner_subject, client_id),
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def fetch_saved_analysis(
    pool: ConnectionPool,
    *,
    analysis_id: str,
    owner_subject: str,
    client_id: str,
) -> SavedAnalysisRecord | None:
    with transaction(pool, rls=None) as conn:
        row = conn.execute(
            _SELECT + " WHERE id = %s AND owner_subject = %s AND client_id = %s",
            (analysis_id, owner_subject, client_id),
        ).fetchone()
    if row is None:
        return None
    return _row(row)


def update_saved_analysis(
    pool: ConnectionPool,
    *,
    analysis_id: str,
    owner_subject: str,
    client_id: str,
    title: str | None,
    state: dict[str, Any] | None,
) -> SavedAnalysisRecord | None:
    now = datetime.now(tz=UTC)
    with transaction(pool, rls=None) as conn:
        current = conn.execute(
            _SELECT + " WHERE id = %s AND owner_subject = %s AND client_id = %s",
            (analysis_id, owner_subject, client_id),
        ).fetchone()
        if current is None:
            return None
        next_title = title if title is not None else str(current["title"])
        next_state = state if state is not None else current["state"]
        row = conn.execute(
            f"""
            UPDATE analytics_saved_analysis
            SET title = %s, state = %s, updated_at = %s
            WHERE id = %s AND owner_subject = %s AND client_id = %s
            RETURNING {_SELECT_COLS}
            """,
            (next_title, Json(next_state), now, analysis_id, owner_subject, client_id),
        ).fetchone()
    if row is None:
        return None
    return _row(row)


def delete_saved_analysis(
    pool: ConnectionPool,
    *,
    analysis_id: str,
    owner_subject: str,
    client_id: str,
) -> bool:
    with transaction(pool, rls=None) as conn:
        row = conn.execute(
            """
            DELETE FROM analytics_saved_analysis
            WHERE id = %s AND owner_subject = %s AND client_id = %s
            RETURNING id
            """,
            (analysis_id, owner_subject, client_id),
        ).fetchone()
    return row is not None


def purge_saved_analyses_for_client(pool: ConnectionPool, client_id: str) -> None:
    with transaction(pool, rls=None) as conn:
        conn.execute("DELETE FROM analytics_saved_analysis WHERE client_id = %s", (client_id,))
