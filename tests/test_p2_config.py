"""P2 configuration / business-logic data tests. No live database. No engines."""

from __future__ import annotations

import inspect
import re
from collections import Counter
from datetime import date

from dfip_config import resolve as resolve_mod
from dfip_config.rate_cards import RATE_CARD_RULES
from dfip_config.resolve import (
    resolve_campaign_label,
    resolve_filter_logic_1_group,
    resolve_group_display_name,
    resolve_rate_card_rule,
    resolve_template_status,
    select_version_for_day,
)
from dfip_config.store import (
    CAMPAIGN_OUTPUT_FIELDS,
    campaign_versions,
    load_campaign_version,
    load_label_group_captions,
    load_label_group_version,
    load_manifest,
    load_template_version,
    template_versions,
)
from dfip_db.catalog import QA_KPI_FORMULAS, QA_KPI_NAMES
from dfip_db.paths import migration_files, read_migrations, read_v1_migrations
from dfip_db.sql_inspect import parse_insert_tuples, unquote_sql_string

DUP_NAME = (
    "WP_Consumer_Survey_Champ & Delight - Eco | Between 18 to 24 months "
    "25/4/25 | Survey | Eco | Post Purchase | Event Base | Uty |"
)
LEADING_SPACE_NAME = (
    " d2c_indp_sale_wp_05082025 | D2C | independenceday sale | pre Sale | Checkout |mkt"
)
CAMPAIGN_EFFECTIVE = (
    ("campaign-v1", "2025-04-01", "2025-08-01"),
    ("campaign-v2", "2025-08-01", None),
)
TEMPLATE_EFFECTIVE = (
    ("template-v1", "2025-04-01", "2025-06-01"),
    ("template-v2", "2025-06-01", "2025-07-01"),
    ("template-v3", "2025-07-01", "2025-08-01"),
    ("template-v4", "2025-08-01", None),
)
RATE_EFFECTIVE = (
    ("rate-v1", "2025-04-01", "2025-08-01"),
    ("rate-v2", "2025-08-01", None),
)


def test_expected_configuration_versions_exist() -> None:
    manifest = load_manifest()
    assert campaign_versions() == ("campaign-v1", "campaign-v2")
    assert template_versions() == ("template-v1", "template-v2", "template-v3", "template-v4")
    assert manifest["rate_card_versions"] == ["rate-v1", "rate-v2"]
    assert manifest["label_group_versions"] == ["fl1-group-v1"]
    assert len(load_campaign_version("campaign-v1").rows) == 3905
    assert len(load_campaign_version("campaign-v2").rows) == 4093
    assert [len(load_template_version(label).rows) for label in template_versions()] == [
        41,
        41,
        42,
        49,
    ]
    assert len(load_label_group_version().rows) == 43


def test_sql_row_counts_match_json_snapshots() -> None:
    files = {path.name: path.read_text(encoding="utf-8") for path in migration_files()}
    v1 = "a0000000-0000-4000-8000-000000000021"
    v2 = "a0000000-0000-4000-8000-000000000022"
    assert files["20260823000006_p2_campaign_v1.sql"].count(v1) == 3905
    assert files["20260823000007_p2_campaign_v2.sql"].count(v2) == 4093
    tmpl = files["20260823000008_p2_templates.sql"]
    assert tmpl.count("a0000000-0000-4000-8000-000000000031") == 41
    assert tmpl.count("a0000000-0000-4000-8000-000000000034") == 49


def test_duplicate_campaign_names_are_retained() -> None:
    rows = load_campaign_version("campaign-v1").rows
    names = [row["campaign_name"] for row in rows]
    counts = Counter(names)
    assert len(rows) == 3905
    assert len(counts) == 3174
    assert sum(1 for count in counts.values() if count > 1) == 371
    assert counts[DUP_NAME] >= 2
    v2_names = [row["campaign_name"] for row in load_campaign_version("campaign-v2").rows]
    assert len(v2_names) == 4093
    assert len(set(v2_names)) == 3339


def test_first_matching_campaign_row_is_lowest_row_order() -> None:
    rows = [
        row for row in load_campaign_version("campaign-v1").rows if row["campaign_name"] == DUP_NAME
    ]
    assert len(rows) >= 2
    first = min(rows, key=lambda row: row["row_order"])
    resolved = resolve_campaign_label("campaign-v1", DUP_NAME)
    assert resolved.match_status == "matched"
    assert resolved.row_order == first["row_order"]
    assert resolved.filter_logic_1 == first["filter_logic_1"]
    assert resolved.row_order == rows[0]["row_order"]


