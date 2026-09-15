"""P7 publication service. Application-level pointer, not a replacement for HTTP authz."""

from __future__ import annotations

from datetime import date

from dfip_analytics.qa import PUBLISHABLE_QA_VERDICTS, QA_VERDICT_FAIL, QA_VERDICT_UNAVAILABLE
from dfip_core.ingest.ports import IngestStore
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.ports import FactStore
from dfip_web.client_report_download import (
    ARTIFACT_REFRESHABLE,
    ARTIFACT_STATIC,
    CLIENT_REPORT_MACRO_MEDIA_TYPE,
    CLIENT_REPORT_MEDIA_TYPE,
    client_report_download_filename,
    render_client_report_xlsx,
)
from dfip_web.refreshable_xlsm_stamp import (
    resolve_refreshable_xlsm_template,
    stamp_refreshable_xlsm_file,
)

from dfip_api.auth import Principal
from dfip_api.client_directory import ClientDirectory
from dfip_api.errors import (
    INTERNAL_ERROR,
    ApiError,
    AuthorizationError,
    NotFoundError,
    ValidationFailed,
)
from dfip_api.lifecycle import require_company_active
from dfip_api.membership import requires_client_selection
from dfip_api.publication_progress import PublicationProgressRegistry
from dfip_api.publication_store import (
    ALLOWED_FACT_SCOPES,
    FACT_SCOPE_PROCESSING_RUN,
    SNAPSHOT_STATUS_COMPLETE,
    PublicationCurrentRecord,
    PublicationRecord,
)
from dfip_api.published_download import (
    CSV_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    published_download_filename,
    render_history_facts_csv,
    render_published_csv,
    render_published_xlsx,
)
from dfip_api.roles import can_publish
from dfip_api.schemas import (
    HISTORY_FACTS_MAX_PAGE_LIMIT,
    FactPage,
    FactResponse,
    FactTablePage,
    PaginationMeta,
    PublicationCurrentPointer,
    PublicationCurrentResponse,
    PublicationPage,
    PublicationResponse,
    PublicationStateResponse,
)
from dfip_api.service import fact_to_response, facts_to_table_page

SUCCEEDED_RUN_STATUS = "succeeded"
SNAPSHOT_READ_PAGE = 1000
NO_PUBLICATION_WORKBOOK_MESSAGE = (
    "Cannot generate Company Workbook: this company has no current published report."
)
ROW_CAP_WORKBOOK_MESSAGE = (
    "Cannot generate Company Workbook: the published slice is larger than the "
    "supported workbook download limit."
)


def publication_to_response(record: PublicationRecord) -> PublicationResponse:
    return PublicationResponse(
        publication_id=record.id,
        client_id=record.client_id,
        processing_run_id=record.processing_run_id,
        period_start=record.period_start,
        period_end=record.period_end,
        published_at=record.published_at,
        published_by=record.published_by,
        notes=record.notes,
        fact_scope=record.fact_scope,
        snapshot_status=record.snapshot_status,
        snapshot_row_count=record.snapshot_row_count,
    )


def current_to_response(record: PublicationCurrentRecord) -> PublicationCurrentPointer:
    return PublicationCurrentPointer(
        client_id=record.client_id,
        publication_id=record.publication_id,
        updated_at=record.updated_at,
    )


def state_to_response(
    publication: PublicationRecord,
    current: PublicationCurrentRecord,
) -> PublicationStateResponse:
    return PublicationStateResponse(
        publication=publication_to_response(publication),
        current=current_to_response(current),
    )


