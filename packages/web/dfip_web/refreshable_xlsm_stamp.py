"""Stamp a fresh Excel grant into the Data Model refreshable .xlsm template.

The website refreshable download copies the Excel-authored Data Model workbook
and replaces only Settings BearerToken. It does not clone excel/Client_Report.xlsx,
does not inject publication rows, and does not rebuild PivotCaches or VertiPaq.

BearerToken in the accepted template is Facts!B3 stored as a shared string
(``xl/sharedStrings.xml``). ApiBaseUrl is already production HTTPS and is left
unchanged. Refresh All reads cumulative history from that URL.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path
from zipfile import ZipFile

from dfip_web.client_workbook import EXCEL_DIR, ROOT
from dfip_web.daily_report import _clone_zipinfo, _sheet_part_map, _xml_text

CANONICAL_SHA256 = "cd6d53d7ba5f13a4ea58e809bb4928881635a21ee052e3bb68ff443d7fdf7b70"
PRODUCTION_API_BASE_URL = "https://dfip-by-pratham.duckdns.org"
MODEL_PART = "xl/model/item.data"
VBA_PART = "xl/vbaProject.bin"
SHARED_STRINGS_PART = "xl/sharedStrings.xml"
FACTS_SHEET = "Facts"
_CANONICAL_MIN_BYTES = 1_000_000
_SI_RE = re.compile(r"<si\b[^>]*>.*?</si>", re.DOTALL)
_B3_RE = re.compile(r'<c r="B3"[^/]*/>|<c r="B3"[^>]*>.*?</c>', re.DOTALL)
_A3_RE = re.compile(r'(<c r="A3"[^/]*/>|<c r="A3"[^>]*>.*?</c>)', re.DOTALL)
_CELL_V_RE = re.compile(r"<v>([^<]*)</v>")
_T_RE = re.compile(r"(<t\b[^>]*>)(.*?)(</t>)", re.DOTALL)


def refreshable_xlsm_candidates(*, override: str = "") -> tuple[Path, ...]:
    """Return template search paths. Override wins when set."""
    if override.strip():
        return (Path(override.strip()),)
    return (
        EXCEL_DIR / "Client_Report_Refreshable.xlsm",
        ROOT / "Client_Report_DataModel_Typed_POC.xlsm",
        ROOT / "DFIP_Client_Refreshable_Final.xlsm",
    )


def resolve_refreshable_xlsm_template(override: str = "") -> Path:
    """Return the Data Model .xlsm used for refreshable website downloads."""
    env = os.environ.get("DFIP_REFRESHABLE_XLSM_TEMPLATE", "").strip()
    for path in refreshable_xlsm_candidates(override=override or env):
        if path.is_file():
            return path
    raise FileNotFoundError("Refreshable Data Model workbook template is missing.")


def stamp_refreshable_xlsm(
    template: bytes,
    *,
    bearer_token: str,
    require_canonical_sha: bool | None = None,
) -> bytes:
    """Return a copy of ``template`` with Settings BearerToken replaced.

    Other OPC parts keep their uncompressed bytes. Large canonical templates
    must match ``CANONICAL_SHA256``. The previous token must not remain in
    any package part.
    """
    if not template.startswith(b"PK"):
        raise ValueError("Refreshable Data Model workbook template is invalid.")
    if require_canonical_sha is None:
        require_canonical_sha = len(template) >= _CANONICAL_MIN_BYTES
    if require_canonical_sha:
        digest = hashlib.sha256(template).hexdigest()
        if digest != CANONICAL_SHA256:
            raise ValueError("Refreshable Data Model workbook template SHA-256 mismatch.")
    token = (bearer_token or "").strip()
    with ZipFile(io.BytesIO(template), "r") as original:
        names = original.namelist()
        if MODEL_PART not in names:
            raise ValueError("Refreshable Data Model workbook template is incomplete.")
        if VBA_PART not in names:
            raise ValueError("Refreshable Data Model workbook template is incomplete.")
        if SHARED_STRINGS_PART not in names:
            raise ValueError("Refreshable Data Model workbook template is incomplete.")
        try:
            workbook_xml = original.read("xl/workbook.xml").decode("utf-8")
            rels_xml = original.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        except KeyError as exc:
            raise ValueError("Refreshable Data Model workbook template is invalid.") from exc
        parts = _sheet_part_map(workbook_xml, rels_xml)
        facts_part = parts.get(FACTS_SHEET)
        if not facts_part or facts_part not in names:
            raise ValueError("Refreshable Data Model workbook template is incomplete.")
        facts_xml = original.read(facts_part).decode("utf-8")
        shared_xml = original.read(SHARED_STRINGS_PART).decode("utf-8")
        replacements, previous = _bearer_replacements(facts_part, facts_xml, shared_xml, token)
        if previous and previous != token:
            needle = previous.encode("utf-8")
            for info in original.infolist():
                if info.filename in replacements:
                    continue
                if needle in original.read(info.filename):
                    raise ValueError(
                        "Bearer token is stored outside Settings shared strings."
                    )
        body = _rewrite_opc(original, replacements)
    packed = _uncompressed_bytes(body)
    if previous and previous != token and previous.encode("utf-8") in packed:
        raise ValueError("Previous workbook grant remained in the stamped package.")
    if token and _xml_text(token).encode("utf-8") not in packed:
        raise ValueError("Stamped workbook grant is missing.")
    return body


def stamp_refreshable_xlsm_file(
    path: Path,
    *,
    bearer_token: str,
    require_canonical_sha: bool | None = None,
) -> bytes:
    """Stamp ``path`` without mutating the on-disk template."""
    return stamp_refreshable_xlsm(
        path.read_bytes(),
        bearer_token=bearer_token,
        require_canonical_sha=require_canonical_sha,
    )


def zip_uncompressed_diffs(before: bytes, after: bytes) -> tuple[str, ...]:
    """Return OPC part names whose uncompressed bytes differ."""
    with ZipFile(io.BytesIO(before), "r") as left, ZipFile(io.BytesIO(after), "r") as right:
        left_names = left.namelist()
        right_names = right.namelist()
        if left_names != right_names:
            extra = sorted(set(left_names) ^ set(right_names))
            return tuple(extra or ["<namelist>"])
        changed = [
            name for name in left_names if left.read(name) != right.read(name)
        ]
    return tuple(changed)


def _bearer_replacements(
    facts_part: str,
    facts_xml: str,
    shared_xml: str,
    token: str,
) -> tuple[dict[str, bytes], str]:
    cell = _B3_RE.search(facts_xml)
    if cell and 't="s"' in cell.group(0):
        match = _CELL_V_RE.search(cell.group(0))
        if match is None:
            raise ValueError("Refreshable Data Model Settings BearerToken is invalid.")
        index = int(match.group(1))
        previous = _si_text(shared_xml, index)
        updated = _set_si_text(shared_xml, index, token)
        return {SHARED_STRINGS_PART: updated.encode("utf-8")}, previous
    if cell and "inlineStr" in cell.group(0):
        previous = _inline_text(cell.group(0))
        updated = facts_xml[: cell.start()] + _inline_b3(token) + facts_xml[cell.end() :]
        return {facts_part: updated.encode("utf-8")}, previous
    if _A3_RE.search(facts_xml):
        updated = _A3_RE.sub(r"\1" + _inline_b3(token), facts_xml, count=1)
        return {facts_part: updated.encode("utf-8")}, ""
    raise ValueError("Refreshable Data Model Settings BearerToken cell is missing.")


def _si_text(shared_xml: str, index: int) -> str:
    block = _si_block(shared_xml, index)
    match = _T_RE.search(block)
    if match is None:
        return ""
    return _unescape(match.group(2))


def _set_si_text(shared_xml: str, index: int, value: str) -> str:
    matches = list(_SI_RE.finditer(shared_xml))
    if index < 0 or index >= len(matches):
        raise ValueError("Refreshable Data Model Settings BearerToken is invalid.")
    block = matches[index].group(0)
    escaped = _xml_text(value)
    if _T_RE.search(block):
        new_block = _T_RE.sub(
            lambda match: match.group(1) + escaped + match.group(3),
            block,
            count=1,
        )
    else:
        new_block = f"<si><t xml:space=\"preserve\">{escaped}</t></si>"
    return shared_xml[: matches[index].start()] + new_block + shared_xml[matches[index].end() :]


def _si_block(shared_xml: str, index: int) -> str:
    matches = list(_SI_RE.finditer(shared_xml))
    if index < 0 or index >= len(matches):
        raise ValueError("Refreshable Data Model Settings BearerToken is invalid.")
    return matches[index].group(0)


def _inline_b3(value: str) -> str:
    if value == "":
        return '<c r="B3"/>'
    return (
        f'<c r="B3" t="inlineStr"><is>'
        f'<t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _inline_text(cell_xml: str) -> str:
    match = re.search(r"<t\b[^>]*>(.*?)</t>", cell_xml, flags=re.DOTALL)
    if match is None:
        return ""
    return _unescape(match.group(1))


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _rewrite_opc(original: ZipFile, replacements: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with ZipFile(out, "w") as written:
        for info in original.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = original.read(info.filename)
            written.writestr(_clone_zipinfo(info), data)
    return out.getvalue()


def _uncompressed_bytes(body: bytes) -> bytes:
    with ZipFile(io.BytesIO(body), "r") as archive:
        return b"".join(archive.read(name) for name in archive.namelist())
