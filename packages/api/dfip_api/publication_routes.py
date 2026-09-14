"""P7 publication HTTP routes. Additive; GET /facts is unchanged."""

from __future__ import annotations

import threading
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from dfip_api.audit import write_audit_event
from dfip_api.deps import (
    HistoryFactsPaginationDep,
    PaginationDep,
    PrincipalDep,
    PublicationServiceDep,
    SettingsDep,
    get_principal,
)
from dfip_api.errors import AuthorizationError, TooManyRequests, ValidationFailed
from dfip_api.excel_grant import issue_excel_workbook_token
from dfip_api.publication_service import _resolve_client_id
from dfip_api.schemas import (
    ErrorResponse,
    FactPage,
    FactTablePage,
    PublicationCreateRequest,
    PublicationCurrentResponse,
    PublicationPage,
    PublicationProgressResponse,
    PublicationStateResponse,
)

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    422: {"model": ErrorResponse, "description": "Validation or pagination error."},
    429: {"model": ErrorResponse, "description": "Too many requests."},
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
    request: Request,
    principal: PrincipalDep,
    service: PublicationServiceDep,
) -> PublicationStateResponse:
    created = service.create(
        principal=principal,
        client_id=str(body.client_id),
        processing_run_id=str(body.processing_run_id),
        period_start=body.period_start,
        period_end=body.period_end,
        notes=body.notes,
        fact_scope=body.fact_scope,
    )
    publication = created.publication
    write_audit_event(
        getattr(request.app.state, "db_pool", None),
        actor=principal.subject,
        action="publication.create",
        entity_type="publication",
        entity_id=getattr(publication, "publication_id", None),
        client_id=str(body.client_id),
        after={"status": "created", "processing_run_id": str(body.processing_run_id)},
    )
    return created


@publication_router.get(
    "/publications/progress",
    response_model=PublicationProgressResponse,
    summary="Live publication progress for one processing run",
    description=(
        "Truthful stage text for an in-flight or recently finished publish. "
        "Percentages are returned only when they are honestly measurable. "
        "JWT client_id scopes the result."
    ),
    tags=["Publications"],
)
def get_publication_progress(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    processing_run_id: UUID,
    client_id: OptionalUuid = None,
) -> PublicationProgressResponse:
    item = service.progress_for(
        principal=principal,
        processing_run_id=str(processing_run_id),
        requested_client_id=str(client_id) if client_id else None,
    )
    return PublicationProgressResponse(
        processing_run_id=item.processing_run_id,
        client_id=item.client_id,
        stage=item.stage,
        status=item.status,
        message=item.message,
        current_count=item.current_count,
        total_count=item.total_count,
        progress_percent=item.progress_percent,
        publication_id=item.publication_id,
        error_summary=item.error_summary,
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
    "/publications/history/facts",
    response_model=FactPage | FactTablePage,
    summary="List cumulative published facts for the scoped client",
    description=(
        "Paginated FactResponse union of complete publication snapshots for "
        "the JWT client. Newest publication wins per campaign/variation/day "
        "grain so republished months are not duplicated. Unpublished "
        "working-set facts are omitted. Empty history returns items=[] and "
        "total=0. JWT client_id scopes the result. Excel Refresh All uses "
        "this route with page size 250000 (HISTORY_FACTS_MAX_PAGE_LIMIT) and "
        "layout=table so JSON keys are not repeated per row. Other JSON list "
        "routes remain capped at 200 and keep the object-item contract."
    ),
    tags=["Publications"],
)
def list_published_history_facts(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    pagination: HistoryFactsPaginationDep,
    client_id: OptionalUuid = None,
    layout: Annotated[Literal["objects", "table"], Query()] = "objects",
) -> FactPage | FactTablePage:
    return service.list_published_history_facts(
        principal=principal,
        requested_client_id=str(client_id) if client_id else None,
        limit=pagination.limit,
        offset=pagination.offset,
        layout=layout,
    )


