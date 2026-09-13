"""Reconciliation: prove a transformation run obeyed the locked contract.

These checks are assertions about the *engine*, not about a particular
workbook. They are cheap enough to run over a whole batch and are what a QA
verdict should be based on.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from dfip_config.rate_cards import RATE_CARD_RULES

from dfip_core.transform.cost import ZERO_COST
from dfip_core.transform.derive import hhh, month_label, month_start
from dfip_core.transform.fact import FactRecord

_RATES: dict[tuple[str, str, str], Decimal] = {
    (rule.version_label, rule.match_field, rule.match_value.lower()): Decimal(rule.rate)
    for rule in RATE_CARD_RULES
}


@dataclass
class ReconciliationReport:
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    total_cost: Decimal = ZERO_COST
    total_delivered: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.checks[name] = self.checks.get(name, True) and ok
        if not ok and detail not in self.failures:
            self.failures.append(detail)


def excel_blank_equivalent(value: Any) -> Any:
    """Normalise an Excel-derived cell for comparison with a DFIP fact value.

    A VLOOKUP that hits an *empty* New Logic cell returns numeric 0 in Excel.
    That 0 is a spreadsheet artifact, not a business label, so DFIP stores NULL
    and reconciliation treats the two as equal. A real string '0' is untouched.
    """
    if isinstance(value, str):
        return value
    if value == 0:
        return None
    return value


def expected_rate(rate_card_version_label: str, template_status: str | None, channel: str | None):
    """Recompute the contract rate independently of the engine."""
    if isinstance(template_status, str) and template_status.lower() == "utility":
        return _RATES.get((rate_card_version_label, "template_status", "utility"))
    if isinstance(channel, str):
        return _RATES.get((rate_card_version_label, "channel", channel.lower()))
    return None


def reconcile_fact(fact: FactRecord, rate_card_version_label: str) -> list[str]:
    """Recompute every derived value for one fact. Returns failure descriptions."""
    problems: list[str] = []
    where = f"{fact.campaign_id}/{fact.variation_id_key}/{fact.day.isoformat()}"

    rate = expected_rate(rate_card_version_label, fact.template_status, fact.channel)
    units = Decimal(0 if fact.delivered is None else fact.delivered)
    want_cost = ZERO_COST if rate is None else (units * rate).quantize(ZERO_COST)
    if fact.total_cost != want_cost:
        problems.append(f"{where}: total_cost {fact.total_cost} != Delivered x rate {want_cost}")
    if rate is None and fact.rate_card_rule_id is not None:
        problems.append(f"{where}: unmatched rate rule but rate_card_rule_id is set")
    if rate is not None and fact.rate_card_rule_id is None:
        # Packaged fallback stores neither rate_card_version_id nor a foreign
        # rate_card_rule UUID. Cost math still uses packaged labels.
        if fact.rate_card_version_id is not None:
            problems.append(f"{where}: matched rate rule but rate_card_rule_id is NULL")

    if fact.month_label != month_label(fact.day):
        problems.append(f"{where}: month_label {fact.month_label!r}")
    if fact.month_start != month_start(fact.day):
        problems.append(f"{where}: month_start {fact.month_start!r}")
    if fact.hhh != hhh(fact.start_date):
        problems.append(f"{where}: hhh {fact.hhh!r}")

    if fact.variation_id_key == "" and fact.variation_id not in (None, ""):
        problems.append(f"{where}: blank variation key for variation_id {fact.variation_id!r}")
    if fact.variation_id not in (None, "") and fact.variation_id_key != fact.variation_id:
        problems.append(f"{where}: variation_id_key does not mirror variation_id")

    if fact.template_status is None:
        problems.append(f"{where}: template_status is NULL; the contract uses '' for a miss")
    if fact.label_match_status != "matched" and fact.filter_logic_1 is not None:
        problems.append(f"{where}: labels present without a campaign match")
    return problems


def reconcile_run(
    facts: Iterable[FactRecord],
    *,
    rate_card_version_label: str,
    processing_run_id: str,
    campaign_label_version_id: str | None = None,
    template_label_version_id: str | None = None,
) -> ReconciliationReport:
    """Run every contract check over a batch of facts."""
    report = ReconciliationReport()
    for fact in facts:
        report.counts["facts"] += 1
        report.counts[f"label_{fact.label_match_status}"] += 1
        report.counts[f"template_{fact.template_match_status}"] += 1
        matched_rate = expected_rate(rate_card_version_label, fact.template_status, fact.channel)
        report.counts["rate_matched" if matched_rate is not None else "rate_unmatched"] += 1
        report.total_cost += fact.total_cost or ZERO_COST
        report.total_delivered += fact.delivered or 0

        problems = reconcile_fact(fact, rate_card_version_label)
        report.record("derived_values", not problems, "; ".join(problems))

        lineage_ok = fact.processing_run_id == processing_run_id and fact.batch_id is not None
        report.record(
            "lineage",
            lineage_ok,
            f"{fact.campaign_id}: fact is not bound to run {processing_run_id}",
        )
        version_ok = (
            campaign_label_version_id is None
            or fact.campaign_label_version_id == campaign_label_version_id
        ) and (
            template_label_version_id is None
            or fact.template_label_version_id == template_label_version_id
        )
        report.record(
            "configuration_versions",
            version_ok,
            f"{fact.campaign_id}: configuration version ids do not match the run binding",
        )
    return report
