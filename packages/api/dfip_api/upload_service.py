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
from dfip_core.ingest.progress import (
    ACTIVE_STAGES,
    STAGE_CANCELLED,
    STAGE_CANCELLING,
    STAGE_INGESTING,
    STAGE_PROCESSING,
    STAGE_SUCCEEDED,
    STAGE_VALIDATING,
    ProcessingCancelled,
    ProcessingDurationError,
    progress_percent,
)
from dfip_core.ingest.store import BatchRecord, ProcessingRunRecord
from dfip_core.transform.engine import ENGINE_VERSION, run_transformation
from dfip_core.transform.labels import packaged_label_for_id
from dfip_core.transform.ports import FactStore

from dfip_api.auth import Principal
from dfip_api.client_directory import ClientDirectory
from dfip_api.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PayloadTooLarge,
    PersistenceUnavailableError,
    ValidationFailed,
)
from dfip_api.lifecycle import require_company_active
from dfip_api.ports import PublicationStore
from dfip_api.publication_service import _resolve_client_id
from dfip_api.qa_store import QaFindingStore
from dfip_api.qa_workflow import evaluate_and_persist_run, processing_rls
from dfip_api.recovery import (
    ARCHIVE_MISSING_MESSAGE,
    AUTO_RESUME_REASONS,
    WORKER_INCOMPLETE_REASON,
    ZOMBIE_RUN_REASON,
)
from dfip_api.roles import can_inspect
from dfip_api.schemas import (
    BatchDeleteResponse,
    BatchResponse,
    ProcessingRunResponse,
    ReprocessResponse,
    UploadGroupResponse,
    UploadRejection,
    UploadResponse,
    UploadTransformSummary,
)
from dfip_api.service import (
    batch_to_response,
    processing_run_to_response,
)
from dfip_api.source_storage import InMemorySourceObjectStore, SourceObjectStore

