"""Native Excel PivotTable + slicer package for the nine Daily Report sheets.

Authoring is ZIP/OOXML (no COM generation path). PivotTable parts are
re-serialized through openpyxl so desktop Excel accepts them. One shared
pivot cache binds to the PublishedFacts worksheet in the tracked template, and
P8 rebinds that cache to the publication's static PublishedFacts cells.

Python UNIQUE/FILTER/SUMIFS in daily_report.report_formula remains the P10
reconstruction contract. It is not stored on the delivered report sheets.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from zipfile import ZipFile

from openpyxl.utils import get_column_letter

from dfip_web.client_workbook import FACT_HEADERS, FAKE_MASHUP_ZIP_PARTS
from dfip_web.daily_report import (
    AMC_GROUP,
    CAPTION_KEY_COL,
    CAPTION_NAME_COL,
    D2C_GROUP,
    EMPTY_STATE,
    MEASURE_FORMATS,
    MEASURE_HEADERS,
    MEASURE_OPS,
    NS_MAIN,
    NS_REL,
    PUBLISHED_HEADER,
    REPORT_CONTRACTS,
    REPORT_SHEET_NAMES,
    SERVICE,
    SERVICE_FILTER_LOGIC_1,
    XF_GROUP_BANNER,
    XF_HINT,
    XF_PURPOSE,
    XF_TITLE,
    ZERO_VS_BLANK_HINT,
    ZERO_VS_BLANK_HINT_VERTICAL,
    ReportSheetContract,
    _caption_pairs,
    _clone_zipinfo,
    _col,
    _ensure_report_styles,
    _inline,
    _new_sheet_zipinfo,
    _next_relationship_id,
    _rels_with_metadata,
    _sheet_part_map,
    _types_with_metadata,
    _xml_attr,
    _xml_text,
)

PIVOT_CACHE_ID = 1
# x14 slicer tabular id. Distinct from workbook cacheId; Excel stores both.
SLICER_PIVOT_CACHE_ID = 110000011
PIVOT_CACHE_PART = "xl/pivotCache/pivotCacheDefinition1.xml"
PIVOT_RECORDS_PART = "xl/pivotCache/pivotCacheRecords1.xml"

NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS_XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
NS_SLICER_MAIN = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_PIVOT_CACHE_REL = f"{NS_REL}/pivotCacheDefinition"
NS_PIVOT_TABLE_REL = f"{NS_REL}/pivotTable"
NS_DRAWING_REL = f"{NS_REL}/drawing"
NS_SLICER_REL = "http://schemas.microsoft.com/office/2007/relationships/slicer"
NS_SLICER_CACHE_REL = "http://schemas.microsoft.com/office/2007/relationships/slicerCache"
NS_PIVOT_RECORDS_REL = f"{NS_REL}/pivotCacheRecords"

CT_CACHE = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"
CT_RECORDS = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheRecords+xml"
CT_PIVOT = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotTable+xml"
CT_SLICER = "application/vnd.ms-excel.slicer+xml"
CT_SLICER_CACHE = "application/vnd.ms-excel.slicerCache+xml"
CT_DRAWING = "application/vnd.openxmlformats-officedocument.drawing+xml"

SLICER_CACHES_URI = "{BBE1A952-AA13-448e-AADC-164F8A28A991}"
SHEET_SLICER_URI = "{A8765BA9-456A-4dab-B4F3-ACF838C121DE}"
CACHE_X14_URI = "{725AE2AE-9491-48be-B2B4-4EB974FC3084}"

PIVOT_TABLE_NAMES: dict[str, str] = {
    REPORT_CONTRACTS[0].name: "PivotOverall",
    REPORT_CONTRACTS[1].name: "PivotVertical",
    REPORT_CONTRACTS[2].name: "PivotChannel",
    REPORT_CONTRACTS[3].name: "PivotSubSplit",
    REPORT_CONTRACTS[4].name: "PivotAmcDaywise",
    REPORT_CONTRACTS[5].name: "PivotAmcSplit",
    REPORT_CONTRACTS[6].name: "PivotAmcVertical",
    REPORT_CONTRACTS[7].name: "PivotD2c",
    REPORT_CONTRACTS[8].name: "PivotService",
}

CACHE_FIELD_CAPTIONS: dict[str, str] = {
    "filter_logic_1_group": "Filter Logic 1_2",
}

# Stable template uniques for page fields. Snapshot generation replaces these
# with first-seen PublishedFacts values so item indexes match the worksheet.
PAGE_GROUP_ITEMS: tuple[str, ...] = (
    "Group1",
    "Group2",
    "Group3",
    "Group4",
    "Group5",
    "Group6",
    "Group7",
)

_DATA_NUMFMT = {"count": 164, "money": 165, "rate": 166, "roas": 165}

_SKIP_ZIP_PREFIXES = (
    "xl/pivotCache/",
    "xl/pivotTables/",
    "xl/slicers/",
    "xl/slicerCaches/",
)

_SLICER_FALLBACK = "This shape represents a slicer. Slicers are supported in Excel 2010 or later."


@dataclass(frozen=True)
class ReportPivotBinding:
    contract: ReportSheetContract
    sheet_part: str
    sheet_id: int
    index: int  # 1-based pivot/slicer/drawing number

    @property
    def pivot_name(self) -> str:
        return PIVOT_TABLE_NAMES[self.contract.name]

    @property
    def pivot_part(self) -> str:
        return f"xl/pivotTables/pivotTable{self.index}.xml"

    @property
    def slicer_part(self) -> str:
        return f"xl/slicers/slicer{self.index}.xml"

    @property
    def drawing_part(self) -> str:
        return f"xl/drawings/drawing{self.index}.xml"


def cache_field_names() -> tuple[str, ...]:
    names = list(FACT_HEADERS)
    for header, kind, *_rest in MEASURE_OPS:
        if kind != "sum":
            names.append(header)
    return tuple(names)


def cache_field_index(header: str) -> int:
    published = PUBLISHED_HEADER.get(header, header)
    names = cache_field_names()
    return names.index(published)


def calculated_cache_fields() -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = []
    for op in MEASURE_OPS:
        header, kind = op[0], op[1]
        if kind == "sum":
            continue
        left = _formula_atom(op[2])
        right = _formula_atom(op[3])
        formula = f"{left}-{right}" if kind == "diff" else f"{left}/{right}"
        fields.append((header, formula))
    return tuple(fields)


def _formula_atom(header: str) -> str:
    if re.search(r"[^A-Za-z0-9]", header):
        return "'" + header.replace("'", "''") + "'"
    return header


def _uid(key: str) -> str:
    raw = uuid5(NAMESPACE_URL, f"dfip-p11:{key}")
    blob = bytearray(raw.bytes)
    blob[6] = (blob[6] & 0x0F) | 0x40
    blob[8] = (blob[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(blob))).upper()


def _string_shared_items_xml(values: Sequence[str]) -> str:
    """String shared items with no numeric type flags (those flags crashed refresh)."""
    if not values:
        return "<sharedItems/>"
    body = "".join(f'<s v="{_xml_attr(value)}"/>' for value in values)
    return f'<sharedItems count="{len(values)}">{body}</sharedItems>'


def _shared_items_xml(field_name: str) -> str:
    """Empty shared items except page-filter fields, which need named defaults.

    Numeric min/max=0 on blank columns, and ``rowItems t=data x=0`` against empty
    Month/Day, crashed desktop Excel (RPC 0x8001010A). Page-filter fields only
    store the default string values Excel matches by caption after populate.
    """
    if field_name == "filter_logic_1_group":
        return _string_shared_items_xml(PAGE_GROUP_ITEMS)
    if field_name == "Filter Logic 1":
        return _string_shared_items_xml((SERVICE_FILTER_LOGIC_1,))
    return "<sharedItems/>"


def _published_facts_ref(row_count: int) -> str:
    last_row = max(1 + max(row_count, 1), 2)
    last_col = get_column_letter(len(FACT_HEADERS))
    return f"A1:{last_col}{last_row}"


def _cache_source_xml(*, row_count: int = 1) -> str:
    ref = _published_facts_ref(row_count)
    return (
        '<cacheSource type="worksheet">'
        f'<worksheetSource ref="{_xml_attr(ref)}" sheet="PublishedFacts"/>'
        "</cacheSource>"
    )


def pivot_cache_definition_xml() -> str:
    fields: list[str] = []
    for name in FACT_HEADERS:
        caption = CACHE_FIELD_CAPTIONS.get(name)
        cap = f' caption="{_xml_attr(caption)}"' if caption else ""
        fields.append(
            f'<cacheField name="{_xml_attr(name)}"{cap} numFmtId="0">'
            f"{_shared_items_xml(name)}</cacheField>"
        )
    for name, formula in calculated_cache_fields():
        fields.append(
            f'<cacheField name="{_xml_attr(name)}" numFmtId="0" '
            f'formula="{_xml_attr(formula)}" databaseField="0"/>'
        )
    uid = _uid("pivot-cache-1")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<pivotCacheDefinition xmlns="{NS_MAIN}" xmlns:r="{NS_REL}" '
        f'xmlns:mc="{NS_MC}" mc:Ignorable="xr" xmlns:xr="{NS_XR}" '
        'r:id="rId1" refreshedBy="DFIP" backgroundQuery="0" createdVersion="8" '
        'refreshedVersion="8" minRefreshableVersion="3" recordCount="0" '
        f'refreshOnLoad="0" xr:uid="{{{uid}}}">'
        f"{_cache_source_xml(row_count=1)}"
        f'<cacheFields count="{len(fields)}">{"".join(fields)}</cacheFields>'
        f'<extLst><ext uri="{CACHE_X14_URI}" '
        'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        f'<x14:pivotCacheDefinition pivotCacheId="{SLICER_PIVOT_CACHE_ID}"/>'
        "</ext></extLst></pivotCacheDefinition>"
    )


def pivot_cache_records_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<pivotCacheRecords xmlns="{NS_MAIN}" count="0"/>'
    )


def bind_pivot_cache_for_snapshot(cache_xml: str, row_count: int) -> str:
    """Point the shared cache at static PublishedFacts cells (P8 snapshot).

    Historical snapshots keep ``refreshOnLoad="0"`` so Excel does not auto-
    refresh from the live API. ``recordCount`` stays 0 until Excel rebuilds
    records; inflating it without ``pivotCacheRecords`` crashes refresh.
    """
    source = _cache_source_xml(row_count=row_count)
    xml = re.sub(r"<cacheSource\b.*?</cacheSource>", source, cache_xml, count=1, flags=re.DOTALL)
    xml = re.sub(r"<cacheSource\b[^>]*/>", source, xml, count=1)
    if "refreshOnLoad=" in xml:
        xml = re.sub(r'refreshOnLoad="\d+"', 'refreshOnLoad="0"', xml, count=1)
    else:
        xml = xml.replace("<pivotCacheDefinition ", '<pivotCacheDefinition refreshOnLoad="0" ', 1)
    xml = re.sub(r'recordCount="\d+"', 'recordCount="0"', xml, count=1)
    return xml


def _page_fields(contract: ReportSheetContract) -> list[tuple[str, str | None]]:
    """(display header, selected shared-item value or None for all)."""
    pages: list[tuple[str, str | None]] = []
    if contract.default_filter_logic_1:
        pages.append(("Filter Logic 1", contract.default_filter_logic_1))
    if contract.name == SERVICE:
        pages.append(("Filter Logic 2", None))
    elif contract.default_group:
        pages.append(("Filter Logic 1_2", contract.default_group))
    return pages


def _axis_for_field(contract: ReportSheetContract, field_name: str) -> str | None:
    published_rows = [PUBLISHED_HEADER.get(name, name) for name in contract.display_fields]
    if field_name in published_rows:
        return "axisRow"
    for header, _selected in _page_fields(contract):
        if PUBLISHED_HEADER.get(header, header) == field_name:
            return "axisPage"
    return None


def _page_field_attrs(field_name: str) -> str:
    if field_name == "Filter Logic 2":
        return ' multipleItemSelectionAllowed="1"'
    return ""


def _page_item_values(field_name: str) -> tuple[str, ...]:
    if field_name in {"Filter Logic 1_2", "filter_logic_1_group"}:
        return PAGE_GROUP_ITEMS
    if field_name == "Filter Logic 1":
        return (SERVICE_FILTER_LOGIC_1,)
    return ()


def _page_items_xml(field_name: str, selected: str | None) -> str:
    values = _page_item_values(field_name)
    if not values or selected is None:
        return '<items count="1"><item t="default"/></items>'
    body = "".join(f'<item x="{index}"/>' for index in range(len(values)))
    return f'<items count="{len(values) + 1}">{body}<item t="default"/></items>'


def _pivot_field_xml(contract: ReportSheetContract, field_name: str, is_data: bool) -> str:
    if is_data:
        extra = ""
        if field_name not in FACT_HEADERS:
            extra = ' dragToRow="0" dragToCol="0" dragToPage="0" defaultSubtotal="0"'
        return f'<pivotField dataField="1" showAll="0"{extra}/>'
    axis = _axis_for_field(contract, field_name)
    selected = None
    for header, value in _page_fields(contract):
        if PUBLISHED_HEADER.get(header, header) == field_name:
            selected = value
            break
    axis_attr = f' axis="{axis}"' if axis else ""
    extra = _page_field_attrs(field_name) if axis == "axisPage" else ""
    if axis == "axisRow":
        items = '<items count="1"><item t="default" sd="1"/></items>'
        return f'<pivotField{axis_attr} showAll="0">{items}</pivotField>'
    if axis == "axisPage":
        items = _page_items_xml(field_name, selected)
        return f'<pivotField{axis_attr} showAll="0"{extra}>{items}</pivotField>'
    return f'<pivotField{axis_attr} showAll="0"/>'


def pivot_table_xml(binding: ReportPivotBinding) -> str:
    contract = binding.contract
    names = cache_field_names()
    measure_set = set(MEASURE_HEADERS)
    pivot_fields = []
    for name in names:
        is_measure = name in measure_set
        used_as_axis = _axis_for_field(contract, name) is not None
        pivot_fields.append(_pivot_field_xml(contract, name, is_measure and not used_as_axis))

    row_refs = "".join(
        f'<field x="{cache_field_index(header)}"/>' for header in contract.display_fields
    )
    pages = _page_fields(contract)
    if pages:
        page_xml = "".join(_page_field_tag(header, selected) for header, selected in pages)
        page_block = f'<pageFields count="{len(pages)}">{page_xml}</pageFields>'
    else:
        page_block = ""
    data_xml = []
    for index, header in enumerate(MEASURE_HEADERS):
        fld = cache_field_index(header)
        num = _DATA_NUMFMT[MEASURE_FORMATS[index]]
        display = header if header not in FACT_HEADERS else f"  {header}"
        data_xml.append(
            f'<dataField name="{_xml_attr(display)}" fld="{fld}" '
            f'baseField="0" baseItem="0" numFmtId="{num}"/>'
        )
    uid = _uid(f"pivot-{binding.pivot_name}")
    caption = contract.display_fields[0]
    col_items = "".join(
        f'<i t="data"><x v="{index}"/></i>' for index in range(len(MEASURE_HEADERS))
    )
    # Excel requires rowItems/colItems immediately after their field lists.
    # Compact `rowFields, colFields, pageFields, dataFields, rowItems` is rejected.
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<pivotTableDefinition xmlns="{NS_MAIN}" '
        f'xmlns:r="{NS_REL}" xmlns:mc="{NS_MC}" mc:Ignorable="xr" xmlns:xr="{NS_XR}" '
        f'xr:uid="{{{uid}}}" name="{binding.pivot_name}" cacheId="{PIVOT_CACHE_ID}" '
        'r:id="rId1" dataCaption="Values" showError="1" updatedVersion="8" '
        'minRefreshableVersion="3" itemPrintTitles="1" createdVersion="8" '
        'indent="0" compact="1" outline="1" outlineData="1" compactData="1" '
        f'multipleFieldFilters="0" applyWidthHeightFormats="1" '
        f'rowHeaderCaption="{_xml_attr(caption)}">'
        '<location ref="B9:X10" firstHeaderRow="0" firstDataRow="1" firstDataCol="1"/>'
        f'<pivotFields count="{len(names)}">{"".join(pivot_fields)}</pivotFields>'
        f'<rowFields count="{len(contract.display_fields)}">{row_refs}</rowFields>'
        '<rowItems count="1"><i t="grand"><x/></i></rowItems>'
        '<colFields count="1"><field x="-2"/></colFields>'
        f'<colItems count="{len(MEASURE_HEADERS)}">{col_items}</colItems>'
        f"{page_block}"
        f'<dataFields count="{len(data_xml)}">{"".join(data_xml)}</dataFields>'
        f"{_contract_caption_filters_xml(contract)}"
        '<pivotTableStyleInfo name="PivotStyleLight16" showRowHeaders="1" '
        'showColHeaders="1" showRowStripes="0" showColStripes="0" showLastColumn="1"/>'
        "</pivotTableDefinition>"
    )


def _excel_safe_pivot_xml(xml: str) -> str:
    """Re-emit PivotTable XML in the attribute/child order desktop Excel writes.

    Hand-authored compact parts are valid OOXML but Excel 16 refuses to open
    them (0x800A03EC). openpyxl's TableDefinition serializer matches the
    verbose form Excel accepts, including r:id and rowItems-before-colFields.
    """
    from openpyxl.pivot.table import TableDefinition
    from openpyxl.xml.functions import fromstring, tostring

    table = TableDefinition.from_tree(fromstring(xml.encode("utf-8")))
    table.id = "rId1"
    payload = tostring(table.to_tree())
    if isinstance(payload, bytes):
        text = payload.decode("utf-8")
    else:
        text = payload
    return re.sub(
        r"<rowItems count=\"1\"><i t=\"data\"[^>]*>.*?</i></rowItems>",
        '<rowItems count="1"><i t="grand"><x/></i></rowItems>',
        text,
        count=1,
        flags=re.DOTALL,
    )


def _page_field_tag(header: str, selected: str | None) -> str:
    fld = cache_field_index(header)
    values = _page_item_values(header)
    if selected and selected in values:
        return f'<pageField fld="{fld}" item="{values.index(selected)}" hier="-1"/>'
    return f'<pageField fld="{fld}" hier="-1"/>'


def _items_for_values(values: Sequence[str], selected: str | None) -> str:
    if not values or selected is None:
        return '<items count="1"><item t="default"/></items>'
    body = "".join(f'<item x="{index}"/>' for index in range(len(values)))
    return f'<items count="{len(values) + 1}">{body}<item t="default"/></items>'


def _nth_pivot_field(xml: str, fld: int) -> re.Match[str]:
    matches = list(
        re.finditer(r"<pivotField\b[^>]*/>|<pivotField\b[^>]*>.*?</pivotField>", xml, re.DOTALL)
    )
    if fld >= len(matches):
        raise ValueError(f"pivotField {fld} missing")
    return matches[fld]


def _pin_one_page_field(
    xml: str, header: str, selected: str | None, values: Sequence[str]
) -> str:
    fld = cache_field_index(header)
    if selected and selected in values:
        tag = f'<pageField fld="{fld}" item="{values.index(selected)}" hier="-1"/>'
    else:
        tag = f'<pageField fld="{fld}" hier="-1"/>'
    xml, n = re.subn(rf'<pageField fld="{fld}"[^/]*/>', tag, xml, count=1)
    if n != 1:
        return xml
    match = _nth_pivot_field(xml, fld)
    old = match.group(0)
    if "<items" not in old:
        return xml
    new, replaced = re.subn(
        r"<items[^>]*>.*?</items>",
        _items_for_values(values, selected),
        old,
        count=1,
        flags=re.DOTALL,
    )
    if replaced != 1:
        return xml
    return xml[: match.start()] + new + xml[match.end() :]


def _pin_page_field_state(
    xml: str,
    contract: ReportSheetContract,
    group_values: Sequence[str],
    fl1_values: Sequence[str],
) -> str:
    """Re-apply pageField item indexes after openpyxl re-serialization."""
    for header, selected in _page_fields(contract):
        if header == "Filter Logic 2":
            xml = _pin_one_page_field(xml, header, None, ())
        elif header == "Filter Logic 1":
            xml = _pin_one_page_field(xml, header, selected, fl1_values)
        else:
            xml = _pin_one_page_field(xml, header, selected, group_values)
    return xml


def _replace_cache_shared_items(cache_xml: str, field_name: str, values: Sequence[str]) -> str:
    shared = _string_shared_items_xml(values)
    pattern = (
        rf'(<cacheField name="{re.escape(field_name)}"[^>]*>)\s*'
        r"(?:<sharedItems/>|<sharedItems\b.*?</sharedItems>)\s*(</cacheField>)"
    )
    xml, n = re.subn(pattern, rf"\1{shared}\2", cache_xml, count=1, flags=re.DOTALL)
    if n != 1:
        raise ValueError(f"cache field {field_name} sharedItems not replaced")
    return xml


def _first_seen(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _row_text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return ""


def page_filter_uniques_from_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[str], list[str]]:
    """First-seen page-filter values, with report defaults present for indexing."""
    groups = _first_seen(
        _row_text(row, "filter_logic_1_group", "Filter Logic 1_2") for row in rows
    )
    fl1 = _first_seen(_row_text(row, "filter_logic_1", "Filter Logic 1") for row in rows)
    for default in (AMC_GROUP, D2C_GROUP, "Group2"):
        if default not in groups:
            groups.append(default)
    if SERVICE_FILTER_LOGIC_1 not in fl1:
        fl1.insert(0, SERVICE_FILTER_LOGIC_1)
    return groups, fl1


def apply_page_filter_defaults_xml(
    body: bytes, rows: Sequence[Mapping[str, object]]
) -> bytes:
    """Pin pageField item indexes to first-seen snapshot values (no stale blanks)."""
    if not rows:
        return body
    groups, fl1 = page_filter_uniques_from_rows(rows)
    with ZipFile(io.BytesIO(body), "r") as original:
        workbook_xml = original.read("xl/workbook.xml").decode("utf-8")
        rels_xml = original.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        bindings = bindings_from_workbook(workbook_xml, rels_xml)
        cache = _replace_cache_shared_items(
            original.read(PIVOT_CACHE_PART).decode("utf-8"), "filter_logic_1_group", groups
        )
        cache = _replace_cache_shared_items(cache, "Filter Logic 1", fl1)
        replacements: dict[str, bytes] = {PIVOT_CACHE_PART: cache.encode("utf-8")}
        for binding in bindings:
            xml = _pin_page_field_state(
                original.read(binding.pivot_part).decode("utf-8"),
                binding.contract,
                groups,
                fl1,
            )
            replacements[binding.pivot_part] = xml.encode("utf-8")
        out = io.BytesIO()
        with ZipFile(out, "w") as written:
            for info in original.infolist():
                data = replacements.get(info.filename, original.read(info.filename))
                written.writestr(_clone_zipinfo(info), data)
    return out.getvalue()


def apply_page_filter_defaults_excel(path: str | Path) -> None:
    """Set native CurrentPage after cache population so the selection survives refresh.

    Excel rebuilds page items on the first populated refresh and resets dropdowns
    to (All) unless CurrentPage is applied on the live PivotField. Calculated
    fields are re-added because that same rebuild drops them from DataFields.
    """
    import time

    win32com_client = __import__("win32com.client", fromlist=["DispatchEx"])
    target = Path(path).resolve()
    excel = win32com_client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    excel.EnableEvents = False
    workbook = None
    try:
        workbook = excel.Workbooks.Open(str(target), UpdateLinks=0, ReadOnly=False)
        workbook.Worksheets(REPORT_SHEET_NAMES[0]).PivotTables(1).PivotCache().Refresh()
        for contract in REPORT_CONTRACTS:
            pivot = workbook.Worksheets(contract.name).PivotTables(1)
            if contract.default_filter_logic_1 and pivot.PageFields.Count >= 1:
                pivot.PageFields.Item(1).CurrentPage = contract.default_filter_logic_1
            elif contract.default_group and pivot.PageFields.Count >= 1:
                pivot.PageFields.Item(1).CurrentPage = contract.default_group
            for index in range(1, pivot.CalculatedFields().Count + 1):
                calc = pivot.CalculatedFields().Item(index)
                try:
                    pivot.AddDataField(pivot.PivotFields(calc.Name))
                except Exception:
                    continue
        workbook.Save()
        workbook.Close(False)
        workbook = None
    finally:
        if workbook is not None:
            try:
                workbook.Close(False)
            except Exception:
                pass
        try:
            excel.Quit()
        except Exception:
            pass
        time.sleep(0.4)


def _caption_filter_xml(field_header: str, value: str, filter_id: int) -> str:
    fld = cache_field_index(field_header)
    return (
        f'<filter fld="{fld}" type="captionEqual" evalOrder="0" id="{filter_id}">'
        '<autoFilter ref="A1"><filterColumn colId="0"><filters>'
        f'<filter val="{_xml_attr(value)}"/>'
        "</filters></filterColumn></autoFilter></filter>"
    )


def _contract_caption_filters_xml(contract: ReportSheetContract) -> str:
    """Label filters for P10 page defaults. No shared-item indexes (crash-safe)."""
    parts: list[str] = []
    filter_id = 1
    if contract.default_filter_logic_1:
        parts.append(
            _caption_filter_xml("Filter Logic 1", contract.default_filter_logic_1, filter_id)
        )
        filter_id += 1
    if contract.default_group:
        parts.append(_caption_filter_xml("Filter Logic 1_2", contract.default_group, filter_id))
        filter_id += 1
    if not parts:
        return ""
    return f'<filters count="{len(parts)}">{"".join(parts)}</filters>'


def _slicer_cache_name(binding: ReportPivotBinding, field: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", field).strip("_")
    return f"Slicer_{slug}_{binding.pivot_name}"


def _slicer_object_name(binding: ReportPivotBinding, field: str) -> str:
    return f"{field} {binding.pivot_name}"


def _slicer_source_name(field: str) -> str:
    return PUBLISHED_HEADER.get(field, field)


def _slicer_caption(field: str) -> str:
    if field == "Filter Logic 1_2":
        return "Filter Logic 1_2"
    return field


def slicer_cache_xml(binding: ReportPivotBinding, field: str) -> str:
    name = _slicer_cache_name(binding, field)
    source = _slicer_source_name(field)
    uid = _uid(f"slicer-cache-{name}")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<slicerCacheDefinition xmlns="{NS_SLICER_MAIN}" '
        f'xmlns:mc="{NS_MC}" mc:Ignorable="x xr10" xmlns:x="{NS_MAIN}" '
        'xmlns:xr10="http://schemas.microsoft.com/office/spreadsheetml/2016/revision10" '
        f'name="{_xml_attr(name)}" xr10:uid="{{{uid}}}" sourceName="{_xml_attr(source)}">'
        "<pivotTables>"
        f'<pivotTable tabId="{binding.sheet_id}" name="{binding.pivot_name}"/>'
        "</pivotTables>"
        f'<data><tabular pivotCacheId="{SLICER_PIVOT_CACHE_ID}"/>'
        "</data>"
        '<extLst><x:ext uri="{470722E0-AACD-4C17-9CDC-17EF765DBC7E}" '
        'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main">'
        "<x15:slicerCacheHideItemsWithNoData/></x:ext></extLst>"
        "</slicerCacheDefinition>"
    )


def slicers_xml(binding: ReportPivotBinding) -> str:
    parts = []
    for field in binding.contract.slicers:
        name = _slicer_object_name(binding, field)
        cache = _slicer_cache_name(binding, field)
        caption = _slicer_caption(field)
        uid = _uid(f"slicer-{name}")
        cols = ' columnCount="2"' if field == "Filter Logic 1" else ""
        parts.append(
            f'<slicer name="{_xml_attr(name)}" xr10:uid="{{{uid}}}" '
            f'cache="{_xml_attr(cache)}" caption="{_xml_attr(caption)}"'
            f'{cols} rowHeight="241300"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<slicers xmlns="{NS_SLICER_MAIN}" xmlns:mc="{NS_MC}" '
        f'mc:Ignorable="x xr10" xmlns:x="{NS_MAIN}" '
        'xmlns:xr10="http://schemas.microsoft.com/office/spreadsheetml/2016/revision10">'
        f"{''.join(parts)}</slicers>"
    )


def _slicer_anchor(index: int, name: str, shape_id: int) -> str:
    # Two columns of slicers to the right of the compact pivot (starts at col Z=25).
    col = 25 + (index % 3) * 5
    row = 1 + (index // 3) * 14
    uid = _uid(f"drawing-{name}")
    return (
        '<xdr:twoCellAnchor editAs="oneCell">'
        f"<xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        f"<xdr:to><xdr:col>{col + 4}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{row + 12}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
        f'<mc:AlternateContent xmlns:mc="{NS_MC}" '
        'xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main">'
        '<mc:Choice Requires="a14">'
        '<xdr:graphicFrame macro="">'
        "<xdr:nvGraphicFramePr>"
        f'<xdr:cNvPr id="{shape_id}" name="{_xml_attr(name)}">'
        '<a:extLst><a:ext uri="{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}">'
        f'<a16:creationId xmlns:a16="http://schemas.microsoft.com/office/drawing/2014/main" '
        f'id="{{{uid}}}"/></a:ext></a:extLst></xdr:cNvPr>'
        "<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>"
        '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/drawing/2010/slicer">'
        f'<sle:slicer xmlns:sle="http://schemas.microsoft.com/office/drawing/2010/slicer" '
        f'name="{_xml_attr(name)}"/>'
        "</a:graphicData></a:graphic></xdr:graphicFrame></mc:Choice>"
        '<mc:Fallback xmlns="">'
        '<xdr:sp macro="" textlink="">'
        "<xdr:nvSpPr>"
        f'<xdr:cNvPr id="{shape_id + 100}" name="{_xml_attr(name)} Fallback"/>'
        '<xdr:cNvSpPr><a:spLocks noTextEdit="1"/></xdr:cNvSpPr></xdr:nvSpPr>'
        '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="1000000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '<a:solidFill><a:prstClr val="white"/></a:solidFill></xdr:spPr>'
        '<xdr:txBody><a:bodyPr vertOverflow="clip" horzOverflow="clip"/>'
        '<a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="1100"/>'
        f"<a:t>{_xml_text(_SLICER_FALLBACK)}</a:t></a:r></a:p></xdr:txBody>"
        "</xdr:sp></mc:Fallback></mc:AlternateContent><xdr:clientData/></xdr:twoCellAnchor>"
    )


def drawing_xml(binding: ReportPivotBinding) -> str:
    anchors = []
    for index, field in enumerate(binding.contract.slicers):
        name = _slicer_object_name(binding, field)
        anchors.append(_slicer_anchor(index, name, index + 2))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<xdr:wsDr xmlns:xdr="{NS_XDR}" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'xmlns:mc="{NS_MC}">'
        f"{''.join(anchors)}</xdr:wsDr>"
    )


def pivot_sheet_xml(contract: ReportSheetContract) -> str:
    """Report chrome around a compact native PivotTable at B9. No UNIQUE formulas."""
    uct_start = _col(3 + 12)
    uct_end = _col(3 + 16)
    overall_start = _col(3 + 17)
    overall_end = _col(3 + 21)
    last_col_idx = max(24, CAPTION_NAME_COL)
    last_col = _col(last_col_idx)
    span = f"1:{last_col_idx}"
    hint = ZERO_VS_BLANK_HINT_VERTICAL if contract.device_product_filters else ZERO_VS_BLANK_HINT
    empty_formula = f'IF(IFERROR(COUNTA(PublishedFacts!A:A),0)<=1,"{EMPTY_STATE}","")'
    caption_by_row: dict[int, str] = {}
    for index, (key, caption) in enumerate(_caption_pairs(), start=1):
        caption_by_row[index] = _inline(f"{_col(CAPTION_KEY_COL)}{index}", key) + _inline(
            f"{_col(CAPTION_NAME_COL)}{index}", caption
        )

    def _row(number: int, inner: str, *, height: float | None = None) -> str:
        ht = "" if height is None else f' ht="{height}" customHeight="1"'
        extra = caption_by_row.get(number, "")
        return f'<row r="{number}" spans="{span}"{ht}>{inner}{extra}</row>'

    rows = [
        _row(1, _inline("A1", "Web Engage Daily Report", style=XF_TITLE), height=22),
        _row(2, _inline("A2", contract.purpose, style=XF_PURPOSE)),
        _row(3, ""),
        _row(4, ""),
        _row(5, _inline("A5", hint, style=XF_HINT), height=32),
        _row(6, _formula_cell("A6", empty_formula, XF_HINT)),
        _row(7, ""),
        _row(
            8,
            (
                f"{_inline(uct_start + '8', 'Click-Through Conversions', style=XF_GROUP_BANNER)}"
                f"{_inline(overall_start + '8', 'Overall Conversions', style=XF_GROUP_BANNER)}"
            ),
        ),
    ]
    merges = (
        f'<mergeCells count="2">'
        f'<mergeCell ref="{uct_start}8:{uct_end}8"/>'
        f'<mergeCell ref="{overall_start}8:{overall_end}8"/>'
        f"</mergeCells>"
    )
    uid = _uid(f"sheet-{contract.name}")
    cols = (
        "<cols>"
        '<col min="1" max="1" width="12" customWidth="1"/>'
        '<col min="2" max="2" width="28" customWidth="1"/>'
        '<col min="3" max="24" width="13" customWidth="1"/>'
        f'<col min="25" max="{CAPTION_KEY_COL - 1}" width="0" hidden="1" customWidth="1"/>'
        f'<col min="{CAPTION_KEY_COL}" max="{CAPTION_NAME_COL}" '
        'width="0" hidden="1" customWidth="1"/>'
        "</cols>"
    )
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
        '<pane xSplit="2" ySplit="9" topLeftCell="C10" '
        'activePane="bottomRight" state="frozen"/>'
        '<selection pane="bottomRight" activeCell="B10" sqref="B10"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.3"/>'
        f"{cols}"
        f"<sheetData>{''.join(rows)}</sheetData>"
        f"{merges}"
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75"'
        ' header="0.3" footer="0.3"/>'
        '<drawing r:id="rId3"/>'
        f'<extLst><ext uri="{SHEET_SLICER_URI}" '
        'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        '<x14:slicerList><x14:slicer r:id="rId2"/></x14:slicerList>'
        "</ext></extLst></worksheet>"
    )


def _formula_cell(ref: str, formula: str, style: int) -> str:
    return f'<c r="{ref}" s="{style}"><f>{_xml_text(formula)}</f></c>'


def worksheet_rels_xml(binding: ReportPivotBinding) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<Relationships xmlns="{NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{NS_PIVOT_TABLE_REL}" '
        f'Target="../pivotTables/pivotTable{binding.index}.xml"/>'
        f'<Relationship Id="rId2" Type="{NS_SLICER_REL}" '
        f'Target="../slicers/slicer{binding.index}.xml"/>'
        f'<Relationship Id="rId3" Type="{NS_DRAWING_REL}" '
        f'Target="../drawings/drawing{binding.index}.xml"/>'
        "</Relationships>"
    )


def pivot_table_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<Relationships xmlns="{NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{NS_PIVOT_CACHE_REL}" '
        'Target="../pivotCache/pivotCacheDefinition1.xml"/>'
        "</Relationships>"
    )


def pivot_cache_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<Relationships xmlns="{NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{NS_PIVOT_RECORDS_REL}" '
        'Target="pivotCacheRecords1.xml"/>'
        "</Relationships>"
    )


def _sheet_rels_name(part: str) -> str:
    parent, name = part.rsplit("/", 1)
    return f"{parent}/_rels/{name}.rels"


def _sheet_id_map(workbook_xml: str) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for tag in re.findall(r"<sheet\b[^/]*/>", workbook_xml):
        name = re.search(r'name="([^"]*)"', tag)
        sheet_id = re.search(r'sheetId="(\d+)"', tag)
        if name and sheet_id:
            mapping[name.group(1).replace("&amp;", "&").replace("&quot;", '"')] = int(
                sheet_id.group(1)
            )
    return mapping


def bindings_from_workbook(workbook_xml: str, rels_xml: str) -> list[ReportPivotBinding]:
    parts = _sheet_part_map(workbook_xml, rels_xml)
    ids = _sheet_id_map(workbook_xml)
    missing = [name for name in REPORT_SHEET_NAMES if name not in parts or name not in ids]
    if missing:
        raise ValueError(f"report sheets missing from workbook: {missing}")
    return [
        ReportPivotBinding(contract, parts[contract.name], ids[contract.name], index)
        for index, contract in enumerate(REPORT_CONTRACTS, start=1)
    ]


def _slicer_plan(
    bindings: Iterable[ReportPivotBinding],
) -> list[tuple[ReportPivotBinding, str, int]]:
    plan: list[tuple[ReportPivotBinding, str, int]] = []
    cache_n = 1
    for binding in bindings:
        for field in binding.contract.slicers:
            plan.append((binding, field, cache_n))
            cache_n += 1
    return plan


def build_pivot_parts(bindings: list[ReportPivotBinding]) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        PIVOT_CACHE_PART: pivot_cache_definition_xml().encode("utf-8"),
        PIVOT_RECORDS_PART: pivot_cache_records_xml().encode("utf-8"),
        "xl/pivotCache/_rels/pivotCacheDefinition1.xml.rels": pivot_cache_rels_xml().encode(
            "utf-8"
        ),
    }
    for binding in bindings:
        files[binding.sheet_part] = pivot_sheet_xml(binding.contract).encode("utf-8")
        files[binding.pivot_part] = _pin_page_field_state(
            _excel_safe_pivot_xml(pivot_table_xml(binding)),
            binding.contract,
            PAGE_GROUP_ITEMS,
            (SERVICE_FILTER_LOGIC_1,),
        ).encode("utf-8")
        files[f"xl/pivotTables/_rels/pivotTable{binding.index}.xml.rels"] = (
            pivot_table_rels_xml().encode("utf-8")
        )
        files[binding.slicer_part] = slicers_xml(binding).encode("utf-8")
        files[binding.drawing_part] = drawing_xml(binding).encode("utf-8")
        files[_sheet_rels_name(binding.sheet_part)] = worksheet_rels_xml(binding).encode("utf-8")
    for binding, field, cache_n in _slicer_plan(bindings):
        files[f"xl/slicerCaches/slicerCache{cache_n}.xml"] = slicer_cache_xml(
            binding, field
        ).encode("utf-8")
    return files


def _strip_workbook_rels(xml: str) -> str:
    xml = re.sub(
        r"<Relationship\b[^>]*pivotCacheDefinition[^/]*/>",
        "",
        xml,
    )
    xml = re.sub(r"<Relationship\b[^>]*slicerCache[^/]*/>", "", xml)
    xml = re.sub(r"<Relationship\b[^>]*calcChain[^/]*/>", "", xml)
    return xml


def _patch_workbook_rels(xml: str, slicer_cache_count: int) -> tuple[str, str, list[str]]:
    xml = _strip_workbook_rels(xml)
    next_id = _next_relationship_id(xml)
    cache_rid = f"rId{next_id}"
    extras = [
        f'<Relationship Id="{cache_rid}" Type="{NS_PIVOT_CACHE_REL}" '
        'Target="pivotCache/pivotCacheDefinition1.xml"/>'
    ]
    slicer_rids: list[str] = []
    for offset in range(slicer_cache_count):
        rid = f"rId{next_id + 1 + offset}"
        slicer_rids.append(rid)
        extras.append(
            f'<Relationship Id="{rid}" Type="{NS_SLICER_CACHE_REL}" '
            f'Target="slicerCaches/slicerCache{offset + 1}.xml"/>'
        )
    xml = xml.replace("</Relationships>", "".join(extras) + "</Relationships>")
    return xml, cache_rid, slicer_rids


def _patch_workbook_xml(
    xml: str, cache_rid: str, slicer_rids: list[str], bindings: list[ReportPivotBinding]
) -> str:
    xml = re.sub(r"<pivotCaches>.*?</pivotCaches>", "", xml, flags=re.DOTALL)
    xml = re.sub(
        rf'<ext uri="{re.escape(SLICER_CACHES_URI)}".*?</ext>',
        "",
        xml,
        flags=re.DOTALL,
    )
    xml = re.sub(r'<definedName name="Slicer_[^"]*">#N/A</definedName>', "", xml)
    names = []
    seen: set[str] = set()
    for binding in bindings:
        for field in binding.contract.slicers:
            cache_name = _slicer_cache_name(binding, field)
            if cache_name in seen:
                continue
            seen.add(cache_name)
            names.append(f'<definedName name="{_xml_attr(cache_name)}">#N/A</definedName>')
    if "<definedNames>" in xml:
        xml = xml.replace("</definedNames>", "".join(names) + "</definedNames>", 1)
    else:
        xml = xml.replace(
            "<calcPr",
            "<definedNames>" + "".join(names) + "</definedNames><calcPr",
            1,
        )
    caches = (
        f'<pivotCaches><pivotCache cacheId="{PIVOT_CACHE_ID}" r:id="{cache_rid}"/></pivotCaches>'
    )
    if "<pivotCaches" not in xml:
        if "</calcPr>" in xml:
            xml = xml.replace("</calcPr>", "</calcPr>" + caches, 1)
        else:
            xml = xml.replace("<extLst>", caches + "<extLst>", 1)
    slicer_ext = (
        f'<ext uri="{SLICER_CACHES_URI}" '
        'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        "<x14:slicerCaches>"
        + "".join(f'<x14:slicerCache r:id="{rid}"/>' for rid in slicer_rids)
        + "</x14:slicerCaches></ext>"
    )
    if "<extLst>" in xml:
        xml = xml.replace("<extLst>", "<extLst>" + slicer_ext, 1)
    else:
        xml = xml.replace("</workbook>", f"<extLst>{slicer_ext}</extLst></workbook>", 1)
    return xml


def _strip_content_types(xml: str) -> str:
    patterns = (
        r'<Override PartName="/xl/pivotCache/[^"]+"[^>]*>',
        r'<Override PartName="/xl/pivotTables/[^"]+"[^>]*>',
        r'<Override PartName="/xl/slicers/[^"]+"[^>]*>',
        r'<Override PartName="/xl/slicerCaches/[^"]+"[^>]*>',
        r'<Override PartName="/xl/drawings/drawing\d+\.xml"[^>]*>',
        r'<Override PartName="/xl/calcChain.xml"[^>]*>',
    )
    for pattern in patterns:
        xml = re.sub(pattern, "", xml)
    return xml


def _patch_content_types(
    xml: str, bindings: list[ReportPivotBinding], slicer_cache_count: int
) -> str:
    xml = _strip_content_types(xml)
    extras = [
        f'<Override PartName="/{PIVOT_CACHE_PART}" ContentType="{CT_CACHE}"/>',
        f'<Override PartName="/{PIVOT_RECORDS_PART}" ContentType="{CT_RECORDS}"/>',
    ]
    for binding in bindings:
        extras.append(f'<Override PartName="/{binding.pivot_part}" ContentType="{CT_PIVOT}"/>')
        extras.append(f'<Override PartName="/{binding.slicer_part}" ContentType="{CT_SLICER}"/>')
        extras.append(f'<Override PartName="/{binding.drawing_part}" ContentType="{CT_DRAWING}"/>')
    for index in range(1, slicer_cache_count + 1):
        extras.append(
            f'<Override PartName="/xl/slicerCaches/slicerCache{index}.xml" '
            f'ContentType="{CT_SLICER_CACHE}"/>'
        )
    return xml.replace("</Types>", "".join(extras) + "</Types>")


def _skip_original_part(name: str, generated: dict[str, bytes]) -> bool:
    if name in generated or name == "xl/calcChain.xml":
        return True
    return any(name.startswith(prefix) for prefix in _SKIP_ZIP_PREFIXES)


def apply_native_pivots(raw: bytes) -> bytes:
    """Rewrite report sheets as native PivotTables. Mashup/PublishedFacts stay."""
    with ZipFile(io.BytesIO(raw), "r") as original:
        names = set(original.namelist())
        for fake in FAKE_MASHUP_ZIP_PARTS:
            if fake in names:
                raise ValueError(f"refusing to patch a workbook that contains {fake}")
        workbook_xml = original.read("xl/workbook.xml").decode("utf-8")
        rels_xml = original.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        types_xml = original.read("[Content_Types].xml").decode("utf-8")
        styles_xml = original.read("xl/styles.xml").decode("utf-8")
        bindings = bindings_from_workbook(workbook_xml, rels_xml)
        generated = build_pivot_parts(bindings)
        slicer_count = len(_slicer_plan(bindings))
        rels_xml, cache_rid, slicer_rids = _patch_workbook_rels(rels_xml, slicer_count)
        rels_xml = _rels_with_metadata(rels_xml)
        workbook_xml = _patch_workbook_xml(workbook_xml, cache_rid, slicer_rids, bindings)
        types_xml = _types_with_metadata(_patch_content_types(types_xml, bindings, slicer_count))
        styles_xml = _ensure_report_styles(styles_xml)
        out = io.BytesIO()
        with ZipFile(out, "w") as written:
            for info in original.infolist():
                if _skip_original_part(info.filename, generated):
                    continue
                if info.filename == "xl/workbook.xml":
                    data = workbook_xml.encode("utf-8")
                elif info.filename == "xl/_rels/workbook.xml.rels":
                    data = rels_xml.encode("utf-8")
                elif info.filename == "[Content_Types].xml":
                    data = types_xml.encode("utf-8")
                elif info.filename == "xl/styles.xml":
                    data = styles_xml.encode("utf-8")
                else:
                    data = original.read(info.filename)
                written.writestr(_clone_zipinfo(info), data)
            for name, payload in generated.items():
                written.writestr(_new_sheet_zipinfo(name), payload)
    return out.getvalue()


def assert_native_pivot_package(body: bytes) -> None:
    """Raise ValueError if the nine native PivotTables / slicers are missing."""
    with ZipFile(io.BytesIO(body), "r") as archive:
        names = set(archive.namelist())
        if PIVOT_CACHE_PART not in names or PIVOT_RECORDS_PART not in names:
            raise ValueError("Client report is incomplete.")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        bindings = bindings_from_workbook(workbook_xml, rels_xml)
        if len(bindings) != 9:
            raise ValueError("Client report is incomplete.")
        for binding in bindings:
            if binding.pivot_part not in names or binding.slicer_part not in names:
                raise ValueError("Client report is incomplete.")
            table = archive.read(binding.pivot_part).decode("utf-8")
            if f'name="{binding.pivot_name}"' not in table:
                raise ValueError("Client report is incomplete.")
            if f'cacheId="{PIVOT_CACHE_ID}"' not in table:
                raise ValueError("Client report is incomplete.")
        if "xl/slicerCaches/slicerCache1.xml" not in names:
            raise ValueError("Client report is incomplete.")
        if "xl/slicerCaches/slicerCache35.xml" not in names:
            raise ValueError("Client report is incomplete.")
        if "<pivotCaches>" not in workbook_xml:
            raise ValueError("Client report is incomplete.")
        if "x14:slicerCaches" not in workbook_xml:
            raise ValueError("Client report is incomplete.")
