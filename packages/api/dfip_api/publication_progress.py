"""In-process publication progress. Never invents a percentage."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

STAGE_PREPARING = "preparing"
STAGE_VALIDATING = "validating"
STAGE_CREATING = "creating"
STAGE_HISTORY = "history"
STAGE_FINALIZING = "finalizing"
STAGE_PUBLISHED = "published"
STAGE_FAILED = "failed"

STAGE_MESSAGES = {
    STAGE_PREPARING: "Preparing publication",
    STAGE_VALIDATING: "Validating snapshot",
    STAGE_CREATING: "Creating publication",
    STAGE_HISTORY: "Updating historical serving data",
    STAGE_FINALIZING: "Finalizing",
    STAGE_PUBLISHED: "Published",
    STAGE_FAILED: "Publication failed",
}


@dataclass
class PublicationProgress:
    processing_run_id: str
    client_id: str
    stage: str
    status: str
    message: str
    current_count: int | None = None
    total_count: int | None = None
    publication_id: str | None = None
    error_summary: str | None = None
    updated_at: datetime | None = None

    @property
    def progress_percent(self) -> int | None:
        if self.stage == STAGE_PUBLISHED or self.status == "succeeded":
            return 100
        return None


class PublicationProgressRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PublicationProgress] = {}

    def start(self, *, client_id: str, processing_run_id: str) -> PublicationProgress:
        item = PublicationProgress(
            processing_run_id=processing_run_id,
            client_id=client_id,
            stage=STAGE_PREPARING,
            status="running",
            message=STAGE_MESSAGES[STAGE_PREPARING],
            updated_at=datetime.now(tz=UTC),
        )
        with self._lock:
            self._items[processing_run_id] = item
        return item

    def set(
        self,
        processing_run_id: str,
        stage: str,
        *,
        message: str | None = None,
        current_count: int | None = None,
        total_count: int | None = None,
        publication_id: str | None = None,
        error_summary: str | None = None,
        status: str | None = None,
    ) -> None:
        with self._lock:
            item = self._items.get(processing_run_id)
            if item is None:
                return
            item.stage = stage
            item.message = message or STAGE_MESSAGES.get(stage, stage)
            if current_count is not None:
                item.current_count = current_count
            if total_count is not None:
                item.total_count = total_count
            if publication_id is not None:
                item.publication_id = publication_id
            if error_summary is not None:
                item.error_summary = error_summary
            if status is not None:
                item.status = status
            item.updated_at = datetime.now(tz=UTC)

    def succeed(self, processing_run_id: str, publication_id: str) -> None:
        self.set(
            processing_run_id,
            STAGE_PUBLISHED,
            publication_id=publication_id,
            status="succeeded",
        )

    def fail(self, processing_run_id: str, error_summary: str) -> None:
        self.set(
            processing_run_id,
            STAGE_FAILED,
            error_summary=error_summary,
            status="failed",
        )

    def get(self, processing_run_id: str) -> PublicationProgress | None:
        with self._lock:
            item = self._items.get(processing_run_id)
            if item is None:
                return None
            return PublicationProgress(
                processing_run_id=item.processing_run_id,
                client_id=item.client_id,
                stage=item.stage,
                status=item.status,
                message=item.message,
                current_count=item.current_count,
                total_count=item.total_count,
                publication_id=item.publication_id,
                error_summary=item.error_summary,
                updated_at=item.updated_at,
            )

    def has_running_for_client(self, client_id: str) -> bool:
        with self._lock:
            return any(
                item.client_id == client_id and item.status == "running"
                for item in self._items.values()
            )
