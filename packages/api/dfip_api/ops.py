"""P13D cheap operator readiness probes.

Not imported by public /health. Does not hash archives or run backup verify.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dfip_config.settings import Settings
from dfip_db.connection import DatabaseUnavailableError, open_pool
from psycopg_pool import ConnectionPool

from dfip_api.schemas import (
    ReadyBackup,
    ReadyDatabase,
    ReadyResponse,
    ReadyStorage,
)
from dfip_api.source_storage import FilesystemSourceObjectStore, InMemorySourceObjectStore
from dfip_api.upload_service import UploadService

log = logging.getLogger(__name__)

WorkerState = Literal["idle", "busy"]


def probe_database(pool: ConnectionPool | None) -> Literal["ok", "unavailable"]:
    """Cheap SELECT 1. In-memory mode (no pool) is process-local and ok."""
    if pool is None:
        return "ok"
    try:
        open_pool(pool)
        with pool.connection(timeout=3) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except DatabaseUnavailableError:
        return "unavailable"
    except Exception as exc:
        log.error("readiness-database type=%s", type(exc).__name__)
        return "unavailable"


def probe_storage(source_store: object) -> Literal["ok", "unavailable", "not_configured"]:
    """Directory exists + is readable. No archive walk, no path in the result."""
    if isinstance(source_store, InMemorySourceObjectStore):
        return "not_configured"
    if not isinstance(source_store, FilesystemSourceObjectStore):
        return "not_configured"
    try:
        root = source_store.root
        if root.is_dir() and os.access(root, os.R_OK):
            return "ok"
        return "unavailable"
    except OSError:
        return "unavailable"


def probe_backup(settings: Settings) -> ReadyBackup:
    raw = settings.dfip_backup_last_dir.strip()
    if not raw:
        return ReadyBackup(status="not_configured")
    try:
        manifest = Path(raw) / "manifest.json"
        if not manifest.is_file():
            return ReadyBackup(status="configured")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        created = payload.get("created_at") if isinstance(payload, dict) else None
        if not isinstance(created, str) or not created:
            return ReadyBackup(status="configured")
        stamp = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        age = max(0, int((datetime.now(tz=UTC) - stamp).total_seconds()))
        return ReadyBackup(
            status="configured",
            manifest_created_at=created,
            manifest_age_seconds=age,
        )
    except Exception:
        return ReadyBackup(status="configured")


def build_readiness(
    *,
    pool: ConnectionPool | None,
    source_store: object,
    upload_service: UploadService,
    settings: Settings,
) -> ReadyResponse:
    database = probe_database(pool)
    storage = probe_storage(source_store)
    worker: WorkerState = "busy" if upload_service.is_busy() else "idle"
    ready = database == "ok" and storage != "unavailable"
    return ReadyResponse(
        status="ready" if ready else "not_ready",
        application="dfip-api",
        database=ReadyDatabase(status=database),
        storage=ReadyStorage(status=storage),
        worker=worker,
        backup=probe_backup(settings),
    )


def readiness_payload(report: ReadyResponse) -> dict[str, Any]:
    return report.model_dump(exclude_none=True)
