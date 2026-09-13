"""RUN 007 — deterministic native slicer defaults on generated workbooks.

Preserves the existing 35 per-sheet slicer caches. Does not regenerate
PivotTables, change cache field names/order, or rewrite slicer sourceName.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from zipfile import ZipFile

from dfip_web.client_workbook import FACT_HEADERS
from dfip_web.daily_report import (
    REPORT_CONTRACTS,
    REPORT_SHEET_NAMES,
    ZipParts,
    mutate_xlsx,
)
from dfip_web.pivot_report import (
    PIVOT_CACHE_PART,
    PIVOT_TABLE_NAMES,
    SLICER_PIVOT_CACHE_ID,
    _first_seen,
    _replace_cache_shared_items,
    _row_text,
    cache_field_index,
    page_filter_uniques_from_rows,
    slicer_anchor_box,
)

_FIELDN = re.compile(r"^Field\d+$")
_TABULAR = re.compile(
    r"<tabular pivotCacheId=\"(\d+)\">.*?</tabular>|<tabular pivotCacheId=\"(\d+)\"/>",
    re.DOTALL,
)
_TWO_CELL_ANCHOR = re.compile(
    r"(<xdr:twoCellAnchor\b[^>]*>)(.*?)(</xdr:twoCellAnchor>)",
    re.DOTALL,
)
_FROM_BOX = re.compile(r"<xdr:from>.*?</xdr:from>", re.DOTALL)
_TO_BOX = re.compile(r"<xdr:to>.*?</xdr:to>", re.DOTALL)
_MONTH_LABEL = re.compile(r"^([A-Za-z]{3})-(\d{2})$")
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

# Display slicer field -> PublishedFacts / FactResponse keys.
_SOURCE_ROW_KEYS: dict[str, tuple[str, ...]] = {
    "Filter Logic 1": ("filter_logic_1", "Filter Logic 1"),
    "Channel": ("channel", "Channel"),
    "filter_logic_1_group": ("filter_logic_1_group", "Filter Logic 1_2"),
    "Month": ("month_label", "Month"),
    "AMC Device Category -  Filter Logic 4": (
        "amc_device_category_filter_logic_4",
        "AMC Device Category -  Filter Logic 4",
    ),
    "AMC Product Cat -  Filter Logic 5": (
        "amc_product_cat_filter_logic_5",
        "AMC Product Cat -  Filter Logic 5",
    ),
}

REQUIRED_SLICER_SOURCES = frozenset(_SOURCE_ROW_KEYS)
AMC_SPLIT_PIVOT_NAME = "PivotAmcSplit"
_CACHE_SEED_FIELDS = (
    "Channel",
    "Month",
    "AMC Device Category -  Filter Logic 4",
    "AMC Product Cat -  Filter Logic 5",
)


def month_sort_key(label: str) -> tuple[int, int, str]:
    """Sort MMM-YY labels in calendar order (Aug-25 before Oct-25)."""
    match = _MONTH_LABEL.fullmatch(label.strip())
    if match is None:
        return (9999, 99, label)
    abbr = match.group(1).title()
    try:
        month = _MONTH_ABBR.index(abbr) + 1
    except ValueError:
        return (9999, 99, label)
    return (2000 + int(match.group(2)), month, label)


def slicer_uniques_from_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, list[str]]:
    """First-seen values per slicer cache sourceName. Tenant-scoped via ``rows``."""
    out: dict[str, list[str]] = {}
    for source, keys in _SOURCE_ROW_KEYS.items():
        values = _first_seen(_row_text(row, *keys) for row in rows)
        if source == "Month":
            values = sorted(values, key=month_sort_key)
        out[source] = values
    return out


def latest_month_label(rows: Sequence[Mapping[str, object]]) -> str:
    """Latest month present in this snapshot. Empty if the company has no months."""
    best: tuple[str, str] | None = None
    for row in rows:
        label = _row_text(row, "month_label", "Month")
        if not label:
            continue
        start = row.get("month_start")
        key = str(start).strip() if start not in (None, "") else label
        if best is None or key > best[0]:
            best = (key, label)
    return best[1] if best else ""


def selected_slicer_values(
    source_name: str,
    *,
    contract_name: str,
    uniques: Sequence[str],
    present: Sequence[str],
    latest_month: str,
) -> tuple[str, ...]:
    """Values marked selected. Missing intended defaults fall back to all uniques."""
    if not uniques:
        return ()
    contract = next(item for item in REPORT_CONTRACTS if item.name == contract_name)
    intended: str | None = None
    if source_name == "Month":
        # All months selected so Refresh All can add later published months.
        # A single "latest" selection leaves new months unselected on the
        # six sheets that have a Month slicer.
        return tuple(uniques)
    if source_name == "Filter Logic 1":
        # CaptionEqual pins Service. An index-based single selection remaps
        # when FL1 uniques grow on Refresh All and hides every Service row.
        return tuple(uniques)
    elif source_name == "filter_logic_1_group" and contract.default_group:
        intended = contract.default_group
    if intended and intended in present:
        return (intended,)
    return tuple(uniques)


def _items_xml(values: Sequence[str], selected: Sequence[str]) -> str:
    chosen = set(selected)
    body = []
    for index, value in enumerate(values):
        if value in chosen:
            body.append(f'<i x="{index}" s="1"/>')
        else:
            body.append(f'<i x="{index}"/>')
    return f'<items count="{len(values)}">{"".join(body)}</items>'


def _patch_tabular(xml: str, values: Sequence[str], selected: Sequence[str]) -> str:
    if not values:
        return xml
    items = _items_xml(values, selected)

    def _replace(match: re.Match[str]) -> str:
        cache_id = match.group(1) or match.group(2)
        return f'<tabular pivotCacheId="{cache_id}">{items}</tabular>'

    return _TABULAR.sub(_replace, xml, count=1)


def _contract_for_pivot(pivot_name: str) -> str:
    for sheet, pivot in PIVOT_TABLE_NAMES.items():
        if pivot == pivot_name:
            return sheet
    raise ValueError(f"unknown pivot {pivot_name}")


def apply_slicer_defaults_into(
    parts: dict[str, bytes], rows: Sequence[Mapping[str, object]]
) -> None:
    """Seed slicer cache items from the snapshot onto an in-memory package."""
    if not rows:
        return
    uniques = slicer_uniques_from_rows(rows)
    groups, fl1 = page_filter_uniques_from_rows(rows)
    cache_uniques = dict(uniques)
    cache_uniques["filter_logic_1_group"] = groups
    cache_uniques["Filter Logic 1"] = fl1
    latest = latest_month_label(rows)
    cache_xml = parts[PIVOT_CACHE_PART].decode("utf-8")
    for field in _CACHE_SEED_FIELDS:
        values = cache_uniques.get(field) or []
        if values:
            cache_xml = _replace_cache_shared_items(cache_xml, field, values)
    parts[PIVOT_CACHE_PART] = cache_xml.encode("utf-8")
    names = [
        name for name in parts if name.startswith("xl/slicerCaches/") and name.endswith(".xml")
    ]
    for part in names:
        xml = parts[part].decode("utf-8")
        source = re.search(r'sourceName="([^"]+)"', xml)
        pivot = re.search(r'<pivotTable tabId="\d+" name="([^"]+)"', xml)
        if source is None or pivot is None:
            raise ValueError("slicer cache is incomplete.")
        source_name = source.group(1)
        if _FIELDN.match(source_name):
            raise ValueError("slicer sourceName is invalid.")
        if source_name not in FACT_HEADERS:
            raise ValueError("slicer sourceName is invalid.")
        values = cache_uniques.get(source_name) or []
        if not values:
            continue
        sheet = _contract_for_pivot(pivot.group(1))
        selected = selected_slicer_values(
            source_name,
            contract_name=sheet,
            uniques=values,
            present=uniques.get(source_name) or [],
            latest_month=latest,
        )
        parts[part] = _patch_tabular(xml, values, selected).encode("utf-8")
    assert_slicer_bindings(ZipParts(parts))


def apply_slicer_defaults_xml(body: bytes, rows: Sequence[Mapping[str, object]]) -> bytes:
    """Seed slicer cache items from the snapshot and apply useful defaults."""
    if not rows:
        return body
    return mutate_xlsx(body, lambda parts: apply_slicer_defaults_into(parts, rows))


def _anchor_box_xml(col: int, row: int, col_off: int = 0, row_off: int = 0) -> str:
    return (
        f"<xdr:col>{col}</xdr:col><xdr:colOff>{col_off}</xdr:colOff>"
        f"<xdr:row>{row}</xdr:row><xdr:rowOff>{row_off}</xdr:rowOff>"
    )


def _place_slicer_anchor(inner: str, box: tuple[int, int, int, int, int, int, int, int]) -> str:
    col, row, to_col, to_row, from_col_off, from_row_off, to_col_off, to_row_off = box
    inner = _FROM_BOX.sub(
        f"<xdr:from>{_anchor_box_xml(col, row, from_col_off, from_row_off)}</xdr:from>",
        inner,
        count=1,
    )
    return _TO_BOX.sub(
        f"<xdr:to>{_anchor_box_xml(to_col, to_row, to_col_off, to_row_off)}</xdr:to>",
        inner,
        count=1,
    )


def _layout_drawing_xml(xml: str, sheet_name: str) -> str:
    index = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal index
        inner = match.group(2)
        if "<sle:slicer" not in inner:
            return match.group(0)
        inner = _place_slicer_anchor(inner, slicer_anchor_box(sheet_name, index))
        index += 1
        return match.group(1) + inner + match.group(3)

    return _TWO_CELL_ANCHOR.sub(_replace, xml)


def _apply_month_cache_date_format(xml: str) -> str:
    """Keep date-typed Month items as MMM-YY and drop stale text months.

    Power Query types Month from month_start. Built-in numFmt 17 is mmm-yy, so
    pivot groups and Month slicers stay Aug-25 while sorting by the date value
    (Aug, Sep, Oct). missingItemsLimit=0 clears leftover text labels after a
    republish / Refresh All.
    """
    if "missingItemsLimit=" in xml:
        xml = re.sub(r'missingItemsLimit="[^"]*"', 'missingItemsLimit="0"', xml, count=1)
    else:
        xml = xml.replace(
            "<pivotCacheDefinition ",
            '<pivotCacheDefinition missingItemsLimit="0" ',
            1,
        )
    month = re.search(r'<cacheField name="Month"[^>]*>', xml)
    if month is None:
        return xml
    tag = month.group(0)
    if 'numFmtId="' in tag:
        tag = re.sub(r'numFmtId="[^"]*"', 'numFmtId="17"', tag, count=1)
    else:
        tag = tag.replace('<cacheField name="Month"', '<cacheField name="Month" numFmtId="17"', 1)
    return xml[: month.start()] + tag + xml[month.end() :]


def _sort_month_row_field(xml: str) -> str:
    """Keep Month rows in calendar order after Refresh All rebuilds items.

    Month is a month_start date displayed as MMM-YY (numFmt 17). Ascending
    text sort of those labels is alphabetical (Aug, Oct, Sep). ``manual``
    keeps history-facts ``ORDER BY day``; date + mmm-yy also makes slicer
    ascending chronological for Month 1–12.
    """
    month_index = cache_field_index("Month")
    match = re.search(r"<pivotFields\b[^>]*>.*?</pivotFields>", xml, re.DOTALL)
    if match is None:
        return xml
    fields = list(
        re.finditer(
            r"<pivotField\b[^>]*/>|<pivotField\b[^>]*>.*?</pivotField>",
            match.group(0),
            re.DOTALL,
        )
    )
    if month_index >= len(fields):
        return xml
    tag = fields[month_index].group(0)
    row_month = "axis=" not in tag or "axisRow" in tag
    if row_month:
        if 'sortType="' in tag:
            tag = re.sub(r'sortType="[^"]*"', 'sortType="manual"', tag, count=1)
        else:
            tag = re.sub(r"<pivotField\b", '<pivotField sortType="manual"', tag, count=1)
    if 'numFmtId="' in tag:
        tag = re.sub(r'numFmtId="[^"]*"', 'numFmtId="17"', tag, count=1)
    else:
        tag = re.sub(r"<pivotField\b", '<pivotField numFmtId="17"', tag, count=1)
    start = match.start() + fields[month_index].start()
    end = match.start() + fields[month_index].end()
    return xml[:start] + tag + xml[end:]


def _ensure_amc_split_day_row_field(xml: str) -> str:
    """Put Day under Month on AMC Split so Month/Day collapse-expand works.

    Filter Logic 1 → Filter Logic 2 → Month stay the outer hierarchy. Day is
    the missing inner field; without it ShowDetail on Month has nothing to
    hide. Does not change the Python reconstruction grain.
    """
    if f'name="{AMC_SPLIT_PIVOT_NAME}"' not in xml:
        return xml
    day_index = cache_field_index("Day")
    month_index = cache_field_index("Month")
    wanted = (
        f'<rowFields count="4"><field x="0"/><field x="1"/>'
        f'<field x="{month_index}"/><field x="{day_index}"/></rowFields>'
    )
    xml, replaced = re.subn(
        rf'<rowFields count="3"><field x="0"/><field x="1"/>'
        rf'<field x="{month_index}"/></rowFields>',
        wanted,
        xml,
        count=1,
    )
    if replaced != 1 and wanted not in xml:
        raise ValueError("AMC Split row fields are invalid.")
    match = re.search(r"<pivotFields\b[^>]*>.*?</pivotFields>", xml, re.DOTALL)
    if match is None:
        return xml
    fields = list(
        re.finditer(
            r"<pivotField\b[^>]*/>|<pivotField\b[^>]*>.*?</pivotField>",
            match.group(0),
            re.DOTALL,
        )
    )
    if day_index >= len(fields):
        return xml
    tag = fields[day_index].group(0)
    if 'axis="axisRow"' not in tag:
        tag = re.sub(r"<pivotField\b", '<pivotField axis="axisRow"', tag, count=1)
        if tag.endswith("/>"):
            tag = tag[:-2] + '><items count="1"><item t="default" sd="1"/></items></pivotField>'
        elif "<items" not in tag:
            tag = tag.replace(
                "</pivotField>",
                '<items count="1"><item t="default" sd="1"/></items></pivotField>',
                1,
            )
    start = match.start() + fields[day_index].start()
    end = match.start() + fields[day_index].end()
    return xml[:start] + tag + xml[end:]


def apply_slicer_layout_into(parts: dict[str, bytes]) -> None:
    """Place native slicers on the FY-2026 drawing anchors. Keep cache bindings."""
    drawings = sorted(
        (
            name
            for name in parts
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml")
        ),
        key=lambda name: int(re.search(r"drawing(\d+)", name).group(1)),
    )
    for sheet_name, part in zip(REPORT_SHEET_NAMES, drawings, strict=False):
        parts[part] = _layout_drawing_xml(parts[part].decode("utf-8"), sheet_name).encode("utf-8")
    for index in range(1, 10):
        part = f"xl/pivotTables/pivotTable{index}.xml"
        if part in parts:
            xml = parts[part].decode("utf-8")
            xml = _sort_month_row_field(xml)
            xml = _ensure_amc_split_day_row_field(xml)
            parts[part] = xml.encode("utf-8")
    if PIVOT_CACHE_PART in parts:
        parts[PIVOT_CACHE_PART] = _apply_month_cache_date_format(
            parts[PIVOT_CACHE_PART].decode("utf-8")
        ).encode("utf-8")
    assert_slicer_bindings(ZipParts(parts))


def apply_slicer_layout_xml(body: bytes) -> bytes:
    """Place native slicers on the FY-2026 drawing anchors. Keep cache bindings."""
    return mutate_xlsx(body, apply_slicer_layout_into)


def drawing_anchor_boxes(body: bytes) -> list[tuple[str, int, int, int, int]]:
    """Return (drawing_part, from_col, from_row, to_col, to_row) per slicer."""
    boxes: list[tuple[str, int, int, int, int]] = []
    with ZipFile(io.BytesIO(body), "r") as archive:
        drawings = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml")
        )
        for part in drawings:
            xml = archive.read(part).decode("utf-8")
            for match in _TWO_CELL_ANCHOR.finditer(xml):
                inner = match.group(2)
                if "<sle:slicer" not in inner:
                    continue
                from_box = _FROM_BOX.search(inner)
                to_box = _TO_BOX.search(inner)
                if from_box is None or to_box is None:
                    continue
                cols = re.findall(r"<xdr:col>(\d+)", from_box.group(0) + to_box.group(0))
                rows = re.findall(r"<xdr:row>(\d+)", from_box.group(0) + to_box.group(0))
                if len(cols) >= 2 and len(rows) >= 2:
                    boxes.append((part, int(cols[0]), int(rows[0]), int(cols[1]), int(rows[1])))
    return boxes


def _part_text(archive: ZipFile, part: str, extra: Mapping[str, bytes]) -> str:
    if part in extra:
        return extra[part].decode("utf-8")
    return archive.read(part).decode("utf-8")


def assert_slicer_bindings(archive: ZipFile, extra: Mapping[str, bytes] | None = None) -> None:
    """Raise ValueError if slicer caches are missing, duplicated, or FieldN."""
    extra = extra or {}
    names = [
        name
        for name in archive.namelist()
        if name.startswith("xl/slicerCaches/") and name.endswith(".xml")
    ]
    slicer_parts = [
        name
        for name in archive.namelist()
        if name.startswith("xl/slicers/") and name.endswith(".xml")
    ]
    if len(names) != 35 or len(slicer_parts) != 9:
        raise ValueError("Client report is incomplete.")
    rels = _part_text(archive, "xl/_rels/workbook.xml.rels", extra)
    rel_targets = set(re.findall(r'Target="slicerCaches/([^"]+)"', rels))
    cache_files = {name.rsplit("/", 1)[-1] for name in names}
    if rel_targets != cache_files:
        raise ValueError("slicer cache is incomplete.")
    seen_cache_names: set[str] = set()
    connected_pivots: set[str] = set()
    for part in names:
        xml = _part_text(archive, part, extra)
        cache_name = re.search(r'<slicerCacheDefinition[^>]* name="([^"]+)"', xml)
        source = re.search(r'sourceName="([^"]+)"', xml)
        pivot = re.search(r'<pivotTable tabId="\d+" name="([^"]+)"', xml)
        if cache_name is None or source is None or pivot is None:
            raise ValueError("slicer cache is incomplete.")
        if xml.count("<pivotTable ") != 1:
            raise ValueError("slicer cache is incomplete.")
        if cache_name.group(1) in seen_cache_names:
            raise ValueError("slicer cache is incomplete.")
        seen_cache_names.add(cache_name.group(1))
        connected_pivots.add(pivot.group(1))
        source_name = source.group(1)
        if _FIELDN.match(source_name) or source_name not in FACT_HEADERS:
            raise ValueError("slicer sourceName is invalid.")
        if f'pivotCacheId="{SLICER_PIVOT_CACHE_ID}"' not in xml:
            raise ValueError("slicer cache is incomplete.")
        if pivot.group(1) not in PIVOT_TABLE_NAMES.values():
            raise ValueError("slicer cache is incomplete.")
    if connected_pivots != set(PIVOT_TABLE_NAMES.values()):
        raise ValueError("slicer cache is incomplete.")
    referenced: set[str] = set()
    for part in slicer_parts:
        xml = _part_text(archive, part, extra)
        for cache in re.findall(r' cache="([^"]+)"', xml):
            if cache not in seen_cache_names:
                raise ValueError("slicer cache is incomplete.")
            if cache in referenced:
                raise ValueError("slicer cache is incomplete.")
            referenced.add(cache)
    if referenced != seen_cache_names:
        raise ValueError("slicer cache is incomplete.")


def assert_slicer_package(body: bytes) -> None:
    with ZipFile(io.BytesIO(body), "r") as archive:
        assert_slicer_bindings(archive)
