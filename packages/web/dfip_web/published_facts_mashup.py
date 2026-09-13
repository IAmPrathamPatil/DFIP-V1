"""Inspect the native DataMashup package's PublishedFacts formula.

Python cannot author a Desktop-evaluable DataMashup from scratch. Replacing
``Formulas/Section1.m`` without rebuilding Power Query metadata makes Excel
skip Refresh All (zero HTTP calls). Generated refreshable workbooks keep the
Excel-authored package, including the Chrono step that types Month from
``month_start`` so MMM-YY labels sort in calendar order. Rebuild that
package in Desktop Excel (Queries.Formula / paste ``excel/PublishedFacts.m``)
when the formula must change. Runtime download must not rewrite DataMashup.
"""

from __future__ import annotations

import base64
import io
import re
import struct
from pathlib import Path
from zipfile import ZipFile

from dfip_web.client_workbook import mashup_text

DATAMASHUP_PART = "customXml/item1.xml"
_SECTION_NAME = "Formulas/Section1.m"
_MASHUP_RE = re.compile(r"(<DataMashup[^>]*>)(.*)(</DataMashup>)", re.DOTALL)
# Same length: Excel DataMashup metadata stays valid if only this token changes.
CURRENT_FACTS_PATH_TOKEN = b"publications/current/facts"
HISTORY_FACTS_PATH_TOKEN = b"publications/history/facts"
HISTORY_FACTS_RELATIVE_PATH = "/api/v1/publications/history/facts"
HISTORY_FACTS_CSV_RELATIVE_PATH = "/api/v1/publications/history/facts.csv"
# Same-length Section1.m helper for tests. Do not apply to the tracked
# template or at download time — Excel must rebuild DataMashup metadata.
_MONTH_DATE_MARKER = b"Date.From([month_start])"
_RENAMED_RESULT_TAIL = (
    b"List.Transform(RenamePairs, each {_, Record.Field(HeaderMap, _)})\r\n"
    b"                )\r\n"
    b"            in\r\n"
    b"                Renamed\r\n"
)
_CHRONO_RESULT_TAIL = (
    b"List.Transform(RenamePairs, each {_, Record.Field(HeaderMap, _)})\r\n"
    b"                ),\r\n"
    b"                Chrono = Table.ReorderColumns(Table.RenameColumns("
    b'Table.RemoveColumns(Table.AddColumn(Renamed, "MD", each try Date.From('
    b'[month_start]) otherwise [Month], type date), {"Month"}), '
    b'{{"MD", "Month"}}), Table.ColumnNames(Renamed))\r\n'
    b"            in\r\n"
    b"                Chrono\r\n"
)
_SHRINKABLE_COMMENTS = (
    b"// Excel is a reporting + refresh layer, not the processing engine.\r\n"
    b"// This query calls GET /api/v1/publications/history/facts with Bearer auth.\r\n"
    b"// Page size is the API maximum of 200. Power Query pages until total is consumed.\r\n"
    b"// JWT client_id scopes the result. A development token has no client_id; set\r\n"
    b"// ClientId in the Settings table only for local testing. That is not RLS.\r\n"
)
_SHRUNK_COMMENT = b"// Month values are month_start dates; display stays MMM-YY.\r\n"
_SHRUNK_COMMENT_RE = re.compile(
    rb"// Month values are month_start dates; display stays MMM-YY. *\r\n"
)


def published_facts_section_m(query_m: str | None = None) -> str:
    """Return the DataMashup Section1.m text wrapping on-disk PublishedFacts.m."""
    body = (query_m if query_m is not None else mashup_text()).strip()
    return f"section Section1;\r\n\r\nshared PublishedFacts = {body}\r\n"


def replace_published_facts_mashup(item1_xml: bytes, query_m: str | None = None) -> bytes:
    """Return UTF-16 DataMashup XML with Section1.m replaced by current M."""
    text = item1_xml.decode("utf-16")
    match = _MASHUP_RE.search(text)
    if match is None:
        raise ValueError("Client report template is invalid.")
    blob = base64.b64decode(match.group(2).strip())
    rebuilt = _replace_section1(blob, published_facts_section_m(query_m).encode("utf-8"))
    encoded = base64.b64encode(rebuilt).decode("ascii")
    updated = text[: match.start(2)] + encoded + text[match.end(2) :]
    return updated.encode("utf-16")


