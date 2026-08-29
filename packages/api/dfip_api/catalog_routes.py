"""Authenticated Logic/Labels catalog routes. Additive; does not publish."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile

from dfip_api.catalog_service import PACKAGED_SENTINEL, XLSX_MEDIA_TYPE
from dfip_api.deps import CatalogServiceDep, InspectorDep, get_principal
from dfip_api.errors import ValidationFailed
from dfip_api.schemas import (
    CatalogImportResponse,
    CatalogListResponse,
    CatalogVersionResponse,
    ErrorResponse,
)

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    413: {"model": ErrorResponse, "description": "Workbook exceeds the size limit."},
    422: {"model": ErrorResponse, "description": "Validation error."},
    503: {"model": ErrorResponse, "description": "Persistence unavailable."},
}

catalog_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)

KindPath = Annotated[Literal["logic", "labels"], Path(description="logic or labels")]
OptionalUuid = Annotated[UUID | None, Query()]


@catalog_router.post(
    "/catalogs/{kind}",
    response_model=CatalogImportResponse,
    status_code=201,
    summary="Upload a Logic or Labels workbook",
    description=(
        "Accepts a .xlsx Logic (campaign A:M) or Labels (Filter Logic 1_2) "
        "workbook, validates it, and stores a new draft version. Does not "
        "activate. JWT client_id always wins. Admin/publisher only."
    ),
    tags=["Catalogs"],
)
async def upload_catalog(
    kind: KindPath,
    principal: InspectorDep,
    service: CatalogServiceDep,
    file: Annotated[UploadFile, File()],
    client_id: Annotated[UUID | None, Form()] = None,
) -> CatalogImportResponse:
    payload = await service.read_upload(file)
    return service.import_file(
        principal=principal,
        kind=kind,
        filename=file.filename,
        payload=payload,
        requested_client_id=str(client_id) if client_id else None,
    )


@catalog_router.get(
    "/catalogs/{kind}",
    response_model=CatalogListResponse,
    summary="List Logic or Labels versions",
    tags=["Catalogs"],
)
def list_catalogs(
    kind: KindPath,
    principal: InspectorDep,
    service: CatalogServiceDep,
    client_id: OptionalUuid = None,
) -> CatalogListResponse:
    return service.list_versions(
        principal=principal,
        kind=kind,
        requested_client_id=str(client_id) if client_id else None,
    )


@catalog_router.get(
    "/catalogs/{kind}/{version_id}",
    response_model=CatalogVersionResponse,
    summary="Inspect a Logic or Labels version",
    tags=["Catalogs"],
)
def get_catalog_version(
    kind: KindPath,
    version_id: str,
    principal: InspectorDep,
    service: CatalogServiceDep,
    client_id: OptionalUuid = None,
) -> CatalogVersionResponse:
    return service.get_version(
        principal=principal,
        kind=kind,
        version_id=version_id,
        requested_client_id=str(client_id) if client_id else None,
    )


@catalog_router.post(
    "/catalogs/{kind}/{version_id}/activate",
    response_model=CatalogVersionResponse,
    summary="Activate a draft Logic or Labels version",
    tags=["Catalogs"],
)
def activate_catalog_version(
    kind: KindPath,
    version_id: str,
    principal: InspectorDep,
    service: CatalogServiceDep,
    client_id: OptionalUuid = None,
) -> CatalogVersionResponse:
    if version_id == PACKAGED_SENTINEL:
        raise ValidationFailed("Packaged catalogs cannot be activated through this route.")
    return service.activate(
        principal=principal,
        kind=kind,
        version_id=version_id,
        requested_client_id=str(client_id) if client_id else None,
    )


@catalog_router.post(
    "/catalogs/{kind}/{version_id}/deactivate",
    response_model=CatalogVersionResponse,
    summary="Deactivate an active uploaded version",
    tags=["Catalogs"],
)
def deactivate_catalog_version(
    kind: KindPath,
    version_id: str,
    principal: InspectorDep,
    service: CatalogServiceDep,
    client_id: OptionalUuid = None,
) -> CatalogVersionResponse:
    if version_id == PACKAGED_SENTINEL:
        raise ValidationFailed("Packaged catalogs cannot be deactivated through this route.")
    return service.deactivate(
        principal=principal,
        kind=kind,
        version_id=version_id,
        requested_client_id=str(client_id) if client_id else None,
    )


@catalog_router.get(
    "/catalogs/{kind}/{version_id}/download",
    summary="Download a Logic or Labels version as xlsx",
    tags=["Catalogs"],
)
def download_catalog_version(
    kind: KindPath,
    version_id: str,
    principal: InspectorDep,
    service: CatalogServiceDep,
    client_id: OptionalUuid = None,
) -> Response:
    body, filename = service.download(
        principal=principal,
        kind=kind,
        version_id=version_id,
        requested_client_id=str(client_id) if client_id else None,
    )
    return Response(
        content=body,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