@publication_router.get(
    "/publications/history/facts.csv",
    summary="Download cumulative published facts as CSV",
    description=(
        "Returns the newest-wins published history as a UTF-8 CSV attachment. "
        "Same tenant isolation as GET /publications/history/facts. Header row is "
        "FACT_VALUE_FIELDS / FACT_TABLE_COLUMNS so Excel HeaderMap still matches. "
        "Empty history returns a header-only file. JWT client_id scopes the file. "
        "A development token with client_id=null may pass client_id. Row cap is "
        "HISTORY_FACTS_MAX_PAGE_LIMIT, not the current-facts download cap."
    ),
    tags=["Publications"],
    response_class=Response,
)
def download_published_history_csv(
    service: PublicationServiceDep,
    principal: PrincipalDep,
    client_id: OptionalUuid = None,
) -> Response:
    body = service.download_published_history_csv(
        principal=principal,
        requested_client_id=str(client_id) if client_id else None,
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="published-history-facts.csv"'},
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
        "Same tenant isolation as GET /publications/current/facts. "
        "Filename is DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx. "
        "This is the static recovery snapshot. No current publication returns "
        "404. Does not process or publish."
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
    return _client_report_download(service, principal, settings, client_id, None, artifact="static")


@publication_router.get(
    "/publications/current/refreshable-client-report.xlsx",
    summary="Download the current refreshable nine-sheet client report",
    description=(
        "Returns a refreshable Client_Report for the company. "
        "Refresh All calls GET /publications/history/facts.csv using the "
        "short-lived Excel access JWT written into Settings BearerToken. "
        "JWT downloads register a revocable client-scoped workbook grant so "
        "POST /auth/refresh can mint a new short-lived access JWT after "
        "access exp. Publisher/admin downloads mint a grant for the selected "
        "company with role=client; the publisher session JWT is never "
        "embedded. Not a password, not a URL token, not an "
        "anonymous history endpoint. Filename is "
        "DFIP_<client_code>_<YYYY-MM-DD>_Client_Report_Refreshable.xlsm. "
        "JWT client_id remains authoritative. Settings ClientId stays empty. "
        "No current publication returns 404."
    ),
    tags=["Publications"],
    response_class=Response,
)
def download_current_refreshable_client_report(
    request: Request,
    service: PublicationServiceDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    client_id: OptionalUuid = None,
) -> Response:
    return _client_report_download(
        service,
        principal,
        settings,
        client_id,
        None,
        artifact="refreshable",
        request=request,
    )


@publication_router.get(
    "/publications/{publication_id}/client-report.xlsx",
    summary="Download a publication's nine-sheet client report",
    description=(
        "Returns the native Client_Report workbook bound to that "
        "publication_id. Complete snapshots stay immutable. Filename is "
        "DFIP_<client_code>_<YYYY-MM-DD>_<publication_short_id>_Client_Report.xlsx. "
        "Same tenant isolation as GET /publications/{publication_id}/facts. "
        "Does not process or publish. Not a refreshable workbook."
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
    def build() -> Response:
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

    return _with_report_generation_limit(build)


def _refreshable_excel_stamp(
    request: Request,
    principal,
    settings,
    requested_client_id: str | None,
) -> str | None:
    """Stamp a client-scoped Excel grant JWT. Never a session JWT or dev token."""
    if getattr(principal, "auth_mode", "") != "jwt":
        return None
    scoped = _resolve_client_id(principal, requested_client_id)
    if scoped is None:
        raise ValidationFailed("client_id is required.")
    grants = getattr(request.app.state, "excel_grant_store", None)
    issued = issue_excel_workbook_token(
        settings, principal, grants, client_id=scoped
    )
    if not issued:
        raise AuthorizationError()
    return issued


def _client_report_download(
    service,
    principal,
    settings,
    client_id: UUID | None,
    publication_id: str | None,
    *,
    artifact: str = "static",
    request: Request | None = None,
    refresh_bearer_token: str | None = None,
) -> Response:
    def build() -> Response:
        token = refresh_bearer_token
        if artifact == "refreshable" and token is None and request is not None:
            token = _refreshable_excel_stamp(
                request,
                principal,
                settings,
                str(client_id) if client_id else None,
            )
        body, filename, media_type = service.download_client_report(
            principal=principal,
            publication_id=publication_id,
            requested_client_id=str(client_id) if client_id else None,
            max_rows=settings.dfip_download_max_rows,
            artifact=artifact,
            api_base_url=settings.dfip_api_base_url,
            refresh_bearer_token=token if artifact == "refreshable" else None,
        )
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if artifact == "refreshable":
            headers["Cache-Control"] = "no-store"
        return Response(
            content=body,
            media_type=media_type,
            headers=headers,
        )

    return _with_report_generation_limit(build)


_REPORT_GENERATION = threading.Semaphore(1)
REPORT_GENERATION_BUSY_MESSAGE = (
    "A workbook is already being generated. Wait for it to finish, then try again."
)


def _with_report_generation_limit(builder):
    """Process-local bound on simultaneous Client Report / published-file builds."""
    if not _REPORT_GENERATION.acquire(blocking=False):
        raise TooManyRequests(REPORT_GENERATION_BUSY_MESSAGE)
    try:
        return builder()
    finally:
        _REPORT_GENERATION.release()
