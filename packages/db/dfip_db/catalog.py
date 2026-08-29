"""Locked workbook/KPI/rate-card catalogs for P1 schema tests.

This is metadata, not a transformation engine.
"""

from __future__ import annotations

from typing import NamedTuple


class SourceColumn(NamedTuple):
    excel_position: int
    excel_letter: str
    excel_header: str
    db_column: str
    source_role: str
    value_kind: str


# 67 columns A:BO. Headers are exact, including double spaces on E and F.
SOURCE_COLUMNS: tuple[SourceColumn, ...] = (
    SourceColumn(1, "A", "Filter Logic 1", "filter_logic_1", "derived_excel", "text"),
    SourceColumn(2, "B", "Filter Logic 2", "filter_logic_2", "derived_excel", "text"),
    SourceColumn(3, "C", "Template Status", "template_status", "derived_excel", "text"),
    SourceColumn(
        4, "D", "AMC Status - Filter Logic 3", "amc_status_filter_logic_3", "derived_excel", "text"
    ),
    SourceColumn(
        5,
        "E",
        "AMC Device Category -  Filter Logic 4",
        "amc_device_category_filter_logic_4",
        "derived_excel",
        "text",
    ),
    SourceColumn(
        6,
        "F",
        "AMC Product Cat -  Filter Logic 5",
        "amc_product_cat_filter_logic_5",
        "derived_excel",
        "text",
    ),
    SourceColumn(7, "G", "Manual Or Automated", "manual_or_automated", "derived_excel", "text"),
    SourceColumn(8, "H", "Total Cost", "total_cost", "derived_excel", "numeric"),
    SourceColumn(9, "I", "HHH", "hhh", "derived_excel", "text"),
    SourceColumn(10, "J", "Month", "month_label", "derived_excel", "text"),
    SourceColumn(11, "K", "Day", "day", "web_engage_source", "date"),
    SourceColumn(12, "L", "Campaign Name", "campaign_name", "web_engage_source", "text"),
    SourceColumn(13, "M", "Campaign ID", "campaign_id", "web_engage_source", "text"),
    SourceColumn(14, "N", "Variation Name", "variation_name", "web_engage_source", "text"),
    SourceColumn(15, "O", "Variation ID", "variation_id", "web_engage_source", "text"),
    SourceColumn(16, "P", "Channel", "channel", "web_engage_source", "text"),
    SourceColumn(17, "Q", "Type of Campaign", "type_of_campaign", "web_engage_source", "text"),
    SourceColumn(18, "R", "Status", "status", "web_engage_source", "text"),
    SourceColumn(19, "S", "Segment Name", "segment_name", "web_engage_source", "text"),
    SourceColumn(20, "T", "Segment ID", "segment_id", "web_engage_source", "text"),
    SourceColumn(21, "U", "Journey Name", "journey_name", "web_engage_source", "text"),
    SourceColumn(22, "V", "Journey ID", "journey_id", "web_engage_source", "text"),
    SourceColumn(23, "W", "Campaign Tags", "campaign_tags", "web_engage_source", "text"),
    SourceColumn(24, "X", "Start Date", "start_date", "web_engage_source", "timestamp"),
    SourceColumn(25, "Y", "Created By", "created_by", "web_engage_source", "text"),
    SourceColumn(26, "Z", "Conversion Event", "conversion_event", "web_engage_source", "text"),
    SourceColumn(
        27, "AA", "Conversion Deadline", "conversion_deadline", "web_engage_source", "text"
    ),
    SourceColumn(28, "AB", "Control Group", "control_group", "web_engage_source", "text"),
    SourceColumn(
        29, "AC", "Total in Control Group", "total_in_control_group", "web_engage_source", "integer"
    ),
    SourceColumn(
        30,
        "AD",
        "Unique Control Group Conversions",
        "unique_control_group_conversions",
        "web_engage_source",
        "integer",
    ),
    SourceColumn(
        31,
        "AE",
        "Unique Control Group Conversion Rate",
        "unique_control_group_conversion_rate",
        "web_engage_source",
        "numeric",
    ),
    SourceColumn(32, "AF", "Sent", "sent", "web_engage_source", "integer"),
    SourceColumn(33, "AG", "Failed", "failed", "web_engage_source", "integer"),
    SourceColumn(34, "AH", "Queued", "queued", "web_engage_source", "integer"),
    SourceColumn(35, "AI", "Delivered", "delivered", "web_engage_source", "integer"),
    SourceColumn(
        36, "AJ", "Unique Impressions", "unique_impressions", "web_engage_source", "integer"
    ),
    SourceColumn(37, "AK", "Unique Clicks", "unique_clicks", "web_engage_source", "integer"),
    SourceColumn(
        38, "AL", "Unique Conversions", "unique_conversions", "web_engage_source", "integer"
    ),
    SourceColumn(
        39,
        "AM",
        "Unique Impression-Through Conversions",
        "unique_impression_through_conversions",
        "web_engage_source",
        "integer",
    ),
    SourceColumn(
        40,
        "AN",
        "Unique Click-Through Conversions",
        "unique_click_through_conversions",
        "web_engage_source",
        "integer",
    ),
    SourceColumn(41, "AO", "Failed Rate", "failed_rate", "web_engage_source", "numeric"),
    SourceColumn(42, "AP", "Queued Rate", "queued_rate", "web_engage_source", "numeric"),
    SourceColumn(
        43, "AQ", "Delivered Rate", "delivered_rate_native", "web_engage_source", "numeric"
    ),
    SourceColumn(
        44, "AR", "Unique Impression Rate", "unique_impression_rate", "web_engage_source", "numeric"
    ),
    SourceColumn(
        45, "AS", "Unique Click Rate", "unique_click_rate", "web_engage_source", "numeric"
    ),
    SourceColumn(
        46, "AT", "Unique Conversion Rate", "unique_conversion_rate", "web_engage_source", "numeric"
    ),
    SourceColumn(
        47,
        "AU",
        "Unique Impression-Through Conversion Rate",
        "unique_impression_through_conversion_rate",
        "web_engage_source",
        "numeric",
    ),
    SourceColumn(
        48,
        "AV",
        "Unique Click-Through Conversion Rate",
        "unique_click_through_conversion_rate",
        "web_engage_source",
        "numeric",
    ),
    SourceColumn(49, "AW", "Revenue (INR)", "revenue_inr", "web_engage_source", "numeric"),
    SourceColumn(
        50,
        "AX",
        "Impression-Through Revenue (INR)",
        "impression_through_revenue_inr",
        "web_engage_source",
        "numeric",
    ),
    SourceColumn(
        51,
        "AY",
        "Click-Through Revenue (INR)",
        "click_through_revenue_inr",
        "web_engage_source",
        "numeric",
    ),
    SourceColumn(
        52, "AZ", "Failed (DND Queue Drop)", "failed_dnd_queue_drop", "web_engage_source", "integer"
    ),
    SourceColumn(
        53,
        "BA",
        "Failed (Frequency Capping Queue Drop)",
        "failed_frequency_capping_queue_drop",
        "web_engage_source",
        "integer",
    ),
    SourceColumn(
        54,
        "BB",
        "Failed (Personalization Error)",
        "failed_personalization_error",
        "web_engage_source",
        "integer",
    ),
    SourceColumn(
        55,
        "BC",
        "Failed (Channel Not Available)",
        "failed_channel_not_available",
        "web_engage_source",
        "integer",
    ),
    SourceColumn(56, "BD", "Layout Name", "layout_name", "web_engage_source", "text"),
    SourceColumn(57, "BE", "ESP/SSP/WSP name", "esp_ssp_wsp_name", "web_engage_source", "text"),
    SourceColumn(58, "BF", "Title/Subject Line", "title_subject_line", "web_engage_source", "text"),
    SourceColumn(59, "BG", "Message", "message", "web_engage_source", "text"),
    SourceColumn(60, "BH", "Default CTA Label", "default_cta_label", "web_engage_source", "text"),
    SourceColumn(61, "BI", "Default CTA Link", "default_cta_link", "web_engage_source", "text"),
    SourceColumn(62, "BJ", "Image", "image", "web_engage_source", "text"),
    SourceColumn(63, "BK", "Key-value Pairs", "key_value_pairs", "web_engage_source", "text"),
    SourceColumn(64, "BL", "Sender ID (SMS)", "sender_id_sms", "web_engage_source", "text"),
    SourceColumn(65, "BM", "From Name (Email)", "from_name_email", "web_engage_source", "text"),
    SourceColumn(66, "BN", "From Email (Email)", "from_email_email", "web_engage_source", "text"),
    SourceColumn(
        67, "BO", "Template Name (WhatsApp)", "template_name_whatsapp", "web_engage_source", "text"
    ),
)

