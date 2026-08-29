"""Phase 2A QA rule tests. Reuses P8 reconcile output without forking formulas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dfip_analytics.qa import QaContext, RejectedEvidence, evaluate_qa
from dfip_core.transform.derive import hhh, month_label, month_start
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.reconcile import reconcile_run

CLIENT = "a0000000-0000-4000-8000-000000000001"
RUN = "d0000000-0000-4000-8000-000000000001"
BATCH = "c0000000-0000-4000-8000-000000000001"
DAY = date(2025, 8, 1)


def _fact(**overrides: object) -> FactRecord:
    values: dict[str, object] = {
        "client_id": CLIENT,
        "campaign_id": "camp-1",
        "variation_id": "var-1",
        "variation_id_key": "var-1",
        "day": DAY,
        "month_start": month_start(DAY),
        "month_label": month_label(DAY),
        "hhh": hhh(None),
        "template_status": "",
        "channel": "Push",
        "sent": 10,
        "failed": 1,
        "delivered": 8,
        "unique_impressions": 7,
        "unique_clicks": 2,
        "unique_conversions": 1,
        "unique_impression_through_conversions": 0,
        "unique_click_through_conversions": 1,
        "revenue_inr": Decimal("10.0000"),
        "impression_through_revenue_inr": Decimal("1.0000"),
        "click_through_revenue_inr": Decimal("4.0000"),
        "total_cost": Decimal("0.0000"),
        "processing_run_id": RUN,
        "batch_id": BATCH,
    }
    values.update(overrides)
    return FactRecord(**values)  # type: ignore[arg-type]


def _ids(findings, rule_id: str) -> list:
    return [item for item in findings if item.rule_id == rule_id]


def test_every_supported_rule_id_is_registered() -> None:
    from dfip_analytics.qa import QA_RULES

    assert tuple(rule.rule_id for rule in QA_RULES) == (
        "QA-MISSING-KEY",
        "QA-INVALID-NUMERIC",
        "QA-DUPLICATE-GRAIN",
        "QA-NEGATIVE-COUNT",
        "QA-NEGATIVE-MONEY",
        "QA-IMPOSSIBLE-DELIVERED",
        "QA-IMPOSSIBLE-FAILED",
        "QA-RATIO-GT-ONE",
        "QA-ZERO-DENOMINATOR",
        "QA-MISSING-DATE",
        "QA-MALFORMED-ID",
        "QA-CLIENT-MISMATCH",
        "QA-REJECTED-ROWS",
        "QA-RUN-FAILED",
        "QA-RECONCILE-FAIL",
        "QA-UNPUBLISHED-ONLY",
    )


def test_missing_key_invalid_numeric_duplicate_and_missing_date() -> None:
    rejected = (
        RejectedEvidence(2, "MISSING_CAMPAIGN_ID", "Campaign ID is blank"),
        RejectedEvidence(3, "INVALID_NUMERIC", "Sent='twelve' is not numeric"),
        RejectedEvidence(4, "DUPLICATE_GRAIN_KEY", "grain already produced"),
        RejectedEvidence(5, "MISSING_DAY", "Day is blank"),
        RejectedEvidence(6, "INVALID_DAY", "Day='nope' is not an ISO date"),
    )
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            rejected=rejected,
        )
    )
    assert _ids(findings, "QA-MISSING-KEY")
    assert _ids(findings, "QA-INVALID-NUMERIC")
    assert _ids(findings, "QA-DUPLICATE-GRAIN")
    assert _ids(findings, "QA-MISSING-DATE")
    assert _ids(findings, "QA-MALFORMED-ID")
    rejected_rows = _ids(findings, "QA-REJECTED-ROWS")
    assert len(rejected_rows) == 1
    assert rejected_rows[0].diagnostics["count"] == 5


def test_negative_count_and_money() -> None:
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=[
                _fact(sent=-1),
                _fact(
                    campaign_id="camp-2",
                    revenue_inr=Decimal("-1.0000"),
                    sent=10,
                ),
            ],
        )
    )
    assert _ids(findings, "QA-NEGATIVE-COUNT")
    assert _ids(findings, "QA-NEGATIVE-MONEY")


def test_impossible_delivered_failed_and_ratio_gt_one() -> None:
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=[
                _fact(sent=5, delivered=9, failed=1, unique_clicks=2, unique_impressions=7),
                _fact(
                    campaign_id="camp-2",
                    sent=10,
                    failed=12,
                    delivered=8,
                    unique_clicks=20,
                    unique_impressions=5,
                ),
            ],
        )
    )
    assert _ids(findings, "QA-IMPOSSIBLE-DELIVERED")
    assert _ids(findings, "QA-IMPOSSIBLE-FAILED")
    assert _ids(findings, "QA-RATIO-GT-ONE")


def test_zero_denominator_and_malformed_and_client_mismatch() -> None:
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            expected_client_id=CLIENT,
            facts=[
                _fact(delivered=0, sent=0, unique_clicks=5, unique_impressions=0),
                _fact(
                    campaign_id="camp-2",
                    client_id="a0000000-0000-4000-8000-000000000002",
                ),
                _fact(campaign_id="camp-3", processing_run_id="not-a-uuid"),
            ],
        )
    )
    assert _ids(findings, "QA-ZERO-DENOMINATOR")
    assert _ids(findings, "QA-CLIENT-MISMATCH")
    assert _ids(findings, "QA-MALFORMED-ID")


def test_failed_run_unpublished_and_reconcile_failure() -> None:
    bad = _fact(total_cost=Decimal("99.0000"), delivered=8, template_status="", channel="SMS")
    report = reconcile_run(
        [bad],
        rate_card_version_label="rate-v2",
        processing_run_id=RUN,
    )
    assert report.passed is False
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="failed",
            facts=[bad],
            reconcile_passed=report.passed,
            reconcile_failures=report.failures,
            is_current_publication=False,
        )
    )
    assert _ids(findings, "QA-RUN-FAILED")
    assert _ids(findings, "QA-RECONCILE-FAIL")
    assert _ids(findings, "QA-UNPUBLISHED-ONLY")
    assert "99" in _ids(findings, "QA-RECONCILE-FAIL")[0].diagnostics["failures"][0]


def test_healthy_run_emits_no_error_findings() -> None:
    fact = _fact()
    report = reconcile_run([fact], rate_card_version_label="rate-v2", processing_run_id=RUN)
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=[fact],
            reconcile_passed=report.passed,
            reconcile_failures=report.failures,
            is_current_publication=True,
        )
    )
    errors = [item for item in findings if item.severity == "error"]
    assert errors == []
    assert report.passed is True


def test_verdict_from_findings_uses_existing_severity_model() -> None:
    from dfip_analytics.qa import (
        QA_VERDICT_FAIL,
        QA_VERDICT_PASS,
        QA_VERDICT_WARN,
        verdict_from_findings,
    )

    assert verdict_from_findings([]) == QA_VERDICT_PASS
    info_only = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=[
                _fact(
                    delivered=0,
                    sent=0,
                    failed=0,
                    unique_impressions=0,
                    unique_clicks=0,
                    unique_conversions=0,
                    unique_impression_through_conversions=0,
                    unique_click_through_conversions=0,
                )
            ],
        )
    )
    assert any(item.rule_id == "QA-ZERO-DENOMINATOR" for item in info_only)
    assert verdict_from_findings(info_only) == QA_VERDICT_PASS
    warnings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=[_fact(sent=5, failed=9, delivered=4)],
        )
    )
    assert verdict_from_findings(warnings) == QA_VERDICT_WARN
    errors = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="failed",
            facts=[_fact(sent=-1)],
        )
    )
    assert verdict_from_findings(errors) == QA_VERDICT_FAIL


def test_125_failed_gt_sent_facts_emit_impossible_failed_warnings() -> None:
    facts = [
        _fact(campaign_id=f"camp-{index}", sent=10, failed=12, delivered=8)
        for index in range(125)
    ]
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT,
            processing_run_id=RUN,
            batch_id=BATCH,
            run_status="succeeded",
            facts=facts,
        )
    )
    failed_gt_sent = _ids(findings, "QA-IMPOSSIBLE-FAILED")
    assert len(failed_gt_sent) == 125
    assert {item.severity for item in failed_gt_sent} == {"warning"}
    assert {item.processing_run_id for item in failed_gt_sent} == {RUN}
    assert {item.client_id for item in failed_gt_sent} == {CLIENT}
