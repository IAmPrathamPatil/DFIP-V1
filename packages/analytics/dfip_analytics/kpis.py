"""Authoritative KPI specification.

Formulas match `kpi_definition` rows seeded in migrations 04 (qa) and 09
(client). Calculations are applied only after additives are summed. Native
Web Engage rate columns are never inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from dfip_analytics.divide import MONEY_SCALE, RATE_SCALE, safe_add, safe_divide, safe_subtract

Operation = Literal["divide", "add", "subtract"]
ValueKind = Literal["rate", "money", "count"]
Namespace = Literal["qa", "client"]

# Additive fact measures that may be summed. Native Web Engage rate columns
# (failed_rate, delivered_rate_native, unique_click_rate, …) are excluded.
INTEGER_MEASURES: tuple[str, ...] = (
    "sent",
    "failed",
    "delivered",
    "unique_impressions",
    "unique_clicks",
    "unique_conversions",
    "unique_impression_through_conversions",
    "unique_click_through_conversions",
)
MONEY_MEASURES: tuple[str, ...] = (
    "revenue_inr",
    "impression_through_revenue_inr",
    "click_through_revenue_inr",
    "total_cost",
)
ADDITIVE_MEASURES: tuple[str, ...] = INTEGER_MEASURES + MONEY_MEASURES

# Evidence-only source fields. Must never appear as KPI inputs.
NATIVE_RATE_MEASURES: frozenset[str] = frozenset(
    {
        "failed_rate",
        "queued_rate",
        "delivered_rate_native",
        "unique_impression_rate",
        "unique_click_rate",
        "unique_conversion_rate",
        "unique_impression_through_conversion_rate",
        "unique_click_through_conversion_rate",
        "unique_control_group_conversion_rate",
    }
)

# Requested Amazon-style names that the source schema does not support.
UNAVAILABLE_METRICS: tuple[tuple[str, str], ...] = (
    ("orders", "No order count exists. Use unique_conversions or click-through conversions."),
    ("amazon_cpc", "Not a recovered KPI. Closest recovered metric is Cost/Conv."),
    ("acos", "Amazon advertising metric. Not present. Do not invert ROAS."),
    ("product_asin", "Not a fact grain. amc_product_cat_filter_logic_5 is a campaign slicer."),
)

# Friendly aliases onto additive measures (documentation / Power BI labels).
AVAILABLE_ALIASES: dict[str, str] = {
    "impressions": "unique_impressions",
    "clicks": "unique_clicks",
    "spend": "total_cost",
    "sales": "revenue_inr",
}


@dataclass(frozen=True)
class KpiSpec:
    """One recovered KPI. The engine is the only runtime formula source."""

    id: str
    namespace: Namespace
    display_name: str
    slug: str
    formula: str
    numerator_measure: str | None
    denominator_measure: str | None
    operation: Operation
    is_linear: bool
    solve_order: int
    depends_on_kpi_id: str | None
    is_used_by_reports: bool
    notes: str | None
    description: str
    unit: str
    value_kind: ValueKind
    grain: str = "requested reporting grain after SUM of additives"
    aggregation: str = "SUM numerators and denominators, then apply the operation"
    null_behavior: str = "NULL operand after aggregation yields NULL"
    zero_denominator_behavior: str = "Divide by zero yields NULL, never 0"
    calculated_from_aggregated_values: bool = True

    @property
    def additive(self) -> bool:
        return self.is_linear

    @property
    def power_bi_safe_to_sum(self) -> bool:
        return self.is_linear

    @property
    def decimal_scale(self) -> Decimal:
        if self.value_kind == "rate":
            return RATE_SCALE
        if self.value_kind == "money":
            return MONEY_SCALE
        return Decimal("1")


def _kpi(
    id: str,
    namespace: Namespace,
    display_name: str,
    slug: str,
    formula: str,
    numerator_measure: str | None,
    denominator_measure: str | None,
    operation: Operation,
    is_linear: bool,
    solve_order: int,
    depends_on_kpi_id: str | None,
    is_used_by_reports: bool,
    notes: str | None,
    description: str,
    unit: str,
    value_kind: ValueKind,
) -> KpiSpec:
    return KpiSpec(
        id=id,
        namespace=namespace,
        display_name=display_name,
        slug=slug,
        formula=formula,
        numerator_measure=numerator_measure,
        denominator_measure=denominator_measure,
        operation=operation,
        is_linear=is_linear,
        solve_order=solve_order,
        depends_on_kpi_id=depends_on_kpi_id,
        is_used_by_reports=is_used_by_reports,
        notes=notes,
        description=description,
        unit=unit,
        value_kind=value_kind,
    )


_QA_DEP_CLICK_PLUS_VIEW = "a0000000-0000-4000-8000-000000000210"

KPI_SPECS: tuple[KpiSpec, ...] = (
    _kpi(
        "a0000000-0000-4000-8000-000000000201",
        "qa",
        "CTR",
        "ctr",
        "'Unique Clicks'/Delivered",
        "unique_clicks",
        "delivered",
        "divide",
        False,
        1,
        None,
        True,
        None,
        "Recovered Q&A CTR. Unique clicks divided by delivered, not by impressions.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000202",
        "qa",
        "Conversion Rate",
        "conversion_rate",
        "'Unique Conversions'/Delivered",
        "unique_conversions",
        "delivered",
        "divide",
        False,
        2,
        None,
        True,
        None,
        "Unique conversions divided by delivered. Not Amazon CVR (orders/clicks).",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000203",
        "qa",
        "ROAS",
        "roas",
        "'Revenue (INR)'/'Total Cost'",
        "revenue_inr",
        "total_cost",
        "divide",
        False,
        3,
        None,
        True,
        None,
        "Revenue (INR) divided by Total Cost (Delivered × rate card).",
        "ratio",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000204",
        "qa",
        "Delivered Rate*",
        "delivered_rate_star",
        "Delivered/Sent",
        "delivered",
        "sent",
        "divide",
        False,
        4,
        None,
        True,
        "Exact recovered name includes trailing asterisk.",
        "Delivered divided by sent. Trailing asterisk is the recovered display name.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000205",
        "qa",
        "Cost/Conv",
        "cost_conv",
        "'Total Cost'/'Unique Click-Through Conversions'",
        "total_cost",
        "unique_click_through_conversions",
        "divide",
        False,
        5,
        None,
        True,
        None,
        "Total Cost divided by unique click-through conversions.",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000206",
        "qa",
        "Click thou Conv Rate",
        "click_thou_conv_rate",
        "'Unique Click-Through Conversions'/Delivered",
        "unique_click_through_conversions",
        "delivered",
        "divide",
        False,
        6,
        None,
        True,
        None,
        "Unique click-through conversions divided by delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000207",
        "qa",
        "Click Through Cost/Conv",
        "click_through_cost_conv",
        "'Total Cost'/'Unique Click-Through Conversions'",
        "total_cost",
        "unique_click_through_conversions",
        "divide",
        False,
        7,
        None,
        True,
        "Duplicate formula of Cost/Conv. Both retained.",
        "Duplicate of Cost/Conv. Both recovered names are retained.",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000208",
        "qa",
        "Cost/ View through Conv ",
        "cost_view_through_conv",
        "'Total Cost'/'Unique Impression-Through Conversions'",
        "total_cost",
        "unique_impression_through_conversions",
        "divide",
        False,
        8,
        None,
        True,
        "Exact recovered name includes trailing space.",
        "Total Cost divided by unique impression-through conversions.",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000209",
        "qa",
        "View through Conv Rate",
        "view_through_conv_rate",
        "'Unique Impression-Through Conversions'/Delivered",
        "unique_impression_through_conversions",
        "delivered",
        "divide",
        False,
        9,
        None,
        True,
        None,
        "Unique impression-through conversions divided by delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000210",
        "qa",
        "Click + View Conv ",
        "click_plus_view_conv",
        "'Unique Impression-Through Conversions'+'Unique Click-Through Conversions'",
        "unique_impression_through_conversions",
        "unique_click_through_conversions",
        "add",
        True,
        10,
        None,
        True,
        "Exact recovered name includes trailing space. Linear.",
        "Sum of impression-through and click-through conversions. Additive.",
        "count",
        "count",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000211",
        "qa",
        "Click + View Revenue",
        "click_plus_view_revenue",
        "'Impression-Through Revenue (INR)'+'Click-Through Revenue (INR)'",
        "impression_through_revenue_inr",
        "click_through_revenue_inr",
        "add",
        True,
        11,
        None,
        True,
        "Linear.",
        "Sum of impression-through and click-through revenue. Additive.",
        "INR",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000212",
        "qa",
        "Cost/ Click + View Conv ",
        "cost_click_plus_view_conv",
        "'Total Cost'/'Click + View Conv '",
        "total_cost",
        None,
        "divide",
        False,
        12,
        _QA_DEP_CLICK_PLUS_VIEW,
        True,
        "Depends on KPI 10. Exact recovered name includes trailing space.",
        "Total Cost divided by Click + View Conv (solve_order after the add).",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000213",
        "qa",
        "All Click + View Conv Rate",
        "all_click_plus_view_conv_rate",
        "'Click + View Conv '/Delivered",
        None,
        "delivered",
        "divide",
        False,
        13,
        _QA_DEP_CLICK_PLUS_VIEW,
        True,
        "Depends on KPI 10.",
        "Click + View Conv divided by delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000214",
        "qa",
        "Delivered to Imp. Rate",
        "delivered_to_imp_rate",
        "'Unique Impressions'/Delivered",
        "unique_impressions",
        "delivered",
        "divide",
        False,
        14,
        None,
        True,
        None,
        "Unique impressions divided by delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000215",
        "qa",
        "CTR ( Del to Click )",
        "ctr_del_to_click",
        "'Unique Clicks'/Delivered",
        "unique_clicks",
        "delivered",
        "divide",
        False,
        15,
        None,
        True,
        "Duplicate formula of CTR. Both retained.",
        "Duplicate of CTR (clicks/delivered). Both recovered names are retained.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000216",
        "qa",
        "CTR ( Impr. to Click )",
        "ctr_impr_to_click",
        "'Unique Clicks'/'Unique Impressions'",
        "unique_clicks",
        "unique_impressions",
        "divide",
        False,
        16,
        None,
        True,
        None,
        "Unique clicks divided by unique impressions. Not the recovered Q&A CTR.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000301",
        "client",
        "Delivery Rate",
        "delivery_rate",
        "Delivered/Sent",
        "delivered",
        "sent",
        "divide",
        False,
        1,
        None,
        True,
        None,
        "Client-report delivery rate: delivered / sent.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000302",
        "client",
        "Delivered to Imp. rate",
        "delivered_to_imp_rate",
        "'Unique Impressions'/Delivered",
        "unique_impressions",
        "delivered",
        "divide",
        False,
        2,
        None,
        True,
        None,
        "Client-report unique impressions / delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000303",
        "client",
        "CTR (Del to Clicks)",
        "ctr_del_to_clicks",
        "'Unique Clicks'/Delivered",
        "unique_clicks",
        "delivered",
        "divide",
        False,
        3,
        None,
        True,
        None,
        "Client-report clicks / delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000304",
        "client",
        "CTR ( Impr. to Click )",
        "ctr_impr_to_click",
        "'Unique Clicks'/'Unique Impressions'",
        "unique_clicks",
        "unique_impressions",
        "divide",
        False,
        4,
        None,
        True,
        None,
        "Client-report clicks / unique impressions.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000305",
        "client",
        "Click Through Cost/Conv",
        "click_through_cost_conv",
        "'Total Cost'/'Unique Click-Through Conversions'",
        "total_cost",
        "unique_click_through_conversions",
        "divide",
        False,
        5,
        None,
        False,
        "Defined in client cache. Unused by the nine reports.",
        "Client-cache Cost/Conv. Unused by the nine reports.",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000306",
        "client",
        "Cost/Unique Conversion",
        "cost_unique_conversion",
        "'Total Cost'/'Unique Conversions'",
        "total_cost",
        "unique_conversions",
        "divide",
        False,
        6,
        None,
        True,
        None,
        "Total Cost divided by unique conversions.",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000307",
        "client",
        "Delivered Thru Conv. rate",
        "delivered_thru_conv_rate",
        "'Unique Conversions'/Delivered",
        "unique_conversions",
        "delivered",
        "divide",
        False,
        7,
        None,
        True,
        None,
        "Unique conversions divided by delivered.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000308",
        "client",
        "Cost/ UCT conversion",
        "cost_uct_conversion",
        "'Total Cost'/'Unique Click-Through Conversions'",
        "total_cost",
        "unique_click_through_conversions",
        "divide",
        False,
        8,
        None,
        True,
        "Duplicate formula of Click Through Cost/Conv. Both retained.",
        "Duplicate of client Click Through Cost/Conv. Both retained.",
        "INR per conversion",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000309",
        "client",
        "UCT conversion rate",
        "uct_conversion_rate",
        "'Unique Click-Through Conversions'/'Unique Clicks'",
        "unique_click_through_conversions",
        "unique_clicks",
        "divide",
        False,
        9,
        None,
        True,
        None,
        "Unique click-through conversions divided by unique clicks.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000310",
        "client",
        "UC to UCTC Conversions",
        "uc_to_uctc_conversions",
        "'Unique Conversions'-'Unique Click-Through Conversions'",
        "unique_conversions",
        "unique_click_through_conversions",
        "subtract",
        True,
        10,
        None,
        False,
        "Defined in client cache. Unused by the nine reports.",
        "Unique conversions minus unique click-through conversions. Additive.",
        "count",
        "count",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000311",
        "client",
        "UC to UCTC Revenue",
        "uc_to_uctc_revenue",
        "'Revenue (INR)'-'Click-Through Revenue (INR)'",
        "revenue_inr",
        "click_through_revenue_inr",
        "subtract",
        True,
        11,
        None,
        False,
        "Defined in client cache. Unused by the nine reports.",
        "Revenue minus click-through revenue. Additive.",
        "INR",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000312",
        "client",
        "Actual Sent",
        "actual_sent",
        "Sent-Failed",
        "sent",
        "failed",
        "subtract",
        True,
        12,
        None,
        True,
        "Linear.",
        "Sent minus failed. Additive.",
        "count",
        "count",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000313",
        "client",
        "Failed Rate SM",
        "failed_rate_sm",
        "Failed/Sent",
        "failed",
        "sent",
        "divide",
        False,
        13,
        None,
        True,
        None,
        "Failed divided by sent. Does not use the native Failed Rate column.",
        "ratio",
        "rate",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000314",
        "client",
        "Unique Click Through Conv ROAS",
        "unique_click_through_conv_roas",
        "'Click-Through Revenue (INR)'/'Total Cost'",
        "click_through_revenue_inr",
        "total_cost",
        "divide",
        False,
        14,
        None,
        True,
        None,
        "Click-through revenue divided by Total Cost.",
        "ratio",
        "money",
    ),
    _kpi(
        "a0000000-0000-4000-8000-000000000315",
        "client",
        "Overall ROAS",
        "overall_roas",
        "'Revenue (INR)'/'Total Cost'",
        "revenue_inr",
        "total_cost",
        "divide",
        False,
        15,
        None,
        True,
        None,
        "Overall ROAS: revenue_inr / total_cost. Same formula as Q&A ROAS.",
        "ratio",
        "money",
    ),
)

_BY_ID: dict[str, KpiSpec] = {spec.id: spec for spec in KPI_SPECS}
_BY_NS_SLUG: dict[tuple[str, str], KpiSpec] = {
    (spec.namespace, spec.slug): spec for spec in KPI_SPECS
}


def specs_for(namespace: Namespace | None = None) -> tuple[KpiSpec, ...]:
    if namespace is None:
        return KPI_SPECS
    return tuple(spec for spec in KPI_SPECS if spec.namespace == namespace)


def spec_by_slug(namespace: Namespace, slug: str) -> KpiSpec:
    return _BY_NS_SLUG[(namespace, slug)]


def _operand(
    spec: KpiSpec,
    side: str,
    measures: dict[str, Decimal | None],
    computed: dict[str, Decimal | None],
) -> Decimal | None:
    measure = spec.numerator_measure if side == "numerator" else spec.denominator_measure
    if measure is not None:
        if measure in NATIVE_RATE_MEASURES:
            raise RuntimeError(f"KPI {spec.slug} must not read native rate {measure}")
        return measures.get(measure)
    if spec.depends_on_kpi_id is None:
        return None
    parent = _BY_ID[spec.depends_on_kpi_id]
    return computed.get(parent.slug)


def compute_kpis(
    measures: dict[str, Decimal | None],
    *,
    namespace: Namespace | None = None,
) -> dict[str, Decimal | None]:
    """Apply KPI operations to already-aggregated measures.

    Dependent KPIs use `solve_order`. Ratio KPIs are never averaged.
    """
    for name in measures:
        if name in NATIVE_RATE_MEASURES:
            raise RuntimeError(f"native rate measure {name} is not a KPI input")
    results: dict[str, Decimal | None] = {}
    grouped: dict[str, list[KpiSpec]] = {"qa": [], "client": []}
    for spec in specs_for(namespace):
        grouped[spec.namespace].append(spec)
    namespaces: tuple[Namespace, ...]
    if namespace is None:
        namespaces = ("qa", "client")
    else:
        namespaces = (namespace,)
    output: dict[str, Decimal | None] = {}
    for ns in namespaces:
        results = {}
        for spec in sorted(grouped[ns], key=lambda item: item.solve_order):
            scale = spec.decimal_scale if spec.value_kind != "count" else None
            left = _operand(spec, "numerator", measures, results)
            right = _operand(spec, "denominator", measures, results)
            if spec.operation == "divide":
                value = safe_divide(left, right, scale=spec.decimal_scale)
            elif spec.operation == "add":
                value = safe_add(left, right, scale=scale)
            else:
                value = safe_subtract(left, right, scale=scale)
            results[spec.slug] = value
            output[f"{spec.namespace}.{spec.slug}"] = value
            if namespace is not None:
                output[spec.slug] = value
    return output
