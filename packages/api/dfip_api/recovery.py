"""P13B durable processing recovery helpers.

PostgreSQL-backed abandoned-run sweep and stable operator-facing reasons.
Does not introduce a job table or resume mid-chunk fact persistence.
"""

from __future__ import annotations

import logging

from dfip_core.ingest.ports import IngestStore

log = logging.getLogger(__name__)

ABANDONED_RUN_REASON = "Processing abandoned because the API process restarted. Retry is available."
ARCHIVE_MISSING_MESSAGE = "Source archive is missing. Upload the workbook again."
WORKER_INCOMPLETE_REASON = "worker terminated before completion"
ZOMBIE_RUN_REASON = "Processing stopped because no worker was running this job. Recovery will continue automatically."

AUTO_RESUME_REASONS = frozenset(
    {
        ABANDONED_RUN_REASON,
        WORKER_INCOMPLETE_REASON,
        ZOMBIE_RUN_REASON,
        "worker stalled during fact persistence",
        "worker cancelled",
        "worker finished while run still running",
    }
)


def fail_abandoned_processing_runs(ingest_store: IngestStore) -> int:
    """Mark leftover pending/running runs failed. Idempotent. Does not touch facts."""
    count = ingest_store.fail_abandoned_processing_runs(reason=ABANDONED_RUN_REASON)
    log.info("abandoned-run-sweep marked=%s", count)
    return count
