"""Power BI semantic-model contract for Phase 2B.

Does not change KPI formulas. DAX is generated from `kpi_definition` /
`KpiSpec` so ratios stay post-aggregation. Power BI may bind only the
published `rpt_*` objects listed here.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfip_analytics.kpis import KPI_SPECS, NATIVE_RATE_MEASURES, KpiSpec

FACT = "rpt_published_fact"

ALLOWED_TABLES: tuple[str, ...] = (
    "rpt_published_fact",
    "rpt_dim_date",
    "rpt_dim_client",
    "rpt_dim_campaign",
    "rpt_dim_variation",
)

FORBIDDEN_TABLES: tuple[str, ...] = (
    "fact_campaign_day",
    "fact_campaign_day_history",
    "stg_source_row",
    "stg_rejected_row",
    "processing_run",
    "qa_finding",
    "batch",
    "source_file",
    "publication",
    "publication_current",
)

UNAVAILABLE_IN_MODEL: tuple[str, ...] = (
    "Orders",
    "Amazon CPC",
    "ACOS",
    "ASIN",
    "Product grain",
)


@dataclass(frozen=True)
class Relationship:
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    cardinality: str
    cross_filter: str
    key_column: str


@dataclass(frozen=True)
class AdditiveMeasure:
    name: str
    column: str
    description: str
    format: str


@dataclass(frozen=True)
class HealthMeasure:
    name: str
    dax: str
    description: str


@dataclass(frozen=True)
class ReportPage:
    page_id: str
    title: str
    purpose: str
    slicers: tuple[str, ...]
    cards: tuple[str, ...]
    visuals: tuple[str, ...]
    drill_through: tuple[str, ...]
    notes: str


def unique_measure_name(spec: KpiSpec) -> str:
    """Power BI measure names cannot collide across namespaces."""
    collisions = [item for item in KPI_SPECS if item.display_name == spec.display_name]
    label = spec.display_name.strip()
    if len(collisions) > 1:
        prefix = "QA " if spec.namespace == "qa" else "Client "
        return prefix + label
    return label


def _sum(column: str) -> str:
    return f"SUM({FACT}[{column}])"


def _blank_safe_binary(left: str, right: str, op: str) -> str:
    return (
        f"VAR _left = {left}\n"
        f"VAR _right = {right}\n"
        f"RETURN IF ( OR ( ISBLANK ( _left ), ISBLANK ( _right ) ), BLANK (), _left {op} _right )"
    )


def dax_expression(spec: KpiSpec) -> str:
    """Return the DAX body for one recovered KPI.

    Ratios use DIVIDE(SUM(num), SUM(den)). Dependent KPIs reference the parent
    measure after the additive parts have been summed. Native rate columns are
    never referenced.
    """
    for field in (spec.numerator_measure, spec.denominator_measure):
        if field in NATIVE_RATE_MEASURES:
            raise RuntimeError(f"native rate {field} cannot appear in DAX")
    parent = None
    if spec.depends_on_kpi_id:
        parent = next(item for item in KPI_SPECS if item.id == spec.depends_on_kpi_id)
        left_parent = f"[{unique_measure_name(parent)}]"
    else:
        left_parent = ""
    if spec.operation == "divide":
        num = _sum(spec.numerator_measure) if spec.numerator_measure else left_parent
        den = _sum(spec.denominator_measure) if spec.denominator_measure else left_parent
        return f"DIVIDE ( {num}, {den} )"
    if spec.operation == "add":
        assert spec.numerator_measure and spec.denominator_measure
        return _blank_safe_binary(_sum(spec.numerator_measure), _sum(spec.denominator_measure), "+")
    if spec.operation == "subtract":
        assert spec.numerator_measure and spec.denominator_measure
        return _blank_safe_binary(_sum(spec.numerator_measure), _sum(spec.denominator_measure), "-")
    raise RuntimeError(f"unsupported operation {spec.operation}")


def kpi_measure_block(spec: KpiSpec) -> str:
    name = unique_measure_name(spec)
    body = dax_expression(spec)
    indented = body.replace("\n", "\n    ")
    return f"{name} =\n    {indented}"


ADDITIVE_MEASURES: tuple[AdditiveMeasure, ...] = (
    AdditiveMeasure("Sent", "sent", "SUM of sent", "#,0"),
    AdditiveMeasure("Failed", "failed", "SUM of failed", "#,0"),
    AdditiveMeasure("Delivered", "delivered", "SUM of delivered", "#,0"),
    AdditiveMeasure("Impressions", "unique_impressions", "SUM of unique_impressions", "#,0"),
    AdditiveMeasure("Clicks", "unique_clicks", "SUM of unique_clicks", "#,0"),
    AdditiveMeasure("Conversions", "unique_conversions", "SUM of unique_conversions", "#,0"),
    AdditiveMeasure(
        "Impression-Through Conversions",
        "unique_impression_through_conversions",
        "SUM of unique impression-through conversions",
        "#,0",
    ),
    AdditiveMeasure(
        "Click-Through Conversions",
        "unique_click_through_conversions",
        "SUM of unique click-through conversions",
        "#,0",
    ),
    AdditiveMeasure("Revenue", "revenue_inr", "SUM of revenue_inr (INR)", "#,0.00"),
    AdditiveMeasure(
        "Impression-Through Revenue",
        "impression_through_revenue_inr",
        "SUM of impression-through revenue",
        "#,0.00",
    ),
    AdditiveMeasure(
        "Click-Through Revenue",
        "click_through_revenue_inr",
        "SUM of click-through revenue",
        "#,0.00",
    ),
    AdditiveMeasure("Total Cost", "total_cost", "SUM of total_cost (INR)", "#,0.00"),
)


def additive_measure_block(measure: AdditiveMeasure) -> str:
    return f"{measure.name} =\n    {_sum(measure.column)}"


CONTEXT_MEASURES: tuple[tuple[str, str], ...] = (
    (
        "Report Period",
        "VAR _min = MIN ( rpt_dim_date[day] )\n"
        "    VAR _max = MAX ( rpt_dim_date[day] )\n"
        '    RETURN FORMAT ( _min, "DD MMM YYYY" ) & " – " & FORMAT ( _max, "DD MMM YYYY" )',
    ),
    (
        "Selected Client",
        "IF (\n"
        "        HASONEVALUE ( rpt_dim_client[name] ),\n"
        "        SELECTEDVALUE ( rpt_dim_client[name] ),\n"
        '        "All clients"\n'
        "    )",
    ),
)


HEALTH_MEASURES: tuple[HealthMeasure, ...] = (
    HealthMeasure(
        "Published Fact Rows",
        f"COUNTROWS ( {FACT} )",
        "Row count of the published slice currently in context.",
    ),
    HealthMeasure(
        "Published Campaigns",
        f"DISTINCTCOUNT ( {FACT}[campaign_id] )",
        "Distinct published campaigns in context.",
    ),
    HealthMeasure(
        "Published Days",
        f"DISTINCTCOUNT ( {FACT}[day] )",
        "Distinct published calendar days in context.",
    ),
    HealthMeasure(
        "Impossible Delivered Rows",
        f"CALCULATE ( COUNTROWS ( {FACT} ), FILTER ( {FACT}, "
        f"NOT ISBLANK ( {FACT}[delivered] ) && NOT ISBLANK ( {FACT}[sent] ) "
        f"&& {FACT}[delivered] > {FACT}[sent] ) )",
        "Published rows where delivered exceeds sent.",
    ),
    HealthMeasure(
        "Impossible Failed Rows",
        f"CALCULATE ( COUNTROWS ( {FACT} ), FILTER ( {FACT}, "
        f"NOT ISBLANK ( {FACT}[failed] ) && NOT ISBLANK ( {FACT}[sent] ) "
        f"&& {FACT}[failed] > {FACT}[sent] ) )",
        "Published rows where failed exceeds sent.",
    ),
    HealthMeasure(
        "Clicks Exceed Impressions Rows",
        f"CALCULATE ( COUNTROWS ( {FACT} ), FILTER ( {FACT}, "
        f"NOT ISBLANK ( {FACT}[unique_clicks] ) && NOT ISBLANK ( {FACT}[unique_impressions] ) "
        f"&& {FACT}[unique_clicks] > {FACT}[unique_impressions] ) )",
        "Published rows where unique clicks exceed unique impressions.",
    ),
    HealthMeasure(
        "Zero Delivery Rows",
        f"CALCULATE ( COUNTROWS ( {FACT} ), FILTER ( {FACT}, "
        f"NOT ISBLANK ( {FACT}[delivered] ) && {FACT}[delivered] = 0 ) )",
        "Published rows with delivered = 0 (zero-denominator risk for CTR).",
    ),
    HealthMeasure(
        "Unmatched Label Rows",
        f'CALCULATE ( COUNTROWS ( {FACT} ), {FACT}[label_match_status] <> "matched" )',
        "Published rows whose campaign label did not match.",
    ),
    HealthMeasure(
        "Unmatched Template Rows",
        f'CALCULATE ( COUNTROWS ( {FACT} ), {FACT}[template_match_status] <> "matched" )',
        "Published rows whose template status did not match.",
    ),
    HealthMeasure(
        "Blank Variation Rows",
        f'CALCULATE ( COUNTROWS ( {FACT} ), {FACT}[variation_id_key] = "" )',
        "Published rows that use the OPEN-A6 empty variation key.",
    ),
    HealthMeasure(
        "CTR Denominator Is Zero",
        "IF ( SUM ( rpt_published_fact[delivered] ) = 0, 1, 0 )",
        "1 when the filtered delivered total is zero so CTR is BLANK.",
    ),
    HealthMeasure(
        "ROAS Denominator Is Zero",
        "IF ( SUM ( rpt_published_fact[total_cost] ) = 0, 1, 0 )",
        "1 when the filtered total cost is zero so ROAS is BLANK.",
    ),
)


def health_measure_block(measure: HealthMeasure) -> str:
    return f"{measure.name} =\n    {measure.dax}"


RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship(
        "rpt_dim_date",
        ("day",),
        FACT,
        ("day",),
        "1:*",
        "single",
        "day",
    ),
    Relationship(
        "rpt_dim_client",
        ("client_id",),
        FACT,
        ("client_id",),
        "1:*",
        "single",
        "client_id",
    ),
    Relationship(
        "rpt_dim_campaign",
        ("client_id", "campaign_id"),
        FACT,
        ("client_id", "campaign_id"),
        "1:*",
        "single",
        "campaign_key",
    ),
    Relationship(
        "rpt_dim_variation",
        ("client_id", "campaign_id", "variation_id_key"),
        FACT,
        ("client_id", "campaign_id", "variation_id_key"),
        "1:*",
        "single",
        "variation_key",
    ),
)

DATE_TABLE = "rpt_dim_date"

POWER_QUERY_KEYS: tuple[tuple[str, str], ...] = (
    (
        "campaign_key",
        'Text.From([client_id]) & "|" & [campaign_id]',
    ),
    (
        "variation_key",
        'Text.From([client_id]) & "|" & [campaign_id] & "|" & [variation_id_key]',
    ),
)

PAGES: tuple[ReportPage, ...] = (
    ReportPage(
        "executive_overview",
        "Executive Overview",
        "Portfolio KPI cards, trend, and channel mix for the published slice.",
        (
            "rpt_dim_client[name]",
            "rpt_dim_date[day] (between)",
            "rpt_dim_campaign[channel]",
            "rpt_dim_campaign[type_of_campaign]",
        ),
        (
            "Sent",
            "Failed",
            "Delivered",
            "Impressions",
            "Clicks",
            "Conversions",
            "Revenue",
            "Total Cost",
            "CTR",
            "Conversion Rate",
            "ROAS",
            "Delivery Rate",
            "Cost/Conv",
        ),
        (
            "Line: Delivered, Clicks, Revenue by rpt_dim_date[day]",
            "Bar: Revenue and Total Cost by rpt_dim_campaign[channel]",
            "Table: campaign_name, Sent, Delivered, CTR, ROAS",
        ),
        ("campaign_name",),
        "Use Q&A CTR (clicks/delivered), not impression CTR, on this page.",
    ),
    ReportPage(
        "campaign_performance",
        "Campaign Performance",
        "Campaign ranking, comparison, channel/type breakdown, variation drill.",
        (
            "rpt_dim_client[name]",
            "rpt_dim_date[month_label]",
            "rpt_dim_campaign[channel]",
            "rpt_dim_campaign[type_of_campaign]",
            "rpt_dim_campaign[campaign_name]",
        ),
        ("Delivered", "CTR", "ROAS", "Total Cost", "Revenue"),
        (
            "Ranked bar: campaign_name by Revenue",
            "KPI table: campaign, channel, Sent, Delivered, Clicks, CTR, Conv Rate, ROAS",
            "Trend: campaign_name × day for Delivered and CTR",
            "Clustered bar: channel and type_of_campaign by Delivered",
            "Comparison clustered bar: campaign_name by Delivered and Total Cost",
        ),
        ("rpt_dim_variation[variation_id_key]", "rpt_dim_variation[variation_name]"),
        "Empty variation_id_key is a valid OPEN-A6 member. Do not replace it with a sentinel.",
    ),
    ReportPage(
        "client_performance",
        "Client Performance",
        "Client KPIs, trend, and campaign contribution.",
        ("rpt_dim_client[name]", "rpt_dim_date[day] (between)", "rpt_dim_campaign[channel]"),
        ("Sent", "Delivered", "Revenue", "Total Cost", "CTR", "ROAS", "Actual Sent"),
        (
            "Matrix: client name × month_label for Revenue, CTR, ROAS",
            "Line: Revenue and Total Cost by day",
            "Stacked bar: campaign contribution to Revenue",
            "Table: client / campaign comparison for Delivered, Revenue, CTR, ROAS",
        ),
        ("campaign_name",),
        "Client isolation is enforced by PostgreSQL GUCs on rpt_*, not by this page.",
    ),
    ReportPage(
        "campaign_label_analysis",
        "Campaign Label / Product-Category Analysis",
        "Label-attribute analysis. Not an ASIN or product grain.",
        (
            "rpt_dim_client[name]",
            "rpt_dim_date[month_label]",
            "rpt_dim_campaign[amc_product_cat_filter_logic_5]",
            "rpt_dim_campaign[amc_device_category_filter_logic_4]",
            "rpt_dim_campaign[amc_status_filter_logic_3]",
            "rpt_dim_campaign[filter_logic_1_group]",
            "rpt_dim_campaign[manual_or_automated]",
        ),
        ("Delivered", "Revenue", "CTR", "ROAS"),
        (
            "Bar: amc_product_cat_filter_logic_5 by Revenue and Delivered",
            "Table: label attributes, campaigns, CTR, ROAS",
            "Treemap: filter_logic_1_group by Delivered",
        ),
        ("campaign_name",),
        "amc_product_cat_filter_logic_5 is a campaign slicer, not a product key.",
    ),
    ReportPage(
        "published_data_health",
        "Published Data Health",
        "Health of the published slice only. Inspector QA tables are not in this model.",
        ("rpt_dim_client[name]", "rpt_dim_date[day] (between)"),
        (
            "Published Fact Rows",
            "Impossible Delivered Rows",
            "Impossible Failed Rows",
            "Clicks Exceed Impressions Rows",
            "Zero Delivery Rows",
            "Unmatched Label Rows",
            "CTR Denominator Is Zero",
            "ROAS Denominator Is Zero",
        ),
        (
            "Trend: Published Fact Rows and Impossible Delivered Rows by day",
            "Table: campaign_name, day, Delivered, Sent, label_match_status, template_match_status",
        ),
        (),
        "Failed runs, rejected rows, reconcile failures, and qa_finding severity "
        "remain inspector-only. They are not sourced here.",
    ),
)

THEME = {
    "name": "DFIP Executive",
    "dataColors": ["#1B3A4B", "#2E7D4F", "#C45C26", "#4C6B8A", "#7A9E7E", "#B42318"],
    "background": "#F4F6F8",
    "foreground": "#1A1A1A",
    "tableAccent": "#1B3A4B",
    "good": "#2E7D4F",
    "neutral": "#4C6B8A",
    "bad": "#B42318",
    "maximum": "#1B3A4B",
    "center": "#C45C26",
    "minimum": "#B42318",
}


def render_measures_dax() -> str:
    lines = [
        "// DFIP Phase 2B — canonical measures.",
        "// Paste into Power BI Desktop on rpt_published_fact (model measures table).",
        "// Ratios are DIVIDE(SUM(num), SUM(den)). Do not average daily ratios.",
        "// Source tables allowed: " + ", ".join(ALLOWED_TABLES) + ".",
        "// Forbidden: " + ", ".join(FORBIDDEN_TABLES) + ".",
        "",
        "// --- Additive facts -------------------------------------------------",
        "",
    ]
    for measure in ADDITIVE_MEASURES:
        lines.append(additive_measure_block(measure))
        lines.append("")
    lines.append("// --- Recovered KPIs (aggregate first) -----------------------------")
    lines.append("")
    for spec in KPI_SPECS:
        lines.append(f"// {spec.namespace}.{spec.slug} :: {spec.formula}")
        lines.append(kpi_measure_block(spec))
        lines.append("")
    lines.append("// --- Report context ----------------------------------------------")
    lines.append("")
    for name, body in CONTEXT_MEASURES:
        lines.append(f"{name} =\n    {body}")
        lines.append("")
    lines.append("// --- Published-slice data health ---------------------------------")
    lines.append("// Not qa_finding. Inspector findings stay out of this model.")
    lines.append("")
    for measure in HEALTH_MEASURES:
        lines.append(f"// {measure.description}")
        lines.append(health_measure_block(measure))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
