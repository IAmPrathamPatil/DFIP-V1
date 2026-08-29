"""Authenticated multipart workbook ingest. Additive; does not publish."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from dfip_api.deps import InspectorDep, UploadServiceDep, get_principal
from dfip_api.errors import ValidationFailed
from dfip_api.schemas import ErrorResponse, UploadGroupResponse, UploadResponse

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    413: {"model": ErrorResponse, "description": "Workbook exceeds the size limit."},
    422: {"model": ErrorResponse, "description": "Validation error."},
    503: {"model": ErrorResponse, "description": "Persistence unavailable."},
}

upload_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)


@upload_router.post(
    "/uploads",
    response_model=UploadResponse | UploadGroupResponse,
    status_code=202,
    summary="Ingest a Web Engage workbook",
    description=(
        "Accepts one or more .xlsx workbooks, validates ZIP/size, then runs the "
        "existing ingest and transform pipeline off the API event loop. A single "
        "file keeps the existing UploadResponse. Multiple files return "
        "UploadGroupResponse; each file is its own source_file/batch/run. New "
        "work returns 202 with a received batch; poll GET /batches/{id} and "
        "GET /processing-runs?batch_id=. Replay of a successful SHA returns 200. "
        "JWT client_id always wins. Admin/publisher only. Does not publish."
    ),
    tags=["Uploads"],
)
async def upload_workbook(
    principal: InspectorDep,
    service: UploadServiceDep,
    response: Response,
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    client_id: Annotated[UUID | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
) -> UploadResponse | UploadGroupResponse:
    parts = _multipart_workbooks(file, files)
    payloads: list[tuple[str | None, bytes]] = []
    for upload in parts:
        payloads.append((upload.filename, await service.read_upload(upload)))
    body, status = service.accept_parts(
        principal=principal,
        parts=payloads,
        requested_client_id=str(client_id) if client_id else None,
        force=force,
    )
    response.status_code = status
    return body


def _multipart_workbooks(
    file: UploadFile | None, files: list[UploadFile] | None
) -> list[UploadFile]:
    parts: list[UploadFile] = []
    if file is not None and file.filename:
        parts.append(file)
    if files:
        for item in files:
            if item is not None and item.filename:
                parts.append(item)
    if not parts:
        raise ValidationFailed("At least one .xlsx workbook is required.")
    return parts
