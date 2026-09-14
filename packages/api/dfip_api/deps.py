"""FastAPI dependencies: settings, service, and authentication."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from dfip_config.settings import Settings
from dfip_db.rls import bind_rls, reset_rls
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials

from dfip_api.auth import Principal, bearer_scheme, decode_jwt_claims, principal_from_credentials
from dfip_api.catalog_service import CatalogService
from dfip_api.errors import AuthorizationError
from dfip_api.excel_grant import EXCEL_TOKEN_TYP, principal_from_excel_grant
from dfip_api.lifecycle import require_company_active
from dfip_api.membership import enrich_principal, rls_context_for
from dfip_api.publication_service import PublicationService
from dfip_api.qa_store import QaFindingStore
from dfip_api.roles import can_inspect
from dfip_api.schemas import (
    DEFAULT_PAGE_LIMIT,
    HISTORY_FACTS_MAX_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    HistoryFactsPaginationParams,
    PaginationParams,
)
from dfip_api.service import ReadService
from dfip_api.upload_service import UploadService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_service(request: Request) -> ReadService:
    return request.app.state.service


def get_publication_service(request: Request) -> PublicationService:
    return request.app.state.publication_service


def get_upload_service(request: Request) -> UploadService:
    return request.app.state.upload_service


def get_qa_store(request: Request) -> QaFindingStore:
    return request.app.state.qa_store


def get_catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog_service


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Iterator[Principal]:
    settings = request.app.state.settings
    principal = principal_from_credentials(settings, credentials)
    store = getattr(request.app.state, "identity_store", None)
    if principal.auth_mode == "jwt" and credentials is not None:
        claims = decode_jwt_claims(settings, credentials.credentials, verify_exp=True)
        if claims.get("typ") == EXCEL_TOKEN_TYP:
            principal = principal_from_excel_grant(
                settings=settings,
                identity_store=store,
                grants=getattr(request.app.state, "excel_grant_store", None),
                token=credentials.credentials,
                verify_exp=True,
            )
        elif store is not None:
            principal = enrich_principal(store, principal, settings)
    if principal.role in {"client", "reader"}:
        require_company_active(
            getattr(request.app.state, "client_directory", None), principal.client_id
        )
    pool = getattr(request.app.state, "db_pool", None)
    bind_rls(rls_context_for(principal, db_mode=pool is not None))
    try:
        yield principal
    finally:
        reset_rls()


def pagination_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


def history_facts_pagination_params(
    limit: Annotated[int, Query(ge=1, le=HISTORY_FACTS_MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryFactsPaginationParams:
    return HistoryFactsPaginationParams(limit=limit, offset=offset)


ServiceDep = Annotated[ReadService, Depends(get_service)]
PublicationServiceDep = Annotated[PublicationService, Depends(get_publication_service)]
UploadServiceDep = Annotated[UploadService, Depends(get_upload_service)]
QaStoreDep = Annotated[QaFindingStore, Depends(get_qa_store)]
CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
PaginationDep = Annotated[PaginationParams, Depends(pagination_params)]
HistoryFactsPaginationDep = Annotated[
    HistoryFactsPaginationParams, Depends(history_facts_pagination_params)
]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_inspector(principal: PrincipalDep) -> Principal:
    """Admin/publisher only. Client/reader receive the existing 403 envelope."""
    if not can_inspect(principal.role):
        raise AuthorizationError()
    return principal


InspectorDep = Annotated[Principal, Depends(require_inspector)]


def get_ready_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    """Authenticate readiness without opening PostgreSQL.

    Identity/token_version lookup stays on data routes. This endpoint must
    still return structured not_ready when the database is down.
    """
    return principal_from_credentials(request.app.state.settings, credentials)


def require_ready_inspector(
    principal: Annotated[Principal, Depends(get_ready_principal)],
) -> Principal:
    if not can_inspect(principal.role):
        raise AuthorizationError()
    return principal


ReadyInspectorDep = Annotated[Principal, Depends(require_ready_inspector)]