def retarget_published_facts_history_path(item1_xml: bytes) -> bytes:
    """Replace current/facts with history/facts inside Section1.m only.

    Token lengths match so the rest of the Excel-authored DataMashup package
    stays intact. Runtime download must not call this; apply it to the tracked
    template once.
    """
    if len(CURRENT_FACTS_PATH_TOKEN) != len(HISTORY_FACTS_PATH_TOKEN):
        raise ValueError("History facts path token length must match current.")
    text = item1_xml.decode("utf-16")
    match = _MASHUP_RE.search(text)
    if match is None:
        raise ValueError("Client report template is invalid.")
    blob = base64.b64decode(match.group(2).strip())
    rebuilt = _transform_section1(blob, _retarget_history_path)
    encoded = base64.b64encode(rebuilt).decode("ascii")
    updated = text[: match.start(2)] + encoded + text[match.end(2) :]
    return updated.encode("utf-16")


def retarget_template_history_path(xlsx_path: Path) -> None:
    """Same-length current→history path replace in a tracked native workbook."""
    _rewrite_template_datamashup(xlsx_path, retarget_published_facts_history_path)


def type_published_facts_month_dates(item1_xml: bytes) -> bytes:
    """Type the Month column from month_start inside Section1.m only.

    Length of Formulas/Section1.m stays unchanged so Excel-authored DataMashup
    metadata remains valid. Runtime download must not call this; apply it to
    the tracked template once.
    """
    text = item1_xml.decode("utf-16")
    match = _MASHUP_RE.search(text)
    if match is None:
        raise ValueError("Client report template is invalid.")
    blob = base64.b64decode(match.group(2).strip())
    rebuilt = _transform_section1(blob, _type_month_from_month_start)
    encoded = base64.b64encode(rebuilt).decode("ascii")
    updated = text[: match.start(2)] + encoded + text[match.end(2) :]
    return updated.encode("utf-16")


def apply_template_month_date_typing(xlsx_path: Path) -> None:
    """Same-length Month date typing in a tracked native workbook.

    Do not apply at runtime. Replacing Section1.m content (even at the same
    length) makes Desktop Excel skip Refresh All unless the rest of the
    Excel-authored DataMashup package is rebuilt in Desktop.
    """
    _rewrite_template_datamashup(xlsx_path, type_published_facts_month_dates)


def revert_published_facts_month_dates(item1_xml: bytes) -> bytes:
    """Undo same-length Month date typing so the Excel-authored package matches."""
    text = item1_xml.decode("utf-16")
    match = _MASHUP_RE.search(text)
    if match is None:
        raise ValueError("Client report template is invalid.")
    blob = base64.b64decode(match.group(2).strip())
    rebuilt = _transform_section1(blob, _revert_month_date_typing)
    encoded = base64.b64encode(rebuilt).decode("ascii")
    updated = text[: match.start(2)] + encoded + text[match.end(2) :]
    return updated.encode("utf-16")


def revert_template_month_date_typing(xlsx_path: Path) -> None:
    _rewrite_template_datamashup(xlsx_path, revert_published_facts_month_dates)


_PAGE_LIMIT_TOKEN = b"PageLimit = 200,"
_PAGE_LIMIT_ENLARGED = b"PageLimit = 5000,"


def enlarge_published_facts_page_limit(item1_xml: bytes) -> bytes:
    """Raise Section1.m PageLimit 200 → 5000; keep Formulas/Section1.m length.

    Runtime download must not call this; apply it to the tracked template once.
    """
    text = item1_xml.decode("utf-16")
    match = _MASHUP_RE.search(text)
    if match is None:
        raise ValueError("Client report template is invalid.")
    blob = base64.b64decode(match.group(2).strip())
    rebuilt = _transform_section1(blob, _enlarge_page_limit)
    encoded = base64.b64encode(rebuilt).decode("ascii")
    updated = text[: match.start(2)] + encoded + text[match.end(2) :]
    return updated.encode("utf-16")


