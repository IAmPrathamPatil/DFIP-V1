"""P7 publication HTTP routes. Additive; GET /facts is unchanged."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from dfip_api.deps import (
    PaginationDep,
    PrincipalDep,
    PublicationServiceDep,
    SettingsDep,
    get_principal,
)
from dfip_api.schemas import (
    ErrorResponse,
    FactPage,
    PublicationCreateRequest,
    PublicationCurrentResponse,
    PublicationPage,
    PublicationStateResponse,
)

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    422: {"model": ErrorResponse, "description": "Validation or pagination error."},
    503: {"model": ErrorResponse, "description": "Persistence unavailable."},
}

publication_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)
OptionalUuid = Annotated[UUID | None, Query()]


@publication_router.post(
    "/publications",
    response_model=PublicationStateResponse,
    status_code=201,
    summary="Publish a succeeded processing run",
    description=(
        "Creates a publication row, writes the immutable publication_fact "
        "snapshot, and replaces publication_current for the client. "
        "Admin/publisher only. This is not RLS."
    ),
    tags=["Publications"],
)
def create_publication(
    body: PublicationCreateRequest,
    principal: PrincipalDep,
    service: PublicationServiceDep,
) -> PublicationStateResponse:
    return service.create(
        principal=principal,
        client_id=str(body.client_id),
        processing_run_id=str(body.processing_run_id),
        period_start=body.period_start,
        period_end=body.period_end,
        notes=body.notes,
        fact_scope=body.fact_scope,
    )


@publication_router.get(
    "/publications",
    response_model=PublicationPage,
    summary="List publication history for the scoped client",
    description=(
        "Returns publication rows for the JWT client (newest first). "
        "Republish keeps prior rows. This is not GET /facts/history. "
        "JWT client_id always wins."
    ),
    tags=["Publications"],
)
def list_publications(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    pagination: PaginationDep,
    client_id: OptionalUuid = None,
) -> PublicationPage:
    return service.list_for_client(
        principal=principal,
        requested_client_id=str(client_id) if client_id else None,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@publication_router.get(
    "/publications/current/facts",
    response_model=FactPage,
    summary="List facts for the current publication",
    description=(
        "Paginated FactResponse rows for publication_current. Empty current "
        "publication returns items=[] and total=0. JWT client_id scopes the "
        "result. A development token with client_id=null may pass client_id "
        "as a query parameter; that is not production tenant isolation. "
        "Max page size remains 200."
    ),
    tags=["Publications"],
)
def list_published_facts(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    pagination: PaginationDep,
    client_id: OptionalUuid = None,
) -> FactPage:
    return service.list_published_facts(
        principal=principal,
        requested_client_id=str(client_id) if client_id else None,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@publication_router.get(
    "/publications/{publication_id}/facts",
    response_model=FactPage,
    summary="List facts for a historical publication",
    description=(
        "Paginated FactResponse rows for one publication_id. Complete snapshots "
        "are immutable. Legacy publications without a snapshot still join live "
        "facts and are not claimed to be immutable. JWT client_id scopes the "
        "result. Max page size remains 200."
    ),
    tags=["Publications"],
)
def list_publication_facts(
    publication_id: UUID,
    service: PublicationServiceDep,
    principal: PrincipalDep,
    pagination: PaginationDep,
    client_id: OptionalUuid = None,
) -> FactPage:
    return service.list_publication_facts(
        principal=principal,
        publication_id=str(publication_id),
        requested_client_id=str(client_id) if client_id else None,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@publication_router.get(
    "/publications/current",
    response_model=PublicationCurrentResponse,
    summary="Current publication pointer",
    description=(
        "Returns publication_current for the appropriate client. When nothing "
        "is published, publication and current are null."
    ),
    tags=["Publications"],
)
def get_current_publication(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
) -> PublicationCurrentResponse:
    return service.get_current(
        principal=principal,
        requested_client_id=str(client_id) if client_id else None,
    )


@publication_router.get(
    "/publications/current/facts.csv",
    summary="Download current published facts as CSV",
    description=(
        "Returns the publication_current slice as a CSV attachment. Same "
        "tenant isolation as GET /publications/current/facts. Empty current "
        "publication returns header-only CSV. JWT client_id scopes the file. "
        "A development token with client_id=null must pass client_id."
    ),
    tags=["Publications"],
    response_class=Response,
)
def download_published_csv(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    client_id: OptionalUuid = None,
) -> Response:
    return _published_download(service, principal, settings, client_id, "csv")


@publication_router.get(
    "/publications/current/facts.xlsx",
    summary="Download current published facts as XLSX",
    description=(
        "Returns the publication_current slice as an XLSX attachment. Same "
        "tenant isolation as GET /publications/current/facts. Empty current "
        "publication returns a header-only workbook."
    ),
    tags=["Publications"],
    response_class=Response,
)
def download_published_xlsx(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    client_id: OptionalUuid = None,
) -> Response:
    return _published_download(service, principal, settings, client_id, "xlsx")


@publication_router.get(
    "/publications/current/client-report.xlsx",
    summary="Download the current nine-sheet client report",
    description=(
        "Returns the native Client_Report workbook for publication_current. "
        "Same tenant isolation as GET /publications/current/facts. Empty "
        "current publication returns the nine-sheet workbook with no fact "
        "rows. Does not process or publish."
    ),
    tags=["Publications"],
    response_class=Response,
)
def download_current_client_report(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    client_id: OptionalUuid = None,
) -> Response:
    return _client_report_download(service, principal, settings, client_id, None)


@publication_router.get(
    "/publications/{publication_id}/client-report.xlsx",
    summary="Download a publication's nine-sheet client report",
    description=(
        "Returns the native Client_Report workbook bound to that "
        "publication_id. Complete snapshots stay immutable. Same tenant "
        "isolation as GET /publications/{publication_id}/facts. Does not "
        "process or publish."
    ),
    tags=["Publications"],
    response_class=Response,
)
def download_publication_client_report(
    publication_id: UUID,
    service: PublicationServiceDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    client_id: OptionalUuid = None,
) -> Response:
    return _client_report_download(service, principal, settings, client_id, str(publication_id))


def _published_download(
    service,
    principal,
    settings,
    client_id: UUID | None,
    kind: str,
) -> Response:
    body, filename, media_type = service.download_published(
        principal=principal,
        requested_client_id=str(client_id) if client_id else None,
        kind=kind,
        max_rows=settings.dfip_download_max_rows,
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _client_report_download(
    service,
    principal,
    settings,
    client_id: UUID | None,
    publication_id: str | None,
) -> Response:
    body, filename, media_type = service.download_client_report(
        principal=principal,
        publication_id=publication_id,
        requested_client_id=str(client_id) if client_id else None,
        max_rows=settings.dfip_download_max_rows,
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
