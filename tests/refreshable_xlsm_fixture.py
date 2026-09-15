"""Disposable Data Model-shaped .xlsm used by refreshable download tests."""
# ruff: noqa: E501

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from dfip_web.refreshable_xlsm_stamp import (
    MODEL_PART,
    PRODUCTION_API_BASE_URL,
    VBA_PART,
)

TEMPLATE_PLACEHOLDER_JWT = (
    "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZW1wbGF0ZS1wbGFjZWhvbGRlciIsInR5cCI6ImV4Y2VsIn0."
    "placeholder"
)
_MINI_PATH: Path | None = None


def ensure_mini_refreshable_xlsm() -> Path:
    """Write once per process. Does not use the 20.9 MB canonical workbook."""
    global _MINI_PATH
    if _MINI_PATH is not None and _MINI_PATH.is_file():
        return _MINI_PATH
    path = Path(tempfile.gettempdir()) / "dfip_mini_refreshable_fixture.xlsm"
    path.write_bytes(build_minimal_refreshable_xlsm())
    _MINI_PATH = path
    return path


def build_minimal_refreshable_xlsm(
    *,
    api_base_url: str = PRODUCTION_API_BASE_URL,
    bearer_token: str = TEMPLATE_PLACEHOLDER_JWT,
) -> bytes:
    """OPC package with Facts Settings, VertiPaq part, VBA, cache, and slicer."""
    strings = (
        "Parameter",
        "Value",
        "ApiBaseUrl",
        "BearerToken",
        "ClientId",
        api_base_url,
        bearer_token,
    )
    si = "".join(f"<si><t>{_xml(value)}</t></si>" for value in strings)
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{si}</sst>'
    )
    facts = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>5</v></c></row>
<row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3" t="s"><v>6</v></c></row>
<row r="4"><c r="A4" t="s"><v>4</v></c></row>
</sheetData>
</worksheet>
"""
    published = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>Campaign ID</t></is></c></row>
</sheetData>
</worksheet>
"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="PublishedFacts" sheetId="1" r:id="rId1"/>
<sheet name="Facts" sheetId="2" r:id="rId2"/>
</sheets>
</workbook>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
<Relationship Id="rId4" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" Target="vbaProject.bin"/>
</Relationships>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>
<Default Extension="data" ContentType="application/vnd.ms-excel.model+data"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
<Override PartName="/xl/pivotCache/pivotCacheDefinition1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"/>
<Override PartName="/xl/slicerCaches/slicerCache1.xml" ContentType="application/vnd.ms-excel.slicerCache+xml"/>
</Types>
"""
    pkg_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    cache = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' refreshOnLoad="1"><cacheSource type="external"/></pivotCacheDefinition>'
    )
    slicer = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<slicerCacheDefinition xmlns="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"'
        ' name="Slicer_Month" sourceName="Month"/>'
    )
    parts = {
        "[Content_Types].xml": content_types.encode("utf-8"),
        "_rels/.rels": pkg_rels.encode("utf-8"),
        "xl/workbook.xml": workbook.encode("utf-8"),
        "xl/_rels/workbook.xml.rels": rels.encode("utf-8"),
        "xl/worksheets/sheet1.xml": published.encode("utf-8"),
        "xl/worksheets/sheet2.xml": facts.encode("utf-8"),
        "xl/sharedStrings.xml": shared.encode("utf-8"),
        VBA_PART: b"VBA-PROJECT",
        MODEL_PART: b"MODEL-ITEM-DATA",
        "xl/pivotCache/pivotCacheDefinition1.xml": cache.encode("utf-8"),
        "xl/slicerCaches/slicerCache1.xml": slicer.encode("utf-8"),
    }
    out = io.BytesIO()
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            info = ZipInfo(name)
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    return out.getvalue()


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
