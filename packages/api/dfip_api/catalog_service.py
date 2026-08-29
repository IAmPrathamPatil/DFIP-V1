"""Publisher Logic/Labels catalog import, versioning, and download.

Valid files are stored as ``draft``. Activation is explicit. Invalid files
never write a version and never replace the active catalog.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any
from uuid import uuid4

from dfip_config.catalog import (
    LOGIC_KIND,
    UPLOAD_NOTES,
    CatalogKind,
    CatalogStore,
    CatalogVersion,
    InMemoryCatalogStore,
    new_version_label,
)
from dfip_config.settings import Settings
from dfip_config.store import (
    CAMPAIGN_OUTPUT_FIELDS,
    load_campaign_version,
    load_label_group_version,
    load_manifest,
)
from dfip_config.workbook import CatalogWorkbookError, parse_catalog_workbook
from dfip_core.transform.labels import DEFAULT_LABEL_GROUP_VERSION
from openpyxl import Workbook

from dfip_api.auth import Principal
from dfip_api.errors import AuthorizationError, NotFoundError, PayloadTooLarge, ValidationFailed
from dfip_api.publication_service import _resolve_client_id
from dfip_api.roles import can_inspect
from dfip_api.schemas import (
    CatalogImportResponse,
    CatalogListResponse,
    CatalogVersionResponse,
)
from dfip_api.upload_service import _assert_xlsx_payload, _safe_workbook_name

PACKAGED_SENTINEL = "packaged"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LOGIC_DOWNLOAD_HEADERS = ("Campaign Name",) + tuple(
    {
        "filter_logic_1": "Filter Logic 1",
        "filter_logic_2": "Filter Logic 2",
        "amc_status_filter_logic_3": "AMC Status - Filter Logic 3",
        "amc_device_category_filter_logic_4": "AMC Device Category -  Filter Logic 4",
        "amc_product_cat_filter_logic_5": "AMC Product Cat -  Filter Logic 5",
        "manual_or_automated": "Manual Or Automated",
    }[field]
    for field in CAMPAIGN_OUTPUT_FIELDS
)
LABEL_DOWNLOAD_HEADERS = ("Group Name", "Filter Logic 1")


class CatalogService:
    def __init__(self, store: CatalogStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    @property
    def store(self) -> CatalogStore:
        return self._store

    async def read_upload(self, upload) -> bytes:
        limit = self._settings.dfip_upload_max_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise PayloadTooLarge(
                    f"Workbook exceeds the maximum allowed size of {limit} bytes."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def import_file(
        self,
        *,
        principal: Principal,
        kind: CatalogKind,
        filename: str | None,
        payload: bytes,
        requested_client_id: str | None,
    ) -> CatalogImportResponse:
        client_id = self._client(principal, requested_client_id)
        safe_name = _safe_workbook_name(filename)
        _assert_xlsx_payload(payload)
        try:
            parsed = parse_catalog_workbook(kind, payload)
        except CatalogWorkbookError as exc:
            raise ValidationFailed(
                str(exc.message),
                [_issue(item.row, item.code, item.detail) for item in exc.issues],
            ) from exc
        now = datetime.now(tz=UTC)
        record = CatalogVersion(
            id=str(uuid4()),
            client_id=client_id,
            kind=kind,
            version_label=new_version_label(kind, now),
            status="draft",
            row_count=len(parsed.rows),
            distinct_key_count=parsed.distinct_key_count,
            duplicate_key_count=parsed.duplicate_key_count,
            created_at=now,
            created_by=principal.subject,
            notes=UPLOAD_NOTES,
            source_filename=safe_name,
            rows=parsed.rows,
        )
        stored = self._store.add_version(record)
        return CatalogImportResponse(version=_uploaded_response(stored), errors=[])

    def list_versions(
        self,
        *,
        principal: Principal,
        kind: CatalogKind,
        requested_client_id: str | None,
    ) -> CatalogListResponse:
        client_id = self._client(principal, requested_client_id)
        items = [_uploaded_response(item) for item in self._store.list_for_client(client_id, kind)]
        active = self._store.active_for(client_id, kind)
        packaged = _packaged_fallback(kind, client_id)
        return CatalogListResponse(
            kind=kind,
            client_id=client_id,
            processing_active=_uploaded_response(active) if active else packaged,
            packaged_fallback=packaged,
            items=items,
        )

    def get_version(
        self,
        *,
        principal: Principal,
        kind: CatalogKind,
        version_id: str,
        requested_client_id: str | None,
    ) -> CatalogVersionResponse:
        client_id = self._client(principal, requested_client_id)
        if _is_packaged_id(kind, version_id):
            return _packaged_fallback(kind, client_id)
        record = self._store.get_for_client(client_id, version_id)
        if record is None or record.kind != kind:
            raise NotFoundError("Resource not found.")
        return _uploaded_response(record)

    def activate(
        self,
        *,
        principal: Principal,
        kind: CatalogKind,
        version_id: str,
        requested_client_id: str | None,
    ) -> CatalogVersionResponse:
        client_id = self._client(principal, requested_client_id)
        record = self._store.get_for_client(client_id, version_id)
        if record is None or record.kind != kind:
            raise NotFoundError("Resource not found.")
        try:
            activated = self._store.activate(client_id, version_id, activated_by=principal.subject)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        return _uploaded_response(activated)

    def deactivate(
        self,
        *,
        principal: Principal,
        kind: CatalogKind,
        version_id: str,
        requested_client_id: str | None,
    ) -> CatalogVersionResponse:
        client_id = self._client(principal, requested_client_id)
        record = self._store.get_for_client(client_id, version_id)
        if record is None or record.kind != kind:
            raise NotFoundError("Resource not found.")
        try:
            updated = self._store.deactivate(client_id, version_id)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        return _uploaded_response(updated)

    def download(
        self,
        *,
        principal: Principal,
        kind: CatalogKind,
        version_id: str,
        requested_client_id: str | None,
    ) -> tuple[bytes, str]:
        client_id = self._client(principal, requested_client_id)
        if _is_packaged_id(kind, version_id):
            rows = _packaged_rows(kind)
            label = _packaged_fallback(kind, client_id).version_label
            filename = f"{kind}-{label}.xlsx"
            return _render_xlsx(kind, rows), filename
        record = self._store.get_for_client(client_id, version_id)
        if record is None or record.kind != kind:
            raise NotFoundError("Resource not found.")
        if not record.rows:
            loaded = self._store.get_for_client(client_id, version_id)
            rows = loaded.rows if loaded is not None else ()
        else:
            rows = record.rows
        filename = f"{kind}-{record.version_label}.xlsx"
        return _render_xlsx(kind, rows), filename

    def _client(self, principal: Principal, requested_client_id: str | None) -> str:
        if not can_inspect(principal.role):
            raise AuthorizationError()
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            raise ValidationFailed("client_id is required.")
        return client_id


def default_catalog_store() -> InMemoryCatalogStore:
    return InMemoryCatalogStore()


def _issue(row: int | None, code: str, detail: str) -> dict[str, Any]:
    return {"row": row, "code": code, "detail": detail}


def _uploaded_response(record: CatalogVersion) -> CatalogVersionResponse:
    return CatalogVersionResponse(
        version_id=record.id,
        client_id=record.client_id,
        kind=record.kind,
        version_label=record.version_label,
        status=record.status,
        origin="uploaded",
        row_count=record.row_count,
        distinct_key_count=record.distinct_key_count,
        duplicate_key_count=record.duplicate_key_count,
        created_at=record.created_at,
        created_by=record.created_by,
        source_filename=record.source_filename,
        effective_from=date.fromisoformat(record.effective_from) if record.effective_from else None,
        effective_to=date.fromisoformat(record.effective_to) if record.effective_to else None,
        validation_status="valid",
    )


def _packaged_fallback(kind: CatalogKind, client_id: str) -> CatalogVersionResponse:
    ids = load_manifest()["ids"]
    if kind == LOGIC_KIND:
        snapshot = load_campaign_version("campaign-v2")
        version_id = ids["campaign-v2"]
        label = "campaign-v2"
        distinct = len({row.get("campaign_name") for row in snapshot.rows})
        duplicate = max(len(snapshot.rows) - distinct, 0)
    else:
        snapshot = load_label_group_version(DEFAULT_LABEL_GROUP_VERSION)
        version_id = ids[DEFAULT_LABEL_GROUP_VERSION]
        label = DEFAULT_LABEL_GROUP_VERSION
        distinct = len(snapshot.rows)
        duplicate = 0
    return CatalogVersionResponse(
        version_id=version_id,
        client_id=client_id,
        kind=kind,
        version_label=label,
        status="active",
        origin="packaged",
        row_count=len(snapshot.rows),
        distinct_key_count=distinct,
        duplicate_key_count=duplicate,
        created_at=None,
        created_by=None,
        source_filename=snapshot.source_filename,
        effective_from=date.fromisoformat(snapshot.effective_from)
        if snapshot.effective_from
        else None,
        effective_to=date.fromisoformat(snapshot.effective_to) if snapshot.effective_to else None,
        validation_status="packaged",
    )


def _is_packaged_id(kind: CatalogKind, version_id: str) -> bool:
    if version_id == PACKAGED_SENTINEL:
        return True
    ids = load_manifest()["ids"]
    if kind == LOGIC_KIND:
        return version_id in {ids["campaign-v1"], ids["campaign-v2"]}
    return version_id == ids[DEFAULT_LABEL_GROUP_VERSION]


def _packaged_rows(kind: CatalogKind) -> tuple[dict[str, Any], ...]:
    if kind == LOGIC_KIND:
        return load_campaign_version("campaign-v2").rows
    return load_label_group_version(DEFAULT_LABEL_GROUP_VERSION).rows


def _render_xlsx(kind: CatalogKind, rows: tuple[dict[str, Any], ...]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    if kind == LOGIC_KIND:
        sheet.append(list(LOGIC_DOWNLOAD_HEADERS))
        for row in rows:
            sheet.append(
                [
                    row.get("campaign_name"),
                    row.get("filter_logic_1"),
                    row.get("filter_logic_2"),
                    row.get("amc_status_filter_logic_3"),
                    row.get("amc_device_category_filter_logic_4"),
                    row.get("amc_product_cat_filter_logic_5"),
                    row.get("manual_or_automated"),
                ]
            )
    else:
        sheet.append(list(LABEL_DOWNLOAD_HEADERS))
        for row in rows:
            sheet.append([row.get("group_name"), row.get("filter_logic_1_value")])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()