class PublicationService:
    def __init__(
        self,
        ingest_store: IngestStore,
        fact_store: FactStore,
        publication_store: PublicationStore,
        client_directory: ClientDirectory | None = None,
    ) -> None:
        self._ingest = ingest_store
        self._facts = fact_store
        self._publications = publication_store
        self._clients = client_directory
        self._progress = PublicationProgressRegistry()

    def has_in_flight_publish(self, client_id: str) -> bool:
        return self._progress.has_running_for_client(client_id)

    def create(
        self,
        *,
        principal: Principal,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        notes: str | None,
        fact_scope: str = FACT_SCOPE_PROCESSING_RUN,
    ) -> PublicationStateResponse:
        if not can_publish(principal.role):
            raise AuthorizationError("Not authorized to create publications.")
        scoped = _enforce_client_scope(principal, client_id)
        require_company_active(self._clients, scoped)
        if fact_scope not in ALLOWED_FACT_SCOPES:
            raise ValidationFailed("fact_scope must be processing_run or client_current.")
        if period_start is not None and period_end is not None and period_start > period_end:
            raise ValidationFailed("period_start must be on or before period_end.")
        run = self._ingest.get_processing_run(processing_run_id)
        if run is None:
            raise NotFoundError("Processing run not found.")
        batch = self._ingest.get_batch(run.batch_id)
        if batch is None:
            raise NotFoundError("Processing run not found.")
        if batch.client_id != scoped:
            raise ValidationFailed("Processing run does not belong to this client.")
        if run.status != SUCCEEDED_RUN_STATUS:
            raise ValidationFailed("Processing run must have status succeeded.")
        if run.qa_verdict is None:
            raise ValidationFailed("Processing run has no QA verdict.")
        if run.qa_verdict == QA_VERDICT_UNAVAILABLE:
            raise ValidationFailed("Processing run QA did not complete.")
        if run.qa_verdict == QA_VERDICT_FAIL:
            raise ValidationFailed("Processing run QA verdict is fail.")
        if run.qa_verdict not in PUBLISHABLE_QA_VERDICTS:
            raise ValidationFailed("Processing run QA verdict does not allow publication.")
        self._progress.start(client_id=scoped, processing_run_id=processing_run_id)
        self._progress.set(processing_run_id, "preparing")
        copy_live = bool(getattr(self._publications, "supports_live_fact_copy", False))
        snapshot_facts = None
        total = None
        try:
            self._progress.set(processing_run_id, "validating")
            if copy_live:
                _page, total = self._facts.list_published_slice(
                    client_id=scoped,
                    processing_run_id=processing_run_id,
                    period_start=period_start,
                    period_end=period_end,
                    fact_scope=fact_scope,
                    limit=1,
                    offset=0,
                    working_set=True,
                )
            else:
                snapshot_facts = self._load_candidate_facts(
                    client_id=scoped,
                    processing_run_id=processing_run_id,
                    period_start=period_start,
                    period_end=period_end,
                    fact_scope=fact_scope,
                )
                total = len(snapshot_facts)
            self._progress.set(
                processing_run_id,
                "validating",
                total_count=total,
                current_count=total,
            )
            self._progress.set(processing_run_id, "creating", total_count=total)

            def on_progress(stage: str, message: str) -> None:
                self._progress.set(
                    processing_run_id,
                    stage,
                    message=message,
                    total_count=total,
                )

            publication, pointer = self._publications.create(
                client_id=scoped,
                processing_run_id=processing_run_id,
                period_start=period_start,
                period_end=period_end,
                published_by=principal.subject,
                notes=notes,
                fact_scope=fact_scope,
                snapshot_facts=snapshot_facts,
                on_progress=on_progress,
            )
            self._progress.succeed(processing_run_id, publication.id)
            return state_to_response(publication, pointer)
        except Exception:
            self._progress.fail(processing_run_id, "Publication failed.")
            raise

    def progress_for(
        self,
        *,
        principal: Principal,
        processing_run_id: str,
        requested_client_id: str | None,
    ):
        item = self._progress.get(processing_run_id)
        if item is None:
            raise NotFoundError("Publication progress not found.")
        scoped = _resolve_client_id(principal, requested_client_id)
        if scoped is None:
            _enforce_client_scope(principal, item.client_id)
        elif scoped != item.client_id:
            raise NotFoundError("Publication progress not found.")
        else:
            _enforce_client_scope(principal, item.client_id)
        return item

    def get_current(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
    ) -> PublicationCurrentResponse:
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            return PublicationCurrentResponse(publication=None, current=None)
        publication, pointer = self._publications.get_current(client_id)
        if publication is None or pointer is None:
            return PublicationCurrentResponse(publication=None, current=None)
        return PublicationCurrentResponse(
            publication=publication_to_response(publication),
            current=current_to_response(pointer),
        )

    def list_for_client(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
        limit: int,
        offset: int,
    ) -> PublicationPage:
        """Publication rows for the scoped client. Newest first. Not the fact history table."""
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            return PublicationPage(
                items=[],
                pagination=PaginationMeta(limit=limit, offset=offset, total=0),
            )
        rows = list(reversed(self._publications.list_for_client(client_id)))
        total = len(rows)
        page = rows[offset : offset + limit]
        return PublicationPage(
            items=[publication_to_response(item) for item in page],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def list_published_facts(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
        limit: int,
        offset: int,
    ) -> FactPage:
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            return FactPage(
                items=[],
                pagination=PaginationMeta(limit=limit, offset=offset, total=0),
            )
        require_company_active(self._clients, client_id)
        publication, pointer = self._publications.get_current(client_id)
        if publication is None or pointer is None:
            return FactPage(
                items=[],
                pagination=PaginationMeta(limit=limit, offset=offset, total=0),
            )
        records, total = self._facts_for_publication(publication, limit=limit, offset=offset)
        return FactPage(
            items=[fact_to_response(item) for item in records],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def list_published_history_facts(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
        limit: int,
        offset: int,
        layout: str = "objects",
    ) -> FactPage | FactTablePage:
        """Paginated union of complete published snapshots for one tenant.

        Newest publication wins per campaign/variation/day grain, matching
        existing republish pointer semantics. Unpublished working-set facts
        are not included. JWT client_id scopes the result. layout=table is
        the compact Excel Refresh All encoding; objects remains the default
        JSON list contract.
        """
        client_id = _resolve_client_id(principal, requested_client_id)
        pagination = PaginationMeta(limit=limit, offset=offset, total=0)
        empty_objects = FactPage(items=[], pagination=pagination)
        if client_id is None:
            if layout == "table":
                return facts_to_table_page([], pagination)
            return empty_objects
        require_company_active(self._clients, client_id)
        records, total = self._publications.list_published_history(
            client_id,
            limit=limit,
            offset=offset,
        )
        pagination = PaginationMeta(limit=limit, offset=offset, total=total)
        if layout == "table":
            return facts_to_table_page(records, pagination)
        return FactPage(
            items=[fact_to_response(item) for item in records],
            pagination=pagination,
        )

    def download_published_history_csv(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
    ) -> bytes:
        """Cumulative newest-wins history as CSV. Same tenant rules as history/facts.

        Does not change JSON history/facts. Cap matches HISTORY_FACTS_MAX_PAGE_LIMIT
        so Excel Refresh All can load current tenants in one file.
        """
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            return render_history_facts_csv([])
        require_company_active(self._clients, client_id)
        return self._publications.copy_published_history_csv(
            client_id,
            max_rows=HISTORY_FACTS_MAX_PAGE_LIMIT,
        )

    def list_publication_facts(
        self,
        *,
        principal: Principal,
        publication_id: str,
        requested_client_id: str | None,
        limit: int,
        offset: int,
    ) -> FactPage:
        publication = self._scoped_publication(principal, publication_id, requested_client_id)
        records, total = self._facts_for_publication(publication, limit=limit, offset=offset)
        return FactPage(
            items=[fact_to_response(item) for item in records],
            pagination=PaginationMeta(limit=limit, offset=offset, total=total),
        )

    def download_published(
        self,
        *,
        principal: Principal,
        requested_client_id: str | None,
        kind: str,
        max_rows: int,
        page_size: int = 1000,
    ) -> tuple[bytes, str, str]:
        """Return the current published slice as CSV or XLSX bytes.

        Uses the same tenant and publication_current rules as list_published_facts.
        Internal paging is not the HTTP MAX_PAGE_LIMIT.
        """
        if kind not in {"csv", "xlsx"}:
            raise ValidationFailed("Download format must be csv or xlsx.")
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            raise ValidationFailed("client_id is required.")
        require_company_active(self._clients, client_id)
        publication, pointer = self._publications.get_current(client_id)
        filename = published_download_filename(
            client_id,
            publication.id if publication is not None else None,
            kind,
        )
        if publication is None or pointer is None:
            items: list[FactResponse] = []
        else:
            items = self._load_published_items(
                publication=publication,
                max_rows=max_rows,
                page_size=page_size,
            )
        if kind == "csv":
            return render_published_csv(items), filename, CSV_MEDIA_TYPE
        return render_published_xlsx(items), filename, XLSX_MEDIA_TYPE

    def download_client_report(
        self,
        *,
        principal: Principal,
        publication_id: str | None,
        requested_client_id: str | None,
        max_rows: int,
        page_size: int = 5000,
        artifact: str = ARTIFACT_STATIC,
        api_base_url: str | None = None,
        refresh_bearer_token: str | None = None,
        refreshable_xlsm_template: str | None = None,
    ) -> tuple[bytes, str, str]:
        """Return the nine-sheet Client_Report for current or one publication.

        Read-only. Uses the same tenant and snapshot rules as published facts.
        Does not process, publish, or move publication_current. Current
        download without a publication returns an explicit error rather than
        an empty workbook. Refreshable artifacts copy the Data Model .xlsm
        and stamp a fresh client-scoped Excel grant. Refresh All pages
        cumulative published history for the company. Static artifacts still
        clone excel/Client_Report.xlsx.
        """
        if artifact == ARTIFACT_REFRESHABLE and publication_id is not None:
            raise ValidationFailed("Refreshable workbooks follow publication_current only.")
        if publication_id is None:
            client_id = _resolve_client_id(principal, requested_client_id)
            if client_id is None:
                raise ValidationFailed("client_id is required.")
            require_company_active(self._clients, client_id)
            publication, pointer = self._publications.get_current(client_id)
            if publication is None or pointer is None:
                raise NotFoundError(NO_PUBLICATION_WORKBOOK_MESSAGE)
        else:
            publication = self._scoped_publication(principal, publication_id, requested_client_id)
            client_id = publication.client_id
        published_at = publication.published_at
        code, name = self._company_identity(client_id)
        if artifact == ARTIFACT_REFRESHABLE:
            try:
                template = resolve_refreshable_xlsm_template(
                    refreshable_xlsm_template or ""
                )
                body = stamp_refreshable_xlsm_file(
                    template,
                    bearer_token=refresh_bearer_token or "",
                )
            except ApiError:
                raise
            except Exception:
                raise ApiError(
                    500,
                    INTERNAL_ERROR,
                    "Client report could not be generated.",
                ) from None
            filename = client_report_download_filename(
                code,
                published_at,
                artifact=artifact,
                publication_id=publication_id,
            )
            return body, filename, CLIENT_REPORT_MACRO_MEDIA_TYPE
        try:
            items = self._load_published_items(
                publication=publication,
                max_rows=max_rows,
                page_size=page_size,
            )
        except ValidationFailed as exc:
            if "exceeds the download row limit" in str(exc):
                raise ValidationFailed(ROW_CAP_WORKBOOK_MESSAGE) from None
            raise
        payloads = [item.model_dump() for item in items]
        try:
            body = render_client_report_xlsx(
                payloads,
                published_at=published_at,
                client_id=client_id,
                client_code=code,
                client_name=name,
                publication_id=publication.id,
                artifact=artifact,
                api_base_url=api_base_url,
                refresh_bearer_token=refresh_bearer_token,
            )
        except ApiError:
            raise
        except Exception:
            raise ApiError(
                500,
                INTERNAL_ERROR,
                "Client report could not be generated.",
            ) from None
        filename = client_report_download_filename(
            code,
            published_at,
            artifact=artifact,
            publication_id=publication_id,
        )
        return body, filename, CLIENT_REPORT_MEDIA_TYPE

    def _company_identity(self, client_id: str) -> tuple[str, str]:
        record = self._clients.get(client_id) if self._clients is not None else None
        if record is None:
            return client_id, client_id
        return record.code, record.name

    def _load_published_items(
        self,
        *,
        publication: PublicationRecord,
        max_rows: int,
        page_size: int,
    ) -> list[FactResponse]:
        first, total = self._facts_for_publication(
            publication,
            limit=min(page_size, max_rows),
            offset=0,
        )
        if total > max_rows:
            raise ValidationFailed("Published slice exceeds the download row limit.")
        records = list(first)
        offset = len(records)
        while offset < total:
            chunk, _ignored = self._facts_for_publication(
                publication,
                limit=min(page_size, total - offset),
                offset=offset,
            )
            if not chunk:
                break
            records.extend(chunk)
            offset += len(chunk)
        return [fact_to_response(item) for item in records]

    def _load_candidate_facts(
        self,
        *,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        fact_scope: str,
    ) -> list[FactRecord]:
        records: list[FactRecord] = []
        offset = 0
        while True:
            page, total = self._facts.list_published_slice(
                client_id=client_id,
                processing_run_id=processing_run_id,
                period_start=period_start,
                period_end=period_end,
                fact_scope=fact_scope,
                limit=SNAPSHOT_READ_PAGE,
                offset=offset,
                working_set=True,
            )
            records.extend(page)
            offset += len(page)
            if offset >= total or not page:
                break
        return records

    def _facts_for_publication(
        self, publication: PublicationRecord, *, limit: int, offset: int
    ) -> tuple[list[FactRecord], int]:
        if publication.snapshot_status == SNAPSHOT_STATUS_COMPLETE:
            return self._publications.list_snapshot(
                publication.id,
                client_id=publication.client_id,
                limit=limit,
                offset=offset,
            )
        return self._facts.list_published_slice(
            **_published_slice_args(publication),
            limit=limit,
            offset=offset,
        )

    def _scoped_publication(
        self,
        principal: Principal,
        publication_id: str,
        requested_client_id: str | None,
    ) -> PublicationRecord:
        publication = self._publications.get(publication_id)
        if publication is None:
            raise NotFoundError("Publication not found.")
        scoped = _resolve_client_id(principal, requested_client_id)
        if scoped is None:
            _enforce_client_scope(principal, publication.client_id)
        elif scoped != publication.client_id:
            raise NotFoundError("Publication not found.")
        else:
            _enforce_client_scope(principal, publication.client_id)
        return publication


def _published_slice_args(publication: PublicationRecord) -> dict[str, object]:
    return {
        "client_id": publication.client_id,
        "processing_run_id": publication.processing_run_id,
        "period_start": publication.period_start,
        "period_end": publication.period_end,
        "fact_scope": publication.fact_scope,
    }


def _enforce_client_scope(principal: Principal, client_id: str) -> str:
    if requires_client_selection(principal):
        raise AuthorizationError("Not authorized to access this client.")
    if principal.client_id and principal.client_id != client_id:
        raise AuthorizationError("Not authorized to access this client.")
    if (
        not principal.platform_admin
        and principal.membership_client_ids is not None
        and client_id not in principal.membership_client_ids
    ):
        raise AuthorizationError("Not authorized to access this client.")
    return client_id


def inspector_filter_client_ids(principal: Principal) -> tuple[str, ...] | None:
    """Allowed clients for inspector reads. None means no membership filter.

    JWT or bound development ``client_id`` is a hard bound. Empty tuple is
    fail-closed (no rows). A multi-membership inspector JWT without a bound
    client must select a company first.
    """
    if requires_client_selection(principal):
        return ()
    if principal.client_id:
        if (
            principal.membership_client_ids is not None
            and principal.client_id not in principal.membership_client_ids
        ):
            return ()
        return (principal.client_id,)
    return principal.membership_client_ids


def inspector_read_scope(
    principal: Principal, requested_client_id: str | None = None
) -> tuple[str | None, tuple[str, ...] | None]:
    """JWT client_id always wins. Conflicting query client_id is 403."""
    resolved = _resolve_client_id(principal, requested_client_id)
    return resolved, inspector_filter_client_ids(principal)


def _resolve_client_id(principal: Principal, requested_client_id: str | None) -> str | None:
    """JWT or bound development client_id always wins.

    Unscoped in-memory ``dev_token`` (no ``DFIP_DEV_AUTH_CLIENT_ID``, no
    ``DATABASE_URL``) may pass ``client_id`` explicitly. That combination is
    refused when a database is configured.
    """
    if requires_client_selection(principal):
        raise AuthorizationError("Not authorized to access this client.")
    if principal.client_id:
        if requested_client_id and requested_client_id != principal.client_id:
            raise AuthorizationError("Not authorized to access this client.")
        return principal.client_id
    if requested_client_id:
        return _enforce_client_scope(principal, requested_client_id)
    return requested_client_id
