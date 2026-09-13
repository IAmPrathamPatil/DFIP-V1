"""Run existing evaluate_qa / reconcile_run after HTTP transform.

Persists qa_finding rows and processing_run.qa_verdict. Does not publish.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import contextmanager

from dfip_analytics.qa import (
    QA_RULES,
    QA_VERDICT_UNAVAILABLE,
    QaContext,
    RejectedEvidence,
    evaluate_qa,
    verdict_from_findings,
)
from dfip_core.ingest.progress import STAGE_VALIDATING, ProcessingCancelled, emit_progress
from dfip_core.ingest.ports import IngestStore
from dfip_core.ingest.store import ProcessingRunRecord, RejectedRowRecord
from dfip_core.transform.labels import packaged_label_for_id
from dfip_core.transform.ports import FactStore
from dfip_core.transform.reconcile import reconcile_run
from dfip_db.rls import RlsContext, bind_rls, reset_rls

from dfip_api.qa_store import QaFindingStore

log = logging.getLogger(__name__)


@contextmanager
def processing_rls(client_id: str):
    """Bind inspector RLS for a background processing worker.

    Thread-pool jobs must not rely on the HTTP request ContextVar remaining
    set after the 202 response. Without this, FORCE RLS can make for_run()
    return zero rows and QA would persist an empty finding set.
    """
    bind_rls(
        RlsContext(
            user_id="dfip-processing-worker",
            role="publisher",
            client_ids=(client_id,),
            platform_admin=False,
            subject="dfip-processing-worker",
        )
    )
    try:
        yield
    finally:
        reset_rls()


def evaluate_and_persist_run(
    *,
    ingest_store: IngestStore,
    fact_store: FactStore,
    qa_store: QaFindingStore,
    run: ProcessingRunRecord,
    client_id: str,
    rate_card_version_labels: set[str] | None = None,
    extra_rejected: Sequence[RejectedEvidence] | None = None,
    on_progress: Callable[[str, int | None, int | None, str | None], None] | None = None,
) -> str:
    """Evaluate the complete run-scoped working set and persist findings + verdict.

    Returns the stored verdict. Evaluation failure is recorded as
    ``unavailable`` and is never treated as PASS or as zero findings.
    """
    qa_total = 4
    try:
        emit_progress(on_progress, STAGE_VALIDATING, 0, qa_total, "Loading facts...")
        facts = fact_store.for_run(run.id)
        rejected = [
            _rejected_evidence(item) for item in ingest_store.rejected_for_batch(run.batch_id)
        ]
        if extra_rejected:
            rejected.extend(extra_rejected)
        reconcile_passed = None
        reconcile_failures: tuple[str, ...] = ()
        rate_label = _rate_card_label(run, facts, rate_card_version_labels)
        emit_progress(on_progress, STAGE_VALIDATING, 1, qa_total, "Reconciling...")
        if facts and rate_label is not None:
            report = reconcile_run(
                facts,
                rate_card_version_label=rate_label,
                processing_run_id=run.id,
                campaign_label_version_id=run.campaign_label_version_id,
                template_label_version_id=run.template_label_version_id,
            )
            reconcile_passed = report.passed
            reconcile_failures = tuple(report.failures)
        emit_progress(on_progress, STAGE_VALIDATING, 2, qa_total, "Running QA checks...")
        findings = evaluate_qa(
            QaContext(
                client_id=client_id,
                processing_run_id=run.id,
                batch_id=run.batch_id,
                run_status=run.status,
                facts=facts,
                rejected=rejected,
                expected_client_id=client_id,
                reconcile_passed=reconcile_passed,
                reconcile_failures=reconcile_failures,
            )
        )
        emit_progress(
            on_progress,
            STAGE_VALIDATING,
            3,
            qa_total,
            f"{len(QA_RULES)} / {len(QA_RULES)} checks complete",
        )
        qa_store.replace_for_run(run.id, findings)
        verdict = verdict_from_findings(findings)
        _persist_verdict(ingest_store, run, verdict)
        emit_progress(on_progress, STAGE_VALIDATING, 4, qa_total, "QA complete")
        return verdict
    except ProcessingCancelled:
        raise
    except Exception:
        log.exception("QA evaluation failed for processing run %s", run.id)
        try:
            _persist_verdict(ingest_store, run, QA_VERDICT_UNAVAILABLE)
        except Exception:
            log.exception(
                "Failed to persist QA unavailable verdict for processing run %s",
                run.id,
            )
            run.qa_verdict = QA_VERDICT_UNAVAILABLE
        return QA_VERDICT_UNAVAILABLE


def _persist_verdict(ingest_store: IngestStore, run: ProcessingRunRecord, verdict: str) -> None:
    current = ingest_store.get_processing_run(run.id) or run
    current.qa_verdict = verdict
    ingest_store.save_processing_run(current)
    run.qa_verdict = verdict


def _rejected_evidence(item: RejectedRowRecord) -> RejectedEvidence:
    return RejectedEvidence(
        source_row_number=item.source_row_number,
        reason_code=item.reason_code,
        reason_detail=item.reason_detail,
    )


def _rate_card_label(
    run: ProcessingRunRecord,
    facts: Sequence[object],
    rate_card_version_labels: set[str] | None,
) -> str | None:
    if rate_card_version_labels is not None and len(rate_card_version_labels) == 1:
        return next(iter(rate_card_version_labels))
    packaged = packaged_label_for_id(run.rate_card_version_id)
    if packaged is not None:
        return packaged
    ids = {
        getattr(fact, "rate_card_version_id", None)
        for fact in facts
        if getattr(fact, "rate_card_version_id", None)
    }
    if len(ids) == 1:
        return packaged_label_for_id(next(iter(ids)))
    return None
