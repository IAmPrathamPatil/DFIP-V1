"""Build a publication-bound nine-sheet Client_Report workbook without Excel/COM.

Clones the tracked native V2-X template at ZIP level, writes the selected
publication's facts as static PublishedFacts cells, and clears Settings token
fields. Native PivotTables stay in the clone. The shared pivot cache is rebound
from the live PublishedFacts query to those static worksheet cells so a
historical download cannot refresh into another publication.

The tracked template is unchanged. Delivered copies neutralize the live query
connection so Refresh All cannot silently replace a historical snapshot with
the current publication. Desktop Refresh All of the tracked template still
pages GET /publications/current/facts and still requires a user-supplied
Bearer token that is never embedded here.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile

from openpyxl.utils import get_column_letter

from dfip_web.client_workbook import (
    FACT_HEADERS,
    FAKE_MASHUP_ZIP_PARTS,
    XLSX_PATH,
)
from dfip_web.daily_report import (
    CLIENT_WORKBOOK_SHEET_NAMES,
    REPORT_SHEET_NAMES,
    _clone_zipinfo,
    _inline,
    _sheet_part_map,
    _xml_attr,
)
from dfip_web.pivot_report import (
    PIVOT_CACHE_PART,
    apply_page_filter_defaults_xml,
    assert_native_pivot_package,
    bind_pivot_cache_for_snapshot,
)

CLIENT_REPORT_DOWNLOAD_NAME = "Client_Report.xlsx"
CLIENT_REPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# HeaderMap order from excel/PublishedFacts.m. Must match FACT_HEADERS.
FACT_VALUE_FIELDS: tuple[str, ...] = (
    "filter_logic_1",
    "filter_logic_2",
    "template_status",
    "amc_status_filter_logic_3",
    "amc_device_category_filter_logic_4",
    "amc_product_cat_filter_logic_5",
    "manual_or_automated",
    "total_cost",
    "hhh",
    "month_label",
    "day",
    "campaign_name",
    "campaign_id",
    "variation_name",
    "variation_id",
    "channel",
    "type_of_campaign",
    "start_date",
    "sent",
    "failed",
    "delivered",
    "unique_impressions",
    "unique_clicks",
    "unique_conversions",
    "unique_impression_through_conversions",
    "unique_click_through_conversions",
    "revenue_inr",
    "impression_through_revenue_inr",
    "click_through_revenue_inr",
    "template_name_whatsapp",
    "client_id",
    "variation_id_key",
    "month_start",
    "filter_logic_1_group",
    "label_match_status",
    "template_match_status",
    "rate_card_rule_id",
    "processing_run_id",
    "batch_id",
    "campaign_label_version_id",
    "template_label_version_id",
    "rate_card_version_id",
    "label_group_version_id",
    "first_seen_at",
    "last_seen_at",
)

_INT_FIELDS = frozenset(
    {
        "sent",
        "failed",
        "delivered",
        "unique_impressions",
        "unique_clicks",
        "unique_conversions",
        "unique_impression_through_conversions",
        "unique_click_through_conversions",
    }
)
_DEC_FIELDS = frozenset(
    {
        "total_cost",
        "revenue_inr",
        "impression_through_revenue_inr",
        "click_through_revenue_inr",
    }
)

_TEMPLATE_SETTINGS_CLIENT_ID = "a0000000-0000-4000-8000-000000000001"

_EMPTY_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
)

if len(FACT_VALUE_FIELDS) != len(FACT_HEADERS):
    raise RuntimeError("FACT_VALUE_FIELDS must match FACT_HEADERS.")


def render_client_report_xlsx(
    rows: Sequence[Mapping[str, object]],
    *,
    published_at: datetime | None,
    template_path: Path | None = None,
) -> bytes:
    """Return a complete nine-sheet workbook for one publication snapshot.

    ``rows`` are FactResponse-shaped mappings for that publication only.
    Raises ValueError if the template is missing/invalid or the result is not a
    complete workbook. Never returns a truncated zip.
    """
    src = XLSX_PATH if template_path is None else Path(template_path)
    if not src.is_file():
        raise ValueError("Client report template is missing.")
    raw = src.read_bytes()
    with ZipFile(io.BytesIO(raw), "r") as original:
        names = set(original.namelist())
        for fake in FAKE_MASHUP_ZIP_PARTS:
            if fake in names:
                raise ValueError("Client report template is invalid.")
        if any(name.startswith("xl/queryMashup/") for name in names):
            raise ValueError("Client report template is invalid.")
        try:
            workbook_xml = original.read("xl/workbook.xml").decode("utf-8")
            rels_xml = original.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        except KeyError as exc:
            raise ValueError("Client report template is invalid.") from exc
        parts = _sheet_part_map(workbook_xml, rels_xml)
        missing = [name for name in CLIENT_WORKBOOK_SHEET_NAMES if name not in parts]
        if missing:
            raise ValueError("Client report template is invalid.")
        published_part = parts["PublishedFacts"]
        facts_part = parts["Facts"]
        published_rels = _sheet_rels_name(published_part)
        stamp = _published_stamp(published_at)
        replacements: dict[str, bytes] = {
            published_part: _published_facts_sheet_xml(
                original.read(published_part).decode("utf-8"),
                rows,
            ).encode("utf-8"),
            facts_part: _facts_settings_xml(
                original.read(facts_part).decode("utf-8"),
                stamp,
            ).encode("utf-8"),
            "xl/connections.xml": _disable_connections_xml(
                original.read("xl/connections.xml").decode("utf-8")
            ).encode("utf-8"),
        }
        if published_rels in names:
            replacements[published_rels] = _EMPTY_RELS_XML.encode("utf-8")
        if "xl/sharedStrings.xml" in names:
            replacements["xl/sharedStrings.xml"] = _blank_template_client_id(
                original.read("xl/sharedStrings.xml").decode("utf-8")
            ).encode("utf-8")
        if PIVOT_CACHE_PART in names:
            replacements[PIVOT_CACHE_PART] = bind_pivot_cache_for_snapshot(
                original.read(PIVOT_CACHE_PART).decode("utf-8"),
                len(rows),
            ).encode("utf-8")
        types_xml = original.read("[Content_Types].xml").decode("utf-8")
        types_xml = re.sub(
            r'<Override PartName="/xl/queryTables/[^"]+"[^>]*>',
            "",
            types_xml,
        )
        replacements["[Content_Types].xml"] = types_xml.encode("utf-8")
        out = io.BytesIO()
        with ZipFile(out, "w") as written:
            for info in original.infolist():
                if info.filename.startswith("xl/queryTables/"):
                    continue
                data = replacements.get(info.filename, original.read(info.filename))
                written.writestr(_clone_zipinfo(info), data)
    body = apply_page_filter_defaults_xml(out.getvalue(), rows)
    _require_complete_workbook(body)
    return body


def _sheet_rels_name(part: str) -> str:
    parent, name = part.rsplit("/", 1)
    return f"{parent}/_rels/{name}.rels"


def _published_stamp(published_at: datetime | None) -> str:
    if published_at is None:
        return ""
    if published_at.tzinfo is None:
        return published_at.isoformat()
    return published_at.isoformat()


def _blank_template_client_id(xml: str) -> str:
    return xml.replace(_TEMPLATE_SETTINGS_CLIENT_ID, "")


def _disable_connections_xml(xml: str) -> str:
    """Keep a well-formed connection part so Excel can open the snapshot.

    Empty ``<connections/>`` and ``deleted="1"`` are rejected by desktop Excel.
    Disable keep-alive / background / refresh-on-load so Refresh All cannot
    silently replace the static PublishedFacts cells via the mashup query.
    """
    xml = xml.replace(' keepAlive="1"', ' keepAlive="0"')
    xml = xml.replace(' background="1"', ' background="0"')
    if 'refreshOnLoad="' not in xml:
        xml = xml.replace("<connection ", '<connection refreshOnLoad="0" ', 1)
    else:
        xml = re.sub(r'refreshOnLoad="\d+"', 'refreshOnLoad="0"', xml, count=1)
    return xml


def _published_facts_sheet_xml(template_xml: str, rows: Sequence[Mapping[str, object]]) -> str:
    last_col = get_column_letter(len(FACT_HEADERS))
    last_row = 1 + len(rows)
    dimension = f"A1:{last_col}{last_row}"
    n_cols = len(FACT_HEADERS)
    header_cells = "".join(
        _inline(f"{get_column_letter(index)}1", header)
        for index, header in enumerate(FACT_HEADERS, start=1)
    )
    sheet_rows = [f'<row r="1" spans="1:{n_cols}">{header_cells}</row>']
    for offset, row in enumerate(rows):
        excel_row = offset + 2
        sheet_rows.append(_fact_row_xml(excel_row, row, n_cols))
    sheet_data = "".join(sheet_rows)
    xml = re.sub(
        r"<dimension\b[^/]*/>",
        f'<dimension ref="{_xml_attr(dimension)}"/>',
        template_xml,
        count=1,
    )
    xml = re.sub(
        r"<sheetData>.*?</sheetData>",
        f"<sheetData>{sheet_data}</sheetData>",
        xml,
        count=1,
        flags=re.DOTALL,
    )
    xml = re.sub(r"<tableParts\b.*?</tableParts>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"<tableParts\b[^/]*/>", "", xml)
    return xml


def _fact_row_xml(excel_row: int, row: Mapping[str, object], n_cols: int) -> str:
    cells: list[str] = []
    for index, field in enumerate(FACT_VALUE_FIELDS, start=1):
        ref = f"{get_column_letter(index)}{excel_row}"
        cell = _value_cell(ref, field, row.get(field))
        if cell:
            cells.append(cell)
    return f'<row r="{excel_row}" spans="1:{n_cols}">{"".join(cells)}</row>'


def _value_cell(ref: str, field: str, value: object) -> str:
    if value is None or value == "":
        return ""
    if field in _INT_FIELDS:
        try:
            return f'<c r="{ref}" t="n"><v>{int(value)}</v></c>'
        except (TypeError, ValueError):
            return _inline(ref, str(value))
    if field in _DEC_FIELDS:
        text = str(value).strip()
        if not text:
            return ""
        return f'<c r="{ref}" t="n"><v>{text}</v></c>'
    if isinstance(value, datetime):
        return _inline(ref, value.isoformat())
    if isinstance(value, date):
        return _inline(ref, value.isoformat())
    return _inline(ref, str(value))


def _facts_settings_xml(xml: str, published_stamp: str) -> str:
    xml = re.sub(r'<c r="B2"[^/]*/>', "", xml)
    xml = re.sub(r'<c r="B2"[^>]*>.*?</c>', "", xml, flags=re.DOTALL)
    xml = re.sub(r'<c r="B3"[^/]*/>', "", xml)
    xml = re.sub(r'<c r="B3"[^>]*>.*?</c>', "", xml, flags=re.DOTALL)
    xml = re.sub(r'<c r="B4"[^/]*/>', "", xml)
    xml = re.sub(r'<c r="B4"[^>]*>.*?</c>', "", xml, flags=re.DOTALL)
    stamp_row = (
        f'<row r="5" spans="1:45">'
        f"{_inline('A5', 'Published')}"
        f"{_inline('B5', published_stamp)}"
        f"</row>"
    )
    if re.search(r'<row r="5"(?=[\s/>])', xml) is None:
        if re.search(r'<row r="6"(?=[\s/>])', xml):
            xml = re.sub(r'(<row r="6"(?=[\s/>]))', stamp_row + r"\1", xml, count=1)
        else:
            xml = xml.replace("</sheetData>", f"{stamp_row}</sheetData>", 1)
    return xml


def _require_complete_workbook(body: bytes) -> None:
    if not body.startswith(b"PK"):
        raise ValueError("Client report is incomplete.")
    try:
        with ZipFile(io.BytesIO(body), "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("Client report is incomplete.")
            names = set(archive.namelist())
            for fake in FAKE_MASHUP_ZIP_PARTS:
                if fake in names:
                    raise ValueError("Client report is incomplete.")
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Client report is incomplete.") from exc
    parts = _sheet_part_map(workbook_xml, rels_xml)
    missing = [name for name in CLIENT_WORKBOOK_SHEET_NAMES if name not in parts]
    if missing:
        raise ValueError("Client report is incomplete.")
    for name in REPORT_SHEET_NAMES:
        if f'name="{name}"' not in workbook_xml and f'name="{_xml_attr(name)}"' not in workbook_xml:
            raise ValueError("Client report is incomplete.")
    assert_native_pivot_package(body)