# K:BO. Exact Excel headers. P3 ingests these 57 columns only.
WEB_ENGAGE_SOURCE_COLUMNS: tuple[SourceColumn, ...] = tuple(
    col for col in SOURCE_COLUMNS if col.source_role == "web_engage_source"
)
WEB_ENGAGE_SOURCE_HEADERS: tuple[str, ...] = tuple(
    col.excel_header for col in WEB_ENGAGE_SOURCE_COLUMNS
)

CAMPAIGN_LABEL_OUTPUTS: tuple[str, ...] = (
    "filter_logic_1",
    "filter_logic_2",
    "amc_status_filter_logic_3",
    "amc_device_category_filter_logic_4",
    "amc_product_cat_filter_logic_5",
    "manual_or_automated",
)

BUSINESS_KEY: tuple[str, ...] = ("campaign_id", "variation_id", "day")

REQUIRED_TABLES: tuple[str, ...] = (
    "client",
    "we_source_column",
    "source_file",
    "batch",
    "campaign_label_version",
    "campaign_label_row",
    "template_label_version",
    "template_label_row",
    "rate_card_version",
    "rate_card_rule",
    "label_group_version",
    "label_group_member",
    "processing_run",
    "stg_source_row",
    "stg_rejected_row",
    "fact_campaign_day",
    "fact_campaign_day_history",
    "publication",
    "publication_current",
    "publication_fact",
    "kpi_definition",
    "audit_log",
)

