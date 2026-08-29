"""Reconcile the P4 engine against the derived A:J values cached in the source
workbooks.

The workbooks are gitignored, so these tests skip when they are absent. When
they are present they are the strongest available evidence that the engine
reproduces the locked contract: every row is staged through the real P3 reader
and transformed by the real P4 engine, then compared against the values Excel
itself computed.

Two deliberate, documented divergences from the raw cell values:

1. A VLOOKUP that lands on an *empty* New Logic cell renders as numeric 0 in
   Excel. That 0 is a spreadsheet artifact, not a business label, so DFIP
   stores NULL. `excel_blank_equivalent` normalises it for comparison.
2. A VLOOKUP miss renders as the error text '#N/A'. DFIP stores NULL and marks
   `label_match_status = 'unmatched'`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from dfip_core.ingest.reader import build_source_row
from dfip_core.ingest.store import StagedRowRecord
from dfip_core.transform import ConfigBinder, excel_blank_equivalent, transform_row

openpyxl = pytest.importorskip("openpyxl")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSHEET = "Web-Engage Raw"
SOURCE_START_INDEX = 10  # column K
ROW_LIMIT = int(os.environ.get("DFIP_RECONCILE_ROWS", "4000"))
EXCEL_NA = "#N/A"
CLIENT_ID = "a0000000-0000-4000-8000-000000000001"


@dataclass(frozen=True)
class Vintage:
    glob: str
    campaign_version: str
    template_version: str
    rate_version: str
    day_from: date
    day_to: date


VINTAGES = (
    Vintage(
        "*Apr*Prod.xlsx",
        "campaign-v1",
        "template-v1",
        "rate-v1",
        date(2025, 4, 1),
        date(2025, 5, 31),
    ),
    Vintage(
        "*Oct*Prod.xlsx",
        "campaign-v2",
        "template-v4",
        "rate-v2",
        date(2025, 10, 1),
        date(2025, 10, 31),
    ),
)


def find_workbook(pattern: str) -> Path | None:
    matches = sorted(REPO_ROOT.glob(pattern))
    return matches[0] if matches else None


def label_matches(fact_value, excel_value) -> bool:
    """Compare one campaign label output against its Excel cell."""
    expected = excel_blank_equivalent(excel_value)
    if expected == EXCEL_NA:
        return fact_value is None
    return fact_value == expected


@dataclass
class Tally:
    compared: int = 0
    label_mismatch: list[str] = field(default_factory=list)
    template_mismatch: list[str] = field(default_factory=list)
    cost_mismatch: list[str] = field(default_factory=list)
    month_mismatch: list[str] = field(default_factory=list)
    hhh_mismatch: list[str] = field(default_factory=list)


def reconcile_workbook(path: Path, vintage: Vintage, *, row_limit: int | None = None) -> Tally:
    """Compare derived A:J against Excel. ``row_limit=-1`` means no cap."""
    cap = ROW_LIMIT if row_limit is None else row_limit
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tally = Tally()
    binder = ConfigBinder()
    try:
        worksheet = workbook[WORKSHEET]
        for excel_row, values in enumerate(
            worksheet.iter_rows(min_row=2, max_col=67, values_only=True), start=2
        ):
            if cap >= 0 and tally.compared >= cap:
                break
            source = build_source_row(excel_row, list(values), SOURCE_START_INDEX)
            if source.is_empty or source.day is None:
                continue
            if not (vintage.day_from <= source.day <= vintage.day_to):
                continue

            outcome = transform_row(
                StagedRowRecord(
                    id=f"row-{excel_row}",
                    batch_id="reconcile",
                    source_row_number=excel_row,
                    raw=source.raw,
                    campaign_id=source.campaign_id,
                    variation_id=source.variation_id,
                    day=source.day,
                ),
                binder,
                client_id=CLIENT_ID,
                batch_id="reconcile",
                processing_run_id="reconcile",
            )
            if outcome.fact is None:
                continue
            fact = outcome.fact
            tally.compared += 1

            bundle = binder.for_day(source.day)
            assert bundle.campaign_version_label == vintage.campaign_version
            assert bundle.template_version_label == vintage.template_version
            assert bundle.rate_card_version_label == vintage.rate_version

            a, b, c, d, e, f, g, h, i, j = values[0:10]
            pairs = (
                ("Filter Logic 1", fact.filter_logic_1, a),
                ("Filter Logic 2", fact.filter_logic_2, b),
                ("AMC Status FL3", fact.amc_status_filter_logic_3, d),
                ("AMC Device FL4", fact.amc_device_category_filter_logic_4, e),
                ("AMC Product FL5", fact.amc_product_cat_filter_logic_5, f),
                ("Manual Or Automated", fact.manual_or_automated, g),
            )
            for name, got, want in pairs:
                if not label_matches(got, want):
                    tally.label_mismatch.append(f"row {excel_row} {name}: {got!r} != {want!r}")

            want_c = "" if c is None else c
            if fact.template_status != want_c:
                tally.template_mismatch.append(
                    f"row {excel_row}: {fact.template_status!r} != {want_c!r}"
                )

            want_h = Decimal("0") if h is None else Decimal(str(h))
            if abs((fact.total_cost or Decimal("0")) - want_h) > Decimal("0.0001"):
                tally.cost_mismatch.append(f"row {excel_row}: {fact.total_cost} != {want_h}")

            if fact.month_label != j:
                tally.month_mismatch.append(f"row {excel_row}: {fact.month_label!r} != {j!r}")
            if fact.hhh != i:
                tally.hhh_mismatch.append(f"row {excel_row}: {fact.hhh!r} != {i!r}")
    finally:
        workbook.close()
    return tally


@pytest.mark.parametrize("vintage", VINTAGES, ids=lambda v: v.rate_version)
def test_engine_reproduces_excel_derived_columns(vintage: Vintage) -> None:
    path = find_workbook(vintage.glob)
    if path is None:
        pytest.skip(f"source workbook {vintage.glob} is not in this checkout")

    tally = reconcile_workbook(path, vintage)
    assert tally.compared > 0, f"no rows in {vintage.day_from}..{vintage.day_to}"
    assert tally.cost_mismatch == [], tally.cost_mismatch[:5]
    assert tally.template_mismatch == [], tally.template_mismatch[:5]
    assert tally.label_mismatch == [], tally.label_mismatch[:5]
    assert tally.month_mismatch == [], tally.month_mismatch[:5]
    assert tally.hhh_mismatch == [], tally.hhh_mismatch[:5]


def test_source_workbooks_are_never_written() -> None:
    """Guard: the reconciliation path opens workbooks read-only."""
    path = find_workbook(VINTAGES[1].glob)
    if path is None:
        pytest.skip("source workbook is not in this checkout")
    before = path.stat().st_mtime_ns
    reconcile_workbook(path, VINTAGES[1])
    assert path.stat().st_mtime_ns == before
