"""Read published reporting views and persist inspector-only QA findings."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from typing import Any

from dfip_analytics.qa import QaFinding, assert_unique_qa_finding_keys
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.rls import current_rls

log = logging.getLogger(__name__)

# Live inserts into qa_finding measured ~90-140 rows/s over the session pooler
# while maintaining qa_finding_run_rule_entity_key plus four other indexes.
# Default statement_timeout is 30s; a 5_000-row statement exceeded it.
# 1_000-row UNNEST statements stay under that budget (~11s at 90 rows/s).
QA_PERSIST_STATEMENT_TIMEOUT = "120s"
QA_FINDING_INSERT_CHUNK = 1000

_QA_UNNEST_INSERT = """
INSERT INTO qa_finding (
    client_id, processing_run_id, batch_id, rule_id, severity, status,
    entity_type, entity_key, message, diagnostics
)
SELECT
    client_id, processing_run_id, batch_id, rule_id, severity, status,
    entity_type, entity_key, message, diagnostics::jsonb
FROM unnest(
    %s::uuid[], %s::uuid[], %s::uuid[], %s::text[], %s::text[], %s::text[],
    %s::text[], %s::text[], %s::text[], %s::text[]
) AS t(
    client_id, processing_run_id, batch_id, rule_id, severity, status,
    entity_type, entity_key, message, diagnostics
)
"""


def qa_persist_plan(
    finding_count: int, chunk_size: int = QA_FINDING_INSERT_CHUNK
) -> dict[str, int]:
    insert_batches = math.ceil(finding_count / chunk_size) if finding_count else 0
    return {
        "delete_transactions": 1,
        "insert_transactions": insert_batches,
        "insert_statements": insert_batches,
        "transaction_count": 1 + insert_batches,
        "chunk_size": chunk_size,
        "finding_count": finding_count,
    }


def finding_persist_tuple(finding: QaFinding) -> tuple[Any, ...]:
    diagnostics = finding.diagnostics if isinstance(finding.diagnostics, dict) else {}
    return (
        finding.client_id,
        finding.processing_run_id,
        finding.batch_id,
        finding.rule_id,
        finding.severity,
        finding.status,
        finding.entity_type,
        finding.entity_key,
        finding.message,
        json.dumps(diagnostics),
    )


def insert_finding_unnest(conn: Any, rows: Sequence[tuple[Any, ...]]) -> None:
    if not rows:
        return
    columns = list(zip(*rows, strict=True))
    conn.execute(
        _QA_UNNEST_INSERT,
        (
            list(columns[0]),
            list(columns[1]),
            list(columns[2]),
            list(columns[3]),
            list(columns[4]),
            list(columns[5]),
            list(columns[6]),
            list(columns[7]),
            list(columns[8]),
            list(columns[9]),
        ),
    )


class PostgresAnalyticsRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _tx(self):
        return transaction(self._pool, current_rls())

    def replace_findings(self, findings: Sequence[QaFinding]) -> None:
        if not findings:
            return
        self.replace_for_run(findings[0].processing_run_id, findings)

    def replace_for_run(self, processing_run_id: str, findings: Sequence[QaFinding]) -> None:
        assert_unique_qa_finding_keys(findings)
        rows = [finding_persist_tuple(item) for item in findings]
        try:
            self._delete_for_run(processing_run_id)
            chunk = QA_FINDING_INSERT_CHUNK
            total = len(rows)
            for offset in range(0, total, chunk):
                batch = rows[offset : offset + chunk]
                with self._tx() as conn:
                    _set_persist_timeout(conn)
                    insert_finding_unnest(conn, batch)
                log.info(
                    "persisted QA findings run=%s offset=%s count=%s of %s",
                    processing_run_id,
                    offset,
                    len(batch),
                    total,
                )
        except Exception:
            log.exception("QA finding persist failed for processing run %s", processing_run_id)
            try:
                self._delete_for_run(processing_run_id)
            except Exception:
                log.exception(
                    "QA finding persist cleanup failed for processing run %s",
                    processing_run_id,
                )
            raise

    def _delete_for_run(self, processing_run_id: str) -> None:
        with self._tx() as conn:
            _set_persist_timeout(conn)
            conn.execute(
                "DELETE FROM qa_finding WHERE processing_run_id = %s",
                (processing_run_id,),
            )

    def list_findings(self, processing_run_id: str) -> list[dict[str, Any]]:
        rows = self.list_for_run(processing_run_id)
        return [
            {
                "rule_id": row["rule_id"],
                "severity": row["severity"],
                "status": row["status"],
                "entity_type": row["entity_type"],
                "entity_key": row["entity_key"],
                "message": row["message"],
                "diagnostics": row["diagnostics"],
            }
            for row in rows
        ]

    def list_for_run(self, processing_run_id: str) -> list[dict[str, Any]]:
        with self._tx() as conn:
            _set_persist_timeout(conn)
            rows = conn.execute(
                """
                SELECT id::text AS finding_id,
                       client_id::text AS client_id,
                       processing_run_id::text AS processing_run_id,
                       batch_id::text AS batch_id,
                       rule_id, severity, status, entity_type, entity_key, message,
                       diagnostics, created_at, updated_at
                FROM qa_finding
                WHERE processing_run_id = %s
                ORDER BY rule_id, entity_key
                """,
                (processing_run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_published_facts(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT client_id::text AS client_id, campaign_id, variation_id, variation_id_key,
                       day, sent, failed, delivered, unique_impressions, unique_clicks,
                       unique_conversions, revenue_inr, total_cost,
                       amc_product_cat_filter_logic_5
                FROM rpt_published_fact
                ORDER BY client_id, campaign_id, variation_id_key, day
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT client_id::text AS client_id, campaign_id, campaign_name, channel,
                       type_of_campaign, amc_product_cat_filter_logic_5
                FROM rpt_dim_campaign
                ORDER BY client_id, campaign_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_variations(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT client_id::text AS client_id, campaign_id, variation_id, variation_id_key,
                       variation_name
                FROM rpt_dim_variation
                ORDER BY client_id, campaign_id, variation_id_key
                """
            ).fetchall()
        return [dict(row) for row in rows]


def _set_persist_timeout(conn: Any) -> None:
    conn.execute(f"SET LOCAL statement_timeout = '{QA_PERSIST_STATEMENT_TIMEOUT}'")