def apply_template_history_page_limit(xlsx_path: Path) -> None:
    """Same-length PageLimit 200→5000 in a tracked native workbook."""
    _rewrite_template_datamashup(xlsx_path, enlarge_published_facts_page_limit)


def _enlarge_page_limit(section_bytes: bytes) -> bytes:
    if _PAGE_LIMIT_ENLARGED in section_bytes:
        return section_bytes
    if _PAGE_LIMIT_TOKEN not in section_bytes:
        raise ValueError("PublishedFacts mashup is missing PageLimit = 200.")
    original_len = len(section_bytes)
    updated = section_bytes.replace(_PAGE_LIMIT_TOKEN, _PAGE_LIMIT_ENLARGED, 1)
    need = len(updated) - original_len
    if need <= 0:
        if len(updated) != original_len:
            raise ValueError("PublishedFacts mashup page-limit replace is invalid.")
        return updated
    match = _SHRUNK_COMMENT_RE.search(updated)
    if match is None:
        raise ValueError("PublishedFacts mashup comments cannot absorb page-limit enlarge.")
    old = match.group(0)
    if not old.endswith(b"\r\n"):
        raise ValueError("PublishedFacts mashup comment line is invalid.")
    body = old[:-2]
    if len(body) < len(_SHRUNK_COMMENT) - 2 + need:
        raise ValueError("PublishedFacts mashup page-limit enlarge exceeds comment budget.")
    replacement = body[:-need] + b"\r\n"
    if len(replacement) != len(old) - need:
        raise ValueError("PublishedFacts mashup comment shrink for page limit is invalid.")
    updated = updated[: match.start()] + replacement + updated[match.end() :]
    if len(updated) != original_len:
        raise ValueError("PublishedFacts mashup page-limit enlarge must keep Section1.m length.")
    if _PAGE_LIMIT_ENLARGED not in updated:
        raise ValueError("PublishedFacts mashup page-limit enlarge did not apply.")
    return updated


def _rewrite_template_datamashup(xlsx_path: Path, transform) -> None:
    from dfip_web.daily_report import _clone_zipinfo

    raw = xlsx_path.read_bytes()
    out = io.BytesIO()
    with ZipFile(io.BytesIO(raw), "r") as original, ZipFile(out, "w") as written:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == DATAMASHUP_PART:
                data = transform(data)
            written.writestr(_clone_zipinfo(info), data)
    xlsx_path.write_bytes(out.getvalue())


def extract_published_facts_section_m(item1_xml: bytes) -> str:
    """Return Section1.m from a DataMashup customXml part."""
    text = item1_xml.decode("utf-16")
    match = _MASHUP_RE.search(text)
    if match is None:
        raise ValueError("DataMashup part is missing.")
    blob = base64.b64decode(match.group(2).strip())
    version, parts = _parse_parts(blob)
    del version
    if not parts:
        raise ValueError("DataMashup part is missing.")
    with ZipFile(io.BytesIO(parts[0])) as archive:
        return archive.read(_SECTION_NAME).decode("utf-8")


def _parse_parts(blob: bytes) -> tuple[int, list[bytes]]:
    if len(blob) < 8:
        raise ValueError("DataMashup part is invalid.")
    version = struct.unpack_from("<I", blob, 0)[0]
    offset = 4
    parts: list[bytes] = []
    while offset + 4 <= len(blob):
        size = struct.unpack_from("<I", blob, offset)[0]
        offset += 4
        if size < 0 or offset + size > len(blob):
            raise ValueError("DataMashup part is invalid.")
        parts.append(blob[offset : offset + size])
        offset += size
    if offset != len(blob):
        raise ValueError("DataMashup part is invalid.")
    return version, parts


def _retarget_history_path(section_bytes: bytes) -> bytes:
    return section_bytes.replace(CURRENT_FACTS_PATH_TOKEN, HISTORY_FACTS_PATH_TOKEN)