def test_campaign_lookup_is_case_insensitive_and_does_not_trim() -> None:
    resolved = resolve_campaign_label("campaign-v1", DUP_NAME.upper())
    assert resolved.match_status == "matched"
    spaced = resolve_campaign_label("campaign-v2", LEADING_SPACE_NAME)
    trimmed = resolve_campaign_label("campaign-v2", LEADING_SPACE_NAME.strip())
    assert spaced.match_status == "matched"
    assert spaced.campaign_name == LEADING_SPACE_NAME
    assert trimmed.match_status == "unmatched"


def test_six_campaign_outputs_map_to_configuration_columns() -> None:
    resolved = resolve_campaign_label("campaign-v1", DUP_NAME)
    row = next(
        item
        for item in load_campaign_version("campaign-v1").rows
        if item["row_order"] == resolved.row_order
    )
    for field in CAMPAIGN_OUTPUT_FIELDS:
        assert getattr(resolved, field) == row[field]


def test_missing_campaign_returns_null_outputs() -> None:
    blank = resolve_campaign_label("campaign-v1", "")
    missing = resolve_campaign_label("campaign-v1", "___not_a_campaign___")
    assert blank.match_status == "blank_key"
    assert missing.match_status == "unmatched"
    assert blank.filter_logic_1 is None
    assert missing.manual_or_automated is None


def test_template_lookup_uses_qr_rate_not_column_i() -> None:
    sample = load_template_version("template-v4").rows[0]
    resolved = resolve_template_status("template-v4", sample["template_name"])
    assert resolved.match_status == "matched"
    assert resolved.template_status == sample["template_status"]
    assert resolved.template_status in {"Utility", "Marketing"}
    assert not hasattr(resolved, "unused_sheet_template_status")
    campaign = load_campaign_version("campaign-v2").rows[0]
    assert "unused_sheet_template_status" in campaign
    assert resolved.template_status != campaign["unused_sheet_template_status"]


def test_missing_template_returns_blank() -> None:
    blank = resolve_template_status("template-v4", None)
    missing = resolve_template_status("template-v4", "no-such-template")
    assert blank.template_status == ""
    assert missing.template_status == ""
    assert blank.match_status == "blank_key"
    assert missing.match_status == "unmatched"


def test_rate_card_precedence_and_locked_values() -> None:
    utility = resolve_rate_card_rule("rate-v2", "Utility", "WhatsApp")
    assert utility.matched is True
    assert utility.rule is not None
    assert utility.rule.match_field == "template_status"
    assert utility.rule.rate == "0.115000"
    whatsapp = resolve_rate_card_rule("rate-v2", "Marketing", "WhatsApp")
    assert whatsapp.rule is not None
    assert whatsapp.rule.match_field == "channel"
    assert whatsapp.rule.rate == "0.785000"
    none = resolve_rate_card_rule("rate-v2", "", "Push")
    assert none.matched is False
    v1 = resolve_rate_card_rule("rate-v1", "Utility", "SMS")
    assert v1.rule is not None
    assert v1.rule.rate == "0.120000"
    sql_rules = parse_insert_tuples(read_migrations(), "rate_card_rule")
    python_rates = [(rule.priority, rule.match_value, rule.rate) for rule in RATE_CARD_RULES]
    sql_rates = [
        (int(row[2]), unquote_sql_string(row[4]), unquote_sql_string(row[6])) for row in sql_rules
    ]
    assert python_rates == sql_rates


def test_filter_logic_1_2_membership_from_pivot_cache() -> None:
    assert resolve_filter_logic_1_group("Service | FMS & LMS | Campaigns") == "Group2"
    assert resolve_filter_logic_1_group("TAMC | D2C AMC|Automated Renewal Campaign") == "Group7"
    assert resolve_filter_logic_1_group("0") == "0"
    assert resolve_filter_logic_1_group(None) is None
    assert (
        resolve_filter_logic_1_group("Brand Marketing| Consumer research | Campaigns") == "Group3"
    )
    unknown = "a-new-filter-logic-value"
    assert resolve_filter_logic_1_group(unknown) == unknown
    trailing = "Service | Campaigns | Termination BP "
    assert resolve_filter_logic_1_group(trailing) == "Group2"


