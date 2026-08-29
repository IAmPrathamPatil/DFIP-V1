"""Authenticated workbook ingest over HTTP.

Uses the existing P3 ingest_workbook and P4 run_transformation entrypoints.
Does not write fact tables directly and does not publish.

Heavy inspect/stage/transform work runs on a dedicated upload thread so the
API event loop stays able to serve /health, /session, catalogs, and status.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from dfip_analytics.qa import RejectedEvidence
from dfip_config.catalog import CatalogStore
from dfip_config.settings import Settings
from dfip_core.ingest.pipeline import bind_versions_for_day, ingest_workbook
from dfip_core.ingest.ports import IngestStore
from dfip_core.ingest.store import BatchRecord, ProcessingRunRecord
from dfip_core.transform.engine import ENGINE_VERSION, run_transformation
from dfip_core.transform.labels import packaged_label_for_id
from dfip_core.transform.ports import FactStore

from dfip_api.auth import Principal
from dfip_api.errors import (
    AuthorizationError,
    NotFoundError,
    PayloadTooLarge,
    PersistenceUnavailableError,
    ValidationFailed,
)
from dfip_api.publication_service import _resolve_client_id
from dfip_api.qa_store import QaFindingStore
from dfip_api.qa_workflow import evaluate_and_persist_run, processing_rls
from dfip_api.roles import can_inspect
from dfip_api.schemas import (
    ReprocessResponse,
    UploadGroupResponse,
    UploadRejection,
    UploadResponse,
    UploadTransformSummary,
)
from dfip_api.service import batch_to_response, processing_run_to_response
from dfip_api.source_storage import InMemorySourceObjectStore, SourceObjectStore

XLSX_MAGIC = b"PK"
SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
READ_CHUNK = 64 * 1024
MAX_REJECTION_ITEMS = 50
PENDING_SOURCE_KIND = "native_export"
WORKER_STALL_REASON = "worker stalled during fact persistence"
log = logging.getLogger(__name__)


class UploadService:
    def __init__(
        self,
        ingest_store: IngestStore,
        fact_store: FactStore,
        settings: Settings,
        catalog_store: CatalogStore | None = None,
        qa_store: QaFindingStore | None = None,
        executor: ThreadPoolExecutor | None = None,
        source_store: SourceObjectStore | None = None,
    ) -> None:
        self._ingest = ingest_store
        self._facts = fact_store
        self._settings = settings
        self._catalog = catalog_store
        self._qa = qa_store
        self._executor = executor
        self._source_store = source_store or InMemorySourceObjectStore(
            settings.dfip_storage_bucket
        )
        self._serialize = threading.Lock()
        self._in_flight_lock = threading.Lock()
        self._in_flight: dict[tuple[str, str], str] = {}
        self._completed: dict[str, UploadResponse] = {}
        self._reprocess_in_flight: dict[str, str] = {}
        self._reprocess_completed: dict[str, ReprocessResponse] = {}

    def set_executor(self, executor: ThreadPoolExecutor | None) -> None:
        self._executor = executor

    def completed_result(self, batch_id: str) -> UploadResponse | None:
        return self._completed.get(batch_id)

    def reprocess_result(self, processing_run_id: str) -> ReprocessResponse | None:
        return self._reprocess_completed.get(processing_run_id)

    async def read_upload(self, upload) -> bytes:
        """Read the multipart file with a hard byte cap. Does not keep a disk path."""
        limit = self._settings.dfip_upload_max_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise PayloadTooLarge(
                    f"Workbook exceeds the maximum allowed size of {limit} bytes."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def accept(
        self,
        *,
        principal: Principal,
        filename: str | None,
        payload: bytes,
        requested_client_id: str | None,
        force: bool = False,
    ) -> tuple[UploadResponse, int]:
        """Validate and enqueue ingest. Returns (body, http_status).

        Replay of a successful SHA is completed immediately (200). New work is
        accepted as a ``received`` batch (202) and processed off the event loop.
        """
        client_id, safe_name = self._authorize(
            principal, filename, requested_client_id
        )
        _assert_xlsx_payload(payload)
        digest = hashlib.sha256(payload).hexdigest()
        existing = self._ingest.get_source_file_by_sha256(digest, client_id)
        if existing is not None and not force:
            successful = self._ingest.successful_batch_for_file(existing.id)
            if successful is not None:
                existing = self._persist_source_bytes(existing, payload)
                body = self._replay_response(existing, successful)
                self._completed[successful.id] = body
                return body, 200
            inflight = self._inflight_batch(client_id, digest, existing)
            if inflight is not None:
                self._persist_source_bytes(existing, payload)
                return self._accepted_response(existing, inflight), 202

        source = self._ingest.register_source_file(
            client_id=client_id,
            sha256=digest,
            original_filename=safe_name,
            byte_size=len(payload),
            source_kind=PENDING_SOURCE_KIND,
        )
        source = self._persist_source_bytes(source, payload)
        batch = self._ingest.create_batch(source_file_id=source.id, client_id=client_id)
        folder = Path(tempfile.mkdtemp(prefix="dfip-upload-"))
        dest = folder / safe_name
        dest.write_bytes(payload)
        accepted = self._accepted_response(source, batch)
        self._remember_inflight(client_id, digest, batch.id)
        if self._executor is None:
            self._run_job(
                dest,
                folder,
                batch.id,
                client_id,
                digest,
                force,
            )
            completed = self._completed.get(batch.id)
            if completed is not None:
                return completed, 200 if completed.replayed else 201
            return accepted, 202
        self._executor.submit(
            self._run_job,
            dest,
            folder,
            batch.id,
            client_id,
            digest,
            force,
        )
        return accepted, 202

    def accept_parts(
        self,
        *,
        principal: Principal,
        parts: list[tuple[str | None, bytes]],
        requested_client_id: str | None,
        force: bool = False,
    ) -> tuple[UploadResponse | UploadGroupResponse, int]:
        """Accept one or more workbooks. One file keeps the single-file contract.

        Multiple files are validated first, then processed independently in a
        stable filename/SHA order through ``accept``. Overlapping grains in the
        same request are restated by the later file in that order. Same-SHA
        duplicates replay. Does not concatenate workbooks or rewrite metrics.
        """
        usable = [(filename, payload) for filename, payload in parts if payload]
        if not usable:
            raise ValidationFailed("At least one .xlsx workbook is required.")
        self._authorize(principal, usable[0][0], requested_client_id)
        prepared: list[tuple[str, bytes, str]] = []
        for filename, payload in usable:
            _assert_xlsx_payload(payload)
            safe_name = _safe_workbook_name(filename)
            digest = hashlib.sha256(payload).hexdigest()
            prepared.append((safe_name, payload, digest))
        prepared.sort(key=_upload_sort_key)
        if len(prepared) == 1:
            safe_name, payload, _digest = prepared[0]
            return self.accept(
                principal=principal,
                filename=safe_name,
                payload=payload,
                requested_client_id=requested_client_id,
                force=force,
            )
        started = time.perf_counter()
        items: list[UploadResponse] = []
        statuses: list[int] = []
        for safe_name, payload, _digest in prepared:
            body, status = self.accept(
                principal=principal,
                filename=safe_name,
                payload=payload,
                requested_client_id=requested_client_id,
                force=force,
            )
            items.append(body)
            statuses.append(status)
        duration_ms = int((time.perf_counter() - started) * 1000)
        group = _group_response(items, duration_ms=duration_ms)
        log.info(
            "upload-group client_id=%s file_count=%s staged_row_count=%s "
            "fact_count=%s duration_ms=%s",
            group.client_id,
            group.file_count,
            group.staged_row_count,
            group.fact_count,
            group.duration_ms,
        )
        if all(status == 200 for status in statuses):
            return group, 200
        if any(status == 202 for status in statuses):
            return group, 202
        return group, 201

    def process(
        self,
        *,
        principal: Principal,
        filename: str | None,
        payload: bytes,
        requested_client_id: str | None,
        force: bool = False,
        reserved_batch: BatchRecord | None = None,
        dest: Path | None = None,
    ) -> UploadResponse:
        """Run ingest + transform. Used by the upload worker and tests."""
        client_id, safe_name = self._authorize(
            principal, filename, requested_client_id
        )
        if dest is None:
            _assert_xlsx_payload(payload)
            folder = Path(tempfile.mkdtemp(prefix="dfip-upload-"))
            dest = folder / safe_name
            dest.write_bytes(payload)
            try:
                return self._ingest_and_transform(
                    dest, client_id=client_id, force=force, reserved_batch=reserved_batch
                )
            finally:
                _remove_upload_dir(folder)
        return self._ingest_and_transform(
            dest, client_id=client_id, force=force, reserved_batch=reserved_batch
        )

    def _authorize(
        self,
        principal: Principal,
        filename: str | None,
        requested_client_id: str | None,
    ) -> tuple[str, str]:
        if not can_inspect(principal.role):
            raise AuthorizationError()
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            raise ValidationFailed("client_id is required.")
        return client_id, _safe_workbook_name(filename)

    def _persist_source_bytes(self, source, payload: bytes):
        """Archive original bytes after validation and before processing.

        Idempotent for the same client/source/SHA key. Sets storage_uri only
        after a successful put. Does not write blobs into PostgreSQL.
        """
        try:
            uri = self._source_store.put(
                client_id=source.client_id,
                source_file_id=source.id,
                sha256=source.sha256,
                payload=payload,
            )
            if source.storage_uri == uri:
                return source
            return self._ingest.set_storage_uri(source.id, uri)
        except PersistenceUnavailableError:
            raise
        except AuthorizationError:
            raise
        except Exception as exc:
            raise PersistenceUnavailableError("Source file could not be archived.") from exc

    def _remember_inflight(self, client_id: str, digest: str, batch_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight[(client_id, digest)] = batch_id

    def _forget_inflight(self, client_id: str, digest: str, batch_id: str) -> None:
        with self._in_flight_lock:
            if self._in_flight.get((client_id, digest)) == batch_id:
                self._in_flight.pop((client_id, digest), None)

    def _inflight_batch(
        self, client_id: str, digest: str, source
    ) -> BatchRecord | None:
        with self._in_flight_lock:
            batch_id = self._in_flight.get((client_id, digest))
        if batch_id is None:
            return None
        batch = self._ingest.get_batch(batch_id)
        if batch is None or batch.source_file_id != source.id:
            return None
        if batch.status in {"failed", "processed"}:
            return None
        return batch

    def _run_job(
        self,
        dest: Path,
        folder: Path,
        batch_id: str,
        client_id: str,
        digest: str,
        force: bool,
    ) -> None:
        reserved = self._ingest.get_batch(batch_id)
        try:
            with processing_rls(client_id), self._serialize:
                if reserved is None:
                    raise KeyError(f"unknown batch {batch_id}")
                body = self._ingest_and_transform(
                    dest,
                    client_id=client_id,
                    force=force,
                    reserved_batch=reserved,
                )
                self._completed[batch_id] = body
        except Exception as exc:
            self._fail_batch(batch_id, _public_text(str(exc)) or "Workbook could not be read.")
        finally:
            self._forget_inflight(client_id, digest, batch_id)
            _remove_upload_dir(folder)

    def _ingest_and_transform(
        self,
        dest: Path,
        *,
        client_id: str,
        force: bool,
        reserved_batch: BatchRecord | None,
    ) -> UploadResponse:
        try:
            result = ingest_workbook(
                dest,
                self._ingest,
                client_id=client_id,
                force=force,
                catalog=self._catalog,
                reserved_batch=reserved_batch,
            )
            stored = self._ingest.get_source_file(result.source_file.id) or result.source_file
            if stored.storage_uri is None:
                self._persist_source_bytes(stored, dest.read_bytes())
        except (OSError, ValueError, KeyError) as exc:
            if reserved_batch is not None:
                self._fail_batch(
                    reserved_batch.id,
                    _public_text(str(exc)) or "Workbook could not be read.",
                )
                failed = self._ingest.get_batch(reserved_batch.id)
                source = self._ingest.get_source_file(reserved_batch.source_file_id)
                if failed is not None and source is not None:
                    return self._response_from_records(source, failed, None, None, False)
            raise ValidationFailed("Workbook could not be read.") from exc
        transform_summary = None
        run = result.processing_run
        if not result.replayed and run is not None:
            transformed = run_transformation(
                self._ingest,
                self._facts,
                result.batch.id,
                processing_run_id=run.id,
                catalog=self._catalog,
            )
            run = transformed.processing_run
            transform_summary = UploadTransformSummary(
                transformed=transformed.transformed,
                inserted=transformed.inserted,
                restated=transformed.restated,
                unchanged=transformed.unchanged,
                rejected=transformed.rejected,
                status=run.status,
            )
            if self._qa is not None:
                evaluate_and_persist_run(
                    ingest_store=self._ingest,
                    fact_store=self._facts,
                    qa_store=self._qa,
                    run=run,
                    client_id=client_id,
                    rate_card_version_labels=transformed.version_labels.get("rate_card"),
                )
                run = self._ingest.get_processing_run(run.id) or run
        elif run is not None:
            run = self._ingest.get_processing_run(run.id) or run
        batch = self._ingest.get_batch(result.batch.id) or result.batch
        source = self._ingest.get_source_file(result.source_file.id) or result.source_file
        return self._response_from_records(
            source, batch, run, transform_summary, result.replayed
        )

    def reprocess(
        self,
        *,
        principal: Principal,
        batch_id: str,
        requested_client_id: str | None,
    ) -> tuple[ReprocessResponse, int]:
        """Create a new processing run for staged rows. Does not publish."""
        if not can_inspect(principal.role):
            raise AuthorizationError()
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            raise ValidationFailed("client_id is required.")
        batch = self._ingest.get_batch(batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        if batch.client_id != client_id:
            if principal.client_id and principal.client_id != batch.client_id:
                raise NotFoundError("Batch not found.")
            raise AuthorizationError("Not authorized to access this client.")
        if batch.status != "processed":
            raise ValidationFailed("Only a processed batch can be reprocessed.")
        if batch.row_count_staged <= 0:
            raise ValidationFailed("Batch has no staged rows to transform.")

        with self._in_flight_lock:
            existing_id = self._reprocess_in_flight.get(batch_id)
        if existing_id:
            existing = self._ingest.get_processing_run(existing_id)
            if existing is not None and existing.status in {"pending", "running"}:
                return self._reprocess_response(batch, existing, None), 202
        active = self._ingest.active_processing_run_for_batch(batch_id)
        if active is not None:
            return self._reprocess_response(batch, active, None), 202

        binding = bind_versions_for_day(
            batch.observed_day_min, catalog=self._catalog, client_id=client_id
        )
        run = self._ingest.add_processing_run(
            batch_id=batch.id,
            campaign_label_version_id=binding.campaign_label_version_id,
            template_label_version_id=binding.template_label_version_id,
            rate_card_version_id=binding.rate_card_version_id,
            label_group_version_id=binding.label_group_version_id,
            engine_version=ENGINE_VERSION,
        )
        with self._in_flight_lock:
            self._reprocess_in_flight[batch_id] = run.id
        accepted = self._reprocess_response(batch, run, None)
        if self._executor is None:
            self._run_reprocess(batch.id, run.id, client_id)
            completed = self._reprocess_completed.get(run.id)
            if completed is not None:
                return completed, 200
            return accepted, 202
        future = self._executor.submit(self._run_reprocess, batch.id, run.id, client_id)
        future.add_done_callback(
            lambda item, run_id=run.id: self._reprocess_future_done(run_id, item)
        )
        return accepted, 202

    def evaluate_run_qa(
        self,
        *,
        principal: Principal,
        processing_run_id: str,
        requested_client_id: str | None,
    ) -> ProcessingRunRecord:
        """Re-evaluate QA for an existing run. Does not reprocess facts or publish."""
        if not can_inspect(principal.role):
            raise AuthorizationError()
        if self._qa is None:
            raise ValidationFailed("QA store is not configured.")
        run = self._ingest.get_processing_run(processing_run_id)
        if run is None:
            raise NotFoundError("Processing run not found.")
        batch = self._ingest.get_batch(run.batch_id)
        if batch is None:
            raise NotFoundError("Processing run not found.")
        client_id = _resolve_client_id(principal, requested_client_id)
        if client_id is None:
            client_id = batch.client_id
        if batch.client_id != client_id:
            if principal.client_id and principal.client_id != batch.client_id:
                raise NotFoundError("Processing run not found.")
            raise AuthorizationError("Not authorized to access this client.")
        labels: set[str] | None = None
        packaged = packaged_label_for_id(run.rate_card_version_id)
        if packaged is not None:
            labels = {packaged}
        with processing_rls(batch.client_id):
            evaluate_and_persist_run(
                ingest_store=self._ingest,
                fact_store=self._facts,
                qa_store=self._qa,
                run=run,
                client_id=batch.client_id,
                rate_card_version_labels=labels,
            )
        return self._ingest.get_processing_run(run.id) or run

    def _run_reprocess(self, batch_id: str, run_id: str, client_id: str) -> None:
        try:
            with processing_rls(client_id), self._serialize:
                batch = self._ingest.get_batch(batch_id)
                run = self._ingest.get_processing_run(run_id)
                if batch is None or run is None:
                    raise KeyError(f"unknown batch or processing_run {batch_id}/{run_id}")
                transformed = run_transformation(
                    self._ingest,
                    self._facts,
                    batch_id,
                    processing_run_id=run_id,
                    catalog=self._catalog,
                    persist_rejections=False,
                )
                run = transformed.processing_run
                if self._qa is not None:
                    extra = [
                        RejectedEvidence(
                            source_row_number=item.source_row_number,
                            reason_code=item.reason_code,
                            reason_detail=item.reason_detail,
                        )
                        for item in transformed.rejections
                    ]
                    evaluate_and_persist_run(
                        ingest_store=self._ingest,
                        fact_store=self._facts,
                        qa_store=self._qa,
                        run=run,
                        client_id=client_id,
                        rate_card_version_labels=transformed.version_labels.get("rate_card"),
                        extra_rejected=extra,
                    )
                    run = self._ingest.get_processing_run(run_id) or run
                batch = self._ingest.get_batch(batch_id) or batch
                summary = UploadTransformSummary(
                    transformed=transformed.transformed,
                    inserted=transformed.inserted,
                    restated=transformed.restated,
                    unchanged=transformed.unchanged,
                    rejected=transformed.rejected,
                    status=run.status,
                )
                self._reprocess_completed[run_id] = self._reprocess_response(
                    batch, run, summary
                )
        except Exception as exc:
            log.exception("Reprocess failed for batch %s run %s", batch_id, run_id)
            reason = _public_text(str(exc)) or f"{type(exc).__name__} during reprocess"
            self._fail_reprocess_run(run_id, reason)
        finally:
            still = self._ingest.get_processing_run(run_id)
            if still is not None and still.status in {"pending", "running"}:
                self._fail_reprocess_run(run_id, "worker terminated before completion")
            with self._in_flight_lock:
                if self._reprocess_in_flight.get(batch_id) == run_id:
                    self._reprocess_in_flight.pop(batch_id, None)

    def _reprocess_future_done(self, run_id: str, future) -> None:
        try:
            error = future.exception()
        except Exception:
            self._fail_reprocess_run(run_id, "worker cancelled")
            return
        if error is not None:
            reason = _public_text(str(error)) or f"{type(error).__name__} during reprocess"
            self._fail_reprocess_run(run_id, reason)
        run = self._ingest.get_processing_run(run_id)
        if run is not None and run.status in {"pending", "running"}:
            self._fail_reprocess_run(run_id, "worker finished while run still running")

    def _fail_reprocess_run(self, run_id: str, reason: str | None = None) -> None:
        run = self._ingest.get_processing_run(run_id)
        if run is None:
            return
        now = datetime.now(tz=UTC)
        if run.status not in {"succeeded", "failed"}:
            run.status = "failed"
            run.finished_at = now
            if reason:
                run.error_summary = reason
            elif not run.error_summary:
                run.error_summary = WORKER_STALL_REASON
            self._ingest.save_processing_run(run)
        elif run.status == "failed" and reason and not run.error_summary:
            run.error_summary = reason
            self._ingest.save_processing_run(run)
        batch = self._ingest.get_batch(run.batch_id)
        if batch is not None:
            self._reprocess_completed[run_id] = self._reprocess_response(batch, run, None)

    def _reprocess_response(
        self,
        batch: BatchRecord,
        run: ProcessingRunRecord,
        transform: UploadTransformSummary | None,
    ) -> ReprocessResponse:
        logic_label, labels_label = self._catalog_labels_for_run(run, batch.client_id)
        return ReprocessResponse(
            batch_id=batch.id,
            processing_run=processing_run_to_response(run),
            logic_version_label=logic_label,
            labels_version_label=labels_label,
            published=False,
            accepted=True,
            transform=transform,
        )

    def _catalog_labels_for_run(
        self, run: ProcessingRunRecord, client_id: str | None
    ) -> tuple[str | None, str | None]:
        logic = packaged_label_for_id(run.campaign_label_version_id)
        labels = packaged_label_for_id(run.label_group_version_id)
        if self._catalog and client_id:
            if run.campaign_label_version_id:
                overlay = self._catalog.get_for_client(client_id, run.campaign_label_version_id)
                if overlay is not None:
                    logic = overlay.version_label
            if run.label_group_version_id:
                overlay = self._catalog.get_for_client(client_id, run.label_group_version_id)
                if overlay is not None:
                    labels = overlay.version_label
        return logic, labels

    def _fail_batch(self, batch_id: str, summary: str) -> None:
        batch = self._ingest.get_batch(batch_id)
        if batch is None:
            return
        now = datetime.now(tz=UTC)
        batch.status = "failed"
        batch.error_summary = summary
        batch.completed_at = now
        self._ingest.save_batch(batch)
        run = self._ingest.processing_run_for_batch(batch_id)
        if run is not None and run.status not in {"succeeded", "failed"}:
            run.status = "failed"
            run.finished_at = now
            self._ingest.save_processing_run(run)
        source = self._ingest.get_source_file(batch.source_file_id)
        if source is not None:
            self._completed[batch_id] = self._response_from_records(
                source, batch, run, None, False
            )

    def _replay_response(self, source, batch: BatchRecord) -> UploadResponse:
        run = self._ingest.processing_run_for_batch(batch.id)
        if run is not None:
            run = self._ingest.get_processing_run(run.id) or run
        return self._response_from_records(source, batch, run, None, True)

    def _accepted_response(self, source, batch: BatchRecord) -> UploadResponse:
        return self._response_from_records(source, batch, None, None, False)

    def _response_from_records(
        self,
        source,
        batch: BatchRecord,
        run,
        transform_summary: UploadTransformSummary | None,
        replayed: bool,
    ) -> UploadResponse:
        rejections = [
            UploadRejection(
                source_row_number=item.source_row_number,
                reason_code=item.reason_code,
                reason_detail=_public_text(item.reason_detail),
            )
            for item in self._ingest.rejected_for_batch(batch.id)[:MAX_REJECTION_ITEMS]
        ]
        batch_body = batch_to_response(batch)
        if batch_body.error_summary:
            batch_body = batch_body.model_copy(
                update={"error_summary": _public_text(batch_body.error_summary)}
            )
        return UploadResponse(
            source_file_id=source.id,
            original_filename=source.original_filename,
            sha256=source.sha256,
            byte_size=source.byte_size,
            client_id=source.client_id,
            replayed=replayed,
            published=False,
            accepted=True,
            batch=batch_body,
            processing_run=processing_run_to_response(run) if run is not None else None,
            transform=transform_summary,
            rejections=rejections,
        )


def _upload_sort_key(item: tuple[str, bytes, str]) -> tuple[str, str]:
    filename, _payload, digest = item
    return (filename.lower(), digest)


def _group_response(
    items: list[UploadResponse], *, duration_ms: int | None
) -> UploadGroupResponse:
    staged = sum(item.batch.row_count_staged for item in items)
    facts = sum(
        item.transform.transformed if item.transform is not None else 0 for item in items
    )
    return UploadGroupResponse(
        client_id=items[0].client_id,
        file_count=len(items),
        replayed=all(item.replayed for item in items),
        published=False,
        accepted=True,
        duration_ms=duration_ms,
        staged_row_count=staged,
        fact_count=facts,
        items=items,
    )


def _safe_workbook_name(filename: str | None) -> str:
    if filename is None or not str(filename).strip():
        raise ValidationFailed("Workbook filename is required.")
    raw = str(filename)
    if "\x00" in raw:
        raise ValidationFailed("Workbook filename is invalid.")
    normalized = raw.replace("\\", "/")
    if ".." in normalized or "/" in normalized or ":" in raw:
        raise ValidationFailed("Workbook filename is invalid.")
    name = Path(raw).name
    if not name.lower().endswith(".xlsx"):
        raise ValidationFailed("Only .xlsx workbooks are accepted.")
    stem = name[:-5]
    cleaned = SAFE_STEM.sub("_", stem).strip("._")
    if not cleaned:
        return "workbook.xlsx"
    return f"{cleaned}.xlsx"


def _assert_xlsx_payload(payload: bytes) -> None:
    if not payload:
        raise ValidationFailed("Workbook file is empty.")
    if payload[:2] != XLSX_MAGIC:
        raise ValidationFailed("Only .xlsx workbooks are accepted.")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            uncompressed = sum(info.file_size for info in archive.infolist())
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValidationFailed("Workbook could not be read.") from exc
    if uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise ValidationFailed("Workbook could not be read.")
    if "[Content_Types].xml" not in names and not any(name.startswith("xl/") for name in names):
        raise ValidationFailed("Only .xlsx workbooks are accepted.")


def _public_text(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if "traceback" in lowered:
        return "Workbook could not be read."
    temp = tempfile.gettempdir().replace("\\", "/").lower()
    normalized = value.replace("\\", "/").lower()
    if temp and temp in normalized:
        return "Workbook could not be read."
    if "dfip-upload-" in lowered:
        return "Workbook could not be read."
    if "dfip-source" in lowered:
        return "Source file could not be archived."
    return value


def _remove_upload_dir(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
