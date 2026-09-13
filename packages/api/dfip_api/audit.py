"""Best-effort application audit_log writes.

Never records passwords, JWTs, or DSNs. Failures are swallowed so audit
never blocks login, upload, or publish.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction

log = logging.getLogger(__name__)

_SAFE_AFTER_KEYS = frozenset(
    {
        "role",
        "auth_mode",
        "status",
        "batch_id",
        "processing_run_id",
        "publication_id",
        "original_filename",
        "replayed",
    }
)


def _safe_after(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {key: payload[key] for key in _SAFE_AFTER_KEYS if key in payload}


def write_audit_event(
    pool: ConnectionPool | None,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    client_id: str | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Insert one audit_log row. No-op without a pool. Never raises to callers."""
    if pool is None:
        return
    entity_uuid = None
    if entity_id:
        try:
            entity_uuid = str(UUID(str(entity_id)))
        except (ValueError, TypeError):
            entity_uuid = None
    client_uuid = None
    if client_id:
        try:
            client_uuid = str(UUID(str(client_id)))
        except (ValueError, TypeError):
            client_uuid = None
    try:
        with transaction(pool, rls=None) as conn:
            conn.execute(
                """
                INSERT INTO audit_log (
                    actor, action, entity_type, entity_id, client_id, before, after
                )
                VALUES (%s, %s, %s, %s, %s, NULL, %s)
                """,
                (
                    (actor or "")[:200],
                    action[:100],
                    entity_type[:100],
                    entity_uuid,
                    client_uuid,
                    Json(_safe_after(after)) if _safe_after(after) else None,
                ),
            )
    except Exception:
        log.warning("audit_log write skipped action=%s entity_type=%s", action, entity_type)
