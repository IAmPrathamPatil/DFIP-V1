"""P10 report-layer identity: Pivot-style case coalesce without rewriting facts."""

from __future__ import annotations

from decimal import Decimal

from dfip_web.daily_report import (
    AMC_DAYWISE,
    AMC_SPLIT,
    AMC_VERTICAL,
    CHANNEL,
    CONTRACTS_BY_NAME,
    D2C,
    OVERALL,
    SERVICE,
    SUB_SPLIT,
    VERTICAL,
    contract_uses_case_identity,
    report_display_equal,
    report_display_key,
    report_formula,
    worksheet_xml,
)


def test_only_campaign_and_fl2_sheets_use_case_identity() -> None:
    expected = {
        OVERALL: False,
        VERTICAL: False,
        CHANNEL: False,
        SUB_SPLIT: True,
        AMC_DAYWISE: False,
        AMC_SPLIT: True,
        AMC_VERTICAL: True,
        D2C: True,
        SERVICE: False,
    }
    for name, uses in expected.items():
        contract = CONTRACTS_BY_NAME[name]
        assert contract_uses_case_identity(contract) is uses, name
        formula = report_formula(contract)
        if uses:
            assert "LOWER(" in formula, name
            assert "filteredKeys" in formula, name
            assert "MATCH(concatUniq,concatSrc,0)" in formula, name
            assert "uniqKeys,origKeys" in formula, name
        else:
            assert "LOWER(" not in formula, name
            assert "filteredKeys" not in formula, name
            assert "uniqKeys,UNIQUE(FILTER(" in formula, name


def test_sub_split_folds_campaign_name_not_filter_logic_1() -> None:
    formula = report_formula(CONTRACTS_BY_NAME[SUB_SPLIT])
    assert "LOWER(INDEX(filteredKeys,0,5))" in formula
    assert "LOWER(INDEX(filteredKeys,0,4))" not in formula
    xml = worksheet_xml(CONTRACTS_BY_NAME[SUB_SPLIT])
    assert "_xlpm.filteredKeys" in xml
    assert "LAMBDA" not in xml
    assert "GROUPBY" not in xml
    assert len(formula) < 8192


def test_d2c_and_amc_fold_filter_logic_2() -> None:
    d2c = report_formula(CONTRACTS_BY_NAME[D2C])
    amc = report_formula(CONTRACTS_BY_NAME[AMC_SPLIT])
    vertical = report_formula(CONTRACTS_BY_NAME[AMC_VERTICAL])
    # groupby includes month_start: D2C/AMC Vertical FL2 is column 4; AMC Split FL2 is 2.
    assert "LOWER(INDEX(filteredKeys,0,4))" in d2c
    assert "LOWER(INDEX(filteredKeys,0,2))" in amc
    assert "LOWER(INDEX(filteredKeys,0,4))" in vertical
    assert "LOWER(INDEX(filteredKeys,0,1))" not in d2c
    assert "LOWER(INDEX(filteredKeys,0,1))" not in amc


def test_report_display_key_coalesces_campaign_case_variants() -> None:
    fields = CONTRACTS_BY_NAME[SUB_SPLIT].display_fields
    lower = {
        "Month": "Aug-25",
        "filter_logic_1_group": "Group7",
        "Filter Logic 1": "AMC | Renewal",
        "Campaign Name": "renewal T-02",
        "Sent": 18,
    }
    mixed = {**lower, "Campaign Name": "Renewal T-02"}
    assert report_display_key(lower, fields) == report_display_key(mixed, fields)
    assert report_display_key(lower, fields)[-1] == "renewal t-02"


def test_report_display_key_coalesces_filter_logic_2_case() -> None:
    fields = CONTRACTS_BY_NAME[D2C].display_fields
    a = {
        "Filter Logic 1": "D2C | Sale Assist",
        "Month": "Aug-25",
        "Filter Logic 2": "sale_assist_your_appointment_is_confirmed",
    }
    b = {**a, "Filter Logic 2": "Sale_assist_your_appointment_is_confirmed"}
    assert report_display_key(a, fields) == report_display_key(b, fields)
    other = {**a, "Filter Logic 2": "different_template"}
    assert report_display_key(a, fields) != report_display_key(other, fields)


def test_money_display_equal_accepts_binary_float_noise() -> None:
    cache = Decimal("6623878.230000000089")
    dfip = Decimal("6623878.2300")
    assert report_display_equal(cache, dfip, kind="money")
    assert report_display_equal(cache, dfip, kind="roas")
    midpoint_cache = Decimal("199378.19499999999989948")
    midpoint_dfip = Decimal("199378.1950")
    assert report_display_equal(midpoint_cache, midpoint_dfip, kind="money")
    assert not report_display_equal(Decimal("11.6150"), Decimal("11.6140"), kind="money")
    assert not report_display_equal(cache, dfip + Decimal("0.01"), kind="money")
    assert report_display_equal(Decimal("10"), Decimal("10"), kind="count")
    assert not report_display_equal(Decimal("10"), Decimal("10.0000001"), kind="count")


def test_rate_display_equal_uses_percent_format_precision() -> None:
    left = Decimal("0.7516377429")
    right = Decimal("0.7516377429000001")
    assert report_display_equal(left, right, kind="rate")
    assert not report_display_equal(left, Decimal("0.7517"), kind="rate")


def test_sumifs_does_not_case_fold_published_facts() -> None:
    """SUMIFS stays case-insensitive on source strings; identity fold is UNIQUE-only."""
    from dfip_web.daily_report import REPORT_CONTRACTS, additive_formula

    for contract in REPORT_CONTRACTS:
        assert "LOWER(" not in additive_formula(contract, "Sent")
        assert "LOWER(" not in additive_formula(contract, "Total Cost")
    for name in (SUB_SPLIT, D2C, AMC_SPLIT):
        formula = report_formula(CONTRACTS_BY_NAME[name])
        assert "SUBSTITUTE(" not in formula
        assert "UPPER(" not in formula
        assert "PublishedFacts" in formula
