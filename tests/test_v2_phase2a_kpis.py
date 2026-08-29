"""Phase 2A KPI specification and aggregation-before-ratio tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from dfip_analytics.aggregate import aggregate_facts, sum_additive_measures
from dfip_analytics.divide import MONEY_SCALE, RATE_SCALE, safe_divide
from dfip_analytics.grains import Grain, GrainError, parse_grain
from dfip_analytics.kpis import (
    ADDITIVE_MEASURES,
    AVAILABLE_ALIASES,
    KPI_SPECS,
    NATIVE_RATE_MEASURES,
    UNAVAILABLE_METRICS,
    compute_kpis,
    spec_by_slug,
    specs_for,
)
from dfip_core.transform.extract import NATIVE_RATE_HEADERS
from dfip_core.transform.fact import FactRecord
from dfip_db.paths import read_migrations
from dfip_db.sql_inspect import parse_insert_tuples, unquote_sql_string

CLIENT = "a0000000-0000-4000-8000-000000000001"
RUN = "d0000000-0000-4000-8000-000000000001"
BATCH = "c0000000-0000-4000-8000-000000000001"

BASE_MEASURES: dict[str, Decimal | None] = {
    "sent": Decimal("100"),
    "failed": Decimal("10"),
    "delivered": Decimal("80"),
    "unique_impressions": Decimal("70"),
    "unique_clicks": Decimal("20"),
    "unique_conversions": Decimal("4"),
    "unique_impression_through_conversions": Decimal("1"),
    "unique_click_through_conversions": Decimal("3"),
    "revenue_inr": Decimal("100.0000"),
    "impression_through_revenue_inr": Decimal("10.0000"),
    "click_through_revenue_inr": Decimal("40.0000"),
    "total_cost": Decimal("20.0000"),
}

EXPECTED_QA = {
    "ctr": Decimal("0.250000"),
    "conversion_rate": Decimal("0.050000"),
    "roas": Decimal("5.0000"),
    "delivered_rate_star": Decimal("0.800000"),
    "cost_conv": Decimal("6.6667"),
    "click_thou_conv_rate": Decimal("0.037500"),
    "click_through_cost_conv": Decimal("6.6667"),
    "cost_view_through_conv": Decimal("20.0000"),
    "view_through_conv_rate": Decimal("0.012500"),
    "click_plus_view_conv": Decimal("4"),
    "click_plus_view_revenue": Decimal("50.0000"),
    "cost_click_plus_view_conv": Decimal("5.0000"),
    "all_click_plus_view_conv_rate": Decimal("0.050000"),
    "delivered_to_imp_rate": Decimal("0.875000"),
    "ctr_del_to_click": Decimal("0.250000"),
    "ctr_impr_to_click": Decimal("0.285714"),
}

EXPECTED_CLIENT = {
    "delivery_rate": Decimal("0.800000"),
    "delivered_to_imp_rate": Decimal("0.875000"),
    "ctr_del_to_clicks": Decimal("0.250000"),
    "ctr_impr_to_click": Decimal("0.285714"),
    "click_through_cost_conv": Decimal("6.6667"),
    "cost_unique_conversion": Decimal("5.0000"),
    "delivered_thru_conv_rate": Decimal("0.050000"),
    "cost_uct_conversion": Decimal("6.6667"),
    "uct_conversion_rate": Decimal("0.150000"),
    "uc_to_uctc_conversions": Decimal("1"),
    "uc_to_uctc_revenue": Decimal("60.0000"),
    "actual_sent": Decimal("90"),
    "failed_rate_sm": Decimal("0.100000"),
    "unique_click_through_conv_roas": Decimal("2.0000"),
    "overall_roas": Decimal("5.0000"),
}


def _fact(**overrides: object) -> FactRecord:
    values: dict[str, object] = {
        "client_id": CLIENT,
        "campaign_id": "camp-1",
        "variation_id": "var-1",
        "variation_id_key": "var-1",
        "day": date(2025, 8, 1),
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
        "total_cost": Decimal("2.0000"),
        "processing_run_id": RUN,
        "batch_id": BATCH,
    }
    values.update(overrides)
    return FactRecord(**values)  # type: ignore[arg-type]


def test_qa_and_client_slug_sets_match_recovered_registry() -> None:
    qa = tuple(spec.slug for spec in specs_for("qa"))
    client = tuple(spec.slug for spec in specs_for("client"))
    assert qa == (
        "ctr",
        "conversion_rate",
        "roas",
        "delivered_rate_star",
        "cost_conv",
        "click_thou_conv_rate",
        "click_through_cost_conv",
        "cost_view_through_conv",
        "view_through_conv_rate",
        "click_plus_view_conv",
        "click_plus_view_revenue",
        "cost_click_plus_view_conv",
        "all_click_plus_view_conv_rate",
        "delivered_to_imp_rate",
        "ctr_del_to_click",
        "ctr_impr_to_click",
    )
    assert "actual_sent" in client
    assert "overall_roas" in client
    assert len(client) == 15
    assert len(KPI_SPECS) == 31


def test_python_specs_match_kpi_definition_seed() -> None:
    rows = parse_insert_tuples(read_migrations(), "kpi_definition")
    assert len(rows) == 31
    by_id = {spec.id: spec for spec in KPI_SPECS}
    for row in rows:
        kpi_id = unquote_sql_string(row[0])
        spec = by_id[kpi_id]
        assert spec.namespace == unquote_sql_string(row[1])
        assert spec.display_name == unquote_sql_string(row[2])
        assert spec.slug == unquote_sql_string(row[3])
        assert spec.formula == unquote_sql_string(row[4])
        num = unquote_sql_string(row[5]) or None
        den = unquote_sql_string(row[6]) or None
        assert spec.numerator_measure == num
        assert spec.denominator_measure == den
        assert spec.operation == unquote_sql_string(row[7])
        assert spec.is_linear is (row[8].strip().lower() == "true")
        assert spec.solve_order == int(row[9])
        dep = unquote_sql_string(row[10]) or None
        assert spec.depends_on_kpi_id == dep
        assert spec.is_used_by_reports is (row[11].strip().lower() == "true")
        notes = unquote_sql_string(row[12]) or None
        assert spec.notes == notes


@pytest.mark.parametrize("slug,expected", list(EXPECTED_QA.items()))
def test_each_qa_kpi(slug: str, expected: Decimal) -> None:
    result = compute_kpis(BASE_MEASURES, namespace="qa")
    assert result[slug] == expected
    spec = spec_by_slug("qa", slug)
    assert spec.calculated_from_aggregated_values is True
    if spec.operation == "divide":
        assert spec.power_bi_safe_to_sum is False


@pytest.mark.parametrize("slug,expected", list(EXPECTED_CLIENT.items()))
def test_each_client_kpi(slug: str, expected: Decimal) -> None:
    result = compute_kpis(BASE_MEASURES, namespace="client")
    assert result[slug] == expected


def test_dependent_kpis_use_solve_order() -> None:
    result = compute_kpis(BASE_MEASURES, namespace="qa")
    assert result["click_plus_view_conv"] == Decimal("4")
    assert result["cost_click_plus_view_conv"] == Decimal("5.0000")
    assert result["all_click_plus_view_conv_rate"] == Decimal("0.050000")
    click_plus = spec_by_slug("qa", "click_plus_view_conv")
    dependent = spec_by_slug("qa", "cost_click_plus_view_conv")
    assert dependent.solve_order > click_plus.solve_order
    assert dependent.depends_on_kpi_id == click_plus.id


def test_ratios_use_aggregated_numerators_not_average_of_daily_ratios() -> None:
    day1 = _fact(day=date(2025, 8, 1), unique_clicks=1, delivered=10)
    day2 = _fact(day=date(2025, 8, 2), unique_clicks=1, delivered=90)
    daily = aggregate_facts([day1, day2], Grain.DATE, namespace="qa")
    combined = aggregate_facts([day1, day2], Grain.CLIENT, namespace="qa")
    assert len(daily) == 2
    day_ctrs = [row.kpis["ctr"] for row in daily]
    assert day_ctrs == [Decimal("0.100000"), Decimal("0.011111")]
    average = (day_ctrs[0] + day_ctrs[1]) / Decimal("2")
    actual = combined[0].kpis["ctr"]
    assert actual == Decimal("0.020000")
    assert actual != average.quantize(RATE_SCALE)
    assert actual == safe_divide(Decimal("2"), Decimal("100"), scale=RATE_SCALE)


def test_null_denominator_yields_null() -> None:
    measures = dict(BASE_MEASURES)
    measures["delivered"] = None
    result = compute_kpis(measures, namespace="qa")
    assert result["ctr"] is None
    assert result["conversion_rate"] is None


def test_zero_denominator_yields_null() -> None:
    measures = dict(BASE_MEASURES)
    measures["delivered"] = Decimal("0")
    measures["total_cost"] = Decimal("0")
    measures["unique_impressions"] = Decimal("0")
    measures["unique_click_through_conversions"] = Decimal("0")
    measures["unique_impression_through_conversions"] = Decimal("0")
    result = compute_kpis(measures, namespace="qa")
    assert result["ctr"] is None
    assert result["roas"] is None
    assert result["ctr_impr_to_click"] is None
    assert result["cost_conv"] is None


def test_money_ratio_precision_matches_numeric_18_4() -> None:
    result = compute_kpis(BASE_MEASURES, namespace="qa")
    assert result["roas"] == Decimal("5.0000")
    assert result["cost_conv"].as_tuple().exponent == MONEY_SCALE.as_tuple().exponent
    assert result["ctr"].as_tuple().exponent == RATE_SCALE.as_tuple().exponent


def test_additive_measures_are_summed() -> None:
    a = _fact(sent=10, delivered=8, unique_clicks=2, total_cost=Decimal("2.0000"))
    b = _fact(
        day=date(2025, 8, 2),
        sent=5,
        delivered=4,
        unique_clicks=1,
        total_cost=Decimal("1.5000"),
    )
    totals = sum_additive_measures([a, b])
    assert totals["sent"] == Decimal("15")
    assert totals["delivered"] == Decimal("12")
    assert totals["unique_clicks"] == Decimal("3")
    assert totals["total_cost"] == Decimal("3.5000")


def test_native_rate_columns_are_rejected_as_inputs() -> None:
    with pytest.raises(RuntimeError, match="native rate"):
        compute_kpis({"failed_rate": Decimal("1"), **BASE_MEASURES}, namespace="qa")
    for spec in KPI_SPECS:
        assert spec.numerator_measure not in NATIVE_RATE_MEASURES
        assert spec.denominator_measure not in NATIVE_RATE_MEASURES
    for header in NATIVE_RATE_HEADERS:
        assert header.lower().replace(" ", "_") not in ADDITIVE_MEASURES


def test_amazon_metrics_are_unavailable() -> None:
    names = {item[0] for item in UNAVAILABLE_METRICS}
    assert names == {"orders", "amazon_cpc", "acos", "product_asin"}
    assert AVAILABLE_ALIASES["impressions"] == "unique_impressions"
    assert AVAILABLE_ALIASES["clicks"] == "unique_clicks"
    assert AVAILABLE_ALIASES["spend"] == "total_cost"
    assert AVAILABLE_ALIASES["sales"] == "revenue_inr"
    with pytest.raises(GrainError, match="Product/ASIN"):
        parse_grain("asin")
    with pytest.raises(GrainError, match="Product/ASIN"):
        parse_grain("client_product_date")


def test_reporting_grains_do_not_double_count() -> None:
    a = _fact(campaign_id="c1", unique_clicks=2, delivered=10)
    b = _fact(campaign_id="c2", unique_clicks=3, delivered=10)
    by_campaign = aggregate_facts([a, b], Grain.CAMPAIGN, namespace="qa")
    overall = aggregate_facts([a, b], Grain.CLIENT, namespace="qa")
    assert len(by_campaign) == 2
    assert sum(row.measures["unique_clicks"] or 0 for row in by_campaign) == Decimal("5")
    assert overall[0].measures["unique_clicks"] == Decimal("5")
    assert overall[0].kpis["ctr"] == Decimal("0.250000")


def test_empty_variation_key_is_a_distinct_grain() -> None:
    blank = _fact(variation_id=None, variation_id_key="", unique_clicks=1, delivered=10)
    named = _fact(variation_id="var-1", variation_id_key="var-1", unique_clicks=1, delivered=10)
    rows = aggregate_facts([blank, named], Grain.VARIATION, namespace="qa")
    keys = {row.key for row in rows}
    assert (CLIENT, "camp-1", "") in keys
    assert (CLIENT, "camp-1", "var-1") in keys
