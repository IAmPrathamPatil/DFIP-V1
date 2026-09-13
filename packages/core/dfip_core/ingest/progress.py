"""Truthful processing-stage names and measurable percentages.

Percentages are computed only from real current/total counters. A stage that
has started but has no counters does not become 50%. Succeeded is the only
state that reports 100%.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

STAGE_RECEIVED = "received"
STAGE_INGESTING = "ingesting"
STAGE_STAGING = "staging"
STAGE_PENDING = "pending"
STAGE_PROCESSING = "processing"
STAGE_VALIDATING = "validating"
STAGE_SUCCEEDED = "succeeded"
STAGE_FAILED = "failed"
STAGE_CANCELLING = "cancelling"
STAGE_CANCELLED = "cancelled"

TERMINAL_STAGES = frozenset({STAGE_SUCCEEDED, STAGE_FAILED, STAGE_CANCELLED})
ACTIVE_STAGES = frozenset(
    {
        STAGE_INGESTING,
        STAGE_STAGING,
        STAGE_PENDING,
        STAGE_PROCESSING,
        STAGE_VALIDATING,
        STAGE_CANCELLING,
    }
)

ProgressCallback: TypeAlias = Callable[[str, int | None, int | None, str | None], None]


class WorkbookCapacityError(Exception):
    """Source row count exceeds the configured ingest cap. Fail closed."""

    def __init__(self, max_facts: int, discovered: int | None = None) -> None:
        self.max_facts = max_facts
        self.discovered = discovered
        extra = f" Observed {discovered} source rows." if discovered is not None else ""
        super().__init__(
            f"Workbook exceeds the maximum of {max_facts} source facts.{extra}".rstrip()
        )


class ProcessingDurationError(Exception):
    """Safety cutoff after the configured maximum processing duration."""

    def __init__(self, max_seconds: int) -> None:
        self.max_seconds = max_seconds
        super().__init__(f"Processing exceeded the maximum duration of {max_seconds} seconds.")


class ProcessingCancelled(Exception):
    """Cooperative stop. Temporary work must be discarded by the caller."""

    def __init__(self, batch_id: str | None = None) -> None:
        self.batch_id = batch_id
        super().__init__("Processing cancelled.")


def progress_percent(
    stage: str | None,
    current: int | None,
    total: int | None,
) -> int | None:
    """Return a percentage only when it is honestly measurable.

    Never returns 100 until the run has succeeded. Never invents a value
    because a stage started.
    """
    if stage == STAGE_SUCCEEDED:
        return 100
    if stage in {STAGE_FAILED, STAGE_CANCELLED, STAGE_CANCELLING}:
        return None
    if current is None or total is None or total <= 0:
        return None
    raw = (int(current) * 100) // int(total)
    if raw >= 100:
        return 99
    return max(0, raw)


def emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> None:
    if callback is None:
        return
    callback(stage, current, total, message)
