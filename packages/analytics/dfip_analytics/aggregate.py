"""SUM additive fact measures first, then apply KPI operations.

Ratio KPIs must not be averaged across days, campaigns, or variations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from dfip_core.transform.fact import FactRecord

from dfip_analytics.divide import as_decimal
from dfip_analytics.grains import Grain, grain_key
from dfip_analytics.kpis import (
    ADDITIVE_MEASURES,
    INTEGER_MEASURES,
    MONEY_MEASURES,
    Namespace,
    compute_kpis,
)


@dataclass(frozen=True)
class AggregatedRow:
    grain: Grain
    key: tuple[object, ...]
    measures: dict[str, Decimal | None]
    kpis: dict[str, Decimal | None]


def _sum_measure(facts: Sequence[FactRecord], name: str) -> Decimal | None:
    total = Decimal("0")
    seen = False
    for fact in facts:
        raw = getattr(fact, name)
        value = as_decimal(raw)
        if value is None:
            continue
        seen = True
        total += value
    return total if seen else None


def sum_additive_measures(facts: Sequence[FactRecord]) -> dict[str, Decimal | None]:
    """SQL SUM semantics: skip NULL, NULL if every contributing value is NULL."""
    return {name: _sum_measure(facts, name) for name in ADDITIVE_MEASURES}


def aggregate_facts(
    facts: Iterable[FactRecord],
    grain: Grain,
    *,
    namespace: Namespace | None = None,
) -> list[AggregatedRow]:
    groups: dict[tuple[object, ...], list[FactRecord]] = defaultdict(list)
    for fact in facts:
        groups[grain_key(fact, grain)].append(fact)
    rows: list[AggregatedRow] = []
    for key in sorted(groups, key=lambda item: tuple(_sort_part(part) for part in item)):
        members = groups[key]
        measures = sum_additive_measures(members)
        kpis = compute_kpis(measures, namespace=namespace)
        rows.append(AggregatedRow(grain=grain, key=key, measures=measures, kpis=kpis))
    return rows


def _sort_part(part: object) -> tuple[int, Any]:
    if part is None:
        return (0, "")
    return (1, part)


def assert_no_native_rates(measures: dict[str, object]) -> None:
    from dfip_analytics.kpis import NATIVE_RATE_MEASURES

    overlap = NATIVE_RATE_MEASURES.intersection(measures)
    if overlap:
        raise RuntimeError(f"native rate measures are not KPI inputs: {sorted(overlap)}")


# Re-export measure lists so tests can assert the SUM set without importing kpis internals.
SUMMABLE_INTEGER_MEASURES = INTEGER_MEASURES
SUMMABLE_MONEY_MEASURES = MONEY_MEASURES