def _type_month_from_month_start(section_bytes: bytes) -> bytes:
    """Replace Month text with month_start dates; keep Section1.m length."""
    if _MONTH_DATE_MARKER in section_bytes:
        return section_bytes
    if _RENAMED_RESULT_TAIL not in section_bytes:
        raise ValueError("PublishedFacts mashup is missing the rename step.")
    original_len = len(section_bytes)
    updated = section_bytes.replace(_RENAMED_RESULT_TAIL, _CHRONO_RESULT_TAIL, 1)
    need = len(updated) - original_len
    if need > 0:
        updated = _shrink_section_comments(updated, need)
    elif need < 0:
        updated = _pad_section_end(updated, -need)
    if len(updated) != original_len:
        raise ValueError("PublishedFacts mashup month typing must keep Section1.m length.")
    if _MONTH_DATE_MARKER not in updated:
        raise ValueError("PublishedFacts mashup month typing did not apply.")
    return updated


def _shrink_section_comments(section_bytes: bytes, need: int) -> bytes:
    if _SHRINKABLE_COMMENTS not in section_bytes:
        raise ValueError("PublishedFacts mashup comments cannot absorb month typing.")
    keep = len(_SHRINKABLE_COMMENTS) - need
    if keep < len(_SHRUNK_COMMENT):
        raise ValueError("PublishedFacts mashup month typing exceeds comment budget.")
    pad = keep - len(_SHRUNK_COMMENT)
    replacement = _SHRUNK_COMMENT[:-2] + (b" " * pad) + b"\r\n"
    if len(replacement) != keep:
        raise ValueError("PublishedFacts mashup comment shrink is invalid.")
    return section_bytes.replace(_SHRINKABLE_COMMENTS, replacement, 1)


def _revert_month_date_typing(section_bytes: bytes) -> bytes:
    """Restore the Excel-authored rename result; keep Section1.m length."""
    if _MONTH_DATE_MARKER not in section_bytes:
        return section_bytes
    original_len = len(section_bytes)
    if _CHRONO_RESULT_TAIL not in section_bytes:
        raise ValueError("PublishedFacts mashup month typing cannot be reverted.")
    updated = section_bytes.replace(_CHRONO_RESULT_TAIL, _RENAMED_RESULT_TAIL, 1)
    if _SHRUNK_COMMENT_RE.search(updated) is None:
        raise ValueError("PublishedFacts mashup comments cannot restore month typing.")
    updated = _SHRUNK_COMMENT_RE.sub(_SHRINKABLE_COMMENTS, updated, count=1)
    if len(updated) != original_len:
        raise ValueError("PublishedFacts mashup month revert must keep Section1.m length.")
    if _MONTH_DATE_MARKER in updated:
        raise ValueError("PublishedFacts mashup month typing did not revert.")
    return updated


def _pad_section_end(section_bytes: bytes, pad: int) -> bytes:
    if not section_bytes.endswith(b";"):
        raise ValueError("PublishedFacts mashup is missing the closing token.")
    return section_bytes[:-1] + (b" " * pad) + b";"


def _replace_section1(blob: bytes, section_bytes: bytes) -> bytes:
    return _transform_section1(blob, lambda _ignored: section_bytes)


def _transform_section1(blob: bytes, transform) -> bytes:
    version, parts = _parse_parts(blob)
    if not parts or parts[0][:2] != b"PK":
        raise ValueError("DataMashup part is invalid.")
    with ZipFile(io.BytesIO(parts[0])) as source:
        if _SECTION_NAME not in source.namelist():
            raise ValueError("DataMashup part is invalid.")
        section = transform(source.read(_SECTION_NAME))
    parts[0] = _rewrite_zip_member(parts[0], _SECTION_NAME, section)
    out = bytearray(struct.pack("<I", version))
    for part in parts:
        out.extend(struct.pack("<I", len(part)))
        out.extend(part)
    return bytes(out)


def _rewrite_zip_member(zip_bytes: bytes, name: str, data: bytes) -> bytes:
    source = ZipFile(io.BytesIO(zip_bytes))
    names = source.namelist()
    if name not in names:
        raise ValueError("DataMashup part is invalid.")
    out = io.BytesIO()
    with ZipFile(out, "w") as dest:
        for info in source.infolist():
            payload = data if info.filename == name else source.read(info.filename)
            dest.writestr(info, payload)
    return out.getvalue()
