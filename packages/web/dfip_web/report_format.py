"""RUN 006 — reference formatting parity for generated client workbooks.

Applies presentation-only OOXML patches. Does not regenerate PivotTables,
change PivotCache field names or order, rewrite slicer caches, or alter
PublishedFacts values / Settings / mashup parts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from uuid import UUID, uuid5

from openpyxl.utils import get_column_letter

from dfip_web.client_workbook import FACT_HEADERS
from dfip_web.daily_report import (
    AMC_DAYWISE,
    AMC_SPLIT,
    AMC_VERTICAL,
    CHANNEL,
    D2C,
    OVERALL,
    REPORT_SHEET_NAMES,
    SERVICE,
    SUB_SPLIT,
    VERTICAL,
    _ensure_report_styles,
    _inline,
    _sheet_part_map,
    _xml_text,
    mutate_xlsx,
)

# Excel built-in formats (ECMA-376). Custom 164 is the FY-2026 0.0% id.
NUM_ROAS_2DP = 2  # 0.00
NUM_INT = 3  # #,##0
NUM_DEC_2 = 4  # #,##0.00
NUM_PCT_0 = 9  # 0%
NUM_PCT_2 = 10  # 0.00%
NUM_PCT_1 = 164  # 0.0% (custom, matches reference workbook)

RUN006_FONT = "Aptos Narrow"
RUN006_TITLE_SIZE = "16"
RUN006_BODY_SIZE = "11"
RUN006_PURPOSE_SIZE = "10"
RUN006_DEFAULT_ROW_HEIGHT = "14.4"
RUN006_FILTER_FILL = "FFFFEB9C"
# Native Excel pivot style already on the template generator. Injected when a
# cloned pivot part has no tableStyleInfo (Excel-saved template often omits it).
PIVOT_TABLE_STYLE = (
    '<pivotTableStyleInfo name="PivotStyleLight16" showRowHeaders="1" '
    'showColHeaders="1" showRowStripes="0" showColStripes="0" showLastColumn="1"/>'
)
# FY-2026 has no purpose/hint/empty-state cells. Strip those report chrome cells.
_CHROME_CELL = re.compile(
    r'<c r="(?:A1|A2|A5|A6|B2|B5|B6)"[^>]*(?:/>|>.*?</c>)',
    re.DOTALL,
)
EUREKA_FORBES_TITLE_NAME = "eureka forbes"
TITLE_MERGE_REF = "B1:X1"
CAPTION_HIDDEN_COLS = '<col min="52" max="53" width="0" hidden="1" customWidth="1"/>'
_TITLE_BANNER_NS = UUID("f9129cfe-d96c-469b-8cdb-0e48652fb966")
_CHROME_CALC_REFS = frozenset({"A1", "A2", "A5", "A6", "B2", "B5", "B6"})
_ONE_CELL_RECTANGLE = re.compile(
    r"<xdr:oneCellAnchor>.*?name=\"Rectangle 1\".*?</xdr:oneCellAnchor>",
    re.DOTALL,
)
_WSDR_OPEN = re.compile(r"<xdr:wsDr\b[^>]*>")
_MONTH_COL = get_column_letter(FACT_HEADERS.index("Month") + 1)
_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_TITLE_CELL = re.compile(r'<c r="B1"[^>]*(?:/>|>.*?</c>)', re.DOTALL)
_MERGE_OPEN = re.compile(r'<mergeCells count="(\d+)">')
_MONTH_LABEL_RE = re.compile(r"^([A-Za-z]{3})-(\d{2})$")

# dataField @name after strip / whitespace collapse. Source: FY-2026 pivots.
DATAFIELD_NUMFMT: dict[str, int] = {
    "Total Cost": NUM_INT,
    "Sent": NUM_INT,
    "Failed": NUM_INT,
    "Failed Rate": NUM_PCT_0,
    "Failed Rate SM": NUM_PCT_0,
    "Actual Sent": NUM_INT,
    "Delivered": NUM_INT,
    "Delivery Rate": NUM_PCT_0,
    "Unique Impressions": NUM_INT,
    "Delivered to Imp. rate": NUM_PCT_0,
    "Unique Clicks": NUM_INT,
    "CTR (Del to Clicks)": NUM_PCT_2,
    "CTR ( Impr. to Click )": NUM_PCT_2,
    "Unique Click-Through Conversions": NUM_INT,
    "Click-Through Revenue (INR)": NUM_INT,
    "Cost/ UCT conversion": NUM_INT,
    "UCT conversion rate": NUM_PCT_1,
    "Unique Click Through Conv ROAS": NUM_DEC_2,
    "Unique Conversions": NUM_INT,
    "Revenue (INR)": NUM_INT,
    "Cost/Unique Conversion": NUM_INT,
    "Delivered Thru Conv. rate": NUM_PCT_2,
    "Overall ROAS": NUM_ROAS_2DP,
}

# Visible columns 1–24 from the FY-2026 workbook. Hidden DFIP slicer columns stay.
_METRIC_DAYWISE = (
    (1, 1, 2.88671875),
    (2, 2, 56.109375),
    (3, 3, 16.5546875),
    (4, 4, 14.44140625),
    (5, 5, 13.6640625),
    (6, 6, 11.6640625),
    (7, 7, 14.0),
    (8, 8, 15.0),
    (9, 9, 15.109375),
    (10, 10, 12.0),
    (11, 11, 13.33203125),
    (12, 12, 9.6640625),
    (13, 13, 12.44140625),
    (14, 14, 12.33203125),
    (15, 15, 14.33203125),
    (16, 16, 13.33203125),
    (17, 17, 13.5546875),
    (18, 18, 11.0),
    (19, 19, 14.109375),
    (20, 20, 13.5546875),
    (21, 21, 13.33203125),
    (22, 22, 13.6640625),
    (23, 23, 15.0),
    (24, 24, 14.44140625),
)
_METRIC_VERTICAL = (
    (1, 1, 2.88671875),
    (2, 2, 56.0),
    (3, 3, 16.5546875),
    (4, 4, 14.44140625),
    (5, 5, 13.6640625),
    (6, 6, 11.6640625),
    (7, 7, 14.0),
    (8, 8, 15.0),
    (9, 9, 15.109375),
    (10, 10, 11.88671875),
    (11, 11, 13.109375),
    (12, 12, 9.6640625),
    (13, 13, 12.33203125),
    (14, 14, 12.109375),
    (15, 15, 14.33203125),
    (16, 16, 13.109375),
    (17, 17, 13.44140625),
    (18, 18, 10.88671875),
    (19, 19, 14.109375),
    (20, 20, 13.5546875),
    (21, 21, 13.109375),
    (22, 22, 13.6640625),
    (23, 23, 15.0),
    (24, 24, 14.44140625),
)
_METRIC_AMC_DAY = (
    (1, 1, 2.88671875),
    (2, 2, 17.33203125),
    (3, 3, 16.5546875),
    (4, 4, 14.44140625),
    (5, 5, 13.6640625),
    (6, 6, 11.6640625),
    (7, 7, 14.0),
    (8, 8, 15.0),
    (9, 9, 15.109375),
    (10, 10, 12.0),
    (11, 11, 13.33203125),
    (12, 12, 9.6640625),
    (13, 13, 12.44140625),
    (14, 14, 12.33203125),
    (15, 15, 14.33203125),
    (16, 16, 13.33203125),
    (17, 17, 13.5546875),
    (18, 18, 11.0),
    (19, 19, 14.109375),
    (20, 20, 13.5546875),
    (21, 21, 13.33203125),
    (22, 22, 13.6640625),
    (23, 23, 15.0),
    (24, 24, 14.44140625),
)
_METRIC_AMC_SPLIT = (
    (1, 1, 2.88671875),
    (2, 2, 41.109375),
    (3, 3, 10.6640625),
    (4, 4, 11.44140625),
    (5, 5, 10.44140625),
    (6, 6, 11.33203125),
    (7, 8, 11.44140625),
    (9, 9, 13.88671875),
    (10, 10, 11.6640625),
    (11, 11, 12.5546875),
    (12, 12, 8.88671875),
    (13, 14, 11.6640625),
    (15, 15, 13.5546875),
    (16, 16, 11.6640625),
    (17, 18, 10.6640625),
    (19, 19, 13.44140625),
    (20, 20, 12.0),
    (21, 21, 13.0),
    (22, 22, 12.5546875),
    (23, 23, 15.0),
    (24, 24, 13.44140625),
)
_METRIC_AMC_VERTICAL = (
    (1, 1, 2.88671875),
    (2, 2, 56.109375),
    (3, 3, 11.109375),
    (4, 4, 12.21875),
    (5, 5, 11.109375),
    (6, 6, 11.44140625),
    (7, 8, 12.21875),
    (9, 9, 14.0),
    (10, 10, 11.77734375),
    (11, 11, 12.6640625),
    (12, 12, 9.5546875),
    (13, 14, 11.77734375),
    (15, 15, 13.6640625),
    (16, 16, 12.21875),
    (17, 18, 10.77734375),
    (19, 19, 13.5546875),
    (20, 20, 12.109375),
    (21, 21, 13.109375),
    (22, 22, 12.6640625),
    (23, 23, 15.0),
    (24, 24, 13.5546875),
)
_METRIC_D2C = (
    (1, 1, 2.88671875),
    (2, 2, 56.109375),
    (3, 3, 10.6640625),
    (4, 4, 11.6640625),
    (5, 5, 10.6640625),
    (6, 6, 11.33203125),
    (7, 8, 11.6640625),
    (9, 9, 14.0),
    (10, 10, 11.6640625),
    (11, 11, 12.6640625),
    (12, 12, 9.33203125),
    (13, 13, 11.88671875),
    (14, 14, 11.6640625),
    (15, 15, 13.6640625),
    (16, 16, 11.88671875),
    (17, 18, 10.6640625),
    (19, 19, 13.5546875),
    (20, 20, 12.109375),
    (21, 21, 13.109375),
    (22, 22, 12.6640625),
    (23, 23, 15.0),
    (24, 24, 13.5546875),
)

REPORT_COL_WIDTHS: dict[str, tuple[tuple[int, int, float], ...]] = {
    OVERALL: _METRIC_DAYWISE,
    VERTICAL: _METRIC_VERTICAL,
    CHANNEL: _METRIC_VERTICAL,
    SUB_SPLIT: _METRIC_VERTICAL,
    AMC_DAYWISE: _METRIC_AMC_DAY,
    AMC_SPLIT: _METRIC_AMC_SPLIT,
    AMC_VERTICAL: _METRIC_AMC_VERTICAL,
    D2C: _METRIC_D2C,
    SERVICE: _METRIC_VERTICAL,
}

HEADER_ROW_HEIGHT: dict[str, str] = {
    OVERALL: "43.2",
    VERTICAL: "43.2",
    CHANNEL: "43.2",
    SUB_SPLIT: "43.2",
    AMC_DAYWISE: "43.2",
    AMC_SPLIT: "57.6",
    AMC_VERTICAL: "57.6",
    D2C: "57.6",
    SERVICE: "43.2",
}

# FY-2026 sheetView zoomScale. None means omit zoomScale (Excel 100%).
REPORT_ZOOM: dict[str, str | None] = {
    OVERALL: "78",
    VERTICAL: None,
    CHANNEL: None,
    SUB_SPLIT: None,
    AMC_DAYWISE: "98",
    AMC_SPLIT: "110",
    AMC_VERTICAL: "104",
    D2C: "119",
    SERVICE: "114",
}

_COL_STYLE: dict[str, dict[int, int]] = {
    OVERALL: {2: 1, 3: 2},
    VERTICAL: {2: 1, 3: 2},
    CHANNEL: {2: 1, 3: 2},
    SUB_SPLIT: {2: 1, 3: 2},
    AMC_DAYWISE: {2: 1, 3: 2},
    AMC_SPLIT: {2: 1, 3: 2},
    AMC_VERTICAL: {2: 1, 3: 2},
    D2C: {2: 1, 3: 2},
    SERVICE: {2: 1, 3: 2},
}
_COL_BESTFIT: dict[str, frozenset[int]] = {
    OVERALL: frozenset({2, 6, 10, 11, 13, 14, 16, 17, 18, 21}),
    VERTICAL: frozenset({2, 6, 10, 11, 13, 14, 16, 17, 18, 21}),
    CHANNEL: frozenset({2, 6, 10, 11, 13, 14, 16, 17, 18, 21}),
    SUB_SPLIT: frozenset({2, 6, 10, 11, 13, 14, 16, 17, 18, 21}),
    AMC_DAYWISE: frozenset({6, 10, 11, 13, 14, 16, 17, 18, 21}),
    AMC_SPLIT: frozenset({3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 17, 19, 20, 21, 22, 24}),
    AMC_VERTICAL: frozenset({2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 17, 19, 20, 21, 22, 24}),
    D2C: frozenset({2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20, 21, 22, 24}),
    SERVICE: frozenset({6, 10, 11, 13, 14, 18}),
}
_SERVICE_HIDDEN_COLS = frozenset({16, 19, 21, 24})

_DATAFIELD_TAG = re.compile(r"<dataField\b[^>]*(?:/>|>)")
_SHEET_TAG = re.compile(r"<sheet\b[^>]*/?>")
_CALC_CELL_TAG = re.compile(r"<c\b[^>]*/?>")
_PANE_TAG = re.compile(r"<pane\b[^>]*/>\s*|<pane\b[^>]*>.*?</pane>\s*", re.DOTALL)
_SELECTION_PANE_ATTR = re.compile(r'\s+pane="(?:bottomRight|topRight|bottomLeft)"')


def normalize_datafield_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def datafield_numfmt(name: str) -> int | None:
    return DATAFIELD_NUMFMT.get(normalize_datafield_name(name))


def apply_styles_xml(styles_xml: str) -> str:
    """Aptos Narrow + FY-2026 custom 0.0%; retarget count cellXfs off 164."""
    styles_xml = _ensure_report_styles(styles_xml)
    styles_xml = styles_xml.replace('name val="Calibri"', f'name val="{RUN006_FONT}"')
    styles_xml = styles_xml.replace(
        f'<font><b/><sz val="16"/><color theme="1"/><name val="{RUN006_FONT}"/>',
        f'<font><sz val="16"/><color theme="1"/><name val="{RUN006_FONT}"/>',
        1,
    )
    styles_xml = styles_xml.replace("FFFFF2CC", RUN006_FILTER_FILL)
    styles_xml = re.sub(
        r'(<numFmt numFmtId="164" formatCode=)"#,##0"',
        r'\1"0.0%"',
        styles_xml,
        count=1,
    )
    styles_xml = styles_xml.replace(
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="2" xfId="0" '
        'applyNumberFormat="1" applyBorder="1"/>',
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="2" xfId="0" '
        'applyNumberFormat="1" applyBorder="1"/>',
        1,
    )
    styles_xml = styles_xml.replace(
        '<xf numFmtId="164" fontId="1" fillId="5" borderId="2" xfId="0" '
        'applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"/>',
        '<xf numFmtId="3" fontId="1" fillId="5" borderId="2" xfId="0" '
        'applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"/>',
        1,
    )
    return _ensure_title_source_xf(styles_xml)


TITLE_SOURCE_MARK = "<!--DFIP-TITLE-SRC-->"
XF_TITLE_SOURCE = 21


def _ensure_title_source_xf(styles_xml: str) -> str:
    """White 11pt font so B1 can feed Rectangle 1 without a second visible title."""
    if TITLE_SOURCE_MARK in styles_xml:
        return styles_xml
    fonts = re.search(r'fonts count="(\d+)"', styles_xml)
    xfs = re.search(r'cellXfs count="(\d+)"', styles_xml)
    if fonts is None or xfs is None:
        return styles_xml
    font_id = int(fonts.group(1))
    xf_count = int(xfs.group(1))
    if xf_count != XF_TITLE_SOURCE:
        return styles_xml
    styles_xml = styles_xml.replace(
        f'fonts count="{font_id}"',
        f'fonts count="{font_id + 1}"',
        1,
    )
    styles_xml = styles_xml.replace(
        "</fonts>",
        f'<font><sz val="11"/><color rgb="FFFFFFFF"/><name val="{RUN006_FONT}"/>'
        f'<family val="2"/><scheme val="minor"/></font></fonts>',
        1,
    )
    styles_xml = styles_xml.replace(
        f'cellXfs count="{xf_count}"',
        f'cellXfs count="{xf_count + 1}"',
        1,
    )
    return styles_xml.replace(
        "</cellXfs>",
        f'{TITLE_SOURCE_MARK}<xf numFmtId="0" fontId="{font_id}" fillId="0" '
        f'borderId="0" xfId="0" applyFont="1"/></cellXfs>',
        1,
    )


def apply_datafield_formats(pivot_xml: str) -> str:
    """Set dataField numFmtId only. Field list / axes / cacheId stay intact."""

    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        name_match = re.search(r' name="([^"]*)"', tag)
        if name_match is None:
            return tag
        fmt = datafield_numfmt(name_match.group(1))
        if fmt is None:
            return tag
        if "numFmtId=" in tag:
            return re.sub(r'numFmtId="\d+"', f'numFmtId="{fmt}"', tag, count=1)
        if tag.endswith("/>"):
            return tag[:-2] + f' numFmtId="{fmt}"/>'
        return tag[:-1] + f' numFmtId="{fmt}">'

    return _DATAFIELD_TAG.sub(_replace, pivot_xml)


def apply_pivot_table_style(pivot_xml: str) -> str:
    """Ensure PivotStyleLight16 without copying FY-2026 dxf/format field indexes."""
    if "pivotTableStyleInfo" in pivot_xml or "tableStyleInfo" in pivot_xml:
        return pivot_xml
    if "</pivotTableDefinition>" not in pivot_xml:
        return pivot_xml
    return pivot_xml.replace(
        "</pivotTableDefinition>",
        PIVOT_TABLE_STYLE + "</pivotTableDefinition>",
        1,
    )


def relocate_title_chrome(xml: str) -> str:
    """Drop purpose/hint/empty-state cells. FY-2026 has none."""
    return _CHROME_CELL.sub("", xml)


def is_eureka_forbes_company(name: str) -> bool:
    return name.strip().casefold() == EUREKA_FORBES_TITLE_NAME


def report_title_prefix(company_name: str) -> str:
    name = company_name.strip() or "Company"
    if is_eureka_forbes_company(name):
        return f"{name} | Web Engage Report"
    return f"{name} | Report"


def _month_label_sort_key(label: str) -> tuple[int, int]:
    match = _MONTH_LABEL_RE.match(label.strip())
    if match is None:
        return (9999, 99)
    abbr = match.group(1).title()
    year = 2000 + int(match.group(2))
    try:
        month = _MONTH_ABBR.index(abbr) + 1
    except ValueError:
        month = 99
    return (year, month)


def _month_label_from_start(value: object) -> str:
    parsed: date | None = None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value or "").strip()
        if text:
            try:
                parsed = date.fromisoformat(text[:10])
            except ValueError:
                parsed = None
    if parsed is None:
        return ""
    return f"{_MONTH_ABBR[parsed.month - 1]}-{parsed.strftime('%y')}"


def month_label_from_fact(row: Mapping[str, object]) -> str:
    """Prefer published month_label; fall back to month_start as MMM-YY."""
    label = str(row.get("month_label") or row.get("Month") or "").strip()
    if _MONTH_LABEL_RE.match(label):
        return label
    from_start = _month_label_from_start(row.get("month_start"))
    if from_start:
        return from_start
    return label


def report_month_range_label(rows: Sequence[Mapping[str, object]]) -> str:
    """MMM-YY span from published facts in the workbook, not filenames."""
    labels: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = month_label_from_fact(row)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    if not labels:
        return ""
    labels.sort(key=_month_label_sort_key)
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} to {labels[-1]}"


def report_workbook_title(company_name: str, rows: Sequence[Mapping[str, object]]) -> str:
    prefix = report_title_prefix(company_name)
    month_range = report_month_range_label(rows)
    if not month_range:
        return prefix
    return f"{prefix} | Month {month_range}"


def _excel_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def report_title_formula(company_name: str, rows: Sequence[Mapping[str, object]]) -> str:
    """Static snapshot title until Refresh All types Month as dates."""
    prefix = report_title_prefix(company_name)
    cached = report_workbook_title(company_name, rows)
    fmt = _excel_quote("[$-409]MMM-YY")
    month = f"PublishedFacts!{_MONTH_COL}:{_MONTH_COL}"
    dynamic = (
        f"{_excel_quote(prefix + ' | Month ')}&TEXT(MIN({month}),{fmt})"
        f'&IF(MIN({month})=MAX({month}),""," to "&TEXT(MAX({month}),{fmt}))'
    )
    return f"IF(COUNT({month})=0,{_excel_quote(cached)},{dynamic})"


def _drop_title_merge(xml: str) -> str:
    """Do not force B1:X1. The FY-2026 title lives in Rectangle 1."""
    if f'mergeCell ref="{TITLE_MERGE_REF}"' not in xml:
        return xml
    xml = xml.replace(f'<mergeCell ref="{TITLE_MERGE_REF}"/>', "")
    match = _MERGE_OPEN.search(xml)
    if match is None:
        return xml
    count = int(match.group(1)) - 1
    if count <= 0:
        return re.sub(r"<mergeCells count=\"\d+\">\s*</mergeCells>", "", xml, count=1)
    return xml[: match.start()] + f'<mergeCells count="{count}">' + xml[match.end() :]


def _col_width_attr(width: float) -> str:
    if float(width).is_integer():
        return str(int(width))
    return str(width)


def _emit_report_cols(sheet_name: str, widths: tuple[tuple[int, int, float], ...]) -> str:
    parts = []
    styles = _COL_STYLE.get(sheet_name, {})
    bestfit = _COL_BESTFIT.get(sheet_name, frozenset())
    for start, end, width in widths:
        extra = ""
        style = styles.get(start)
        if style is not None:
            extra += f' style="{style}"'
        if start in bestfit:
            extra += ' bestFit="1"'
        hidden = ""
        if sheet_name == SERVICE and start in _SERVICE_HIDDEN_COLS:
            hidden = ' hidden="1"'
        parts.append(
            f'<col min="{start}" max="{end}" width="{_col_width_attr(width)}"'
            f'{extra}{hidden} customWidth="1"/>'
        )
    return "".join(parts) + CAPTION_HIDDEN_COLS


def apply_sheet_zoom(xml: str, sheet_name: str) -> str:
    zoom = REPORT_ZOOM.get(sheet_name)

    def _view(match: re.Match[str]) -> str:
        tag = match.group(0)
        tag = re.sub(r'\s+zoomScale="[^"]*"', "", tag)
        tag = re.sub(r'\s+zoomScaleNormal="[^"]*"', "", tag)
        tag = re.sub(r'\s+topLeftCell="[^"]*"', "", tag)
        attrs = ' zoomScaleNormal="100"'
        if zoom:
            attrs = f' zoomScale="{zoom}"' + attrs
        return re.sub(r"<sheetView\b", "<sheetView" + attrs, tag, count=1)

    return re.sub(r"<sheetView\b[^>]*>", _view, xml, count=1)


def apply_report_title(
    xml: str,
    *,
    client_name: str,
    rows: Sequence[Mapping[str, object]],
    refreshable: bool,
) -> str:
    cached = report_workbook_title(client_name, rows)
    if refreshable:
        cell = (
            f'<c r="B1" s="{XF_TITLE_SOURCE}" t="str">'
            f"<f>{_xml_text(report_title_formula(client_name, rows))}</f>"
            f"<v>{_xml_text(cached)}</v></c>"
        )
    else:
        cell = _inline("B1", cached, style=XF_TITLE_SOURCE)
    if _TITLE_CELL.search(xml):
        xml = _TITLE_CELL.sub(cell, xml, count=1)
    elif re.search(r'<row r="1"[^>]*>', xml):
        xml = re.sub(r'(<row r="1"[^>]*>)', rf"\1{cell}", xml, count=1)
    else:
        xml = xml.replace(
            "<sheetData>",
            f'<sheetData><row r="1">{cell}</row>',
            1,
        )
    return _drop_title_merge(xml)


def _title_banner_xml(title: str, sheet_name: str) -> str:
    uid = str(uuid5(_TITLE_BANNER_NS, sheet_name)).upper()
    return (
        "<xdr:oneCellAnchor>"
        "<xdr:from><xdr:col>0</xdr:col><xdr:colOff>595312</xdr:colOff>"
        "<xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        '<xdr:ext cx="13237586" cy="655949"/>'
        '<xdr:sp macro="" textlink="B1">'
        "<xdr:nvSpPr>"
        '<xdr:cNvPr id="1" name="Rectangle 1">'
        '<a:extLst><a:ext uri="{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}">'
        '<a16:creationId xmlns:a16="http://schemas.microsoft.com/office/drawing/2014/main" '
        f'id="{{{uid}}}"/></a:ext></a:extLst></xdr:cNvPr>'
        "<xdr:cNvSpPr/></xdr:nvSpPr>"
        '<xdr:spPr><a:xfrm><a:off x="195262" y="381000"/>'
        '<a:ext cx="13237586" cy="655949"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
        '<xdr:style><a:lnRef idx="2"><a:schemeClr val="dk1"/></a:lnRef>'
        '<a:fillRef idx="1"><a:schemeClr val="lt1"/></a:fillRef>'
        '<a:effectRef idx="0"><a:schemeClr val="dk1"/></a:effectRef>'
        '<a:fontRef idx="minor"><a:schemeClr val="dk1"/></a:fontRef></xdr:style>'
        '<xdr:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" '
        'rIns="91440" bIns="45720"><a:spAutoFit/></a:bodyPr>'
        '<a:lstStyle/><a:p><a:pPr algn="l"/><a:r>'
        '<a:rPr lang="en-US" sz="3600" b="1" u="none" cap="none" spc="0">'
        '<a:ln w="0"/><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
        '<a:effectLst><a:outerShdw blurRad="38100" dist="19050" dir="2700000" '
        'algn="tl" rotWithShape="0"><a:schemeClr val="dk1">'
        '<a:alpha val="40000"/></a:schemeClr></a:outerShdw></a:effectLst></a:rPr>'
        f"<a:t>{_xml_text(title)}</a:t></a:r></a:p></xdr:txBody>"
        "</xdr:sp><xdr:clientData/></xdr:oneCellAnchor>"
    )


def apply_title_banner_xml(xml: str, title: str, sheet_name: str) -> str:
    banner = _title_banner_xml(title, sheet_name)
    if _ONE_CELL_RECTANGLE.search(xml):
        return _ONE_CELL_RECTANGLE.sub(banner, xml, count=1)
    match = _WSDR_OPEN.search(xml)
    if match is None:
        return xml
    return xml[: match.end()] + banner + xml[match.end() :]


def apply_title_banners_into(parts: dict[str, bytes], title: str) -> None:
    """Stamp the FY-2026 Rectangle 1 title onto each report drawing."""
    drawings = sorted(
        (
            name
            for name in parts
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml")
        ),
        key=lambda name: int(re.search(r"drawing(\d+)", name).group(1)),
    )
    for sheet_name, part in zip(REPORT_SHEET_NAMES, drawings, strict=False):
        parts[part] = apply_title_banner_xml(
            parts[part].decode("utf-8"),
            title,
            sheet_name,
        ).encode("utf-8")


def _report_sheet_ids(workbook_xml: str) -> frozenset[int]:
    """sheetId values for the nine report tabs, derived from workbook.xml."""
    ids: set[int] = set()
    for tag in _SHEET_TAG.findall(workbook_xml):
        name_match = re.search(r'\bname="([^"]*)"', tag)
        id_match = re.search(r'\bsheetId="(\d+)"', tag)
        if name_match is None or id_match is None:
            continue
        name = name_match.group(1).replace("&amp;", "&")
        if name in REPORT_SHEET_NAMES:
            ids.add(int(id_match.group(1)))
    return frozenset(ids)


def remap_calcchain_chrome_cells(calcchain_xml: str, workbook_xml: str) -> str:
    """Drop stale purpose/hint/empty-state calcChain refs on report sheets.

    Excel Save writes ``xl/calcChain.xml`` for the empty-state formula at A6.
    Those cells are stripped to match FY-2026. Stale A6 entries make Excel
    refuse ``Workbooks.Open``. Only report-sheet ``i`` (workbook sheetId)
    entries are removed so a Facts/PublishedFacts A6 cell is left alone.
    ``i`` omitted on a later ``<c>`` inherits the previous entry.
    """
    report_ids = _report_sheet_ids(workbook_xml)
    if not report_ids:
        return calcchain_xml
    last_i: int | None = None
    kept: list[str] = []

    def _keep(tag: str) -> str | None:
        nonlocal last_i
        id_match = re.search(r'\bi="(\d+)"', tag)
        if id_match is not None:
            last_i = int(id_match.group(1))
        ref_match = re.search(r'\br="([^"]+)"', tag)
        ref = ref_match.group(1) if ref_match else ""
        if last_i is not None and last_i in report_ids and ref in _CHROME_CALC_REFS:
            return None
        return tag

    start = 0
    for match in _CALC_CELL_TAG.finditer(calcchain_xml):
        kept.append(calcchain_xml[start : match.start()])
        tag = _keep(match.group(0))
        if tag is not None:
            kept.append(tag)
        start = match.end()
    kept.append(calcchain_xml[start:])
    return "".join(kept)


def remove_report_freeze_panes(xml: str) -> str:
    """Drop frozen panes. FY-2026 report sheets have none."""
    xml = _PANE_TAG.sub("", xml)
    return _SELECTION_PANE_ATTR.sub("", xml)


def apply_report_sheet_layout(xml: str, sheet_name: str) -> str:
    xml = relocate_title_chrome(xml)
    xml = remove_report_freeze_panes(xml)
    xml = apply_sheet_zoom(xml, sheet_name)
    widths = REPORT_COL_WIDTHS.get(sheet_name)
    if widths is None:
        return xml
    xml = re.sub(
        r"<cols>.*?</cols>",
        f"<cols>{_emit_report_cols(sheet_name, widths)}</cols>",
        xml,
        count=1,
        flags=re.DOTALL,
    )
    xml = re.sub(
        r'defaultRowHeight="[^"]+"',
        f'defaultRowHeight="{RUN006_DEFAULT_ROW_HEIGHT}"',
        xml,
        count=1,
    )
    height = HEADER_ROW_HEIGHT.get(sheet_name)
    if height:
        if re.search(r'<row r="9"[^>]*>', xml):

            def _row9(match: re.Match[str]) -> str:
                tag = match.group(0)
                if ' ht="' in tag:
                    tag = re.sub(r'ht="[^"]+"', f'ht="{height}"', tag, count=1)
                else:
                    tag = tag.replace("<row ", f'<row ht="{height}" ', 1)
                if "customHeight=" not in tag:
                    tag = tag.replace("<row ", '<row customHeight="1" ', 1)
                return tag

            xml = re.sub(r'<row r="9"[^>]*>', _row9, xml, count=1)
        else:
            xml = xml.replace(
                "</sheetData>",
                f'<row r="9" ht="{height}" customHeight="1"/></sheetData>',
                1,
            )
    return xml


def apply_reference_formatting_into(
    parts: dict[str, bytes],
    *,
    client_name: str | None = None,
    rows: Sequence[Mapping[str, object]] | None = None,
    refreshable: bool = False,
) -> None:
    """Patch styles, pivot dataField formats, and report-sheet layout in memory."""
    workbook_xml = parts["xl/workbook.xml"].decode("utf-8")
    rels_xml = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    sheet_parts = _sheet_part_map(workbook_xml, rels_xml)
    parts["xl/styles.xml"] = apply_styles_xml(parts["xl/styles.xml"].decode("utf-8")).encode(
        "utf-8"
    )
    for name, data in list(parts.items()):
        if not name.startswith("xl/pivotTables/pivotTable") or not name.endswith(".xml"):
            continue
        if "/_rels/" in name:
            continue
        parts[name] = apply_pivot_table_style(apply_datafield_formats(data.decode("utf-8"))).encode(
            "utf-8"
        )
    title_rows = rows or ()
    for sheet_name in REPORT_SHEET_NAMES:
        part = sheet_parts.get(sheet_name)
        if not part:
            continue
        xml = apply_report_sheet_layout(
            parts[part].decode("utf-8"),
            sheet_name,
        )
        if client_name is not None:
            xml = apply_report_title(
                xml,
                client_name=client_name,
                rows=title_rows,
                refreshable=refreshable,
            )
        parts[part] = xml.encode("utf-8")
    if "xl/calcChain.xml" in parts:
        parts["xl/calcChain.xml"] = remap_calcchain_chrome_cells(
            parts["xl/calcChain.xml"].decode("utf-8"),
            workbook_xml,
        ).encode("utf-8")


def apply_reference_formatting(
    body: bytes,
    *,
    client_name: str | None = None,
    rows: Sequence[Mapping[str, object]] | None = None,
    refreshable: bool = False,
) -> bytes:
    """Patch styles, pivot dataField formats, and report-sheet layout.

    When Excel Save left a calcChain, retarget the relocated chrome cells
    there as well. The part is left untouched when absent (tracked template).
    """

    def _apply(parts: dict[str, bytes]) -> None:
        apply_reference_formatting_into(
            parts,
            client_name=client_name,
            rows=rows,
            refreshable=refreshable,
        )

    return mutate_xlsx(body, _apply)