XLSX_MAGIC = b"PK"
SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
# Claimed ZIP uncompressed total. 100 MiB rejected legitimate monthly
# Web-Engage Raw workbooks (July–Sept ~160–177 MiB; Apr–May ~294 MiB).
# October Prod is ~99 MiB and was the only month under the old cap.
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ZIP_MEMBERS = 1024
READ_CHUNK = 64 * 1024
UNCOMPRESSED_TOO_LARGE = "Workbook uncompressed size exceeds the allowed limit."
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
        client_directory: ClientDirectory | None = None,
        publication_store: PublicationStore | None = None,
    ) -> None:
        self._ingest = ingest_store
        self._facts = fact_store
        self._settings = settings
        self._catalog = catalog_store
        self._qa = qa_store
        self._executor = executor
        self._source_store = source_store or InMemorySourceObjectStore(settings.dfip_storage_bucket)
        self._clients = client_directory
        self._publications = publication_store
        self._serialize = threading.Lock()
        self._in_flight_lock = threading.Lock()
        self._in_flight: dict[tuple[str, str], str] = {}
        self._completed: dict[str, UploadResponse] = {}
        self._reprocess_in_flight: dict[str, str] = {}
        self._reprocess_completed: dict[str, ReprocessResponse] = {}
        self._stages: dict[str, str] = {}

    def set_executor(self, executor: ThreadPoolExecutor | None) -> None:
        self._executor = executor

    def is_busy(self) -> bool:
        """Process-local worker occupancy. Not durable job health."""
        with self._in_flight_lock:
            if self._in_flight or self._reprocess_in_flight:
                return True
        executor = self._executor
        if executor is None:
            return False
        queue = getattr(executor, "_work_queue", None)
        if queue is None:
            return False
        try:
            return int(queue.qsize()) > 0
        except Exception:
            return False

    def has_in_flight_for_client(self, client_id: str) -> bool:
        """True when this process is ingesting, processing, or reprocessing the tenant."""
        with self._in_flight_lock:
            if any(cid == client_id for cid, _digest in self._in_flight):
                return True
            for batch_id, run_id in self._reprocess_in_flight.items():
                batch = self._ingest.get_batch(batch_id)
                if batch is not None and batch.client_id == client_id:
                    return True
                run = self._ingest.get_processing_run(run_id)
                if run is not None and run.client_id == client_id:
                    return True
        return False

    def _set_stage(self, batch_id: str, stage: str) -> None:
        self._persist_progress(batch_id, stage)

    def _persist_progress(
        self,
        batch_id: str,
        stage: str,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        self._stages[batch_id] = stage
        saver = getattr(self._ingest, "save_batch_progress", None)
        if saver is not None:
            saver(batch_id, stage=stage, current=current, total=total, message=message)
        max_s = int(getattr(self._settings, "dfip_upload_max_processing_seconds", 0) or 0)
        if max_s <= 0:
            return
        batch = self._ingest.get_batch(batch_id)
        if batch is None or batch.created_at is None:
            return
        started = batch.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed = (datetime.now(tz=UTC) - started).total_seconds()
        if elapsed > max_s:
            raise ProcessingDurationError(max_s)

    def _forget_reprocess(self, batch_id: str, run_id: str | None = None) -> None:
        with self._in_flight_lock:
            current = self._reprocess_in_flight.get(batch_id)
            if current is None:
                return
            if run_id is None or current == run_id:
                self._reprocess_in_flight.pop(batch_id, None)

    def _batch_is_locally_running(self, batch_id: str) -> bool:
        with self._in_flight_lock:
            if batch_id in self._in_flight.values():
                return True
            run_id = self._reprocess_in_flight.get(batch_id)
        if not run_id:
            return False
        run = self._ingest.get_processing_run(run_id)
        if run is not None and run.status in {"pending", "running"}:
            return True
        self._forget_reprocess(batch_id, run_id)
        return False

    def _fail_zombie_run(self, run: ProcessingRunRecord) -> None:
        now = datetime.now(tz=UTC)
        if run.status not in {"pending", "running"}:
            return
        run.status = "failed"
        run.finished_at = now
        run.error_summary = ZOMBIE_RUN_REASON
        self._ingest.save_processing_run(run)

    def resume_orphaned_work(self) -> int:
        """Re-queue recoverable batches that have no live worker.

        Does not retry genuine validation/transform failures. Idempotent.
        """
        started = 0
        try:
            batches = self._ingest.list_auto_resume_batches(
                limit=50, resume_reasons=tuple(AUTO_RESUME_REASONS)
            )
        except Exception:
            log.exception("auto-resume inventory failed")
            return 0
        for batch in batches:
            if self._batch_is_locally_running(batch.id):
                continue
            try:
                if self._resume_one_batch(batch):
                    started += 1
            except Exception:
                log.exception("auto-resume skipped batch_id=%s", batch.id)
        if started:
            log.info("auto-resume queued=%s", started)
        return started

    def _resume_one_batch(self, batch: BatchRecord) -> bool:
        if batch.cancel_requested or batch.status == "cancelled":
            return False
        source = self._ingest.get_source_file(batch.source_file_id)
        if source is None:
            return False
        active = self._ingest.active_processing_run_for_batch(batch.id)
        if active is not None:
            if self._batch_is_locally_running(batch.id):
                return False
            self._fail_zombie_run(active)
        mode = self._recovery_mode(batch)
        if mode == "reject":
            return False
        if mode == "archive":
            try:
                self._read_archive_bytes(source)
            except ValidationFailed:
                return False
            self._start_archive_recovery(source, batch, batch.client_id, source.sha256)
            return True
        self._start_staged_run(batch, batch.client_id)
        return True

    def enrich_batch(self, body: BatchResponse, *, resume: bool = False) -> BatchResponse:
        if resume:
            self._maybe_resume_batch(body.batch_id)
        batch = self._ingest.get_batch(body.batch_id) or None
        run = self._ingest.active_processing_run_for_batch(body.batch_id)
        if run is None:
            run = self._ingest.processing_run_for_batch(body.batch_id)
        if batch is not None:
            body = batch_to_response(batch, run)
            if body.error_summary:
                body = body.model_copy(update={"error_summary": _public_text(body.error_summary)})
        local = self._stages.get(body.batch_id)
        stuck = False
        cancelling = body.status == "cancelled" or body.stage in {
            STAGE_CANCELLING,
            STAGE_CANCELLED,
        }
        if batch is not None and (batch.cancel_requested or batch.status == "cancelled"):
            cancelling = True
        if not cancelling and (
            body.status in {"received", "staged"}
            or (run is not None and run.status in {"pending", "running"})
        ):
            stuck = not self._batch_is_locally_running(body.batch_id)
        stage = body.stage
        running = self._batch_is_locally_running(body.batch_id)
        if (
            local in ACTIVE_STAGES
            and running
            and stage
            in {
                None,
                "received",
                "pending",
                "succeeded",
                "processed",
            }
        ):
            stage = local
        elif local in ACTIVE_STAGES and stage in {None, "received", "pending"}:
            stage = local
        elif stage in {None, "received"} and local:
            stage = local
        progress_at = body.progress_at
        if run is not None and run.progress_at is not None:
            if progress_at is None or run.progress_at >= progress_at:
                progress_at = run.progress_at
        percent = progress_percent(stage, body.progress_current, body.progress_total)
        return body.model_copy(
            update={
                "stage": stage,
                "stuck": stuck,
                "progress_at": progress_at,
                "progress_percent": percent,
            }
        )

    def enrich_run(self, body: ProcessingRunResponse) -> ProcessingRunResponse:
        batch = self._ingest.get_batch(body.batch_id)
        run = self._ingest.get_processing_run(body.processing_run_id)
        if run is not None:
            body = processing_run_to_response(run, batch)
            if body.error_summary:
                body = body.model_copy(update={"error_summary": _public_text(body.error_summary)})
        local = self._stages.get(body.batch_id)
        stuck = False
        if run is not None and run.status in {"pending", "running"}:
            stuck = not self._batch_is_locally_running(body.batch_id)
        stage = local or body.stage
        return body.model_copy(update={"stage": stage, "stuck": stuck})

    def _maybe_resume_batch(self, batch_id: str) -> None:
        if self._batch_is_locally_running(batch_id):
            return
        batch = self._ingest.get_batch(batch_id)
        if batch is None:
            return
        if batch.status not in {"received", "staged", "failed"}:
            return
        if batch.cancel_requested:
            return
        if batch.status == "failed" and (batch.error_summary or "") not in AUTO_RESUME_REASONS:
            run = self._ingest.processing_run_for_batch(batch.id)
            if run is None or (run.error_summary or "") not in AUTO_RESUME_REASONS:
                return
        try:
            self._resume_one_batch(batch)
        except Exception:
            log.exception("poll-resume skipped batch_id=%s", batch_id)

    def completed_result(self, batch_id: str) -> UploadResponse | None:
        return self._completed.get(batch_id)

    def reprocess_result(self, processing_run_id: str) -> ReprocessResponse | None:
        return self._reprocess_completed.get(processing_run_id)

    async def read_upload(self, upload, remaining_total_bytes: int | None = None) -> bytes:
        """Read the multipart file with a hard byte cap. Does not keep a disk path."""
        limit = self._settings.dfip_upload_max_bytes
        if remaining_total_bytes is not None and remaining_total_bytes <= 0:
            raise PayloadTooLarge("Upload request exceeds the maximum allowed size.")
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
            if remaining_total_bytes is not None and total > remaining_total_bytes:
                raise PayloadTooLarge("Upload request exceeds the maximum allowed size.")
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
        client_id, safe_name = self._authorize(principal, filename, requested_client_id)
        _assert_xlsx_payload(payload)
        digest = hashlib.sha256(payload).hexdigest()
        existing = self._ingest.get_source_file_by_sha256(digest, client_id)
        if existing is not None and not force:
            completed = self._ingest.processed_batch_for_file(existing.id)
            if completed is not None:
                existing = self._persist_source_bytes(existing, payload)
                body = self._replay_response(existing, completed)
                self._completed[completed.id] = body
                return body, 200
            inflight = self._inflight_batch(client_id, digest, existing)
            if inflight is not None:
                self._persist_source_bytes(existing, payload)
                return self._accepted_response(existing, inflight), 202
            recoverable = self._ingest.recoverable_batch_for_file(existing.id)
            if recoverable is not None:
                existing = self._persist_source_bytes(existing, payload)
                return self._recover_batch(
                    principal=principal,
                    batch=recoverable,
                    client_id=client_id,
                    digest=digest,
                    source=existing,
                )

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
        future = self._executor.submit(
            self._run_job,
            dest,
            folder,
            batch.id,
            client_id,
            digest,
            force,
        )
        future.add_done_callback(
            lambda item, batch_id=batch.id: self._upload_future_done(batch_id, item)
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
        client_id, safe_name = self._authorize(principal, filename, requested_client_id)
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
        require_company_active(self._clients, client_id)
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

    def _inflight_batch(self, client_id: str, digest: str, source) -> BatchRecord | None:
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

    def _recovery_mode(self, batch: BatchRecord) -> str:
        if batch.status == "cancelled" or batch.cancel_requested:
            return "reject"
        if batch.status == "processed":
            return "staged_run" if batch.row_count_staged > 0 else "reject"
        if batch.status == "staged":
            return "staged_run" if batch.row_count_staged > 0 else "archive"
        if batch.status == "received":
            return "archive"
        if batch.status == "failed":
            return "staged_run" if batch.row_count_staged > 0 else "archive"
        return "reject"

    def _read_archive_bytes(self, source) -> bytes:
        try:
            return self._source_store.get(
                client_id=source.client_id,
                source_file_id=source.id,
                sha256=source.sha256,
            )
        except AuthorizationError as exc:
            raise ValidationFailed(ARCHIVE_MISSING_MESSAGE) from exc
        except PersistenceUnavailableError:
            raise
        except Exception as exc:
            raise ValidationFailed(ARCHIVE_MISSING_MESSAGE) from exc

    def _recover_batch(
        self,
        *,
        principal: Principal,
        batch: BatchRecord,
        client_id: str,
        digest: str,
        source,
    ) -> tuple[UploadResponse, int]:
        del principal
        mode = self._recovery_mode(batch)
        if mode == "reject":
            raise ValidationFailed("Batch cannot be recovered.")
        inflight = self._inflight_batch(client_id, digest, source)
        if inflight is not None:
            return self._accepted_response(source, inflight), 202
        if mode == "archive":
            return self._start_archive_recovery(source, batch, client_id, digest)
        body, status = self._start_staged_run(batch, client_id)
        accepted = self._accepted_response(source, batch)
        return accepted.model_copy(update={"processing_run": body.processing_run}), status

    def _start_archive_recovery(
        self, source, batch: BatchRecord, client_id: str, digest: str
    ) -> tuple[UploadResponse, int]:
        payload = self._read_archive_bytes(source)
        folder = Path(tempfile.mkdtemp(prefix="dfip-upload-"))
        dest = folder / _safe_workbook_name(source.original_filename)
        dest.write_bytes(payload)
        accepted = self._accepted_response(source, batch)
        self._remember_inflight(client_id, digest, batch.id)
        if self._executor is None:
            self._run_archive_job(dest, folder, batch.id, client_id, digest)
            completed = self._completed.get(batch.id)
            if completed is not None:
                return completed, 200 if completed.replayed else 201
            return accepted, 202
        future = self._executor.submit(
            self._run_archive_job, dest, folder, batch.id, client_id, digest
        )
        future.add_done_callback(
            lambda item, batch_id=batch.id: self._upload_future_done(batch_id, item)
        )
        return accepted, 202

    def _start_staged_run(
        self, batch: BatchRecord, client_id: str
    ) -> tuple[ReprocessResponse, int]:
        with self._in_flight_lock:
            existing_id = self._reprocess_in_flight.get(batch.id)
        if existing_id:
            existing = self._ingest.get_processing_run(existing_id)
            if existing is not None and existing.status in {"pending", "running"}:
                return self._reprocess_response(batch, existing, None), 202
        active = self._ingest.active_processing_run_for_batch(batch.id)
        if active is not None and self._batch_is_locally_running(batch.id):
            return self._reprocess_response(batch, active, None), 202
        if active is not None:
            self._fail_zombie_run(active)
        if batch.row_count_staged <= 0:
            raise ValidationFailed("Batch has no staged rows to transform.")
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
            self._reprocess_in_flight[batch.id] = run.id
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

    def _run_archive_job(
        self,
        dest: Path,
        folder: Path,
        batch_id: str,
        client_id: str,
        digest: str,
    ) -> None:
        reserved = self._ingest.get_batch(batch_id)
        try:
            with processing_rls(client_id), self._serialize:
                if reserved is None:
                    raise KeyError(f"unknown batch {batch_id}")
                self._ingest.reset_staging_for_batch(batch_id)
                reserved = self._ingest.get_batch(batch_id) or reserved
                body = self._ingest_and_transform(
                    dest,
                    client_id=client_id,
                    force=True,
                    reserved_batch=reserved,
                )
                self._completed[batch_id] = body
        except ProcessingCancelled:
            self._finalize_cancel(batch_id)
        except Exception as exc:
            self._fail_batch(batch_id, _public_text(str(exc)) or "Workbook could not be read.")
        finally:
            self._finalize_incomplete_job(batch_id)
            self._forget_inflight(client_id, digest, batch_id)
            self._forget_reprocess(batch_id)
            _remove_upload_dir(folder)

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
        except ProcessingCancelled:
            self._finalize_cancel(batch_id)
        except Exception as exc:
            self._fail_batch(batch_id, _public_text(str(exc)) or "Workbook could not be read.")
        finally:
            self._finalize_incomplete_job(batch_id)
            self._forget_inflight(client_id, digest, batch_id)
            _remove_upload_dir(folder)

    def _finalize_incomplete_job(self, batch_id: str) -> None:
        batch = self._ingest.get_batch(batch_id)
        if batch is not None and (batch.status == "cancelled" or batch.cancel_requested):
            if batch.status != "cancelled":
                self._finalize_cancel(batch_id)
            return
        run = self._ingest.active_processing_run_for_batch(batch_id)
        if run is not None and run.status in {"pending", "running"}:
            now = datetime.now(tz=UTC)
            run.status = "failed"
            run.finished_at = now
            if not run.error_summary:
                run.error_summary = WORKER_INCOMPLETE_REASON
            self._ingest.save_processing_run(run)
        batch = self._ingest.get_batch(batch_id)
        if batch is not None and batch.status not in {
            "processed",
            "failed",
            "staged",
            "cancelled",
        }:
            self._fail_batch(batch_id, WORKER_INCOMPLETE_REASON)

    def _upload_future_done(self, batch_id: str, future) -> None:
        try:
            error = future.exception()
        except Exception:
            self._finalize_incomplete_job(batch_id)
            return
        if error is not None:
            if isinstance(error, ProcessingCancelled):
                self._finalize_cancel(batch_id)
            else:
                self._fail_batch(batch_id, _public_text(str(error)) or WORKER_INCOMPLETE_REASON)
        self._finalize_incomplete_job(batch_id)

    def _progress_callback(
        self,
        batch_id: str,
        heartbeat_stop: threading.Event | None,
        last_stage: dict[str, str] | None = None,
    ):
        gate = last_stage if last_stage is not None else {}

        def on_progress(
            stage: str,
            current: int | None,
            total: int | None,
            message: str | None,
        ) -> None:
            gate["name"] = stage
            if heartbeat_stop is not None and stage != STAGE_INGESTING:
                heartbeat_stop.set()
            self._persist_progress(batch_id, stage, current=current, total=total, message=message)

        return on_progress

    def _ingest_and_transform(
        self,
        dest: Path,
        *,
        client_id: str,
        force: bool,
        reserved_batch: BatchRecord | None,
    ) -> UploadResponse:
        heartbeat_stop: threading.Event | None = None
        batch_id = reserved_batch.id if reserved_batch is not None else None
        on_progress = None
        max_facts = int(getattr(self._settings, "dfip_upload_max_facts", 0) or 0) or None
        try:
            if reserved_batch is not None:
                heartbeat_stop = threading.Event()
                last_stage = {"name": STAGE_INGESTING}
                on_progress = self._progress_callback(reserved_batch.id, heartbeat_stop, last_stage)
                self._persist_progress(
                    reserved_batch.id, STAGE_INGESTING, message="Reading workbook..."
                )

                def _heartbeat() -> None:
                    while not heartbeat_stop.wait(2.0):
                        if last_stage.get("name") != STAGE_INGESTING:
                            return
                        try:
                            self._persist_progress(
                                reserved_batch.id,
                                STAGE_INGESTING,
                                message="Reading workbook...",
                            )
                        except ProcessingCancelled:
                            heartbeat_stop.set()
                            return
                        except ProcessingDurationError:
                            heartbeat_stop.set()
                            return

                threading.Thread(
                    target=_heartbeat, name="dfip-ingest-heartbeat", daemon=True
                ).start()
            result = ingest_workbook(
                dest,
                self._ingest,
                client_id=client_id,
                force=force,
                engine_version=ENGINE_VERSION,
                catalog=self._catalog,
                reserved_batch=reserved_batch,
                on_progress=on_progress,
                max_facts=max_facts,
            )
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            stored = self._ingest.get_source_file(result.source_file.id) or result.source_file
            if stored.storage_uri is None:
                self._persist_source_bytes(stored, dest.read_bytes())
        except ProcessingDurationError as exc:
            if batch_id is not None:
                self._fail_batch(batch_id, str(exc))
                failed = self._ingest.get_batch(batch_id)
                source = (
                    self._ingest.get_source_file(reserved_batch.source_file_id)
                    if reserved_batch is not None
                    else None
                )
                if failed is not None and source is not None:
                    return self._response_from_records(source, failed, None, None, False)
            raise ValidationFailed(str(exc)) from exc
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
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
        transform_summary = None
        run = result.processing_run
        if not result.replayed and run is not None:
            self._persist_progress(
                result.batch.id,
                STAGE_PROCESSING,
                message="Transforming campaign/day facts...",
            )
            transformed = run_transformation(
                self._ingest,
                self._facts,
                result.batch.id,
                processing_run_id=run.id,
                catalog=self._catalog,
                on_progress=self._progress_callback(result.batch.id, None),
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
                self._persist_progress(
                    result.batch.id,
                    STAGE_VALIDATING,
                    message="Running QA checks...",
                )
                evaluate_and_persist_run(
                    ingest_store=self._ingest,
                    fact_store=self._facts,
                    qa_store=self._qa,
                    run=run,
                    client_id=client_id,
                    rate_card_version_labels=transformed.version_labels.get("rate_card"),
                    on_progress=self._progress_callback(result.batch.id, None),
                )
                run = self._ingest.get_processing_run(run.id) or run
        elif run is not None:
            run = self._ingest.get_processing_run(run.id) or run
        batch = self._ingest.get_batch(result.batch.id) or result.batch
        source = self._ingest.get_source_file(result.source_file.id) or result.source_file
        if batch.status == "processed" or (run is not None and run.status == "succeeded"):
            self._set_stage(batch.id, STAGE_SUCCEEDED)
        elif batch.status == "failed" or (run is not None and run.status == "failed"):
            self._set_stage(batch.id, "failed")
        return self._response_from_records(source, batch, run, transform_summary, result.replayed)

    def reprocess(
        self,
        *,
        principal: Principal,
        batch_id: str,
        requested_client_id: str | None,
    ) -> tuple[ReprocessResponse, int]:
        """Retry or re-process an existing batch. Does not publish.

        Staged/processed batches with staged rows create a new processing_run.
        Received or failed-without-staging recover the same batch from the
        source archive. Does not resume a pending/running run in place.
        """
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
        mode = self._recovery_mode(batch)
        if mode == "reject":
            raise ValidationFailed("Batch cannot be recovered.")
        active = self._ingest.active_processing_run_for_batch(batch.id)
        if active is not None and self._batch_is_locally_running(batch.id):
            return self._reprocess_response(batch, active, None), 202
        if active is not None:
            self._fail_zombie_run(active)
        require_company_active(self._clients, client_id)
        if mode == "archive":
            source = self._ingest.get_source_file(batch.source_file_id)
            if source is None:
                raise NotFoundError("Batch not found.")
            self._read_archive_bytes(source)
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
                self._reprocess_in_flight[batch.id] = run.id
            body, status = self._start_archive_recovery(source, batch, client_id, source.sha256)
            latest = self._ingest.get_processing_run(run.id) or run
            refreshed = self._ingest.get_batch(batch.id) or batch
            return self._reprocess_response(refreshed, latest, body.transform), status
        return self._start_staged_run(batch, client_id)

    def _owned_batch(
        self,
        *,
        principal: Principal,
        batch_id: str,
        requested_client_id: str | None,
    ) -> tuple[BatchRecord, str]:
        if not can_inspect(principal.role):
            raise AuthorizationError()
        client_id = _resolve_client_id(principal, requested_client_id)
        batch = self._ingest.get_batch(batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        if client_id is None:
            client_id = batch.client_id
        if batch.client_id != client_id:
            if principal.client_id and principal.client_id != batch.client_id:
                raise NotFoundError("Batch not found.")
            raise AuthorizationError("Not authorized to access this client.")
        if principal.client_id and principal.client_id != batch.client_id:
            raise NotFoundError("Batch not found.")
        return batch, batch.client_id

    def _run_ids_for_batch(self, batch_id: str) -> list[str]:
        lister = getattr(self._ingest, "list_processing_runs_for_batch", None)
        if lister is not None:
            return [run.id for run in lister(batch_id)]
        run = self._ingest.processing_run_for_batch(batch_id)
        return [run.id] if run is not None else []

    def _publication_exists(self, run_ids: list[str]) -> bool:
        store = self._publications
        if store is None or not run_ids:
            return False
        checker = getattr(store, "has_publication_for_runs", None)
        if checker is None:
            return False
        return bool(checker(run_ids))

    def _revert_runs(self, run_ids: list[str]) -> None:
        for run_id in run_ids:
            self._facts.revert_run(run_id)
            if self._qa is not None:
                self._qa.replace_for_run(run_id, [])

    def _finalize_cancel(self, batch_id: str) -> None:
        batch = self._ingest.get_batch(batch_id)
        if batch is None:
            return
        if batch.status == "cancelled":
            self._set_stage(batch_id, STAGE_CANCELLED)
            return
        now = datetime.now(tz=UTC)
        runs = []
        lister = getattr(self._ingest, "list_processing_runs_for_batch", None)
        if lister is not None:
            runs = list(lister(batch_id))
        else:
            run = self._ingest.processing_run_for_batch(batch_id)
            if run is not None:
                runs = [run]
        self._revert_runs([run.id for run in runs])
        for run in runs:
            if run.status != "cancelled":
                run.status = "cancelled"
                run.finished_at = now
                run.error_summary = "Processing cancelled."
                self._ingest.save_processing_run(run)
        discard = getattr(self._ingest, "discard_staging", None)
        if discard is not None:
            discard(batch_id)
        batch = self._ingest.get_batch(batch_id) or batch
        batch.status = "cancelled"
        batch.cancel_requested = True
        batch.completed_at = now
        batch.error_summary = "Processing cancelled."
        batch.row_count_staged = 0
        self._ingest.save_batch(batch)
        saver = getattr(self._ingest, "save_batch_progress", None)
        if saver is not None:
            saver(
                batch_id,
                stage=STAGE_CANCELLED,
                message="Cancelled",
                current=None,
                total=None,
            )
        self._set_stage(batch_id, STAGE_CANCELLED)
        source = self._ingest.get_source_file(batch.source_file_id)
        latest = runs[0] if runs else None
        if source is not None:
            self._completed[batch_id] = self._response_from_records(
                source, batch, latest, None, False
            )

    def cancel(
        self,
        *,
        principal: Principal,
        batch_id: str,
        requested_client_id: str | None,
    ) -> BatchResponse:
        """Cooperative stop. Discards temporary work; never publishes."""
        batch, client_id = self._owned_batch(
            principal=principal,
            batch_id=batch_id,
            requested_client_id=requested_client_id,
        )
        if batch.status == "cancelled":
            return self.enrich_batch(batch_to_response(batch), resume=False)
        if self._publication_exists(self._run_ids_for_batch(batch.id)):
            raise ConflictError("A published upload cannot be cancelled.")
        if batch.status == "processed":
            raise ConflictError(
                "A processed upload cannot be cancelled. Delete it if it was never published."
            )
        if batch.status == "failed":
            raise ConflictError("A failed upload cannot be cancelled. Delete it instead.")
        running = self._batch_is_locally_running(batch.id)
        requester = getattr(self._ingest, "request_cancel", None)
        if requester is None:
            raise PersistenceUnavailableError("Cancellation is not available.")
        requester(batch.id)
        self._set_stage(batch.id, STAGE_CANCELLING)
        if not running:
            with processing_rls(client_id):
                self._finalize_cancel(batch.id)
        refreshed = self._ingest.get_batch(batch.id) or batch
        run = self._ingest.processing_run_for_batch(batch.id)
        return self.enrich_batch(batch_to_response(refreshed, run), resume=False)

    def delete_batch(
        self,
        *,
        principal: Principal,
        batch_id: str,
        requested_client_id: str | None,
    ) -> BatchDeleteResponse:
        """Remove a non-authoritative upload. Published publications are blocked."""
        batch, client_id = self._owned_batch(
            principal=principal,
            batch_id=batch_id,
            requested_client_id=requested_client_id,
        )
        if self._batch_is_locally_running(batch.id):
            raise ConflictError("Cancel processing before deleting this upload.")
        if batch.status not in {"failed", "cancelled"} and (
            batch.cancel_requested or (batch.progress_stage or "") == STAGE_CANCELLING
        ):
            raise ConflictError("Wait until cancellation finishes before deleting.")
        run_ids = self._run_ids_for_batch(batch.id)
        if self._publication_exists(run_ids):
            raise ConflictError("Published uploads cannot be deleted through batch Delete.")
        if batch.status not in {
            "failed",
            "cancelled",
            "received",
            "staged",
            "validated",
            "processed",
        }:
            raise ConflictError("This upload cannot be deleted in its current state.")
        source = self._ingest.get_source_file(batch.source_file_id)
        with processing_rls(client_id):
            self._revert_runs(run_ids)
            deleter = getattr(self._ingest, "delete_batch_record", None)
            if deleter is None:
                raise PersistenceUnavailableError("Delete is not available.")
            deleted_source_id = deleter(batch.id)
        if deleted_source_id and source is not None:
            try:
                self._source_store.delete(
                    client_id=source.client_id,
                    source_file_id=source.id,
                    sha256=source.sha256,
                )
            except Exception:
                log.exception("source archive delete failed for batch_id=%s", batch.id)
        self._stages.pop(batch.id, None)
        self._completed.pop(batch.id, None)
        with self._in_flight_lock:
            self._reprocess_in_flight.pop(batch.id, None)
        return BatchDeleteResponse(batch_id=batch.id, deleted=True)

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
        require_company_active(self._clients, batch.client_id)
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
                self._set_stage(batch_id, "processing")
                progress = self._progress_callback(batch_id, None)
                transformed = run_transformation(
                    self._ingest,
                    self._facts,
                    batch_id,
                    processing_run_id=run_id,
                    catalog=self._catalog,
                    persist_rejections=False,
                    on_progress=progress,
                )
                run = transformed.processing_run
                if self._qa is not None:
                    self._set_stage(batch_id, "validating")
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
                        on_progress=progress,
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
                self._reprocess_completed[run_id] = self._reprocess_response(batch, run, summary)
                self._set_stage(batch_id, "succeeded" if run.status == "succeeded" else "failed")
                self._stash_batch_completion(batch, run, summary)
        except ProcessingCancelled:
            self._finalize_cancel(batch_id)
        except Exception as exc:
            log.exception("Reprocess failed for batch %s run %s", batch_id, run_id)
            reason = _public_text(str(exc)) or f"{type(exc).__name__} during reprocess"
            self._fail_reprocess_run(run_id, reason)
        finally:
            still = self._ingest.get_processing_run(run_id)
            batch = self._ingest.get_batch(batch_id)
            if batch is not None and (batch.status == "cancelled" or batch.cancel_requested):
                if batch.status != "cancelled":
                    self._finalize_cancel(batch_id)
            elif still is not None and still.status in {"pending", "running"}:
                self._fail_reprocess_run(run_id, WORKER_INCOMPLETE_REASON)
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
            if isinstance(error, ProcessingCancelled):
                run = self._ingest.get_processing_run(run_id)
                if run is not None:
                    self._finalize_cancel(run.batch_id)
            else:
                reason = _public_text(str(error)) or f"{type(error).__name__} during reprocess"
                self._fail_reprocess_run(run_id, reason)
        run = self._ingest.get_processing_run(run_id)
        if run is not None and run.status in {"pending", "running"}:
            batch = self._ingest.get_batch(run.batch_id)
            if batch is not None and (batch.status == "cancelled" or batch.cancel_requested):
                self._finalize_cancel(run.batch_id)
            else:
                self._fail_reprocess_run(run_id, "worker finished while run still running")

    def _fail_reprocess_run(self, run_id: str, reason: str | None = None) -> None:
        run = self._ingest.get_processing_run(run_id)
        if run is None:
            return
        now = datetime.now(tz=UTC)
        if run.status not in {"succeeded", "failed", "cancelled"}:
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
            self._stash_batch_completion(batch, run, None)

    def _stash_batch_completion(
        self,
        batch: BatchRecord,
        run: ProcessingRunRecord | None,
        transform: UploadTransformSummary | None,
    ) -> None:
        source = self._ingest.get_source_file(batch.source_file_id)
        if source is None:
            return
        self._completed[batch.id] = self._response_from_records(
            source, batch, run, transform, False
        )

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
        if batch.status == "cancelled" or batch.cancel_requested:
            self._finalize_cancel(batch_id)
            return
        now = datetime.now(tz=UTC)
        batch.status = "failed"
        batch.error_summary = summary
        batch.completed_at = now
        self._ingest.save_batch(batch)
        self._set_stage(batch_id, "failed")
        run = self._ingest.processing_run_for_batch(batch_id)
        if run is not None and run.status not in {"succeeded", "failed", "cancelled"}:
            run.status = "failed"
            run.finished_at = now
            self._ingest.save_processing_run(run)
        source = self._ingest.get_source_file(batch.source_file_id)
        if source is not None:
            self._completed[batch_id] = self._response_from_records(source, batch, run, None, False)

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
        batch_body = batch_to_response(batch, run)
        if batch_body.error_summary:
            batch_body = batch_body.model_copy(
                update={"error_summary": _public_text(batch_body.error_summary)}
            )
        local = self._stages.get(batch.id)
        if local:
            batch_body = batch_body.model_copy(update={"stage": local})
        run_body = processing_run_to_response(run, batch) if run is not None else None
        if run_body is not None and local:
            run_body = run_body.model_copy(update={"stage": local})
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
            processing_run=run_body,
            transform=transform_summary,
            rejections=rejections,
        )


def _upload_sort_key(item: tuple[str, bytes, str]) -> tuple[str, str]:
    filename, _payload, digest = item
    return (filename.lower(), digest)


def _group_response(items: list[UploadResponse], *, duration_ms: int | None) -> UploadGroupResponse:
    staged = sum(item.batch.row_count_staged for item in items)
    facts = sum(item.transform.transformed if item.transform is not None else 0 for item in items)
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


def _unsafe_zip_member_name(name: str) -> bool:
    """Reject absolute ZIP names and parent-directory traversal. Does not extract."""
    raw = name.replace("\\", "/")
    if raw.startswith("/") or raw.startswith("\\"):
        return True
    if len(raw) >= 2 and raw[1] == ":":
        return True
    return any(part == ".." for part in raw.split("/"))


def _assert_xlsx_payload(payload: bytes) -> None:
    if not payload:
        raise ValidationFailed("Workbook file is empty.")
    if payload[:2] != XLSX_MAGIC:
        raise ValidationFailed("Only .xlsx workbooks are accepted.")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                raise ValidationFailed("Workbook could not be read.")
            for info in infos:
                if _unsafe_zip_member_name(info.filename):
                    raise ValidationFailed("Workbook could not be read.")
            uncompressed = sum(info.file_size for info in infos)
            names = [info.filename for info in infos]
    except zipfile.BadZipFile as exc:
        raise ValidationFailed("Workbook could not be read.") from exc
    if uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise ValidationFailed(UNCOMPRESSED_TOO_LARGE)
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
