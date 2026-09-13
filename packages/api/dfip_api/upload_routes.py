"""Authenticated multipart workbook ingest. Additive; does not publish."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from dfip_api.audit import write_audit_event
from dfip_api.deps import InspectorDep, SettingsDep, UploadServiceDep, get_principal
from dfip_api.errors import PayloadTooLarge, ValidationFailed
from dfip_api.schemas import ErrorResponse, UploadGroupResponse, UploadResponse

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    413: {"model": ErrorResponse, "description": "Workbook exceeds the size limit."},
    422: {"model": ErrorResponse, "description": "Validation error."},
    429: {"model": ErrorResponse, "description": "Too many requests."},
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
    request: Request,
    principal: InspectorDep,
    service: UploadServiceDep,
    settings: SettingsDep,
    response: Response,
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    client_id: Annotated[UUID | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
) -> UploadResponse | UploadGroupResponse:
    parts = _multipart_workbooks(file, files)
    max_files = settings.dfip_upload_max_files
    if len(parts) > max_files:
        raise PayloadTooLarge("Too many files in one upload request.")
    max_total = settings.upload_max_total_bytes
    payloads: list[tuple[str | None, bytes]] = []
    total = 0
    for upload in parts:
        remaining = max_total - total
        if remaining <= 0:
            raise PayloadTooLarge("Upload request exceeds the maximum allowed size.")
        payload = await service.read_upload(upload, remaining_total_bytes=remaining)
        total += len(payload)
        if total > max_total:
            raise PayloadTooLarge("Upload request exceeds the maximum allowed size.")
        payloads.append((upload.filename, payload))
    body, status = service.accept_parts(
        principal=principal,
        parts=payloads,
        requested_client_id=str(client_id) if client_id else None,
        force=force,
    )
    batch_id = getattr(getattr(body, "batch", None), "batch_id", None)
    write_audit_event(
        getattr(request.app.state, "db_pool", None),
        actor=principal.subject,
        action="upload.accept",
        entity_type="batch",
        entity_id=batch_id,
        client_id=getattr(body, "client_id", None),
        after={
            "status": getattr(getattr(body, "batch", None), "status", None),
            "replayed": getattr(body, "replayed", None),
            "original_filename": getattr(body, "original_filename", None),
        },
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