REQUIRED_INDEXES: tuple[str, ...] = (
    "campaign_label_row_lookup_idx",
    "template_label_row_lookup_idx",
    "rate_card_rule_version_priority_idx",
    "fact_campaign_day_day_idx",
    "fact_campaign_day_batch_idx",
    "stg_source_row_batch_idx",
)

# Exact recovered Q&A KPI display names (M1.3 §8b / M1.4).
QA_KPI_NAMES: tuple[str, ...] = (
    "CTR",
    "Conversion Rate",
    "ROAS",
    "Delivered Rate*",
    "Cost/Conv",
    "Click thou Conv Rate",
    "Click Through Cost/Conv",
    "Cost/ View through Conv ",
    "View through Conv Rate",
    "Click + View Conv ",
    "Click + View Revenue",
    "Cost/ Click + View Conv ",
    "All Click + View Conv Rate",
    "Delivered to Imp. Rate",
    "CTR ( Del to Click )",
    "CTR ( Impr. to Click )",
)

QA_KPI_FORMULAS: tuple[str, ...] = (
    "'Unique Clicks'/Delivered",
    "'Unique Conversions'/Delivered",
    "'Revenue (INR)'/'Total Cost'",
    "Delivered/Sent",
    "'Total Cost'/'Unique Click-Through Conversions'",
    "'Unique Click-Through Conversions'/Delivered",
    "'Total Cost'/'Unique Click-Through Conversions'",
    "'Total Cost'/'Unique Impression-Through Conversions'",
    "'Unique Impression-Through Conversions'/Delivered",
    "'Unique Impression-Through Conversions'+'Unique Click-Through Conversions'",
    "'Impression-Through Revenue (INR)'+'Click-Through Revenue (INR)'",
    "'Total Cost'/'Click + View Conv '",
    "'Click + View Conv '/Delivered",
    "'Unique Impressions'/Delivered",
    "'Unique Clicks'/Delivered",
    "'Unique Clicks'/'Unique Impressions'",
)
