"""Inspector QA finding persistence.

In-memory adapter for tests and empty DATABASE_URL. PostgreSQL uses
``PostgresAnalyticsRepository`` (same ``qa_finding`` table, migration 14).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from dfip_analytics.qa import QaFinding, assert_unique_qa_finding_keys


class QaFindingStore(Protocol):
    def replace_for_run(self, processing_run_id: str, findings: Sequence[QaFinding]) -> None: ...

    def list_for_run(self, processing_run_id: str) -> list[dict[str, Any]]: ...


def stored_row_from_finding(finding: QaFinding, *, now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(tz=UTC)
    return {
        "finding_id": str(uuid4()),
        "client_id": finding.client_id,
        "processing_run_id": finding.processing_run_id,
        "batch_id": finding.batch_id,
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "status": finding.status,
        "entity_type": finding.entity_type,
        "entity_key": finding.entity_key,
        "message": finding.message,
        "diagnostics": dict(finding.diagnostics),
        "created_at": stamp,
        "updated_at": stamp,
    }


class InMemoryQaFindingStore:
    """Process-local qa_finding rows keyed by processing_run_id."""

    def __init__(self) -> None:
        self._by_run: dict[str, list[dict[str, Any]]] = {}

    def replace_for_run(self, processing_run_id: str, findings: Sequence[QaFinding]) -> None:
        assert_unique_qa_finding_keys(findings)
        now = datetime.now(tz=UTC)
        rows = [stored_row_from_finding(item, now=now) for item in findings]
        rows.sort(key=lambda item: (item["rule_id"], item["entity_key"]))
        self._by_run[processing_run_id] = rows

    def list_for_run(self, processing_run_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._by_run.get(processing_run_id, ())]

    def purge_client(self, client_id: str) -> None:
        self._by_run = {
            run_id: rows
            for run_id, rows in self._by_run.items()
            if not any(row.get("client_id") == client_id for row in rows)
        }
