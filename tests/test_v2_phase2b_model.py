"""Phase 2B Power BI contract: DAX lockstep, allowed tables, page specs."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from dfip_analytics.kpis import KPI_SPECS, NATIVE_RATE_MEASURES, UNAVAILABLE_METRICS
from dfip_analytics.powerbi_contract import (
    ALLOWED_TABLES,
    FORBIDDEN_TABLES,
    PAGES,
    dax_expression,
    render_measures_dax,
    unique_measure_name,
)

ROOT = Path(__file__).resolve().parents[1]
POWERBI = ROOT / "powerbi"


def test_measures_dax_matches_renderer() -> None:
    committed = (POWERBI / "dax" / "measures.dax").read_text(encoding="utf-8")
    assert committed == render_measures_dax()


def test_every_kpi_has_post_aggregation_dax() -> None:
    names = [unique_measure_name(spec) for spec in KPI_SPECS]
    assert len(names) == len(set(names))
    dax = render_measures_dax()
    for spec in KPI_SPECS:
        body = dax_expression(spec)
        assert unique_measure_name(spec) in dax
        if spec.operation == "divide":
            assert body.startswith("DIVIDE (")
            assert "SUM(" in body
            assert "AVERAGE" not in body
        else:
            assert "VAR _left" in body
            assert "SUM(" in body
        for native in NATIVE_RATE_MEASURES:
            assert native not in body


def test_ctr_is_clicks_over_delivered_not_average() -> None:
    ctr = dax_expression(
        next(spec for spec in KPI_SPECS if spec.slug == "ctr" and spec.namespace == "qa")
    )
    assert ctr == (
        "DIVIDE ( SUM(rpt_published_fact[unique_clicks]), SUM(rpt_published_fact[delivered]) )"
    )
    full = render_measures_dax()
    assert "AVERAGE(" not in full
    assert "SUM([CTR])" not in full
    assert "SUM ( [CTR] )" not in full


def test_dependent_kpis_reference_parent_measure() -> None:
    cost = next(spec for spec in KPI_SPECS if spec.slug == "cost_click_plus_view_conv")
    rate = next(spec for spec in KPI_SPECS if spec.slug == "all_click_plus_view_conv_rate")
    assert "[Click + View Conv]" in dax_expression(cost)
    assert dax_expression(rate).startswith("DIVIDE ( [Click + View Conv],")


def test_allowed_tables_only_in_semantic_model() -> None:
    model = json.loads((POWERBI / "model" / "semantic-model.json").read_text(encoding="utf-8"))
    assert tuple(model["allowed_tables"]) == ALLOWED_TABLES
    assert set(FORBIDDEN_TABLES) <= set(model["forbidden_tables"])
    assert model["date_table"]["table"] == "rpt_dim_date"
    assert model["drill_path"] == ["Client", "Date", "Campaign", "Variation", "Day"]
    assert "ASIN" in model["unavailable"]
    rels = {item["from"].split(".")[0] for item in model["relationships"]}
    assert rels == {
        "rpt_dim_date",
        "rpt_dim_client",
        "rpt_dim_campaign",
        "rpt_dim_variation",
    }


def test_m_queries_bind_only_rpt_objects() -> None:
    for path in (POWERBI / "queries").glob("*.m"):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_TABLES:
            assert f'"{forbidden}"' not in text, path.name
            assert f'[Name = "{forbidden}"' not in text
        assert "rpt_" in text


def test_pages_cover_required_dashboards() -> None:
    pages = json.loads((POWERBI / "model" / "pages.json").read_text(encoding="utf-8"))
    titles = [page["title"] for page in pages["pages"]]
    assert titles == [
        "Executive Overview",
        "Campaign Performance",
        "Client Performance",
        "Campaign Label / Product-Category Analysis",
        "Published Data Health",
    ]
    executive = pages["pages"][0]
    for card in (
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
    ):
        assert card in executive["cards"]
    label = pages["pages"][3]
    assert "not Product/ASIN" in label["banner"]
    health = pages["pages"][4]
    assert "qa_finding" in " ".join(health["inspector_not_in_model"])
    assert "qa_finding" not in json.dumps(health["visuals"])
    assert PAGES[3].page_id == "campaign_label_analysis"


def test_required_kpi_dax_matches_authoritative_spec() -> None:
    qa = {spec.slug: spec for spec in KPI_SPECS if spec.namespace == "qa"}
    client = {spec.slug: spec for spec in KPI_SPECS if spec.namespace == "client"}
    assert dax_expression(qa["ctr"]) == (
        "DIVIDE ( SUM(rpt_published_fact[unique_clicks]), SUM(rpt_published_fact[delivered]) )"
    )
    assert dax_expression(qa["conversion_rate"]) == (
        "DIVIDE ( SUM(rpt_published_fact[unique_conversions]), SUM(rpt_published_fact[delivered]) )"
    )
    assert dax_expression(qa["roas"]) == (
        "DIVIDE ( SUM(rpt_published_fact[revenue_inr]), SUM(rpt_published_fact[total_cost]) )"
    )
    assert dax_expression(qa["delivered_rate_star"]) == (
        "DIVIDE ( SUM(rpt_published_fact[delivered]), SUM(rpt_published_fact[sent]) )"
    )
    assert dax_expression(client["delivery_rate"]) == dax_expression(qa["delivered_rate_star"])
    assert dax_expression(qa["cost_conv"]) == (
        "DIVIDE ( SUM(rpt_published_fact[total_cost]), "
        "SUM(rpt_published_fact[unique_click_through_conversions]) )"
    )
    assert dax_expression(qa["click_thou_conv_rate"]) == (
        "DIVIDE ( SUM(rpt_published_fact[unique_click_through_conversions]), "
        "SUM(rpt_published_fact[delivered]) )"
    )
    assert dax_expression(qa["view_through_conv_rate"]) == (
        "DIVIDE ( SUM(rpt_published_fact[unique_impression_through_conversions]), "
        "SUM(rpt_published_fact[delivered]) )"
    )
    assert dax_expression(qa["ctr_impr_to_click"]) == (
        "DIVIDE ( SUM(rpt_published_fact[unique_clicks]), "
        "SUM(rpt_published_fact[unique_impressions]) )"
    )
    assert "[Click + View Conv]" in dax_expression(qa["cost_click_plus_view_conv"])
    assert dax_expression(qa["all_click_plus_view_conv_rate"]).startswith(
        "DIVIDE ( [Click + View Conv],"
    )
    assert "SUM(rpt_published_fact[sent])" in dax_expression(client["actual_sent"])
    assert dax_expression(client["overall_roas"]) == dax_expression(qa["roas"])


def test_kpi_mapping_lists_every_recovered_measure() -> None:
    mapping = (POWERBI / "model" / "kpi-mapping.md").read_text(encoding="utf-8")
    for spec in KPI_SPECS:
        assert unique_measure_name(spec) in mapping, unique_measure_name(spec)
    sql = (POWERBI / "sql" / "validation_kpis.sql").read_text(encoding="utf-8")
    assert "view_through_conv_rate" in sql
    assert "overall_roas" in sql
    assert "click_plus_view_conv" in sql
    assert "COALESCE" not in sql


def test_campaign_page_includes_comparison_and_channel_breakdown() -> None:
    pages = json.loads((POWERBI / "model" / "pages.json").read_text(encoding="utf-8"))
    campaign = pages["pages"][1]
    titles = [visual["title"] for visual in campaign["visuals"]]
    assert "Channel and campaign type" in titles
    assert "Campaign comparison" in titles
    client = pages["pages"][2]
    client_titles = [visual["title"] for visual in client["visuals"]]
    assert "Client / campaign comparison" in client_titles
    label = pages["pages"][3]
    assert any(visual["type"] == "treemap" for visual in label["visuals"])


def test_package_does_not_invent_amazon_metrics() -> None:
    mapping = (POWERBI / "model" / "kpi-mapping.md").read_text(encoding="utf-8")
    dax = render_measures_dax()
    assert "Orders" not in dax
    assert "ACOS" not in dax
    assert "Amazon CPC" not in dax
    names = {item[0] for item in UNAVAILABLE_METRICS}
    assert "acos" in names
    assert "Not in the model: Orders" in mapping


def test_no_pbix_is_fabricated() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.pbix" in gitignore
    assert "*.pbit" in gitignore
    assert (POWERBI / "README.md").is_file()
    assert (POWERBI / "VALIDATION.md").is_file()
    assert (POWERBI / "dax" / "measures.dax").is_file()
    readme = (POWERBI / "README.md").read_text(encoding="utf-8")
    assert "sql/reporting_login.example.sql" in readme
    assert "Do not unzip or fabricate a binary" in readme
    tracked = subprocess.check_output(
        ["git", "ls-files", "*.pbix", "*.pbit"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert tracked == ""


def test_reporting_login_example_is_operator_sql_not_a_migration() -> None:
    sql = (POWERBI / "sql" / "reporting_login.example.sql").read_text(encoding="utf-8")
    assert "NOT a migration" in sql
    assert "dfip_api is NOLOGIN" in sql
    assert "CREATE ROLE dfip_desktop_a LOGIN" in sql
    assert "CREATE ROLE dfip_desktop_b LOGIN" in sql
    assert "GRANT dfip_api TO dfip_desktop_a" in sql
    assert "GRANT dfip_api TO dfip_desktop_b" in sql
    assert "ALTER ROLE dfip_desktop_a SET ROLE dfip_api" in sql
    assert "ALTER ROLE dfip_desktop_b SET ROLE dfip_api" in sql
    assert "a0000000-0000-4000-8000-000000000001" in sql
    assert "a0000000-0000-4000-8000-000000000002" in sql
    assert "PASSWORD 'REPLACE_ME_CLIENT_A'" in sql
    assert "PASSWORD 'REPLACE_ME_CLIENT_B'" in sql
    passwords = re.findall(r"PASSWORD '([^']+)'", sql)
    assert passwords == ["REPLACE_ME_CLIENT_A", "REPLACE_ME_CLIENT_B"]
    migrations = list((ROOT / "supabase" / "migrations").glob("*reporting_login*"))
    assert migrations == []
    readme = (POWERBI / "README.md").read_text(encoding="utf-8")
    assert "reporting_login.example.sql" in readme
