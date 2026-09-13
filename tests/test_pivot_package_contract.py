"""Shared PivotCache contract: numeric cacheId may remap; structure may not."""

from __future__ import annotations

import io
import re
from pathlib import Path
from zipfile import ZipFile

import pytest
from dfip_web.client_report_download import resolve_client_report_template
from dfip_web.client_workbook import XLSX_PATH
from dfip_web.pivot_report import (
    PIVOT_CACHE_ID,
    PIVOT_CACHE_PART,
    assert_native_pivot_package,
    assert_renewal_enabled_mashup,
    shared_pivot_cache_binding,
)
from dfip_web.published_facts_mashup import DATAMASHUP_PART


def _rewrite(body: bytes, mutator) -> bytes:
    source = ZipFile(io.BytesIO(body), "r")
    out = io.BytesIO()
    with source, ZipFile(out, "w") as dest:
        for info in source.infolist():
            dest.writestr(info, mutator(info.filename, source.read(info.filename)))
    return out.getvalue()


def _remap_shared_cache_id(body: bytes, new_id: str) -> bytes:
    def mut(name: str, data: bytes) -> bytes:
        if name == "xl/workbook.xml":
            text = data.decode("utf-8")

            def repl(match: re.Match[str]) -> str:
                return re.sub(r'cacheId="\d+"', f'cacheId="{new_id}"', match.group(0))

            text = re.sub(
                r"<pivotCaches>.*?</pivotCaches>",
                repl,
                text,
                count=1,
                flags=re.DOTALL,
            )
            return text.encode("utf-8")
        if (
            name.startswith("xl/pivotTables/")
            and name.endswith(".xml")
            and "/_rels/" not in name
        ):
            text = data.decode("utf-8")
            text = re.sub(
                r'(<pivotTableDefinition\b[^>]*cacheId=")(\d+)(")',
                rf"\g<1>{new_id}\3",
                text,
                count=1,
            )
            return text.encode("utf-8")
        return data

    return _rewrite(body, mut)


def test_tracked_template_still_satisfies_native_package() -> None:
    body = XLSX_PATH.read_bytes()
    assert_native_pivot_package(body)
    with ZipFile(io.BytesIO(body)) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    cache_id, _rid, target = shared_pivot_cache_binding(workbook, rels)
    assert cache_id == str(PIVOT_CACHE_ID)
    assert target.endswith("pivotCache/pivotCacheDefinition1.xml")
    assert_renewal_enabled_mashup(body)


def test_shared_cache_id_zero_is_valid() -> None:
    remapped = _remap_shared_cache_id(XLSX_PATH.read_bytes(), "0")
    assert_native_pivot_package(remapped)
    with ZipFile(io.BytesIO(remapped)) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    cache_id, _rid, _target = shared_pivot_cache_binding(workbook, rels)
    assert cache_id == "0"


def test_shared_cache_id_twelve_is_valid() -> None:
    remapped = _remap_shared_cache_id(XLSX_PATH.read_bytes(), "12")
    assert_native_pivot_package(remapped)


def test_split_cache_ids_are_rejected() -> None:
    def mut(name: str, data: bytes) -> bytes:
        if name == "xl/pivotTables/pivotTable9.xml":
            text = data.decode("utf-8")
            text = re.sub(
                r'(<pivotTableDefinition\b[^>]*cacheId=")(\d+)(")',
                r"\g<1>99\3",
                text,
                count=1,
            )
            return text.encode("utf-8")
        return data

    split = _rewrite(XLSX_PATH.read_bytes(), mut)
    with pytest.raises(ValueError, match="incomplete"):
        assert_native_pivot_package(split)


def test_missing_pivot_table_part_is_rejected() -> None:
    def mut(name: str, data: bytes) -> bytes:
        if name == "xl/pivotTables/pivotTable1.xml":
            return b""
        return data

    broken = _rewrite(XLSX_PATH.read_bytes(), mut)
    with pytest.raises(ValueError):
        assert_native_pivot_package(broken)


def test_missing_slicer_cache_is_rejected() -> None:
    source = ZipFile(XLSX_PATH, "r")
    out = io.BytesIO()
    with source, ZipFile(out, "w") as dest:
        for info in source.infolist():
            if info.filename == "xl/slicerCaches/slicerCache35.xml":
                continue
            dest.writestr(info, source.read(info.filename))
    with pytest.raises(ValueError, match="incomplete"):
        assert_native_pivot_package(out.getvalue())


def test_missing_publishedfacts_connection_is_rejected() -> None:
    def mut(name: str, data: bytes) -> bytes:
        if name == "xl/connections.xml":
            return data.replace(b"Query - PublishedFacts", b"Query - Other")
        return data

    broken = _rewrite(XLSX_PATH.read_bytes(), mut)
    with pytest.raises(ValueError, match="incomplete"):
        assert_native_pivot_package(broken)


def test_missing_datamashup_is_rejected() -> None:
    def mut(name: str, data: bytes) -> bytes:
        if name == DATAMASHUP_PART:
            return b""
        return data

    broken = _rewrite(XLSX_PATH.read_bytes(), mut)
    with pytest.raises(ValueError, match="incomplete"):
        assert_native_pivot_package(broken)


def test_broken_cache_relationship_is_rejected() -> None:
    def mut(name: str, data: bytes) -> bytes:
        if name == "xl/_rels/workbook.xml.rels":
            return data.replace(
                b"pivotCache/pivotCacheDefinition1.xml",
                b"pivotCache/pivotCacheDefinition9.xml",
            )
        return data

    broken = _rewrite(XLSX_PATH.read_bytes(), mut)
    with pytest.raises(ValueError, match="incomplete"):
        assert_native_pivot_package(broken)


def test_resolve_template_override(monkeypatch, tmp_path: Path) -> None:
    copy = tmp_path / "disposable.xlsx"
    copy.write_bytes(XLSX_PATH.read_bytes())
    monkeypatch.setenv("DFIP_CLIENT_REPORT_TEMPLATE", str(copy))
    assert resolve_client_report_template().resolve() == copy.resolve()
    monkeypatch.delenv("DFIP_CLIENT_REPORT_TEMPLATE")
    assert resolve_client_report_template().resolve() == XLSX_PATH.resolve()
    assert resolve_client_report_template(copy).resolve() == copy.resolve()