GROUP7_MEMBERS = {
    "TAMC | D2C AMC|Manual Campaign",
    "AMC | FSC | Service | Campaigns",
    "TAMC | D2C AMC|Automated Renewal Campaign",
}
GROUP5_MEMBERS = {
    "D2C Product| CLTV | Campaigns",
    "D2C Product| Pros | Campaigns",
    "D2C Product |Sales Assist | Campaigns",
}
GROUP2_SAMPLE = "Service | FMS & LMS | Campaigns"


def test_fl1_2_captions_do_not_change_membership() -> None:
    rows = load_label_group_version().rows
    assert len(rows) == 43
    g7 = {row["filter_logic_1_value"] for row in rows if row.get("group_name") == "Group7"}
    g5 = {row["filter_logic_1_value"] for row in rows if row.get("group_name") == "Group5"}
    g2 = {row["filter_logic_1_value"] for row in rows if row.get("group_name") == "Group2"}
    assert g7 == GROUP7_MEMBERS
    assert g5 == GROUP5_MEMBERS
    assert GROUP2_SAMPLE in g2
    assert "FSC | Service | Campaigns" in g2
    assert "Service | Technician | Manual Camp" in g2
    captions = dict(load_label_group_captions())
    assert captions["Group7"] == "D2C AMC Vertical"
    assert captions["Group5"] == "D2C Product Vertical"
    assert captions["Group2"] == "Overall Service Campaigns"
    assert captions["Group1"] == "API DATA - No Cost"
    assert captions["Group3"] == "Direct Sales Data"
    assert captions["Group4"] == "Rental vertical"
    assert captions["Group6"] == "IT Vertical"
    assert resolve_group_display_name("Group7") == "D2C AMC Vertical"
    assert resolve_group_display_name("Group5") == "D2C Product Vertical"
    assert resolve_group_display_name("Group2") == "Overall Service Campaigns"
    leftover = "TAMC | D2C AMC|Manual Campaign Bain Trail 4"
    assert resolve_filter_logic_1_group(leftover) == leftover
    assert resolve_group_display_name(leftover) == leftover
    assert resolve_filter_logic_1_group("TAMC | D2C AMC|Automated Renewal Campaign") == "Group7"
    named = {
        row["group_name"]
        for row in rows
        if isinstance(row.get("group_name"), str) and re.fullmatch(r"Group[1-7]", row["group_name"])
    }
    assert named == {f"Group{i}" for i in range(1, 8)}
    assert set(captions) == named
    counts = Counter(row.get("group_name") for row in rows)
    assert counts["Group7"] == 3
    assert counts["Group5"] == 3
    assert counts["Group2"] == 14
    assert resolve_filter_logic_1_group("TAMC | D2C AMC|Manual Campaign") == "Group7"
    assert resolve_filter_logic_1_group("D2C Product| CLTV | Campaigns") == "Group5"


def test_version_selection_is_deterministic() -> None:
    assert select_version_for_day(CAMPAIGN_EFFECTIVE, date(2025, 7, 15)) == "campaign-v1"
    assert select_version_for_day(CAMPAIGN_EFFECTIVE, date(2025, 8, 1)) == "campaign-v2"
    assert select_version_for_day(TEMPLATE_EFFECTIVE, "2025-06-15") == "template-v2"
    assert select_version_for_day(TEMPLATE_EFFECTIVE, "2025-07-01") == "template-v3"
    assert select_version_for_day(RATE_EFFECTIVE, "2025-04-01") == "rate-v1"
    assert select_version_for_day(RATE_EFFECTIVE, "2025-08-20") == "rate-v2"


def test_qa_kpis_unaltered_and_client_namespace_added() -> None:
    rows = parse_insert_tuples(read_migrations(), "kpi_definition")
    qa = [row for row in rows if unquote_sql_string(row[1]) == "qa"]
    client = [row for row in rows if unquote_sql_string(row[1]) == "client"]
    assert len(qa) == 16
    assert tuple(unquote_sql_string(row[2]) for row in qa) == QA_KPI_NAMES
    assert tuple(unquote_sql_string(row[4]) for row in qa) == QA_KPI_FORMULAS
    assert len(client) == 15
    assert unquote_sql_string(client[0][2]) == "Delivery Rate"
    assert unquote_sql_string(client[14][2]) == "Overall ROAS"


def test_p2_does_not_introduce_later_phase_surface() -> None:
    sql = read_v1_migrations().lower()
    assert "enable row level security" not in sql
    assert "create policy" not in sql
    source = inspect.getsource(resolve_mod).lower()
    assert "delivered *" not in source
    assert "* delivered" not in source
    assert "fastapi" not in source
    assert "ingest" not in source
