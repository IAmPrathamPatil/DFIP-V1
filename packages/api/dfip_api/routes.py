"""Authenticated `/api/v1` resource routes.

P5 working-set reads are GET. Batch reprocess is POST
``/batches/{batch_id}/process``. P7 publication writes live in
``publication_routes``. Business rules stay in P2/P3/P4; this module only
validates HTTP input and returns stored state. GET /facts is not
publication-filtered. P9 gates working-set and staging inspection to
admin/publisher. JWT or bound development ``client_id`` scopes inspector
reads the same way publication routes do. That is application-level
authorization, not PostgreSQL RLS and not production tenant isolation.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from dfip_api.auth import Principal
from dfip_api.deps import (
    InspectorDep,
    PaginationDep,
    PrincipalDep,
    QaStoreDep,
    ServiceDep,
    UploadServiceDep,
    get_principal,
    require_inspector,
)
from dfip_api.publication_service import inspector_read_scope
from dfip_api.schemas import (
    BatchPage,
    BatchResponse,
    ErrorResponse,
    FactHistoryPage,
    FactPage,
    ProcessingRunPage,
    ProcessingRunResponse,
    QaFindingPage,
    ReprocessResponse,
    SessionResponse,
    SourceFilePage,
    SourceFileResponse,
    StagedRowPage,
)
from dfip_api.service import processing_run_to_response

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    422: {"model": ErrorResponse, "description": "Validation or pagination error."},
    503: {"model": ErrorResponse, "description": "Persistence unavailable."},
}

router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)
inspector_router = APIRouter(dependencies=[Depends(require_inspector)])
OptionalUuid = Annotated[UUID | None, Query()]
OptionalStatus = Annotated[str | None, Query(min_length=1, max_length=64)]
OptionalText = Annotated[str | None, Query()]
OptionalDay = Annotated[date | None, Query()]
OptionalRowNumber = Annotated[int | None, Query(ge=1)]


def _inspector_scope(
    principal: Principal, requested: UUID | None = None
) -> tuple[str | None, tuple[str, ...] | None]:
    return inspector_read_scope(principal, str(requested) if requested else None)


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Current authenticated identity",
    description=(
        "Returns the authenticated principal. Role and client_id are the "
        "foundation for later Admin/Client authorization. This is not a login "
        "endpoint and does not issue tokens."
    ),
    tags=["Session"],
)
def get_session(principal: PrincipalDep) -> SessionResponse:
    return SessionResponse(
        subject=principal.subject,
        auth_mode=principal.auth_mode,
        role=principal.role,
        client_id=principal.client_id,
    )


@inspector_router.get(
    "/source-files",
    response_model=SourceFilePage,
    summary="List source files",
    description=(
        "Paginated source_file metadata. Filesystem paths and storage_uri are never returned."
    ),
    tags=["Source files"],
)
def list_source_files(
    service: ServiceDep,
    principal: InspectorDep,
    pagination: PaginationDep,
    client_id: OptionalUuid = None,
) -> SourceFilePage:
    scoped_client_id, client_ids = _inspector_scope(principal, client_id)
    return service.list_source_files(
        client_id=scoped_client_id,
        client_ids=client_ids,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@inspector_router.get(
    "/source-files/{source_file_id}",
    response_model=SourceFileResponse,
    summary="Get one source file",
    tags=["Source files"],
)
def get_source_file(
    source_file_id: UUID,
    service: ServiceDep,
    principal: InspectorDep,
) -> SourceFileResponse:
    _ignored, client_ids = _inspector_scope(principal)
    return service.get_source_file(str(source_file_id), client_ids=client_ids)


@inspector_router.get(
    "/batches",
    response_model=BatchPage,
    summary="List batches",
    description="Paginated batch records with worksheet/region metadata and counts.",
    tags=["Batches"],
)
def list_batches(
    service: ServiceDep,
    principal: InspectorDep,
    pagination: PaginationDep,
    source_file_id: OptionalUuid = None,
    client_id: OptionalUuid = None,
    status: OptionalStatus = None,
) -> BatchPage:
    scoped_client_id, client_ids = _inspector_scope(principal, client_id)
    return service.list_batches(
        source_file_id=str(source_file_id) if source_file_id else None,
        client_id=scoped_client_id,
        client_ids=client_ids,
        status=status,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@inspector_router.get(
    "/batches/{batch_id}",
    response_model=BatchResponse,
    summary="Get one batch",
    tags=["Batches"],
)
def get_batch(
    batch_id: UUID,
    service: ServiceDep,
    principal: InspectorDep,
) -> BatchResponse:
    _ignored, client_ids = _inspector_scope(principal)
    return service.get_batch(str(batch_id), client_ids=client_ids)


@inspector_router.post(
    "/batches/{batch_id}/process",
    response_model=ReprocessResponse,
    status_code=202,
    summary="Re-process staged rows of an existing batch",
    description=(
        "Creates a new processing_run for an already-staged batch and transforms "
        "those rows with the currently active Logic and Labels versions. The "
        "previous run is kept. Does not re-read the workbook and does not publish. "
        "JWT client_id always wins. Admin/publisher only."
    ),
    tags=["Batches"],
)
def reprocess_batch(
    batch_id: UUID,
    principal: InspectorDep,
    uploads: UploadServiceDep,
    response: Response,
    client_id: OptionalUuid = None,
) -> ReprocessResponse:
    body, status = uploads.reprocess(
        principal=principal,
        batch_id=str(batch_id),
        requested_client_id=str(client_id) if client_id else None,
    )
    response.status_code = status
    return body


@inspector_router.get(
    "/batches/{batch_id}/staged-rows",
    response_model=StagedRowPage,
    summary="List staged source rows for a batch",
    description=(
        "Paginated stg_source_row records. Filters are exact-match on "
        "source_row_number, campaign_id, variation_id, and day. campaign_id "
        "is TEXT and is not UUID-validated."
    ),
    tags=["Staged rows"],
)
def list_staged_rows(
    batch_id: UUID,
    service: ServiceDep,
    principal: InspectorDep,
    pagination: PaginationDep,
    source_row_number: OptionalRowNumber = None,
    campaign_id: OptionalText = None,
    variation_id: OptionalText = None,
    day: OptionalDay = None,
) -> StagedRowPage:
    _ignored, client_ids = _inspector_scope(principal)
    return service.list_staged_rows(
        batch_id=str(batch_id),
        source_row_number=source_row_number,
        campaign_id=campaign_id,
        variation_id=variation_id,
        day=day,
        client_ids=client_ids,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@inspector_router.get(
    "/processing-runs",
    response_model=ProcessingRunPage,
    summary="List processing runs",
    description="Paginated processing_run records with configuration version bindings.",
    tags=["Processing runs"],
)
def list_processing_runs(
    service: ServiceDep,
    principal: InspectorDep,
    pagination: PaginationDep,
    batch_id: OptionalUuid = None,
    status: OptionalStatus = None,
) -> ProcessingRunPage:
    _ignored, client_ids = _inspector_scope(principal)
    return service.list_processing_runs(
        batch_id=str(batch_id) if batch_id else None,
        client_ids=client_ids,
        status=status,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@inspector_router.get(
    "/processing-runs/{processing_run_id}",
    response_model=ProcessingRunResponse,
    summary="Get one processing run",
    tags=["Processing runs"],
)
def get_processing_run(
    processing_run_id: UUID,
    service: ServiceDep,
    principal: InspectorDep,
) -> ProcessingRunResponse:
    _ignored, client_ids = _inspector_scope(principal)
    return service.get_processing_run(str(processing_run_id), client_ids=client_ids)


@inspector_router.get(
    "/processing-runs/{processing_run_id}/qa-findings",
    response_model=QaFindingPage,
    summary="List inspector QA findings for a processing run",
    description=(
        "Paginated qa_finding rows for one processing run. Inspector-only. "
        "JWT or bound development client_id is authoritative. Findings do not "
        "publish a run and are not a Power BI source."
    ),
    tags=["Processing runs"],
)
def list_processing_run_qa_findings(
    processing_run_id: UUID,
    service: ServiceDep,
    qa_store: QaStoreDep,
    principal: InspectorDep,
    pagination: PaginationDep,
) -> QaFindingPage:
    _ignored, client_ids = _inspector_scope(principal)
    return service.list_qa_findings(
        processing_run_id=str(processing_run_id),
        qa_store=qa_store,
        client_ids=client_ids,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@inspector_router.post(
    "/processing-runs/{processing_run_id}/qa",
    response_model=ProcessingRunResponse,
    summary="Re-evaluate inspector QA for a processing run",
    description=(
        "Runs evaluate_qa over the complete run-scoped working set and replaces "
        "qa_finding plus processing_run.qa_verdict. Does not reprocess facts and "
        "does not publish. Admin/publisher only."
    ),
    tags=["Processing runs"],
)
def evaluate_processing_run_qa(
    processing_run_id: UUID,
    uploads: UploadServiceDep,
    principal: InspectorDep,
    client_id: OptionalUuid = None,
) -> ProcessingRunResponse:
    run = uploads.evaluate_run_qa(
        principal=principal,
        processing_run_id=str(processing_run_id),
        requested_client_id=str(client_id) if client_id else None,
    )
    return processing_run_to_response(run)


@inspector_router.get(
    "/facts",
    response_model=FactPage,
    summary="List campaign-day facts",
    description=(
        "Paginated fact_campaign_day rows. Grain is "
        "(client_id, campaign_id, variation_id_key, day). Decimal fields are "
        "JSON strings. Filters are exact-match; campaign_id and variation_id "
        "are TEXT. Admin/publisher only. This is the working set and is not "
        "publication-filtered. JWT or bound development client_id is authoritative."
    ),
    tags=["Facts"],
)
def list_facts(
    service: ServiceDep,
    principal: InspectorDep,
    pagination: PaginationDep,
    client_id: OptionalUuid = None,
    batch_id: OptionalUuid = None,
    processing_run_id: OptionalUuid = None,
    campaign_id: OptionalText = None,
    variation_id: OptionalText = None,
    day: OptionalDay = None,
) -> FactPage:
    scoped_client_id, client_ids = _inspector_scope(principal, client_id)
    return service.list_facts(
        client_id=scoped_client_id,
        client_ids=client_ids,
        batch_id=str(batch_id) if batch_id else None,
        processing_run_id=str(processing_run_id) if processing_run_id else None,
        campaign_id=campaign_id,
        variation_id=variation_id,
        day=day,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@inspector_router.get(
    "/facts/history",
    response_model=FactHistoryPage,
    summary="List superseded fact history",
    description=(
        "Paginated fact_campaign_day_history rows from the existing P4 "
        "supersede model. No new history schema is introduced."
    ),
    tags=["Facts"],
)
def list_fact_history(
    service: ServiceDep,
    principal: InspectorDep,
    pagination: PaginationDep,
    client_id: OptionalUuid = None,
    batch_id: OptionalUuid = None,
    processing_run_id: OptionalUuid = None,
    campaign_id: OptionalText = None,
    variation_id: OptionalText = None,
    day: OptionalDay = None,
) -> FactHistoryPage:
    scoped_client_id, client_ids = _inspector_scope(principal, client_id)
    return service.list_fact_history(
        client_id=scoped_client_id,
        client_ids=client_ids,
        batch_id=str(batch_id) if batch_id else None,
        processing_run_id=str(processing_run_id) if processing_run_id else None,
        campaign_id=campaign_id,
        variation_id=variation_id,
        day=day,
        limit=pagination.limit,
        offset=pagination.offset,
    )


router.include_router(inspector_router)
