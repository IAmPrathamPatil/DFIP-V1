"""QA / data-quality rules over existing P3/P4/P8 evidence.

Does not fork P8 reconcile formulas. Callers pass `reconcile_run` results.
Findings are inspector-only when persisted. Power BI must not read this table.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from dfip_core.transform.fact import FactRecord

from dfip_analytics.aggregate import sum_additive_measures
from dfip_analytics.kpis import KPI_SPECS, compute_kpis

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

INTEGER_COUNT_FIELDS: tuple[str, ...] = (
    "sent",
    "failed",
    "delivered",
    "unique_impressions",
    "unique_clicks",
    "unique_conversions",
    "unique_impression_through_conversions",
    "unique_click_through_conversions",
)
MONEY_FIELDS: tuple[str, ...] = (
    "revenue_inr",
    "impression_through_revenue_inr",
    "click_through_revenue_inr",
    "total_cost",
)

REJECT_TO_RULE: dict[str, str] = {
    "MISSING_CAMPAIGN_ID": "QA-MISSING-KEY",
    "MISSING_DAY": "QA-MISSING-DATE",
    "INVALID_DAY": "QA-MALFORMED-ID",
    "INVALID_NUMERIC": "QA-INVALID-NUMERIC",
    "DUPLICATE_GRAIN_KEY": "QA-DUPLICATE-GRAIN",
    "IMPOSSIBLE_ROW": "QA-MALFORMED-ID",
}


@dataclass(frozen=True)
class RejectedEvidence:
    source_row_number: int | None
    reason_code: str
    reason_detail: str | None = None
    campaign_id: str | None = None
    day: date | None = None


@dataclass(frozen=True)
class QaFinding:
    rule_id: str
    severity: str
    description: str
    entity_type: str
    entity_key: str
    message: str
    status: str = "detected"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    client_id: str = ""
    processing_run_id: str = ""
    batch_id: str | None = None


class DuplicateQaFindingKeyError(ValueError):
    """Raised when two findings share (processing_run_id, rule_id, entity_key)."""

    def __init__(self, processing_run_id: str, rule_id: str, entity_key: str) -> None:
        super().__init__(
            "duplicate QA finding key "
            f"({processing_run_id}, {rule_id}, {entity_key})"
        )
        self.processing_run_id = processing_run_id
        self.rule_id = rule_id
        self.entity_key = entity_key


def assert_unique_qa_finding_keys(findings: Sequence[QaFinding]) -> None:
    """Reject evaluator collisions before they reach the unique index."""
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.processing_run_id, finding.rule_id, finding.entity_key)
        if key in seen:
            raise DuplicateQaFindingKeyError(*key)
        seen.add(key)


@dataclass(frozen=True)
class QaContext:
    client_id: str
    processing_run_id: str
    batch_id: str | None
    run_status: str
    facts: Sequence[FactRecord] = ()
    rejected: Sequence[RejectedEvidence] = ()
    expected_client_id: str | None = None
    reconcile_passed: bool | None = None
    reconcile_failures: Sequence[str] = ()
    is_current_publication: bool | None = None


@dataclass(frozen=True)
class QaRule:
    rule_id: str
    severity: str
    description: str
    entity: str


QA_RULES: tuple[QaRule, ...] = (
    QaRule("QA-MISSING-KEY", "error", "Campaign ID is blank and is part of the fact key", "row"),
    QaRule("QA-INVALID-NUMERIC", "error", "A count or amount is not a strict numeric value", "row"),
    QaRule("QA-DUPLICATE-GRAIN", "error", "Duplicate fact grain inside a processing run", "row"),
    QaRule("QA-NEGATIVE-COUNT", "error", "A count measure is negative", "fact"),
    QaRule("QA-NEGATIVE-MONEY", "error", "A money measure is negative", "fact"),
    QaRule("QA-IMPOSSIBLE-DELIVERED", "warning", "Delivered exceeds sent", "fact"),
    QaRule("QA-IMPOSSIBLE-FAILED", "warning", "Failed exceeds sent", "fact"),
    QaRule(
        "QA-RATIO-GT-ONE",
        "warning",
        "A count relationship that should be <= 1 is inverted",
        "fact",
    ),
    QaRule("QA-ZERO-DENOMINATOR", "info", "A ratio KPI denominator summed to zero", "kpi"),
    QaRule("QA-MISSING-DATE", "error", "Day is blank and is part of the fact key", "row"),
    QaRule("QA-MALFORMED-ID", "error", "An identifier or date is malformed", "row"),
    QaRule("QA-CLIENT-MISMATCH", "error", "Fact client_id does not match the run client", "fact"),
    QaRule("QA-REJECTED-ROWS", "warning", "The run produced rejected source rows", "run"),
    QaRule("QA-RUN-FAILED", "error", "processing_run.status is failed", "run"),
    QaRule("QA-RECONCILE-FAIL", "error", "P8 reconcile reported a contract failure", "run"),
    QaRule(
        "QA-UNPUBLISHED-ONLY",
        "info",
        "The run has facts but is not the current publication",
        "run",
    ),
)

_RULES = {rule.rule_id: rule for rule in QA_RULES}

QA_VERDICT_PASS = "pass"
QA_VERDICT_WARN = "warn"
QA_VERDICT_FAIL = "fail"
QA_VERDICT_UNAVAILABLE = "unavailable"

PUBLISHABLE_QA_VERDICTS = frozenset({QA_VERDICT_PASS, QA_VERDICT_WARN})


def verdict_from_findings(findings: Sequence[QaFinding]) -> str:
    """Map persisted finding severities onto the run-level QA verdict.

    Existing rule severities are ``error``, ``warning``, and ``info``.
    ``error`` is blocking (FAIL). ``warning`` is non-blocking (WARN).
    Info-only or no findings is PASS. Callers record ``unavailable`` when
    evaluation itself does not complete; that is not a PASS.
    """
    severities = {item.severity for item in findings}
    if "error" in severities:
        return QA_VERDICT_FAIL
    if "warning" in severities:
        return QA_VERDICT_WARN
    return QA_VERDICT_PASS


def _key_for_fact(fact: FactRecord) -> str:
    day = fact.day.isoformat() if fact.day else ""
    return f"{fact.campaign_id}|{fact.variation_id_key}|{day}"


def _finding(
    context: QaContext,
    rule_id: str,
    *,
    entity_type: str,
    entity_key: str,
    message: str,
    diagnostics: dict[str, Any] | None = None,
) -> QaFinding:
    rule = _RULES[rule_id]
    return QaFinding(
        rule_id=rule.rule_id,
        severity=rule.severity,
        description=rule.description,
        entity_type=entity_type,
        entity_key=entity_key,
        message=message,
        diagnostics=diagnostics or {},
        client_id=context.client_id,
        processing_run_id=context.processing_run_id,
        batch_id=context.batch_id,
    )


def _is_uuid(value: str | None) -> bool:
    if value is None or value == "":
        return False
    if not _UUID.match(value):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def evaluate_qa(context: QaContext) -> list[QaFinding]:
    """Detect supported quality problems. Does not mutate facts or P4 modules."""
    findings: list[QaFinding] = []
    run_key = context.processing_run_id

    for rejected in context.rejected:
        mapped = REJECT_TO_RULE.get(rejected.reason_code)
        if mapped is None:
            continue
        entity_key = (
            f"row:{rejected.source_row_number}"
            if rejected.source_row_number is not None
            else f"reject:{rejected.reason_code}"
        )
        findings.append(
            _finding(
                context,
                mapped,
                entity_type="rejected_row",
                entity_key=entity_key,
                message=rejected.reason_detail or rejected.reason_code,
                diagnostics={
                    "reason_code": rejected.reason_code,
                    "source_row_number": rejected.source_row_number,
                    "campaign_id": rejected.campaign_id,
                    "day": rejected.day.isoformat() if rejected.day else None,
                },
            )
        )

    if context.rejected:
        counts = Counter(item.reason_code for item in context.rejected)
        findings.append(
            _finding(
                context,
                "QA-REJECTED-ROWS",
                entity_type="run",
                entity_key=run_key,
                message=f"{len(context.rejected)} rejected source row(s)",
                diagnostics={"count": len(context.rejected), "reason_codes": dict(counts)},
            )
        )

    expected_client = context.expected_client_id or context.client_id
    for fact in context.facts:
        entity_key = _key_for_fact(fact)
        if not fact.campaign_id:
            findings.append(
                _finding(
                    context,
                    "QA-MISSING-KEY",
                    entity_type="fact",
                    entity_key=entity_key,
                    message="Campaign ID is blank",
                    diagnostics={"campaign_id": fact.campaign_id},
                )
            )
        if fact.day is None:
            findings.append(
                _finding(
                    context,
                    "QA-MISSING-DATE",
                    entity_type="fact",
                    entity_key=entity_key,
                    message="Day is blank",
                )
            )
        malformed_run = fact.processing_run_id is not None and not _is_uuid(fact.processing_run_id)
        malformed_batch = fact.batch_id is not None and not _is_uuid(fact.batch_id)
        if not _is_uuid(fact.client_id) or malformed_run or malformed_batch:
            findings.append(
                _finding(
                    context,
                    "QA-MALFORMED-ID",
                    entity_type="fact",
                    entity_key=entity_key,
                    message="client_id, processing_run_id, or batch_id is not a UUID",
                    diagnostics={
                        "client_id": fact.client_id,
                        "processing_run_id": fact.processing_run_id,
                        "batch_id": fact.batch_id,
                    },
                )
            )
        if fact.client_id != expected_client:
            findings.append(
                _finding(
                    context,
                    "QA-CLIENT-MISMATCH",
                    entity_type="fact",
                    entity_key=entity_key,
                    message="fact.client_id does not match the processing run client",
                    diagnostics={
                        "fact_client_id": fact.client_id,
                        "expected_client_id": expected_client,
                    },
                )
            )
        for name in INTEGER_COUNT_FIELDS:
            value = getattr(fact, name)
            if isinstance(value, int) and value < 0:
                findings.append(
                    _finding(
                        context,
                        "QA-NEGATIVE-COUNT",
                        entity_type="fact",
                        entity_key=f"{entity_key}:{name}",
                        message=f"{name} is negative",
                        diagnostics={"field": name, "value": value},
                    )
                )
        for name in MONEY_FIELDS:
            value = getattr(fact, name)
            if isinstance(value, Decimal) and value < 0:
                findings.append(
                    _finding(
                        context,
                        "QA-NEGATIVE-MONEY",
                        entity_type="fact",
                        entity_key=f"{entity_key}:{name}",
                        message=f"{name} is negative",
                        diagnostics={"field": name, "value": str(value)},
                    )
                )
        if fact.delivered is not None and fact.sent is not None and fact.delivered > fact.sent:
            findings.append(
                _finding(
                    context,
                    "QA-IMPOSSIBLE-DELIVERED",
                    entity_type="fact",
                    entity_key=f"{entity_key}:delivered",
                    message="delivered exceeds sent",
                    diagnostics={"sent": fact.sent, "delivered": fact.delivered},
                )
            )
        if fact.failed is not None and fact.sent is not None and fact.failed > fact.sent:
            findings.append(
                _finding(
                    context,
                    "QA-IMPOSSIBLE-FAILED",
                    entity_type="fact",
                    entity_key=f"{entity_key}:failed",
                    message="failed exceeds sent",
                    diagnostics={"sent": fact.sent, "failed": fact.failed},
                )
            )
        ratio_pairs = (
            ("unique_clicks", "unique_impressions"),
            ("unique_conversions", "delivered"),
            ("unique_click_through_conversions", "unique_clicks"),
            ("unique_impression_through_conversions", "unique_impressions"),
            ("unique_impressions", "delivered"),
        )
        for left_name, right_name in ratio_pairs:
            left = getattr(fact, left_name)
            right = getattr(fact, right_name)
            if left is not None and right is not None and left > right:
                findings.append(
                    _finding(
                        context,
                        "QA-RATIO-GT-ONE",
                        entity_type="fact",
                        entity_key=f"{entity_key}:{left_name}:{right_name}",
                        message=f"{left_name} exceeds {right_name}",
                        diagnostics={left_name: left, right_name: right},
                    )
                )

    if context.run_status == "failed":
        findings.append(
            _finding(
                context,
                "QA-RUN-FAILED",
                entity_type="run",
                entity_key=run_key,
                message="processing_run.status is failed",
                diagnostics={"status": context.run_status},
            )
        )

    if context.reconcile_passed is False:
        findings.append(
            _finding(
                context,
                "QA-RECONCILE-FAIL",
                entity_type="run",
                entity_key=run_key,
                message="P8 reconcile did not pass",
                diagnostics={"failures": list(context.reconcile_failures)},
            )
        )

    if context.facts and context.is_current_publication is False:
        findings.append(
            _finding(
                context,
                "QA-UNPUBLISHED-ONLY",
                entity_type="run",
                entity_key=run_key,
                message="Run has facts but is not the current publication",
                diagnostics={"fact_count": len(context.facts)},
            )
        )

    if context.facts:
        measures = sum_additive_measures(list(context.facts))
        kpis = compute_kpis(measures, namespace="qa")
        for spec in KPI_SPECS:
            if spec.namespace != "qa" or spec.operation != "divide":
                continue
            denom_name = spec.denominator_measure
            if denom_name is None:
                parent = next(item for item in KPI_SPECS if item.id == spec.depends_on_kpi_id)
                denom_value = kpis.get(parent.slug)
            else:
                denom_value = measures.get(denom_name)
            if denom_value == 0:
                findings.append(
                    _finding(
                        context,
                        "QA-ZERO-DENOMINATOR",
                        entity_type="kpi",
                        entity_key=f"{spec.namespace}.{spec.slug}",
                        message=f"{spec.slug} denominator is zero",
                        diagnostics={
                            "slug": spec.slug,
                            "denominator_measure": denom_name,
                            "kpi_value": (
                                None if kpis.get(spec.slug) is None else str(kpis.get(spec.slug))
                            ),
                        },
                    )
                )

    return findings
