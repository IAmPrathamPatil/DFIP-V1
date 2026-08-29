"""Nine-sheet Web Engage Daily Report contract and ZIP-safe workbook attachment.

Excel remains a reporting + refresh layer. PublishedFacts.m is unchanged.
Sheets are injected into the native V2-X workbook without rewriting DataMashup,
connections, or query tables.

P11 report sheets are native PivotTables (see dfip_web.pivot_report).
report_formula() remains the P10 UNIQUE/FILTER/SUMIFS reconstruction contract
and is not written onto the delivered sheets.

This Excel Desktop (16.0 build 20326) supports LET, UNIQUE, FILTER, SEQUENCE,
SORTBY, INDEX, MATCH, and SUMIFS, but returns #NAME? for LAMBDA, GROUPBY,
HSTACK, CHOOSECOLS, and DROP. The reconstruction formulas UNIQUE/FILTER the
grouping columns and SUMIFS the additives (sum first, then divide for rates).

Campaign Name and Filter Logic 2 use Pivot-compatible report identity: UNIQUE
sees LOWER() of those columns, then MATCH restores the first-seen source
casing for display. Source facts are not case-folded. SUMIFS is already
case-insensitive, so one UNIQUE row receives the combined total — the same
grouping a native PivotTable uses.

Worksheet XML stores those formulas as Excel Desktop writes them: _xlfn /
_xlpm names, FILTER as _xlfn._xlws.FILTER, and spilled refs as
_xlfn.ANCHORARRAY. Plain LET names that look like A1 cells (f1, flt1) are
rejected by Formula2 and can prevent Workbooks.Open.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from dfip_analytics.kpis import compute_kpis
from dfip_config.resolve import resolve_group_display_name
from dfip_config.store import load_label_group_captions
from openpyxl.utils import get_column_letter

from dfip_web.client_workbook import FAKE_MASHUP_ZIP_PARTS, XLSX_PATH

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

SOURCE_WORKBOOK = "Web Engage - Daily Report - FY-2026.xlsx"
EMPTY_STATE = "No published data available."

# Exact source tab names, including trailing spaces recovered from workbook.xml.
OVERALL = "Overall Daywise Report "
VERTICAL = "Vertical Level"
CHANNEL = "Channel Wise"
SUB_SPLIT = "Sub-Split"
AMC_DAYWISE = "AMC DayWise"
AMC_SPLIT = "AMC Split"
AMC_VERTICAL = "AMC Vertical Monthly Split"
D2C = "D2C Vertical "
SERVICE = "Service Campaigns"

REPORT_SHEET_NAMES: tuple[str, ...] = (
    OVERALL,
    VERTICAL,
    CHANNEL,
    SUB_SPLIT,
    AMC_DAYWISE,
    AMC_SPLIT,
    AMC_VERTICAL,
    D2C,
    SERVICE,
)

NATIVE_SHEET_NAMES: tuple[str, ...] = ("PublishedFacts", "Facts")
CLIENT_WORKBOOK_SHEET_NAMES: tuple[str, ...] = NATIVE_SHEET_NAMES + REPORT_SHEET_NAMES

SERVICE_FILTER_LOGIC_1 = "Service | FMS & LMS | Campaigns"
AMC_GROUP = "Group7"
D2C_GROUP = "Group5"
SERVICE_GROUP = "Group2"

FL4_HEADER = "AMC Device Category -  Filter Logic 4"
FL5_HEADER = "AMC Product Cat -  Filter Logic 5"
CAPTION_KEY_COL = 52  # AZ — far-right lookup table, not a classification engine
CAPTION_NAME_COL = 53  # BA

# Additive published columns. Native Web Engage rate columns are never inputs.
ADDITIVE_HEADERS: tuple[str, ...] = (
    "Total Cost",
    "Sent",
    "Failed",
    "Delivered",
    "Unique Impressions",
    "Unique Clicks",
    "Unique Click-Through Conversions",
    "Click-Through Revenue (INR)",
    "Unique Conversions",
    "Revenue (INR)",
)

MEASURE_HEADERS: tuple[str, ...] = (
    "Total Cost",
    "Sent",
    "Failed",
    "Failed Rate SM",
    "Actual Sent",
    "Delivered",
    "Delivery Rate",
    "Unique Impressions",
    "Delivered to Imp. rate",
    "Unique Clicks",
    "CTR (Del to Clicks)",
    "CTR ( Impr. to Click )",
    "Unique Click-Through Conversions",
    "Click-Through Revenue (INR)",
    "Cost/ UCT conversion",
    "UCT conversion rate",
    "Unique Click Through Conv ROAS",
    "Unique Conversions",
    "Revenue (INR)",
    "Cost/Unique Conversion",
    "Delivered Thru Conv. rate",
    "Overall ROAS",
)

# Display header -> published Excel header after PublishedFacts.m rename.
PUBLISHED_HEADER = {
    "Filter Logic 1": "Filter Logic 1",
    "Filter Logic 2": "Filter Logic 2",
    "Filter Logic 1_2": "filter_logic_1_group",
    "Month": "Month",
    "Day": "Day",
    "Campaign Name": "Campaign Name",
    "Channel": "Channel",
    "month_start": "month_start",
}

# PivotTables coalesce these labels case-insensitively. Report UNIQUE does the
# same via LOWER(); stored Campaign Name / Filter Logic 2 strings stay as-is.
PIVOT_CASE_IDENTITY_HEADERS: frozenset[str] = frozenset({"Campaign Name", "Filter Logic 2"})

# Excel report formats. Comparator display-equivalence uses these quanta;
# stored Total Cost / KPI values are not rounded.
MONEY_DISPLAY_QUANTUM = Decimal("0.01")  # #,##0.00 (money, cost-per, ROAS)
MONEY_STORED_QUANTUM = Decimal("0.0001")  # DFIP Total Cost decimal scale
RATE_DISPLAY_QUANTUM = Decimal("0.0001")  # 0.00% (ratio units)

# Client KPI slugs used as report-layer calculations (sum first, then operate).
REPORT_KPI_SLUGS: tuple[str, ...] = (
    "failed_rate_sm",
    "actual_sent",
    "delivery_rate",
    "delivered_to_imp_rate",
    "ctr_del_to_clicks",
    "ctr_impr_to_click",
    "cost_uct_conversion",
    "uct_conversion_rate",
    "unique_click_through_conv_roas",
    "cost_unique_conversion",
    "delivered_thru_conv_rate",
    "overall_roas",
)


@dataclass(frozen=True)
class ReportSheetContract:
    name: str
    purpose: str
    grain: tuple[str, ...]
    groupby_fields: tuple[str, ...]
    display_fields: tuple[str, ...]
    default_channel: str = ""
    default_month: str = ""
    default_filter_logic_1: str = ""
    default_group: str = ""
    device_product_filters: bool = False
    slicers: tuple[str, ...] = (
        "Filter Logic 1",
        "Channel",
        "Filter Logic 1_2",
        "Month",
    )


REPORT_CONTRACTS: tuple[ReportSheetContract, ...] = (
    ReportSheetContract(
        OVERALL,
        "Broadest daily performance: Month then Day over the published slice.",
        ("Month", "Day"),
        ("month_start", "Month", "Day"),
        ("Month", "Day"),
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2"),
    ),
    ReportSheetContract(
        VERTICAL,
        "Month then Filter Logic 1_2 (Main Verticals / label group).",
        ("Month", "Filter Logic 1_2"),
        ("month_start", "Month", "Filter Logic 1_2"),
        ("Month", "Filter Logic 1_2"),
        slicers=(
            "Filter Logic 1",
            "Channel",
            "AMC Device Category -  Filter Logic 4",
            "AMC Product Cat -  Filter Logic 5",
            "Filter Logic 1_2",
        ),
        device_product_filters=True,
    ),
    ReportSheetContract(
        CHANNEL,
        "Month then vertical then Filter Logic 1. Channel is a filter, not a row field.",
        ("Month", "Filter Logic 1_2", "Filter Logic 1"),
        ("month_start", "Month", "Filter Logic 1_2", "Filter Logic 1"),
        ("Month", "Filter Logic 1_2", "Filter Logic 1"),
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2"),
    ),
    ReportSheetContract(
        SUB_SPLIT,
        "Month then vertical then Filter Logic 1 then Campaign Name.",
        ("Month", "Filter Logic 1_2", "Filter Logic 1", "Campaign Name"),
        ("month_start", "Month", "Filter Logic 1_2", "Filter Logic 1", "Campaign Name"),
        ("Month", "Filter Logic 1_2", "Filter Logic 1", "Campaign Name"),
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2", "Month"),
    ),
    ReportSheetContract(
        AMC_DAYWISE,
        "Daywise AMC slice. Default Filter Logic 1_2 = Group7 (AMC/TAMC labels).",
        ("Month", "Day"),
        ("month_start", "Month", "Day"),
        ("Month", "Day"),
        default_group=AMC_GROUP,
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2", "Month"),
    ),
    ReportSheetContract(
        AMC_SPLIT,
        "AMC campaign split: Filter Logic 1 then Filter Logic 2 then Month.",
        ("Filter Logic 1", "Filter Logic 2", "Month"),
        ("Filter Logic 1", "Filter Logic 2", "month_start", "Month"),
        ("Filter Logic 1", "Filter Logic 2", "Month"),
        default_group=AMC_GROUP,
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2", "Month"),
    ),
    ReportSheetContract(
        AMC_VERTICAL,
        "AMC vertical monthly: Filter Logic 1 then Month then Filter Logic 2.",
        ("Filter Logic 1", "Month", "Filter Logic 2"),
        ("Filter Logic 1", "month_start", "Month", "Filter Logic 2"),
        ("Filter Logic 1", "Month", "Filter Logic 2"),
        default_group=AMC_GROUP,
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2", "Month"),
    ),
    ReportSheetContract(
        D2C,
        "D2C Product vertical. Default Filter Logic 1_2 = Group5.",
        ("Filter Logic 1", "Month", "Filter Logic 2"),
        ("Filter Logic 1", "month_start", "Month", "Filter Logic 2"),
        ("Filter Logic 1", "Month", "Filter Logic 2"),
        default_group=D2C_GROUP,
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2", "Month"),
    ),
    ReportSheetContract(
        SERVICE,
        "Service campaigns. Default Filter Logic 1 = Service | FMS & LMS | Campaigns.",
        ("Filter Logic 1_2", "Month", "Day"),
        ("Filter Logic 1_2", "month_start", "Month", "Day"),
        ("Filter Logic 1_2", "Month", "Day"),
        default_filter_logic_1=SERVICE_FILTER_LOGIC_1,
        default_group=SERVICE_GROUP,
        slicers=("Filter Logic 1", "Channel", "Filter Logic 1_2", "Month"),
    ),
)

CONTRACTS_BY_NAME = {item.name: item for item in REPORT_CONTRACTS}


# kind: sum (PublishedFacts column), ratio/diff (measure headers of sum columns).
MEASURE_OPS: tuple[tuple[str, ...], ...] = (
    ("Total Cost", "sum", "Total Cost"),
    ("Sent", "sum", "Sent"),
    ("Failed", "sum", "Failed"),
    ("Failed Rate SM", "ratio", "Failed", "Sent"),
    ("Actual Sent", "diff", "Sent", "Failed"),
    ("Delivered", "sum", "Delivered"),
    ("Delivery Rate", "ratio", "Delivered", "Sent"),
    ("Unique Impressions", "sum", "Unique Impressions"),
    ("Delivered to Imp. rate", "ratio", "Unique Impressions", "Delivered"),
    ("Unique Clicks", "sum", "Unique Clicks"),
    ("CTR (Del to Clicks)", "ratio", "Unique Clicks", "Delivered"),
    ("CTR ( Impr. to Click )", "ratio", "Unique Clicks", "Unique Impressions"),
    ("Unique Click-Through Conversions", "sum", "Unique Click-Through Conversions"),
    ("Click-Through Revenue (INR)", "sum", "Click-Through Revenue (INR)"),
    ("Cost/ UCT conversion", "ratio", "Total Cost", "Unique Click-Through Conversions"),
    ("UCT conversion rate", "ratio", "Unique Click-Through Conversions", "Unique Clicks"),
    ("Unique Click Through Conv ROAS", "ratio", "Click-Through Revenue (INR)", "Total Cost"),
    ("Unique Conversions", "sum", "Unique Conversions"),
    ("Revenue (INR)", "sum", "Revenue (INR)"),
    ("Cost/Unique Conversion", "ratio", "Total Cost", "Unique Conversions"),
    ("Delivered Thru Conv. rate", "ratio", "Unique Conversions", "Delivered"),
    ("Overall ROAS", "ratio", "Revenue (INR)", "Total Cost"),
)

# Presentation formats only. They do not change SUMIFS / UNIQUE / KPI formulas.
# 0 = mathematically zero additive; blank = ratio with a 0/non-numeric denominator.
# Money/ROAS display is #,##0.00; rates are 0.00%. Stored values are not rounded.
MEASURE_FORMATS: tuple[str, ...] = (
    "money",
    "count",
    "count",
    "rate",
    "count",
    "count",
    "rate",
    "count",
    "rate",
    "count",
    "rate",
    "rate",
    "count",
    "money",
    "money",
    "rate",
    "roas",
    "count",
    "money",
    "money",
    "rate",
    "roas",
)

MEASURE_KIND_BY_HEADER: dict[str, str] = dict(zip(MEASURE_HEADERS, MEASURE_FORMATS, strict=True))

ZERO_VS_BLANK_HINT = (
    "Blank filters = all published values. 0 is a real total of zero. "
    "A blank rate, ROAS, or cost-per means the denominator is 0 or not applicable. "
    "Data -> Refresh All."
)
ZERO_VS_BLANK_HINT_VERTICAL = (
    "Blank filters = all published values, including blank FL4/FL5. "
    "0 is a real total of zero. A blank rate, ROAS, or cost-per means the "
    "denominator is 0 or not applicable. F4/G4 filter AMC Device and AMC Product. "
    "Data -> Refresh All."
)

DIM_COL_WIDTHS: dict[str, float] = {
    "Month": 12.0,
    "Day": 12.0,
    "Filter Logic 1_2": 28.0,
    "Filter Logic 1": 36.0,
    "Filter Logic 2": 28.0,
    "Campaign Name": 42.0,
}
METRIC_COL_WIDTHS: dict[str, float] = {
    "count": 12.0,
    "money": 14.0,
    "rate": 13.0,
    "roas": 13.0,
}

# Native workbook cellXfs 0..2 stay untouched (Facts bold headers use xf 1).
XF_TITLE = 3
XF_PURPOSE = 4
XF_FILTER_LABEL = 5
XF_FILTER_VALUE = 6
XF_HINT = 7
XF_DIM_HEADER = 8
XF_METRIC_HEADER = 9
XF_GROUP_BANNER = 10
XF_TOTAL_LABEL = 11
XF_COUNT = 12
XF_MONEY = 13
XF_RATE = 14
XF_ROAS = 15
XF_COUNT_TOTAL = 16
XF_MONEY_TOTAL = 17
XF_RATE_TOTAL = 18
XF_ROAS_TOTAL = 19
XF_DIM_VALUE = 20
DFIP_NUMFMT_MARK = 'numFmtId="164"'

_MEASURE_XF = {"count": XF_COUNT, "money": XF_MONEY, "rate": XF_RATE, "roas": XF_ROAS}
_MEASURE_TOTAL_XF = {
    "count": XF_COUNT_TOTAL,
    "money": XF_MONEY_TOTAL,
    "rate": XF_RATE_TOTAL,
    "roas": XF_ROAS_TOTAL,
}


def _col(n: int) -> str:
    return get_column_letter(n)


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_attr(text: str) -> str:
    return _xml_text(text).replace('"', "&quot;")


def _style_attr(style: int | None) -> str:
    return "" if style is None else f' s="{style}"'


def _inline(ref: str, value: str, *, style: int | None = None) -> str:
    """Write an inline string. Styled empty cells keep filter/input boxes visible."""
    attr = _style_attr(style)
    if value == "":
        return f'<c r="{ref}"{attr}/>' if style is not None else ""
    return (
        f'<c r="{ref}"{attr} t="inlineStr"><is>'
        f'<t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _formula(
    ref: str,
    formula: str,
    *,
    array_ref: str | None = None,
    style: int | None = None,
) -> str:
    stored = _xml_formula(formula)
    if array_ref:
        body = f'<f t="array" ref="{array_ref}">{_xml_text(stored)}</f>'
    else:
        body = f"<f>{_xml_text(stored)}</f>"
    cm = ""
    if any(token in formula for token in ("LET(", "UNIQUE(", "FILTER(", "#")):
        cm = ' cm="1"'
    return f'<c r="{ref}"{_style_attr(style)}{cm}>{body}</c>'


_XML_LET_NAMES = (
    "logic1Filter",
    "monthFilter",
    "groupFilter",
    "chanFilter",
    "groupResolved",
    "deviceFilter",
    "productFilter",
    "captionKeys",
    "captionNames",
    "sortedKeys",
    "keepMask",
    "uniqKeys",
    "filteredKeys",
    "idKeys",
    "uniqId",
    "concatSrc",
    "concatUniq",
    "rowIdx",
    "origKeys",
    "dataRows",
    "dayCol",
    "nRows",
    "hdr",
)


def _xml_formula(formula: str) -> str:
    """Store dynamic-array formulas the way this Excel Desktop writes them.

    Formula2 accepts the Python-side text. The Open XML loader requires _xlfn /
    _xlpm names, FILTER as _xlfn._xlws.FILTER, and spilled refs as
    _xlfn.ANCHORARRAY. Measure cells must also store t="array" ref="{cell}"
    (Excel Formula2 save). Without that flag SUMIFS(INDEX($B10#,0,n)) stays a
    scalar and only the first hierarchy row calculates. Do not expand the array
    ref to a multi-row range; the single-cell ref is what allows DA spill.
    The group UNIQUE still uses t="array" ref="B10:{lastDim}10".
    """
    text = formula
    for name in _XML_LET_NAMES:
        text = re.sub(rf"\b{name}\b", f"_xlpm.{name}", text)
    text = text.replace("FILTER(", "_xlfn._xlws.FILTER(")
    text = text.replace("SEQUENCE(", "_xlfn.SEQUENCE(")
    text = text.replace("UNIQUE(", "_xlfn.UNIQUE(")
    text = text.replace("SORTBY(", "_xlfn.SORTBY(")
    text = text.replace("LET(", "_xlfn.LET(")
    return re.sub(r"(\$?[A-Za-z]{1,3}\$?\d+)#", r"_xlfn.ANCHORARRAY(\1)", text)


DA_METADATA_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:xda="http://schemas.microsoft.com/office/spreadsheetml/2017/dynamicarray">'
    '<metadataTypes count="1">'
    '<metadataType name="XLDAPR" minSupportedVersion="120000" copy="1" pasteAll="1" '
    'pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" '
    'clearComments="1" assign="1" coerce="1" cellMeta="1"/>'
    "</metadataTypes>"
    '<futureMetadata name="XLDAPR" count="1"><bk><extLst>'
    '<ext uri="{bdbb8cdc-fa1e-496e-a857-3c3f30c029c3}">'
    '<xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/>'
    "</ext></extLst></bk></futureMetadata>"
    '<cellMetadata count="1"><bk><rc t="1" v="0"/></bk></cellMetadata>'
    "</metadata>"
)
DA_METADATA_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata"
)
DA_METADATA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"


def _rels_with_metadata(rels_xml: str) -> str:
    if "sheetMetadata" in rels_xml:
        return rels_xml
    rid = f"rId{_next_relationship_id(rels_xml)}"
    rel = f'<Relationship Id="{rid}" Type="{DA_METADATA_REL}" Target="metadata.xml"/>'
    return rels_xml.replace("</Relationships>", rel + "</Relationships>")


def _types_with_metadata(types_xml: str) -> str:
    if "/xl/metadata.xml" in types_xml:
        return types_xml
    extra = f'<Override PartName="/xl/metadata.xml" ContentType="{DA_METADATA_TYPE}"/>'
    return types_xml.replace("</Types>", extra + "</Types>")


def _choose(parts: list[str]) -> str:
    nums = ",".join(str(i) for i in range(1, len(parts) + 1))
    return f"CHOOSE({{{nums}}},{','.join(parts)})"


def contract_uses_case_identity(contract: ReportSheetContract) -> bool:
    """True when UNIQUE must coalesce Campaign Name and/or Filter Logic 2 case."""
    return any(
        PUBLISHED_HEADER[name] in PIVOT_CASE_IDENTITY_HEADERS for name in contract.groupby_fields
    )


def report_identity_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def report_identity_fold(value: object) -> str:
    """Excel LOWER-compatible grouping key. Does not rewrite stored facts."""
    return report_identity_text(value).lower()


def report_display_key(row: Mapping[str, object], display_fields: Sequence[str]) -> tuple[str, ...]:
    """Report grain key after Pivot-compatible case identity on Campaign Name / FL2."""
    parts: list[str] = []
    for name in display_fields:
        header = PUBLISHED_HEADER[name]
        text = report_identity_text(row.get(header))
        if header in PIVOT_CASE_IDENTITY_HEADERS:
            parts.append(report_identity_fold(text))
        else:
            parts.append(text)
    return tuple(parts)


def report_display_equal(
    left: Decimal | None,
    right: Decimal | None,
    *,
    kind: str,
) -> bool:
    """True when both values show the same text at the report number format.

    Money / ROAS: snap IEEE cache floats to DFIP's 4-decimal stored scale, then
    compare Excel ``#,##0.00`` (2 decimal places, half-up). This treats
    ``199378.19499999999989948`` vs ``199378.1950`` as the same displayed
    Total Cost. A 0.01 difference after that snap is DATA, not FORMAT.
    Rate: Excel ``0.00%`` (2 percent decimals = 4 decimal ratio units).
    Count: exact equality only. Does not round stored database values.
    """
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if kind == "count":
        return left == right
    if kind in {"money", "roas"}:
        left_stored = left.quantize(MONEY_STORED_QUANTUM, rounding=ROUND_HALF_UP)
        right_stored = right.quantize(MONEY_STORED_QUANTUM, rounding=ROUND_HALF_UP)
        if left_stored == right_stored:
            return True
        return left_stored.quantize(
            MONEY_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP
        ) == right_stored.quantize(MONEY_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)
    if kind == "rate":
        return left.quantize(RATE_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP) == right.quantize(
            RATE_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP
        )
    return left == right


def _identity_choose(group_cols: list[str], source: str = "filteredKeys") -> str:
    parts: list[str] = []
    for index, header in enumerate(group_cols, start=1):
        expr = f"INDEX({source},0,{index})"
        if header in PIVOT_CASE_IDENTITY_HEADERS:
            expr = f"LOWER({expr})"
        parts.append(expr)
    return _choose(parts)


def _concat_identity_row(array_name: str, n_cols: int) -> str:
    return "&CHAR(9)&".join(f"INDEX({array_name},0,{i})" for i in range(1, n_cols + 1))


def _orig_keys_choose(n_cols: int) -> str:
    return _choose([f"INDEX(filteredKeys,rowIdx,{i})" for i in range(1, n_cols + 1)])


def _unique_keys_let(group_cols: list[str], choose_src: str) -> str:
    """UNIQUE of filtered grouping columns, with Pivot-style case identity.

    Campaign Name / Filter Logic 2 are UNIQUE'd on LOWER() so case variants
    occupy one report row. MATCH restores the first-seen source casing for
    display. Sheets without those fields keep the previous UNIQUE(FILTER(...)).
    """
    if not any(header in PIVOT_CASE_IDENTITY_HEADERS for header in group_cols):
        return f"uniqKeys,UNIQUE(FILTER({choose_src},keepMask)),"
    n_cols = len(group_cols)
    return (
        f"filteredKeys,FILTER({choose_src},keepMask),"
        f"idKeys,{_identity_choose(group_cols)},"
        f"uniqId,UNIQUE(idKeys),"
        f"concatSrc,{_concat_identity_row('idKeys', n_cols)},"
        f"concatUniq,{_concat_identity_row('uniqId', n_cols)},"
        f"rowIdx,MATCH(concatUniq,concatSrc,0),"
        f"origKeys,{_orig_keys_choose(n_cols)},"
        "uniqKeys,origKeys,"
    )


def _col_all(header: str) -> str:
    return f'INDEX(PublishedFacts!$A:$XFD,0,MATCH("{header}",hdr,0))'


def _col_rows(header: str) -> str:
    return f'INDEX(PublishedFacts!$A:$XFD,dataRows,MATCH("{header}",hdr,0))'


def _measure_letter(contract: ReportSheetContract, header: str) -> str:
    start = 2 + len(contract.display_fields)
    return _col(start + MEASURE_HEADERS.index(header))


def _empty_guard() -> str:
    return f'OR($B10="",$B10="{EMPTY_STATE}")'


def _caption_pairs() -> tuple[tuple[str, str], ...]:
    return load_label_group_captions()


def group_display_name(group_key: str) -> str:
    """Excel filter-cell caption. Empty key stays empty (all values)."""
    if not group_key:
        return ""
    return resolve_group_display_name(group_key) or group_key


def _caption_key_range() -> str:
    n = max(len(_caption_pairs()), 1)
    return f"${_col(CAPTION_KEY_COL)}$1:${_col(CAPTION_KEY_COL)}${n}"


def _caption_name_range() -> str:
    n = max(len(_caption_pairs()), 1)
    return f"${_col(CAPTION_NAME_COL)}$1:${_col(CAPTION_NAME_COL)}${n}"


def _caption_let() -> str:
    return (
        f"captionKeys,{_caption_key_range()},"
        f"captionNames,{_caption_name_range()},"
        "groupResolved,IFERROR(INDEX(captionKeys,MATCH(TRIM($E$4),captionNames,0)),TRIM($E$4)),"
    )


def _display_group_expr(group_cols: list[str], header: str) -> str:
    index = group_cols.index(header) + 1
    base = f"INDEX(sortedKeys,0,{index})"
    if header == "filter_logic_1_group":
        return f"IFERROR(INDEX(captionNames,MATCH({base},captionKeys,0)),{base})"
    return base


def _spill_sumifs_criteria(header: str, index: int) -> str:
    spill = f"INDEX($B10#,0,{index})"
    if header == "filter_logic_1_group":
        return f"IFERROR(INDEX(captionKeys,MATCH({spill},captionNames,0)),{spill})"
    return spill


def report_formula(contract: ReportSheetContract) -> str:
    """UNIQUE/FILTER grouping over PublishedFacts. No LAMBDA/GROUPBY/HSTACK."""
    group_cols = [PUBLISHED_HEADER[name] for name in contract.groupby_fields]
    display_headers = [PUBLISHED_HEADER[name] for name in contract.display_fields]
    choose_src = _choose([_col_rows(name) for name in group_cols])
    display_parts = [_display_group_expr(group_cols, name) for name in display_headers]
    display = display_parts[0] if len(display_parts) == 1 else _choose(display_parts)
    sort_args = ["uniqKeys"]
    if "month_start" in group_cols:
        sort_args.extend([f"INDEX(uniqKeys,0,{group_cols.index('month_start') + 1})", "1"])
    if "Day" in group_cols:
        sort_args.extend([f"INDEX(uniqKeys,0,{group_cols.index('Day') + 1})", "1"])
    if len(sort_args) == 1:
        sort_args.extend(["INDEX(uniqKeys,0,1)", "1"])
    sorted_expr = f"SORTBY({','.join(sort_args)})"
    extra_filters = ""
    extra_mask = ""
    if contract.device_product_filters:
        extra_filters = "deviceFilter,TRIM($F$4),productFilter,TRIM($G$4),"
        extra_mask = (
            f'((deviceFilter="")+({_col_rows(FL4_HEADER)}=deviceFilter))*'
            f'((productFilter="")+({_col_rows(FL5_HEADER)}=productFilter))*'
        )
    return (
        "LET("
        "hdr,PublishedFacts!$1:$1,"
        'dayCol,IFERROR(MATCH("Day",hdr,0),0),'
        "nRows,IF(dayCol=0,0,MAX(0,COUNTA(INDEX(PublishedFacts!$A:$XFD,0,dayCol))-1)),"
        f'IF(nRows=0,"{EMPTY_STATE}",'
        "LET("
        "dataRows,SEQUENCE(nRows)+1,"
        "chanFilter,TRIM($B$4),monthFilter,TRIM($C$4),"
        "logic1Filter,TRIM($D$4),groupFilter,TRIM($E$4),"
        f"{extra_filters}"
        f"{_caption_let()}"
        f'keepMask,((chanFilter="")+({_col_rows("Channel")}=chanFilter))*'
        f'((monthFilter="")+({_col_rows("Month")}=monthFilter))*'
        f'((logic1Filter="")+({_col_rows("Filter Logic 1")}=logic1Filter))*'
        f"{extra_mask}"
        f'((groupFilter="")+({_col_rows("filter_logic_1_group")}=groupResolved)),'
        f"{_unique_keys_let(group_cols, choose_src)}"
        f"sortedKeys,{sorted_expr},"
        f'IFERROR({display},"{EMPTY_STATE}")))'
        ")"
    )


def _sumifs_arg_list(
    contract: ReportSheetContract,
    additive_header: str,
    *,
    device: bool = False,
    product: bool = False,
) -> str:
    display_headers = [PUBLISHED_HEADER[name] for name in contract.display_fields]
    args = [_col_all(additive_header)]
    for index, header in enumerate(display_headers, start=1):
        args.extend([_col_all(header), _spill_sumifs_criteria(header, index)])
    for cell, header in (
        ("$B$4", "Channel"),
        ("$C$4", "Month"),
        ("$D$4", "Filter Logic 1"),
        ("$E$4", "filter_logic_1_group"),
    ):
        if header == "filter_logic_1_group":
            args.extend([_col_all(header), 'IF($E$4="","<>",groupResolved)'])
        else:
            args.extend([_col_all(header), f'IF({cell}="","<>",{cell})'])
    if device:
        args.extend([_col_all(FL4_HEADER), "TRIM($F$4)"])
    if product:
        args.extend([_col_all(FL5_HEADER), "TRIM($G$4)"])
    return ",".join(args)


def additive_formula(contract: ReportSheetContract, additive_header: str) -> str:
    body_prefix = f'LET(hdr,PublishedFacts!$1:$1,{_caption_let()}IF({_empty_guard()},""'
    if not contract.device_product_filters:
        return f"{body_prefix},SUMIFS({_sumifs_arg_list(contract, additive_header)})))"
    s_all = _sumifs_arg_list(contract, additive_header)
    s_device = _sumifs_arg_list(contract, additive_header, device=True)
    s_product = _sumifs_arg_list(contract, additive_header, product=True)
    s_both = _sumifs_arg_list(contract, additive_header, device=True, product=True)
    return (
        f"{body_prefix},"
        'IF(TRIM($F$4)="",'
        f'IF(TRIM($G$4)="",SUMIFS({s_all}),SUMIFS({s_product})),'
        f'IF(TRIM($G$4)="",SUMIFS({s_device}),SUMIFS({s_both})))))'
    )


def derived_formula(contract: ReportSheetContract, op: tuple[str, ...]) -> str:
    kind = op[1]
    if kind == "sum":
        return additive_formula(contract, op[2])
    left = _measure_letter(contract, op[2])
    right = _measure_letter(contract, op[3])
    if kind == "diff":
        return f'IF({_empty_guard()},"",{left}10#-{right}10#)'
    return (
        f'IF({_empty_guard()},"",'
        f"IF((NOT(ISNUMBER({right}10#)))+({right}10#=0),"
        f'"",IFERROR({left}10#/{right}10#,"")))'
    )


def total_formula(contract: ReportSheetContract, op: tuple[str, ...]) -> str:
    kind = op[1]
    if kind == "sum":
        letter = _measure_letter(contract, op[0])
        return f'IF({_empty_guard()},"",IFERROR(SUM({letter}10#),""))'
    left = _measure_letter(contract, op[2])
    right = _measure_letter(contract, op[3])
    if kind == "diff":
        return f'IF({_empty_guard()},"",IFERROR(SUM({left}10#)-SUM({right}10#),""))'
    return (
        f'IF({_empty_guard()},"",'
        f"IF(IFERROR(SUM({right}10#),0)=0,"
        f'"",IFERROR(SUM({left}10#)/SUM({right}10#),"")))'
    )


def last_visible_col(contract: ReportSheetContract) -> int:
    """Last client-visible column (dimensions + measures). Captions stay at AZ:BA."""
    return 1 + len(contract.display_fields) + len(MEASURE_HEADERS)


def _cols_xml(contract: ReportSheetContract) -> str:
    parts = [
        '<col min="1" max="1" width="12" customWidth="1"/>',
    ]
    start = 2
    for offset, name in enumerate(contract.display_fields):
        width = DIM_COL_WIDTHS.get(name, 18.0)
        idx = start + offset
        parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
    measure_start = start + len(contract.display_fields)
    for offset, kind in enumerate(MEASURE_FORMATS):
        width = METRIC_COL_WIDTHS[kind]
        idx = measure_start + offset
        parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
    last_vis = last_visible_col(contract)
    if last_vis + 1 <= CAPTION_KEY_COL - 1:
        parts.append(
            f'<col min="{last_vis + 1}" max="{CAPTION_KEY_COL - 1}" '
            'width="0" hidden="1" customWidth="1"/>'
        )
    parts.append(
        f'<col min="{CAPTION_KEY_COL}" max="{CAPTION_NAME_COL}" '
        'width="0" hidden="1" customWidth="1"/>'
    )
    return "<cols>" + "".join(parts) + "</cols>"


def _measure_xf(index: int, *, total: bool = False) -> int:
    kind = MEASURE_FORMATS[index]
    return _MEASURE_TOTAL_XF[kind] if total else _MEASURE_XF[kind]


def _header_row(contract: ReportSheetContract) -> list[tuple[str, str, int]]:
    start = 2  # column B
    cells: list[tuple[str, str, int]] = []
    for offset, name in enumerate(contract.display_fields):
        cells.append((_col(start + offset) + "9", name, XF_DIM_HEADER))
    measure_start = start + len(contract.display_fields)
    for offset, name in enumerate(MEASURE_HEADERS):
        cells.append((_col(measure_start + offset) + "9", name, XF_METRIC_HEADER))
    return cells


def worksheet_xml(contract: ReportSheetContract) -> str:
    group_formula = report_formula(contract)
    header_cells = "".join(
        _inline(ref, value, style=style) for ref, value, style in _header_row(contract)
    )
    measure_start = 2 + len(contract.display_fields)
    freeze_col = 1 + len(contract.display_fields)
    freeze_cell = f"{_col(freeze_col + 1)}10"
    uct_start = _col(measure_start + 12)
    uct_end = _col(measure_start + 16)
    overall_start = _col(measure_start + 17)
    overall_end = _col(measure_start + 21)
    last_col_idx = max(measure_start + len(MEASURE_HEADERS) - 1, CAPTION_NAME_COL)
    last_col = _col(last_col_idx)
    span = f"1:{last_col_idx}"
    empty_formula = f'IF(IFERROR(COUNTA(PublishedFacts!A:A),0)<=1,"{EMPTY_STATE}","")'
    measure_cells = "".join(
        _formula(
            _col(measure_start + index) + "10",
            derived_formula(contract, op),
            array_ref=_col(measure_start + index) + "10",
            style=_measure_xf(index),
        )
        for index, op in enumerate(MEASURE_OPS)
    )
    total_cells = _inline("A7", "Total", style=XF_TOTAL_LABEL) + "".join(
        _formula(
            _col(measure_start + index) + "7",
            total_formula(contract, op),
            style=_measure_xf(index, total=True),
        )
        for index, op in enumerate(MEASURE_OPS)
    )
    filter_labels = (
        f"{_inline('B3', 'Channel', style=XF_FILTER_LABEL)}"
        f"{_inline('C3', 'Month', style=XF_FILTER_LABEL)}"
        f"{_inline('D3', 'Filter Logic 1', style=XF_FILTER_LABEL)}"
        f"{_inline('E3', 'Filter Logic 1_2', style=XF_FILTER_LABEL)}"
    )
    filter_values = (
        f"{_inline('B4', contract.default_channel, style=XF_FILTER_VALUE)}"
        f"{_inline('C4', contract.default_month, style=XF_FILTER_VALUE)}"
        f"{_inline('D4', contract.default_filter_logic_1, style=XF_FILTER_VALUE)}"
        f"{_inline('E4', group_display_name(contract.default_group), style=XF_FILTER_VALUE)}"
    )
    hint = ZERO_VS_BLANK_HINT
    if contract.device_product_filters:
        filter_labels += (
            f"{_inline('F3', FL4_HEADER, style=XF_FILTER_LABEL)}"
            f"{_inline('G3', FL5_HEADER, style=XF_FILTER_LABEL)}"
        )
        filter_values += (
            f"{_inline('F4', '', style=XF_FILTER_VALUE)}{_inline('G4', '', style=XF_FILTER_VALUE)}"
        )
        hint = ZERO_VS_BLANK_HINT_VERTICAL
    caption_by_row: dict[int, str] = {}
    for index, (key, caption) in enumerate(_caption_pairs(), start=1):
        caption_by_row[index] = _inline(f"{_col(CAPTION_KEY_COL)}{index}", key) + _inline(
            f"{_col(CAPTION_NAME_COL)}{index}", caption
        )

    def _row(number: int, inner: str, *, height: float | None = None) -> str:
        ht = "" if height is None else f' ht="{height}" customHeight="1"'
        return f'<row r="{number}" spans="{span}"{ht}>{inner}{caption_by_row.get(number, "")}</row>'

    rows = [
        _row(1, _inline("A1", "Web Engage Daily Report", style=XF_TITLE), height=22),
        _row(2, _inline("A2", contract.purpose, style=XF_PURPOSE)),
        _row(3, filter_labels),
        _row(4, filter_values, height=18),
        _row(5, _inline("A5", hint, style=XF_HINT), height=32),
        _row(6, _formula("A6", empty_formula, style=XF_HINT)),
        _row(7, total_cells),
        _row(
            8,
            (
                f"{_inline(uct_start + '8', 'Click-Through Conversions', style=XF_GROUP_BANNER)}"
                f"{_inline(overall_start + '8', 'Overall Conversions', style=XF_GROUP_BANNER)}"
            ),
        ),
        _row(9, header_cells, height=36),
        _row(
            10,
            _formula(
                "B10",
                group_formula,
                array_ref=f"B10:{_col(1 + len(contract.display_fields))}10",
                style=XF_DIM_VALUE,
            )
            + measure_cells,
        ),
    ]
    merges = (
        f'<mergeCells count="2">'
        f'<mergeCell ref="{uct_start}8:{uct_end}8"/>'
        f'<mergeCell ref="{overall_start}8:{overall_end}8"/>'
        f"</mergeCells>"
    )
    uid = str(uuid4()).upper()
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'mc:Ignorable="x14ac xr xr2 xr3" '
        'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" '
        'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
        'xmlns:xr2="http://schemas.microsoft.com/office/spreadsheetml/2015/revision2" '
        'xmlns:xr3="http://schemas.microsoft.com/office/spreadsheetml/2016/revision3" '
        f'xr:uid="{{{uid}}}">'
        f'<dimension ref="A1:{last_col}10"/>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0" zoomScale="90">'
        f'<pane xSplit="{freeze_col}" ySplit="9" topLeftCell="{freeze_cell}" '
        'activePane="bottomRight" state="frozen"/>'
        '<selection pane="bottomRight" activeCell="B10" sqref="B10"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.3"/>'
        f"{_cols_xml(contract)}"
        f"<sheetData>{''.join(rows)}</sheetData>"
        f"{merges}"
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75"'
        ' header="0.3" footer="0.3"/>'
        "</worksheet>"
    )


def _ensure_report_styles(styles_xml: str) -> str:
    """Append report number formats and cellXfs. Native xf 0-2 stay in place."""
    if DFIP_NUMFMT_MARK in styles_xml:
        return styles_xml
    num_fmts = (
        '<numFmts count="3">'
        '<numFmt numFmtId="164" formatCode="#,##0"/>'
        '<numFmt numFmtId="165" formatCode="#,##0.00"/>'
        '<numFmt numFmtId="166" formatCode="0.00%"/>'
        "</numFmts>"
    )
    if "<numFmts" not in styles_xml:
        styles_xml = styles_xml.replace("<fonts ", num_fmts + "<fonts ", 1)
    extra_fonts = (
        '<font><b/><sz val="16"/><color theme="1"/><name val="Calibri"/><family val="2"/>'
        '<scheme val="minor"/></font>'
        '<font><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/><family val="2"/>'
        '<scheme val="minor"/></font>'
    )
    styles_xml = styles_xml.replace('fonts count="2"', 'fonts count="4"', 1)
    styles_xml = styles_xml.replace("</fonts>", extra_fonts + "</fonts>", 1)
    extra_fills = (
        '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD6E3F0"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFBDD7EE"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
    )
    styles_xml = styles_xml.replace('fills count="2"', 'fills count="7"', 1)
    styles_xml = styles_xml.replace("</fills>", extra_fills + "</fills>", 1)
    extra_borders = (
        '<border><left style="thin"><color rgb="FFB0B0B0"/></left>'
        '<right style="thin"><color rgb="FFB0B0B0"/></right>'
        '<top style="thin"><color rgb="FFB0B0B0"/></top>'
        '<bottom style="thin"><color rgb="FFB0B0B0"/></bottom>'
        "<diagonal/></border>"
        "<border><left/><right/><top/>"
        '<bottom style="thin"><color rgb="FF808080"/></bottom>'
        "<diagonal/></border>"
    )
    styles_xml = styles_xml.replace('borders count="1"', 'borders count="3"', 1)
    styles_xml = styles_xml.replace("</borders>", extra_borders + "</borders>", 1)

    def xf(
        *,
        num: int = 0,
        font: int = 0,
        fill: int = 0,
        border: int = 0,
        wrap: bool = False,
        horizontal: str | None = None,
    ) -> str:
        apply = []
        if num:
            apply.append('applyNumberFormat="1"')
        if font:
            apply.append('applyFont="1"')
        if fill:
            apply.append('applyFill="1"')
        if border:
            apply.append('applyBorder="1"')
        align = ""
        if wrap or horizontal:
            hor = f' horizontal="{horizontal}"' if horizontal else ""
            wrap_attr = ' wrapText="1"' if wrap else ""
            align = f'<alignment vertical="center"{hor}{wrap_attr}/>'
            apply.append('applyAlignment="1"')
        attrs = " ".join(
            [
                f'numFmtId="{num}"',
                f'fontId="{font}"',
                f'fillId="{fill}"',
                f'borderId="{border}"',
                'xfId="0"',
                *apply,
            ]
        )
        if align:
            return f"<xf {attrs}>{align}</xf>"
        return f"<xf {attrs}/>"

    extra_xfs = "".join(
        [
            xf(font=2),  # 3 title
            xf(font=3, wrap=True),  # 4 purpose
            xf(font=1, fill=2, border=2),  # 5 filter label
            xf(fill=3, border=1),  # 6 filter value
            xf(font=3, wrap=True),  # 7 hint
            xf(font=1, fill=2, border=2, wrap=True, horizontal="center"),  # 8 dim header
            xf(font=1, fill=4, border=2, wrap=True, horizontal="center"),  # 9 metric header
            xf(font=1, fill=6, border=2, horizontal="center"),  # 10 group banner
            xf(font=1, fill=5, border=2),  # 11 total label
            xf(num=164, border=2),  # 12 count
            xf(num=165, border=2),  # 13 money
            xf(num=166, border=2),  # 14 rate
            xf(num=165, border=2),  # 15 roas
            xf(num=164, font=1, fill=5, border=2),  # 16 count total
            xf(num=165, font=1, fill=5, border=2),  # 17 money total
            xf(num=166, font=1, fill=5, border=2),  # 18 rate total
            xf(num=165, font=1, fill=5, border=2),  # 19 roas total
            xf(border=2),  # 20 dim value
        ]
    )
    styles_xml = styles_xml.replace('cellXfs count="3"', 'cellXfs count="21"', 1)
    styles_xml = styles_xml.replace("</cellXfs>", extra_xfs + "</cellXfs>", 1)
    return styles_xml


def _insert_sheets_into_workbook_xml(xml: str, extra: list[tuple[str, str, int]]) -> str:
    if "</sheets>" not in xml:
        raise ValueError("workbook.xml missing </sheets>")
    added = "".join(
        f'<sheet name="{_xml_attr(name)}" sheetId="{sheet_id}" r:id="{rid}"/>'
        for name, rid, sheet_id in extra
    )
    return xml.replace("</sheets>", added + "</sheets>", 1)


def _add_content_types(xml: str, parts: list[str]) -> str:
    extras = "".join(
        f'<Override PartName="/{part}" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for part in parts
    )
    return xml.replace("</Types>", extras + "</Types>")


def _clone_zipinfo(info: ZipInfo) -> ZipInfo:
    cloned = ZipInfo(filename=info.filename, date_time=info.date_time)
    cloned.compress_type = info.compress_type
    cloned.comment = info.comment
    cloned.extra = info.extra
    cloned.create_system = info.create_system
    cloned.create_version = info.create_version
    cloned.extract_version = info.extract_version
    # Data-descriptor bit 0x08 makes Excel reject a writestr-rewritten zip.
    cloned.flag_bits = info.flag_bits & ~0x08
    cloned.volume = info.volume
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    return cloned


def _new_sheet_zipinfo(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=(2026, 8, 27, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.create_system = 0
    info.create_version = 20
    info.extract_version = 20
    info.flag_bits = 0
    info.external_attr = 0x1800000
    return info


def _next_relationship_id(rels_xml: str) -> int:
    ids = [int(value) for value in re.findall(r'Id="rId(\d+)"', rels_xml)]
    return (max(ids) if ids else 0) + 1


def _next_sheet_id(workbook_xml: str) -> int:
    ids = [int(value) for value in re.findall(r'sheetId="(\d+)"', workbook_xml)]
    return (max(ids) if ids else 0) + 1


def _next_sheet_part(names: list[str]) -> int:
    nums = [int(value) for value in re.findall(r"worksheets/sheet(\d+)\.xml", "\n".join(names))]
    return (max(nums) if nums else 0) + 1


def _update_app_xml(xml: str, extra_names: list[str]) -> str:
    worksheets = (
        r"(<vt:lpstr>Worksheets</vt:lpstr></vt:variant>"
        r"<vt:variant><vt:i4>)(\d+)(</vt:i4>)"
    )
    xml = re.sub(
        worksheets,
        lambda match: f"{match.group(1)}{int(match.group(2)) + len(extra_names)}{match.group(3)}",
        xml,
        count=1,
    )
    titles = (
        r'(<TitlesOfParts><vt:vector size=")(\d+)(" baseType="lpstr">)'
        r"(.*?)(</vt:vector></TitlesOfParts>)"
    )
    match = re.search(titles, xml, re.DOTALL)
    if match is None:
        return xml
    parts = re.findall(r"<vt:lpstr>.*?</vt:lpstr>", match.group(4))
    extras = "".join(f"<vt:lpstr>{_xml_text(name)}</vt:lpstr>" for name in extra_names)
    if len(parts) >= 2:
        inner = "".join(parts[:2]) + extras + "".join(parts[2:])
    else:
        inner = match.group(4) + extras
    size = int(match.group(2)) + len(extra_names)
    return (
        xml[: match.start()]
        + f"{match.group(1)}{size}{match.group(3)}{inner}{match.group(5)}"
        + xml[match.end() :]
    )


def _add_workbook_rels(xml: str, rels: list[tuple[str, str]]) -> str:
    extras = "".join(
        f'<Relationship Id="{rid}" Type="{NS_REL}/worksheet" Target="{target}"/>'
        for rid, target in rels
    )
    return xml.replace("</Relationships>", extras + "</Relationships>")


def _attr_map(tag: str) -> dict[str, str]:
    return dict(re.findall(r'([:\w]+)="([^"]*)"', tag))


def _sheet_part_map(workbook_xml: str, rels_xml: str) -> dict[str, str]:
    """Map workbook sheet names to xl/worksheets/*.xml parts."""
    targets: dict[str, str] = {}
    for tag in re.findall(r"<Relationship\b[^>]*/?>", rels_xml):
        attrs = _attr_map(tag)
        rid = attrs.get("Id")
        target = attrs.get("Target", "")
        if rid and target:
            targets[rid] = target
    mapping: dict[str, str] = {}
    for tag in re.findall(r"<sheet\b[^>]*/?>", workbook_xml):
        attrs = _attr_map(tag)
        name = attrs.get("name")
        rid = attrs.get("r:id")
        if not name or not rid:
            continue
        target = targets.get(rid, "")
        part = target[3:] if target.startswith("../") else target
        if part.startswith("/"):
            part = part[1:]
        if part.startswith("worksheets/"):
            part = "xl/" + part
        mapping[name.replace("&amp;", "&").replace("&quot;", '"')] = part
    return mapping


def _workbook_has_report_sheets(workbook_xml: str) -> bool:
    return all(
        f'name="{name}"' in workbook_xml or f'name="{_xml_attr(name)}"' in workbook_xml
        for name in REPORT_SHEET_NAMES
    )


def attach_daily_report_sheets(
    source: str | Path | None = None, target: str | Path | None = None
) -> str:
    """Copy the native workbook and add or rewrite the nine report sheets.

    Does not touch mashup parts. Report sheets become native PivotTables bound
    to the shared PublishedFacts cache. Python reconstruction formulas stay in
    report_formula() and are not stored on the delivered sheets.
    """
    src = XLSX_PATH if source is None else source
    dest = XLSX_PATH if target is None else target
    src_path = Path(src)
    dest_path = Path(dest)
    from dfip_web.pivot_report import apply_native_pivots

    raw = src_path.read_bytes()
    with ZipFile(io.BytesIO(raw), "r") as original:
        workbook_xml = original.read("xl/workbook.xml").decode("utf-8")
        rels_xml = original.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    if _workbook_has_report_sheets(workbook_xml):
        parts = _sheet_part_map(workbook_xml, rels_xml)
        missing = [name for name in REPORT_SHEET_NAMES if name not in parts]
        if missing:
            raise ValueError(f"report sheets listed in workbook.xml but parts missing: {missing}")
        dest_path.write_bytes(apply_native_pivots(raw))
        return str(dest_path)
    extra_sheets: list[tuple[str, str, int]] = []
    extra_rels: list[tuple[str, str]] = []
    extra_parts: list[str] = []
    extra_names: list[str] = []
    new_files: dict[str, bytes] = {}
    with ZipFile(io.BytesIO(raw), "r") as original:
        rels_xml = original.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        next_rid = _next_relationship_id(rels_xml)
        next_sheet_id = _next_sheet_id(workbook_xml)
        next_part = _next_sheet_part(original.namelist())
    for offset, contract in enumerate(REPORT_CONTRACTS):
        rid = f"rId{next_rid + offset}"
        sheet_id = next_sheet_id + offset
        part_n = next_part + offset
        part = f"xl/worksheets/sheet{part_n}.xml"
        extra_sheets.append((contract.name, rid, sheet_id))
        extra_rels.append((rid, f"worksheets/sheet{part_n}.xml"))
        extra_parts.append(part)
        extra_names.append(contract.name)
        new_files[part] = worksheet_xml(contract).encode("utf-8")

    out = io.BytesIO()
    with ZipFile(io.BytesIO(raw), "r") as original, ZipFile(out, "w") as written:
        names = set(original.namelist())
        for fake in FAKE_MASHUP_ZIP_PARTS:
            if fake in names:
                raise ValueError(f"refusing to patch a workbook that contains {fake}")
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "xl/workbook.xml":
                data = _insert_sheets_into_workbook_xml(data.decode("utf-8"), extra_sheets).encode(
                    "utf-8"
                )
            elif info.filename == "xl/_rels/workbook.xml.rels":
                data = _rels_with_metadata(
                    _add_workbook_rels(data.decode("utf-8"), extra_rels)
                ).encode("utf-8")
            elif info.filename == "[Content_Types].xml":
                data = _types_with_metadata(
                    _add_content_types(data.decode("utf-8"), extra_parts)
                ).encode("utf-8")
            elif info.filename == "xl/styles.xml":
                data = _ensure_report_styles(data.decode("utf-8")).encode("utf-8")
            elif info.filename == "docProps/app.xml":
                data = _update_app_xml(data.decode("utf-8"), extra_names).encode("utf-8")
            written.writestr(_clone_zipinfo(info), data)
        for name, payload in new_files.items():
            written.writestr(_new_sheet_zipinfo(name), payload)
        written.writestr(_new_sheet_zipinfo("xl/metadata.xml"), DA_METADATA_XML.encode("utf-8"))
    dest_path.write_bytes(apply_native_pivots(out.getvalue()))
    return str(dest_path)


def facts_match_report_filter(
    row: dict[str, object],
    contract: ReportSheetContract,
    *,
    channel: str | None = None,
    month: str | None = None,
    filter_logic_1: str | None = None,
    group: str | None = None,
) -> bool:
    ch = contract.default_channel if channel is None else channel
    mo = contract.default_month if month is None else month
    f1 = contract.default_filter_logic_1 if filter_logic_1 is None else filter_logic_1
    gr = contract.default_group if group is None else group
    if ch and str(row.get("Channel") or "") != ch:
        return False
    if mo and str(row.get("Month") or "") != mo:
        return False
    if f1 and str(row.get("Filter Logic 1") or "") != f1:
        return False
    if gr and str(row.get("filter_logic_1_group") or "") != gr:
        return False
    return True


def facts_match_device_product_filters(
    row: dict[str, object],
    *,
    device: str = "",
    product: str = "",
) -> bool:
    """Blank device/product means all values, including blank source cells."""
    if device and str(row.get(FL4_HEADER) or "") != device:
        return False
    if product and str(row.get(FL5_HEADER) or "") != product:
        return False
    return True


def _as_number(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def aggregate_published_rows(
    rows: list[dict[str, object]],
    contract: ReportSheetContract,
    *,
    device: str = "",
    product: str = "",
) -> dict[str, Decimal | None]:
    """Sum additives then apply client KPI formulas. Matches the workbook layer."""
    matched = [row for row in rows if facts_match_report_filter(row, contract)]
    if contract.device_product_filters:
        matched = [
            row
            for row in matched
            if facts_match_device_product_filters(row, device=device, product=product)
        ]
    measures = {
        "total_cost": None,
        "sent": None,
        "failed": None,
        "delivered": None,
        "unique_impressions": None,
        "unique_clicks": None,
        "unique_conversions": None,
        "unique_click_through_conversions": None,
        "revenue_inr": None,
        "click_through_revenue_inr": None,
    }
    header_to_measure = {
        "Total Cost": "total_cost",
        "Sent": "sent",
        "Failed": "failed",
        "Delivered": "delivered",
        "Unique Impressions": "unique_impressions",
        "Unique Clicks": "unique_clicks",
        "Unique Conversions": "unique_conversions",
        "Unique Click-Through Conversions": "unique_click_through_conversions",
        "Revenue (INR)": "revenue_inr",
        "Click-Through Revenue (INR)": "click_through_revenue_inr",
    }
    totals: dict[str, Decimal] = {}
    for row in matched:
        for header, key in header_to_measure.items():
            number = _as_number(row.get(header))
            if number is None:
                continue
            totals[key] = totals.get(key, Decimal("0")) + number
    for key in measures:
        measures[key] = totals.get(key)
    return compute_kpis(measures, namespace="client")


if __name__ == "__main__":
    print(attach_daily_report_sheets())
