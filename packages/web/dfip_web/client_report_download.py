"""Build publication-bound nine-sheet Client_Report workbooks without Excel/COM.

Clones the tracked native V2-X template at ZIP level. Two explicit artifacts:

- ``static`` (RUN 005B recovery): writes PublishedFacts cells, strips query
  tables, disables mashup refresh flags, and clears Settings B2–B4. Refresh All
  cannot replace the snapshot. Re-download after publish for a new snapshot.
- ``refreshable`` (RUN 005C / R10): keeps the PublishedFacts query and the
  native DataMashup package, authors the 45 queryTableFields, stamps the
  current snapshot so the file opens populated, and sets ApiBaseUrl.
  Website/API downloads write a client-scoped Excel grant JWT into
  Settings BearerToken so Excel Refresh All can call history/facts.csv.
  Publisher/admin downloads mint the same client-scoped grant for the
  selected company; the publisher session JWT is never embedded. ClientId
  stays empty; JWT ``client_id`` is authoritative. Runtime does not rewrite DataMashup;
  Excel skips a rewritten package. Refresh All calls GET
  /publications/history/facts.csv. Generated downloads hide ``PublishedFacts``
  and ``Facts`` so the client sees only the nine report sheets. Both
  technical sheets stay in the package: PivotCache and Power Query still
  require ``PublishedFacts``; Settings still lives on ``Facts``. Hiding is
  not authorization. Direct ``render_client_report_xlsx`` without a token
  still leaves BearerToken empty (tests / static generation).

The tracked template is unchanged at runtime. RUN 006 then applies
reference formatting (fonts, dataField number formats, report column widths)
without regenerating PivotTables or rewriting DataMashup. RUN 007 seeds the
existing 35 native slicer caches from the snapshot and applies
company-scoped defaults without changing sourceName or cache field order.
RUN 008 hides ``PublishedFacts`` and ``Facts`` on generated downloads so
the client sees only the nine report sheets. Both sheets stay in the
package: PivotCache and Power Query still require ``PublishedFacts``;
Settings still lives on ``Facts``. Hiding is not authorization.
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
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
    NATIVE_SHEET_NAMES,
    REPORT_SHEET_NAMES,
    _clone_zipinfo,
    _inline,
    _new_sheet_zipinfo,
    _next_relationship_id,
    _sheet_part_map,
    _xml_attr,
    _xml_text,
    mutate_xlsx,
    write_xlsx_parts,
    xlsx_parts,
)
from dfip_web.measure_restore_vba import embed_measure_restore_vba_into
from dfip_web.pivot_report import (
    PIVOT_CACHE_PART,
    PIVOT_CACHE_QUERY_NAME,
    apply_page_filter_defaults_into,
    apply_refreshable_conversion_layout_into,
    assert_native_pivot_package,
    assert_renewal_enabled_mashup,
    bind_pivot_cache_for_refreshable,
    bind_pivot_cache_for_snapshot,
    bind_refreshable_query_defined_name,
    mashup_is_renewal_enabled,
    assert_published_facts_mashup,
)
from dfip_web.report_format import (
    apply_reference_formatting_into,
    apply_title_banners_into,
    report_workbook_title,
)
from dfip_web.slicer_defaults import (
    apply_slicer_defaults_into,
    apply_slicer_layout_into,
    assert_slicer_package,
)

CLIENT_REPORT_DOWNLOAD_SUFFIX = "Client_Report.xlsx"
CLIENT_REPORT_DOWNLOAD_NAME = CLIENT_REPORT_DOWNLOAD_SUFFIX
CLIENT_REPORT_REFRESHABLE_SUFFIX = "Client_Report_Refreshable.xlsm"
ARTIFACT_STATIC = "static"
ARTIFACT_REFRESHABLE = "refreshable"
ALLOWED_ARTIFACTS = frozenset({ARTIFACT_STATIC, ARTIFACT_REFRESHABLE})
QUERY_TABLE_PART = "xl/queryTables/queryTable1.xml"
DEFAULT_REFRESH_API_BASE_URL = "http://127.0.0.1:8000"
CLIENT_REPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CLIENT_REPORT_MACRO_MEDIA_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.12"
CUSTOM_PROPS_PART = "docProps/custom.xml"
CUSTOM_PROPS_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
CUSTOM_PROPS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
)
# Generated client downloads hide these; the tracked operator template stays visible.
TECHNICAL_SHEET_NAMES = NATIVE_SHEET_NAMES
SHEET_STATE_HIDDEN = "hidden"
CUSTOM_PROPS_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

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


def query_table_field_names(body: bytes) -> tuple[str, ...]:
    """Return queryTableField names from a refreshable workbook package."""
    with ZipFile(io.BytesIO(body), "r") as archive:
        xml = archive.read(QUERY_TABLE_PART).decode("utf-8")
    return tuple(re.findall(r'<queryTableField\b[^>]*\bname="([^"]+)"', xml))


def publication_short_id(publication_id: str) -> str:
    """Stable 8-character hex prefix of a publication UUID. Not a secret."""
    compact = "".join(ch for ch in publication_id.lower() if ch in "0123456789abcdef")
    token = compact[:8]
    return token or "publication"


def client_report_download_filename(
    client_code: str,
    published_at: datetime | None,
    *,
    artifact: str = ARTIFACT_STATIC,
    publication_id: str | None = None,
) -> str:
    """Return DFIP_<client_code>_<YYYY-MM-DD>[_<short_id>]_Client_Report[.Refreshable].xlsx.

    Current static and refreshable names omit the publication short id (RUN 005B/005C).
    Historical static downloads include the short id so same-day republishes do not
    collide. Uses client_code, never the company display name.
    """
    token = _safe_filename_token(client_code)
    if published_at is None:
        day = datetime.now(UTC).date()
    elif published_at.tzinfo is None:
        day = published_at.date()
    else:
        day = published_at.astimezone(UTC).date()
    suffix = (
        CLIENT_REPORT_REFRESHABLE_SUFFIX
        if artifact == ARTIFACT_REFRESHABLE
        else CLIENT_REPORT_DOWNLOAD_SUFFIX
    )
    if artifact != ARTIFACT_REFRESHABLE and publication_id:
        short = publication_short_id(publication_id)
        return f"DFIP_{token}_{day.isoformat()}_{short}_{suffix}"
    return f"DFIP_{token}_{day.isoformat()}_{suffix}"


def render_client_report_xlsx(
    rows: Sequence[Mapping[str, object]],
    *,
    published_at: datetime | None,
    template_path: Path | None = None,
    client_id: str | None = None,
    client_code: str | None = None,
    client_name: str | None = None,
    publication_id: str | None = None,
    artifact: str = ARTIFACT_STATIC,
    api_base_url: str | None = None,
    refresh_bearer_token: str | None = None,
) -> bytes:
    """Return a complete nine-sheet workbook for one publication snapshot.

    ``rows`` are FactResponse-shaped mappings for that publication only.
    Raises ValueError if the template is missing/invalid or the result is not a
    complete workbook. Never returns a truncated zip.

    Static artifacts clear Settings B2–B4. Refreshable artifacts write
    ApiBaseUrl and, when ``refresh_bearer_token`` is provided (website/API
    download), the client-scoped Excel grant JWT into BearerToken.
    ClientId stays empty. Direct renders without a token still leave
    BearerToken empty. Company identity is inert provenance.
    """
    if artifact not in ALLOWED_ARTIFACTS:
        raise ValueError("Client report artifact must be static or refreshable.")
    refreshable = artifact == ARTIFACT_REFRESHABLE
    src = resolve_client_report_template(template_path)
    if not src.is_file():
        raise ValueError("Client report template is missing.")
    raw = src.read_bytes()
    require_renewal = False
    try:
        require_renewal = mashup_is_renewal_enabled(assert_published_facts_mashup(raw))
    except ValueError:
        require_renewal = False
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
        refresh_url = ""
        refresh_token = ""
        if refreshable:
            refresh_url = (api_base_url or DEFAULT_REFRESH_API_BASE_URL).strip().rstrip("/")
            refresh_token = (refresh_bearer_token or "").strip()
        replacements: dict[str, bytes] = {
            published_part: _published_facts_sheet_xml(
                original.read(published_part).decode("utf-8"),
                rows,
            ).encode("utf-8"),
            facts_part: _facts_settings_xml(
                original.read(facts_part).decode("utf-8"),
                stamp,
                company_name=client_name or "",
                company_code=client_code or "",
                api_base_url=refresh_url,
                refresh_bearer_token=refresh_token,
                months_loaded=_snapshot_month_labels(rows) if refreshable else "",
                refreshable=refreshable,
            ).encode("utf-8"),
            "xl/connections.xml": _connections_xml(
                original.read("xl/connections.xml").decode("utf-8"),
                refreshable=refreshable,
            ).encode("utf-8"),
        }
        if refreshable:
            replacements["xl/workbook.xml"] = bind_refreshable_query_defined_name(
                workbook_xml, len(rows)
            ).encode("utf-8")
            if QUERY_TABLE_PART in names:
                replacements[QUERY_TABLE_PART] = _query_table_fields_xml(
                    original.read(QUERY_TABLE_PART).decode("utf-8")
                ).encode("utf-8")
        elif published_rels in names:
            replacements[published_rels] = _EMPTY_RELS_XML.encode("utf-8")
        if "xl/sharedStrings.xml" in names:
            replacements["xl/sharedStrings.xml"] = _blank_template_client_id(
                original.read("xl/sharedStrings.xml").decode("utf-8")
            ).encode("utf-8")
        if PIVOT_CACHE_PART in names:
            cache_xml = original.read(PIVOT_CACHE_PART).decode("utf-8")
            if refreshable:
                replacements[PIVOT_CACHE_PART] = bind_pivot_cache_for_refreshable(cache_xml).encode(
                    "utf-8"
                )
            else:
                replacements[PIVOT_CACHE_PART] = bind_pivot_cache_for_snapshot(
                    cache_xml,
                    len(rows),
                ).encode("utf-8")
        types_xml = original.read("[Content_Types].xml").decode("utf-8")
        if not refreshable:
            types_xml = re.sub(
                r'<Override PartName="/xl/queryTables/[^"]+"[^>]*>',
                "",
                types_xml,
            )
        package_rels = original.read("_rels/.rels").decode("utf-8")
        custom_xml = None
        if client_id:
            custom_xml = _custom_properties_xml(
                client_id=client_id,
                publication_id=publication_id,
                published_at=stamp,
            )
            types_xml = _ensure_custom_props_content_type(types_xml)
            package_rels = _ensure_custom_props_relationship(package_rels)
            replacements["_rels/.rels"] = package_rels.encode("utf-8")
        replacements["[Content_Types].xml"] = types_xml.encode("utf-8")
        out = io.BytesIO()
        with ZipFile(out, "w") as written:
            for info in original.infolist():
                if not refreshable and info.filename.startswith("xl/queryTables/"):
                    continue
                data = replacements.get(info.filename, original.read(info.filename))
                written.writestr(_clone_zipinfo(info), data)
            if custom_xml is not None:
                written.writestr(_new_sheet_zipinfo(CUSTOM_PROPS_PART), custom_xml.encode("utf-8"))
    infos, parts = xlsx_parts(out.getvalue())
    apply_page_filter_defaults_into(parts, rows)
    apply_slicer_defaults_into(parts, rows)
    apply_reference_formatting_into(
        parts,
        client_name=client_name or "",
        rows=rows,
        refreshable=refreshable,
    )
    apply_hidden_technical_sheets_into(parts, refreshable=refreshable)
    if refreshable:
        apply_refreshable_conversion_layout_into(parts)
        embed_measure_restore_vba_into(parts)
    apply_slicer_layout_into(parts)
    apply_title_banners_into(parts, report_workbook_title(client_name or "", rows))
    body = write_xlsx_parts(infos, parts)
    _require_complete_workbook(
        body,
        refreshable=refreshable,
        require_renewal=bool(refreshable and require_renewal),
    )
    return body


def resolve_client_report_template(template_path: Path | None = None) -> Path:
    """Tracked excel/Client_Report.xlsx unless a disposable template is selected.

    ``template_path`` wins. Otherwise ``DFIP_CLIENT_REPORT_TEMPLATE`` may point
    at an Excel-authored disposable copy. Empty means the tracked master.
    Never write that override into the normal DFIP runtime.
    """
    if template_path is not None:
        return Path(template_path)
    override = os.environ.get("DFIP_CLIENT_REPORT_TEMPLATE", "").strip()
    if override:
        return Path(override)
    return XLSX_PATH


def _safe_filename_token(value: str) -> str:
    text = value.strip().replace(" ", "_")
    text = _UNSAFE_FILENAME.sub("_", text).strip("._")
    return text or "company"


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


def _connections_xml(xml: str, *, refreshable: bool = False) -> str:
    """Keep a well-formed connection part so Excel can open the workbook.

    Empty ``<connections/>`` and ``deleted="1"`` are rejected by desktop Excel.
    ``refreshOnLoad`` stays 0 so open does not call the API without a token.
    ``background="0"`` is authored on the connection and is harmless, but
    desktop Mashup ignores it. Foreground Refresh All is
    ``queryTable@backgroundRefresh="0"`` from ``_query_table_fields_xml``.
    Static copies disable keep-alive / background so Refresh All cannot replace
    the snapshot via the leftover mashup binary.
    """
    _ = refreshable
    xml = xml.replace(' keepAlive="1"', ' keepAlive="0"')
    xml = xml.replace(' background="1"', ' background="0"')
    if 'refreshOnLoad="' not in xml:
        xml = xml.replace("<connection ", '<connection refreshOnLoad="0" ', 1)
    else:
        xml = re.sub(r'refreshOnLoad="\d+"', 'refreshOnLoad="0"', xml, count=1)
    return xml


def _disable_connections_xml(xml: str) -> str:
    return _connections_xml(xml, refreshable=False)


_QUERY_TABLE_BACKGROUND_REFRESH = re.compile(r'(?<![A-Za-z])backgroundRefresh="\d+"')


def _query_table_fields_xml(xml: str) -> str:
    """Author queryTableFields and foreground Refresh All.

    Desktop Mashup honors ``queryTable@backgroundRefresh="0"``. It ignores
    ``connection@background="0"``. Do not match ``firstBackgroundRefresh``.
    """
    if ' headers="' not in xml:
        xml = xml.replace("<queryTable ", '<queryTable headers="1" ', 1)
    xml = xml.replace(' firstBackgroundRefresh="1"', ' firstBackgroundRefresh="0"')
    if _QUERY_TABLE_BACKGROUND_REFRESH.search(xml) is None:
        xml = xml.replace("<queryTable ", '<queryTable backgroundRefresh="0" ', 1)
    else:
        xml = _QUERY_TABLE_BACKGROUND_REFRESH.sub('backgroundRefresh="0"', xml, count=1)
    if re.search(r'growShrinkType="[^"]+"', xml):
        xml = re.sub(r'growShrinkType="[^"]+"', 'growShrinkType="overwriteClear"', xml, count=1)
    else:
        xml = xml.replace("<queryTable ", '<queryTable growShrinkType="overwriteClear" ', 1)
    fields = []
    for index, header in enumerate(FACT_HEADERS, start=1):
        fields.append(
            f'<queryTableField id="{index}" name="{_xml_attr(header)}" tableColumnId="{index}"/>'
        )
    body = (
        f'<queryTableRefresh nextId="{len(FACT_HEADERS) + 1}">'
        f'<queryTableFields count="{len(FACT_HEADERS)}">'
        f"{''.join(fields)}</queryTableFields></queryTableRefresh>"
    )
    if re.search(r"<queryTableRefresh\b", xml):
        xml = re.sub(
            r"<queryTableRefresh\b.*?</queryTableRefresh>",
            body,
            xml,
            count=1,
            flags=re.DOTALL,
        )
        xml = re.sub(r"<queryTableRefresh\b[^>]*/>", body, xml, count=1)
        return xml
    if xml.endswith("/>"):
        return xml[:-2] + f">{body}</queryTable>"
    return xml.replace("</queryTable>", f"{body}</queryTable>", 1)


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


def _snapshot_month_labels(rows: Sequence[Mapping[str, object]]) -> str:
    seen: list[tuple[str, str]] = []
    used: set[str] = set()
    for row in rows:
        label = str(row.get("month_label") or row.get("Month") or "").strip()
        if not label or label in used:
            continue
        used.add(label)
        start = str(row.get("month_start") or "")
        seen.append((start, label))
    seen.sort()
    return ", ".join(label for _start, label in seen)


def _facts_settings_xml(
    xml: str,
    published_stamp: str,
    *,
    company_name: str = "",
    company_code: str = "",
    api_base_url: str = "",
    refresh_bearer_token: str = "",
    months_loaded: str = "",
    refreshable: bool = False,
) -> str:
    """Write inert provenance on row 5 and control Settings B2–B4.

    Row 5 sits between the Settings table (A1:B4) and the Facts table (A6:AS7).
    Power Query reads only the Settings table, so Company/Code/Published here
    cannot retarget the live query. client_id is not written to this sheet.
    Static artifacts clear B2–B4. Refreshable artifacts write ApiBaseUrl and
    optionally the caller's short-lived JWT into BearerToken. ClientId stays
    empty.
    """
    xml = re.sub(r'<c r="B2"[^/]*/>', "", xml)
    xml = re.sub(r'<c r="B2"[^>]*>.*?</c>', "", xml, flags=re.DOTALL)
    xml = re.sub(r'<c r="B3"[^/]*/>', "", xml)
    xml = re.sub(r'<c r="B3"[^>]*>.*?</c>', "", xml, flags=re.DOTALL)
    xml = re.sub(r'<c r="B4"[^/]*/>', "", xml)
    xml = re.sub(r'<c r="B4"[^>]*>.*?</c>', "", xml, flags=re.DOTALL)
    if api_base_url:
        # OOXML requires cells in column order. Prepending B2 before A2 makes
        # Desktop Excel refuse Workbooks.Open (COM -2146827284).
        xml = re.sub(
            r'(<c r="A2"[^/]*/>|<c r="A2"[^>]*>.*?</c>)',
            r"\1" + _inline("B2", api_base_url),
            xml,
            count=1,
            flags=re.DOTALL,
        )
    if refreshable and refresh_bearer_token:
        xml = re.sub(
            r'(<c r="A3"[^/]*/>|<c r="A3"[^>]*>.*?</c>)',
            r"\1" + _inline("B3", refresh_bearer_token),
            xml,
            count=1,
            flags=re.DOTALL,
        )
    xml = re.sub(r'<row r="5"(?=[\s/>]).*?</row>', "", xml, count=1, flags=re.DOTALL)
    extra = ""
    if refreshable:
        extra = (
            f"{_inline('G5', 'LAST SUCCESSFUL DATA')}"
            f"{_inline('H5', months_loaded)}"
            f"{_inline('I5', 'STATUS')}"
            f"{_inline('J5', 'REFRESH REQUIRED')}"
            f"{_inline('K5', 'AUTH')}"
            f"{_inline('L5', 'Session JWT in Settings B3. First-time Excel: Anonymous for this API URL. Never Windows or Basic. Token expires; re-download for a fresh JWT.')}"
        )
    stamp_row = (
        f'<row r="5" spans="1:45">'
        f"{_inline('A5', 'Published')}"
        f"{_inline('B5', published_stamp)}"
        f"{_inline('C5', 'Company')}"
        f"{_inline('D5', company_name)}"
        f"{_inline('E5', 'Code')}"
        f"{_inline('F5', company_code)}"
        f"{extra}"
        f"</row>"
    )
    if re.search(r'<row r="6"(?=[\s/>])', xml):
        xml = re.sub(r'(<row r="6"(?=[\s/>]))', stamp_row + r"\1", xml, count=1)
    else:
        xml = xml.replace("</sheetData>", f"{stamp_row}</sheetData>", 1)
    return xml


def _custom_properties_xml(
    *,
    client_id: str,
    publication_id: str | None,
    published_at: str,
) -> str:
    properties = [
        ("client_id", client_id),
        ("publication_id", publication_id or ""),
        ("published_at", published_at),
    ]
    body = []
    for index, (name, value) in enumerate(properties, start=2):
        body.append(
            f'<property fmtid="{CUSTOM_PROPS_FMTID}" pid="{index}" '
            f'name="{_xml_attr(name)}">'
            f"<vt:lpwstr>{_xml_text(value)}</vt:lpwstr>"
            "</property>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        "<Properties "
        'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f"{''.join(body)}</Properties>"
    )


def _ensure_custom_props_content_type(types_xml: str) -> str:
    if 'PartName="/docProps/custom.xml"' in types_xml:
        return types_xml
    override = (
        f'<Override PartName="/{CUSTOM_PROPS_PART}" ContentType="{CUSTOM_PROPS_CONTENT_TYPE}"/>'
    )
    if "</Types>" not in types_xml:
        raise ValueError("Client report template is invalid.")
    return types_xml.replace("</Types>", f"{override}</Types>", 1)


def _ensure_custom_props_relationship(rels_xml: str) -> str:
    if CUSTOM_PROPS_REL_TYPE in rels_xml:
        return rels_xml
    rid = f"rId{_next_relationship_id(rels_xml)}"
    rel = f'<Relationship Id="{rid}" Type="{CUSTOM_PROPS_REL_TYPE}" Target="{CUSTOM_PROPS_PART}"/>'
    if "</Relationships>" not in rels_xml:
        raise ValueError("Client report template is invalid.")
    return rels_xml.replace("</Relationships>", f"{rel}</Relationships>", 1)


def _decode_sheet_name(value: str) -> str:
    return value.replace("&amp;", "&").replace("&quot;", '"')


def sheet_visibility_states(workbook_xml: str) -> dict[str, str]:
    """Map workbook sheet names to OOXML state (visible when omitted)."""
    states: dict[str, str] = {}
    for tag in re.findall(r"<sheet\b[^>]*/?>", workbook_xml):
        attrs = dict(re.findall(r'([:\w]+)="([^"]*)"', tag))
        name = _decode_sheet_name(attrs.get("name", ""))
        if name:
            states[name] = attrs.get("state", "visible")
    return states


def hide_technical_sheets_xml(workbook_xml: str, *, hide_names: Sequence[str] | None = None) -> str:
    """Hide technical sheets; activate the first report sheet.

    Generated downloads hide both ``PublishedFacts`` and ``Facts``. The
    tracked operator template stays visible. Uses normal ``hidden`` (not
    veryHidden) so support can Unhide to paste a JWT or inspect the query.
    """
    hide = set(hide_names if hide_names is not None else TECHNICAL_SHEET_NAMES)
    tags = re.findall(r"<sheet\b[^>]*/?>", workbook_xml)
    names: list[str] = []
    xml = workbook_xml
    for tag in tags:
        attrs = dict(re.findall(r'([:\w]+)="([^"]*)"', tag))
        name = _decode_sheet_name(attrs.get("name", ""))
        names.append(name)
        if name not in hide:
            continue
        if re.search(r'\sstate="[^"]*"', tag):
            hidden = re.sub(r'\sstate="[^"]*"', f' state="{SHEET_STATE_HIDDEN}"', tag, count=1)
        elif tag.endswith("/>"):
            hidden = f'{tag[:-2]} state="{SHEET_STATE_HIDDEN}"/>'
        else:
            hidden = tag.replace(">", f' state="{SHEET_STATE_HIDDEN}">', 1)
        xml = xml.replace(tag, hidden, 1)
    try:
        active = names.index(REPORT_SHEET_NAMES[0])
    except ValueError as exc:
        raise ValueError("Client report is incomplete.") from exc
    if 'activeTab="' in xml:
        xml = re.sub(r'activeTab="\d+"', f'activeTab="{active}"', xml, count=1)
    else:
        xml = xml.replace("<workbookView ", f'<workbookView activeTab="{active}" ', 1)
    if 'firstSheet="' in xml:
        xml = re.sub(r'firstSheet="\d+"', f'firstSheet="{active}"', xml, count=1)
    else:
        xml = xml.replace("<workbookView ", f'<workbookView firstSheet="{active}" ', 1)
    return xml


def apply_hidden_technical_sheets_into(
    parts: dict[str, bytes], *, refreshable: bool = False
) -> None:
    """Rewrite workbook.xml visibility on an in-memory package."""
    hide_names: tuple[str, ...] = TECHNICAL_SHEET_NAMES
    _ = refreshable
    workbook_xml = hide_technical_sheets_xml(
        parts["xl/workbook.xml"].decode("utf-8"),
        hide_names=hide_names,
    )
    rels_xml = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    sheet_parts = _sheet_part_map(workbook_xml, rels_xml)
    parts["xl/workbook.xml"] = workbook_xml.encode("utf-8")
    for name in TECHNICAL_SHEET_NAMES:
        if name == "PublishedFacts":
            continue
        part = sheet_parts.get(name)
        if part:
            parts[part] = _clear_tab_selected(parts[part].decode("utf-8")).encode("utf-8")
    report_part = sheet_parts.get(REPORT_SHEET_NAMES[0])
    if report_part:
        parts[report_part] = _select_sheet_tab(parts[report_part].decode("utf-8")).encode("utf-8")


def apply_hidden_technical_sheets(body: bytes, *, refreshable: bool = False) -> bytes:
    """Rewrite workbook.xml visibility on a generated package."""
    return mutate_xlsx(
        body, lambda parts: apply_hidden_technical_sheets_into(parts, refreshable=refreshable)
    )


def _clear_tab_selected(xml: str) -> str:
    head, sep, tail = xml.partition("</sheetViews>")
    if not sep:
        return xml
    return re.sub(r'\s+tabSelected="[^"]*"', "", head) + sep + tail


def _select_sheet_tab(xml: str) -> str:
    xml = _clear_tab_selected(xml)
    return re.sub(r"<sheetView\b", '<sheetView tabSelected="1"', xml, count=1)


def assert_technical_sheets_hidden(body: bytes, *, facts_visible: bool = False) -> None:
    """Raise if technical sheets have the wrong visibility or reports are hidden."""
    with ZipFile(io.BytesIO(body), "r") as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
    states = sheet_visibility_states(workbook_xml)
    if states.get("PublishedFacts") != SHEET_STATE_HIDDEN:
        raise ValueError("Client report is incomplete.")
    expected_facts = "visible" if facts_visible else SHEET_STATE_HIDDEN
    if states.get("Facts") != expected_facts:
        raise ValueError("Client report is incomplete.")
    for name in REPORT_SHEET_NAMES:
        if states.get(name, "visible") != "visible":
            raise ValueError("Client report is incomplete.")


def _require_complete_workbook(
    body: bytes,
    *,
    refreshable: bool = False,
    require_renewal: bool = False,
) -> None:
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
            if refreshable and QUERY_TABLE_PART not in names:
                raise ValueError("Client report is incomplete.")
            if refreshable and "xl/connections.xml" not in names:
                raise ValueError("Client report is incomplete.")
            if refreshable and query_table_field_names(body) != FACT_HEADERS:
                raise ValueError("Client report is incomplete.")
            if refreshable:
                cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
                if f'name="{PIVOT_CACHE_QUERY_NAME}"' not in cache:
                    raise ValueError("Client report is incomplete.")
                if re.search(r'<worksheetSource ref="', cache):
                    raise ValueError("Client report is incomplete.")
                last_col = get_column_letter(len(FACT_HEADERS))
                if f"PublishedFacts!$A$1:${last_col}$" not in workbook_xml:
                    raise ValueError("Client report is incomplete.")
                if re.search(
                    rf'name="{re.escape(PIVOT_CACHE_QUERY_NAME)}"[^>]*>PublishedFacts!\$A\$1</definedName>',
                    workbook_xml,
                ):
                    raise ValueError("Client report is incomplete.")
                if "xl/vbaProject.bin" not in names:
                    raise ValueError("Client report is incomplete.")
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
    assert_slicer_package(body)
    if require_renewal:
        assert_renewal_enabled_mashup(body)
    assert_technical_sheets_hidden(body, facts_visible=False)
