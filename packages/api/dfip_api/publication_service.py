"""P7 publication service. Application-level pointer, not a replacement for HTTP authz."""

from __future__ import annotations

from datetime import date

from dfip_analytics.qa import PUBLISHABLE_QA_VERDICTS, QA_VERDICT_FAIL, QA_VERDICT_UNAVAILABLE
from dfip_core.ingest.ports import IngestStore
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.ports import FactStore
from dfip_web.client_report_download import (
    CLIENT_REPORT_DOWNLOAD_NAME,
    render_client_report_xlsx,
)

from dfip_api.auth import Principal
from dfip_api.errors import (
    INTERNAL_ERROR,
    ApiError,
    AuthorizationError,
    NotFoundError,
    ValidationFailed,
)
from dfip_api.ports import PublicationStore
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
    render_published_csv,
    render_published_xlsx,
)
from dfip_api.roles import can_publish
from dfip_api.schemas import (
    FactPage,
    FactResponse,
    PaginationMeta,
    PublicationCurrentPointer,
    PublicationCurrentResponse,
    PublicationPage,
    PublicationResponse,
    PublicationStateResponse,
)
from dfip_api.service import fact_to_response

SUCCEEDED_RUN_STATUS = "succeeded"
SNAPSHOT_READ_PAGE = 1000


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
    ) -> None:
        self._ingest = ingest_store
        self._facts = fact_store
        self._publications = publication_store

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
        if fact_scope not in ALLOWED_FACT_SCOPES:
            raise ValidationFailed("fact_scope must be processing_run or client_current.")
        if period_start is not None and period_end is not None and period_start > period_end:
            raise ValidationFailed("period_start must be on or before period_end.")
        run = self._ingest.get_processing_run(processing_run_id)
        if run is None:
            raise NotFoundError("Processing run not found.")
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
        batch = self._ingest.get_batch(run.batch_id)
        if batch is None:
            raise NotFoundError("Processing run not found.")
        if batch.client_id != scoped:
            raise ValidationFailed("Processing run does not belong to this client.")
        snapshot_facts = self._load_candidate_facts(
            client_id=scoped,
            processing_run_id=processing_run_id,
            period_start=period_start,
            period_end=period_end,
            fact_scope=fact_scope,
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
        )
        return state_to_response(publication, pointer)

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
        page_size: int = 1000,
    ) -> tuple[bytes, str, str]:
        """Return the nine-sheet Client_Report for current or one publication.

        Read-only. Uses the same tenant and snapshot rules as published facts.
        Does not process, publish, or move publication_current.
        """
        if publication_id is None:
            client_id = _resolve_client_id(principal, requested_client_id)
            if client_id is None:
                raise ValidationFailed("client_id is required.")
            publication, pointer = self._publications.get_current(client_id)
            if publication is None or pointer is None:
                items: list[FactResponse] = []
                published_at = None
            else:
                items = self._load_published_items(
                    publication=publication,
                    max_rows=max_rows,
                    page_size=page_size,
                )
                published_at = publication.published_at
        else:
            publication = self._scoped_publication(principal, publication_id, requested_client_id)
            items = self._load_published_items(
                publication=publication,
                max_rows=max_rows,
                page_size=page_size,
            )
            published_at = publication.published_at
        payloads = [item.model_dump() for item in items]
        try:
            body = render_client_report_xlsx(payloads, published_at=published_at)
        except ApiError:
            raise
        except Exception:
            raise ApiError(
                500,
                INTERNAL_ERROR,
                "Client report could not be generated.",
            ) from None
        return body, CLIENT_REPORT_DOWNLOAD_NAME, XLSX_MEDIA_TYPE

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
    fail-closed (no rows).
    """
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
    if principal.client_id:
        if requested_client_id and requested_client_id != principal.client_id:
            raise AuthorizationError("Not authorized to access this client.")
        return principal.client_id
    if requested_client_id:
        return _enforce_client_scope(principal, requested_client_id)
    return requested_client_id
