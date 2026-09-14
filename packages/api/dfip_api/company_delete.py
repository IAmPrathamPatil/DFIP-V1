"""Permanent company deletion after deactivation.

Uses the inspected purge DELETE order. PostgreSQL runs SECURITY DEFINER
``dfip_delete_company``. In-memory tests wipe tenant-scoped stores. No CASCADE.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from psycopg.errors import RaiseException, UndefinedFunction

from dfip_core.ingest.progress import ACTIVE_STAGES
from dfip_db.connection import transaction
from dfip_db.rls import expand_inspector_registry_clients, require_inspector_rls

from dfip_api.auth import Principal
from dfip_api.client_directory import inspector_directory_ids
from dfip_api.errors import AuthorizationError, ConflictError
from dfip_api.lifecycle import LIFECYCLE_INACTIVE, is_protected_company
from dfip_api.purge import collect_inventory, execute_sql_purge, tenant_archive_dir
from dfip_api.roles import ADMIN_ROLES
from dfip_api.schemas import ClientDeleteResponse
from dfip_api.source_storage import DEFAULT_BUCKET

MUST_DEACTIVATE = "Deactivate this company before deleting it."
IN_FLIGHT_MESSAGE = (
    "This company still has processing or publishing in progress. "
    "Wait for it to finish or cancel it, then delete."
)
PROTECTED_MESSAGE = "The packaged default company cannot be deleted."


def delete_company(request: Request, principal: Principal, client_id: str) -> ClientDeleteResponse:
    if principal.role not in ADMIN_ROLES:
        raise AuthorizationError("Not authorized to access this client.")
    directory = request.app.state.client_directory
    store = getattr(request.app.state, "identity_store", None)
    identity = store.get_by_subject(principal.subject) if store is not None else None
    allowed = inspector_directory_ids(principal, identity)
    if client_id not in allowed:
        raise AuthorizationError("Not authorized to access this client.")
    record = directory.get(client_id)
    if record is None:
        return ClientDeleteResponse(deleted=True, client_id=client_id, already_absent=True)
    if record.lifecycle_status != LIFECYCLE_INACTIVE:
        raise ConflictError(MUST_DEACTIVATE)
    if is_protected_company(record):
        raise AuthorizationError(PROTECTED_MESSAGE)
    if _has_in_flight(request, client_id):
        raise ConflictError(IN_FLIGHT_MESSAGE)

    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None:
        _delete_postgres(request, record.client_id)
    else:
        _delete_memory(request, client_id)

    _purge_store(getattr(request.app.state, "source_store", None), client_id)
    identity_store = getattr(request.app.state, "identity_store", None)
    drop = getattr(identity_store, "drop_company", None)
    if callable(drop):
        drop(client_id)
    delete_row = getattr(directory, "delete", None)
    if callable(delete_row):
        delete_row(client_id)
    return ClientDeleteResponse(deleted=True, client_id=client_id, already_absent=False)


def _has_in_flight(request: Request, client_id: str) -> bool:
    upload = getattr(request.app.state, "upload_service", None)
    if upload is not None and upload.has_in_flight_for_client(client_id):
        return True
    publication = getattr(request.app.state, "publication_service", None)
    if publication is not None and publication.has_in_flight_publish(client_id):
        return True
    ingest = getattr(request.app.state, "ingest_store", None)
    if ingest is None:
        return False
    runs = getattr(ingest, "processing_runs", None)
    if isinstance(runs, dict):
        if any(
            (run.client_id == client_id and run.status in {"pending", "running"})
            for run in runs.values()
        ):
            return True
    batches = getattr(ingest, "batches", None)
    if isinstance(batches, dict):
        if any(
            batch.client_id == client_id and (batch.progress_stage or "") in ACTIVE_STAGES
            for batch in batches.values()
        ):
            return True
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return False
    ctx = require_inspector_rls()
    with transaction(pool, rls=ctx) as conn:
        expand_inspector_registry_clients(conn, (client_id,))
        active_run = conn.execute(
            """
            SELECT 1 FROM processing_run
            WHERE client_id = %s AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (client_id,),
        ).fetchone()
        if active_run is not None:
            return True
        active_batch = conn.execute(
            """
            SELECT 1 FROM batch
            WHERE client_id = %s AND progress_stage = ANY(%s)
            LIMIT 1
            """,
            (client_id, list(ACTIVE_STAGES)),
        ).fetchone()
        return active_batch is not None


def _delete_postgres(request: Request, client_id: str) -> None:
    pool = request.app.state.db_pool
    settings = request.app.state.settings
    ctx = require_inspector_rls()
    try:
        with transaction(pool, rls=ctx) as conn:
            expand_inspector_registry_clients(conn, (client_id,))
            conn.execute("SELECT dfip_delete_company(%s)", (client_id,))
    except UndefinedFunction:
        with transaction(pool, rls=ctx) as conn:
            expand_inspector_registry_clients(conn, (client_id,))
            record = request.app.state.client_directory.get(client_id)
            archive = None
            endpoint = (settings.dfip_storage_endpoint or "").strip()
            if endpoint:
                archive = tenant_archive_dir(
                    Path(endpoint),
                    client_id,
                    settings.dfip_storage_bucket or DEFAULT_BUCKET,
                )
            inventory = collect_inventory(conn, record, archive)
            delete_ids = tuple(str(item["user_id"]) for item in inventory["users_deleted"])
            execute_sql_purge(conn, client_id, delete_ids)
    except RaiseException as exc:
        _raise_sql_conflict(exc)


def _raise_sql_conflict(exc: RaiseException) -> None:
    diag = getattr(exc, "diag", None)
    message = str(getattr(diag, "message_primary", None) or exc)
    if "COMPANY_ACTIVE" in message:
        raise ConflictError(MUST_DEACTIVATE) from exc
    if "COMPANY_IN_FLIGHT" in message:
        raise ConflictError(IN_FLIGHT_MESSAGE) from exc
    if "COMPANY_PROTECTED" in message:
        raise AuthorizationError(PROTECTED_MESSAGE) from exc
    raise ConflictError("Company delete failed.") from exc


def _delete_memory(request: Request, client_id: str) -> None:
    for name in (
        "ingest_store",
        "fact_store",
        "publication_store",
        "catalog_store",
        "qa_store",
        "excel_grant_store",
        "saved_analysis_store",
    ):
        _purge_store(getattr(request.app.state, name, None), client_id)


def _purge_store(store: object | None, client_id: str) -> None:
    if store is None:
        return
    method = getattr(store, "purge_client", None)
    if callable(method):
        method(client_id)
