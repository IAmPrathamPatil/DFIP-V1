"""RUN 006: FY-2026 reference formatting parity on generated client workbooks."""

from __future__ import annotations

import io
import re
import time
from zipfile import ZipFile

import pytest
from dfip_web.client_report_download import (
    ARTIFACT_REFRESHABLE,
    ARTIFACT_STATIC,
    FACT_VALUE_FIELDS,
    render_client_report_xlsx,
)
from dfip_web.client_workbook import FACT_HEADERS, XLSX_PATH
from dfip_web.daily_report import (
    AMC_DAYWISE,
    OVERALL,
    REPORT_SHEET_NAMES,
    SERVICE,
    _clone_zipinfo,
    _sheet_part_map,
)
from dfip_web.pivot_report import PIVOT_CACHE_PART, PIVOT_TABLE_NAMES, assert_native_pivot_package
from dfip_web.report_format import (
    DATAFIELD_NUMFMT,
    NUM_INT,
    NUM_PCT_0,
    NUM_PCT_1,
    NUM_PCT_2,
    NUM_ROAS_2DP,
    REPORT_ZOOM,
    RUN006_FONT,
    apply_reference_formatting,
    datafield_numfmt,
    normalize_datafield_name,
    remap_calcchain_chrome_cells,
    remove_report_freeze_panes,
)

from test_p11_pivot_report import _published_fact


def _sample_rows() -> list[dict[str, object]]:
    return [_published_fact(), _published_fact(month_label="Jul-25", sent=3, failed=1)]


def _workbook(artifact: str = ARTIFACT_STATIC) -> bytes:
    return render_client_report_xlsx(
        _sample_rows(),
        published_at=None,
        client_id="a0000000-0000-4000-8000-000000000001",
        client_name="Company 1",
        client_code="C1",
        publication_id="pub-format-1",
        artifact=artifact,
    )


def _datafields(xml: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for tag in re.findall(r"<dataField\b[^>]*(?:/>|>)", xml):
        name = re.search(r' name="([^"]*)"', tag)
        fmt = re.search(r'numFmtId="(\d+)"', tag)
        if name and fmt:
            found.append((normalize_datafield_name(name.group(1)), fmt.group(1)))
    return found


def test_reference_number_formats_on_native_pivots() -> None:
    body = _workbook()
    assert_native_pivot_package(body)
    with ZipFile(io.BytesIO(body)) as archive:
        styles = archive.read("xl/styles.xml").decode("utf-8")
        pivot = archive.read("xl/pivotTables/pivotTable1.xml").decode("utf-8")
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
    assert RUN006_FONT in styles
    assert "Calibri" not in styles
    assert 'formatCode="0.0%"' in styles
    assert 'formatCode="0.00%"' in styles
    assert 'sz val="16"' in styles
    assert "FFFFEB9C" in styles
    names = re.findall(r'<cacheField name="([^"]+)"', cache)
    assert names[:45] == list(FACT_HEADERS)
    mapped = dict(_datafields(pivot))
    assert mapped["Total Cost"] == str(NUM_INT)
    assert mapped["Sent"] == str(NUM_INT)
    assert mapped["Failed Rate SM"] == str(NUM_PCT_0)
    assert mapped["Delivery Rate"] == str(NUM_PCT_0)
    assert mapped["CTR (Del to Clicks)"] == str(NUM_PCT_2)
    assert mapped["UCT conversion rate"] == str(NUM_PCT_1)
    assert mapped["Overall ROAS"] == str(NUM_ROAS_2DP)
    assert mapped["Unique Click Through Conv ROAS"] == "4"
    assert 'name="PivotOverall"' in pivot
    assert "rowFields" in pivot
    assert "PivotStyleLight16" in pivot
    assert "<formats" not in pivot
    assert datafield_numfmt("  Total Cost") == DATAFIELD_NUMFMT["Total Cost"]


def test_remove_report_freeze_panes_strips_frozen_pane() -> None:
    xml = (
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0" zoomScale="90">'
        '<pane xSplit="2" ySplit="9" topLeftCell="C10" '
        'activePane="bottomRight" state="frozen"/>'
        '<selection pane="bottomRight" activeCell="B10" sqref="B10"/>'
        "</sheetView></sheetViews>"
    )
    out = remove_report_freeze_panes(xml)
    assert "<pane" not in out
    assert "frozen" not in out
    assert "xSplit" not in out
    assert 'pane="bottomRight"' not in out
    assert 'activeCell="B10"' in out
    assert 'showGridLines="0"' in out


def test_report_sheet_dimensions_and_header_row() -> None:
    body = _workbook()
    with ZipFile(io.BytesIO(body)) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        parts = _sheet_part_map(workbook_xml, rels)
        overall = archive.read(parts[OVERALL]).decode("utf-8")
        amc = archive.read(parts[AMC_DAYWISE]).decode("utf-8")
        report_xml = {
            name: archive.read(parts[name]).decode("utf-8") for name in REPORT_SHEET_NAMES
        }
    assert 'width="2.88671875"' in overall
    assert 'width="56.109375"' in overall
    assert 'width="17.33203125"' in amc
    assert 'defaultRowHeight="14.4"' in overall
    assert 'ht="43.2"' in overall
    assert 'r="B1"' in overall
    assert "Company 1 | Report | Month Jul-25 to Aug-25" in overall
    assert "Web Engage Daily Report" not in overall
    assert 'mergeCell ref="B1:X1"' not in overall
    assert 'r="A1"' not in overall
    assert 'r="B2"' not in overall
    assert 'r="B5"' not in overall
    assert 'r="B6"' not in overall
    assert 'min="25" max="51"' not in overall
    assert 'min="52" max="53"' in overall
    assert 'hidden="1"' in overall
    assert 'zoomScale="78"' in overall
    assert 'zoomScaleNormal="100"' in overall
    assert 'style="1"' in overall
    assert 'bestFit="1"' in overall
    assert "<pane " not in overall
    assert 'state="frozen"' not in overall
    for name in REPORT_SHEET_NAMES:
        xml = report_xml[name]
        assert "<pane " not in xml, name
        assert 'state="frozen"' not in xml, name
        assert 'zoomScale="90"' not in xml, name
        assert 'min="25" max="51"' not in xml, name
        assert 'min="52" max="53"' in xml, name
        zoom = REPORT_ZOOM[name]
        if zoom:
            assert f'zoomScale="{zoom}"' in xml, name
        else:
            assert "zoomScale=" not in xml or 'zoomScaleNormal="100"' in xml, name
            assert 'zoomScale="90"' not in xml, name
            assert 'zoomScale="' not in xml.replace('zoomScaleNormal="100"', ""), name
        assert f'name="{name}"' in workbook_xml or name.replace("&", "&amp;") in workbook_xml
    service = report_xml[SERVICE]
    assert 'min="16" max="16"' in service and 'hidden="1"' in service
    for col in (16, 19, 21, 24):
        assert re.search(rf'<col min="{col}" max="{col}"[^>]*hidden="1"', service)


def test_calcchain_remaps_report_chrome_only() -> None:
    workbook = (
        '<workbook><sheets>'
        '<sheet name="Facts" sheetId="1" r:id="rId2"/>'
        '<sheet name="Overall Daywise Report " sheetId="3" r:id="rId3"/>'
        '<sheet name="Service Campaigns" sheetId="11" r:id="rId11"/>'
        "</sheets></workbook>"
    )
    chain = (
        '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<c r="A6" i="11" l="1"/><c r="A6" i="1"/><c r="A6" i="3"/>'
        '<c r="A5"/><c r="A1" i="2"/>'
        "</calcChain>"
    )
    out = remap_calcchain_chrome_cells(chain, workbook)
    assert '<c r="A6" i="11" l="1"/>' not in out
    assert '<c r="A6" i="1"/>' in out
    assert '<c r="A6" i="3"/>' not in out
    assert '<c r="A5"/>' not in out
    assert '<c r="B6"' not in out
    assert '<c r="A1" i="2"/>' in out
    assert remap_calcchain_chrome_cells(out, workbook) == out


def test_calcchain_absent_on_tracked_template_stays_absent() -> None:
    body = apply_reference_formatting(XLSX_PATH.read_bytes())
    with ZipFile(io.BytesIO(body)) as archive:
        assert "xl/calcChain.xml" not in archive.namelist()
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        overall = archive.read(_sheet_part_map(workbook_xml, rels)[OVERALL]).decode("utf-8")
    assert 'r="A1"' not in overall
    assert "Web Engage Daily Report" not in overall


def test_excel_saved_calcchain_drops_stripped_chrome() -> None:
    """Excel Save writes A6 calcChain; stripped chrome cells must not remain."""
    calc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<c r="A6" i="11" l="1"/><c r="A6" i="10"/><c r="A6" i="9"/>'
        '<c r="A6" i="8"/><c r="A6" i="7"/><c r="A6" i="6"/>'
        '<c r="A6" i="5"/><c r="A6" i="4"/><c r="A6" i="3"/>'
        "</calcChain>"
    )
    out = io.BytesIO()
    with ZipFile(XLSX_PATH) as original, ZipFile(out, "w") as written:
        for info in original.infolist():
            written.writestr(_clone_zipinfo(info), original.read(info.filename))
        written.writestr("xl/calcChain.xml", calc.encode("utf-8"))
    formatted = apply_reference_formatting(out.getvalue())
    with ZipFile(io.BytesIO(formatted)) as archive:
        chain = archive.read("xl/calcChain.xml").decode("utf-8")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        parts = _sheet_part_map(workbook_xml, rels)
        overall = archive.read(parts[OVERALL]).decode("utf-8")
        facts = archive.read(parts["Facts"]).decode("utf-8")
    assert 'r="A6"' not in chain
    assert 'r="B6"' not in chain
    assert 'r="B6"' not in overall
    assert 'r="A6"' not in overall
    assert 'r="A6"' in facts


def test_formatting_idempotent_and_preserves_values() -> None:
    first = _workbook()
    second = apply_reference_formatting(first)
    with ZipFile(io.BytesIO(first)) as a, ZipFile(io.BytesIO(second)) as b:
        facts_a = a.read(
            _sheet_part_map(
                a.read("xl/workbook.xml").decode("utf-8"),
                a.read("xl/_rels/workbook.xml.rels").decode("utf-8"),
            )["PublishedFacts"]
        )
        facts_b = b.read(
            _sheet_part_map(
                b.read("xl/workbook.xml").decode("utf-8"),
                b.read("xl/_rels/workbook.xml.rels").decode("utf-8"),
            )["PublishedFacts"]
        )
        assert facts_a == facts_b
        assert a.read("xl/pivotCache/pivotCacheDefinition1.xml") == b.read(
            "xl/pivotCache/pivotCacheDefinition1.xml"
        )


def test_static_and_refreshable_share_formats() -> None:
    static = _workbook(ARTIFACT_STATIC)
    refreshable = render_client_report_xlsx(
        _sample_rows(),
        published_at=None,
        client_id="a0000000-0000-4000-8000-000000000001",
        client_name="Company 1",
        client_code="C1",
        publication_id="pub-format-1",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    assert_native_pivot_package(static)
    assert_native_pivot_package(refreshable)
    with ZipFile(io.BytesIO(static)) as a, ZipFile(io.BytesIO(refreshable)) as b:
        static_styles = a.read("xl/styles.xml").decode("utf-8")
        refresh_styles = b.read("xl/styles.xml").decode("utf-8")
        assert RUN006_FONT in static_styles
        assert RUN006_FONT in refresh_styles
        assert "<!--DFIP-CONV-XF:" in refresh_styles
        assert "<!--DFIP-CONV-XF:" not in static_styles
        assert "BearerToken" not in b.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert "local-publisher-pass" not in b.read("xl/worksheets/sheet2.xml").decode("utf-8")
        names = set(b.namelist())
        assert "xl/queryTables/queryTable1.xml" in names
        assert "xl/slicerCaches/slicerCache35.xml" in names
        assert len(FACT_VALUE_FIELDS) == 45


def test_company_workbooks_share_styles() -> None:
    one = render_client_report_xlsx(
        _sample_rows(),
        published_at=None,
        client_id="a0000000-0000-4000-8000-000000000001",
        client_name="Company 1",
        client_code="C1",
        publication_id="p1",
    )
    two = render_client_report_xlsx(
        _sample_rows(),
        published_at=None,
        client_id="b0000000-0000-4000-8000-000000000002",
        client_name="Company 2",
        client_code="C2",
        publication_id="p2",
    )
    with ZipFile(io.BytesIO(one)) as a, ZipFile(io.BytesIO(two)) as b:
        assert a.read("xl/styles.xml") == b.read("xl/styles.xml")
        custom_a = a.read("docProps/custom.xml").decode("utf-8")
        custom_b = b.read("docProps/custom.xml").decode("utf-8")
    assert "a0000000-0000-4000-8000-000000000001" in custom_a
    assert "b0000000-0000-4000-8000-000000000002" in custom_b
    assert "a0000000-0000-4000-8000-000000000001" not in custom_b


def _dispatch_excel():
    win32com_client = pytest.importorskip("win32com.client")
    try:
        excel = win32com_client.DispatchEx("Excel.Application")
    except Exception as exc:
        pytest.skip(f"Excel COM not available: {exc}")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    excel.EnableEvents = False
    return excel


def test_desktop_excel_opens_formatted_static_and_refreshable(tmp_path) -> None:
    """Open/save/reopen only. Does not Refresh All (RUN 005C / RUN 007)."""
    static_path = tmp_path / "run006_static.xlsx"
    refresh_path = tmp_path / "run006_refreshable.xlsm"
    static_path.write_bytes(_workbook(ARTIFACT_STATIC))
    refresh_path.write_bytes(
        render_client_report_xlsx(
            _sample_rows(),
            published_at=None,
            client_id="a0000000-0000-4000-8000-000000000001",
            client_name="Company 1",
            client_code="C1",
            publication_id="pub-format-1",
            artifact=ARTIFACT_REFRESHABLE,
            api_base_url="http://127.0.0.1:8000",
        )
    )
    excel = _dispatch_excel()
    try:
        for path in (static_path, refresh_path):
            workbook = excel.Workbooks.Open(str(path.resolve()), UpdateLinks=0, ReadOnly=False)
            assert workbook.Worksheets.Count == 11
            names = []
            for sheet_name in REPORT_SHEET_NAMES:
                worksheet = workbook.Worksheets(sheet_name)
                tables = worksheet.PivotTables()
                assert tables.Count == 1, sheet_name
                names.append(str(tables.Item(1).Name))
                worksheet.Activate()
                window = excel.ActiveWindow
                assert window.FreezePanes is False, sheet_name
                assert int(window.SplitColumn) == 0, sheet_name
                assert int(window.SplitRow) == 0, sheet_name
            assert set(names) == set(PIVOT_TABLE_NAMES.values())
            saved = path.with_name(path.stem + "_reopen" + path.suffix)
            workbook.SaveAs(str(saved.resolve()))
            workbook.Close(False)
            reopened = excel.Workbooks.Open(str(saved.resolve()), UpdateLinks=0, ReadOnly=True)
            assert reopened.Worksheets.Count == 11
            assert sum(int(ws.PivotTables().Count) for ws in reopened.Worksheets) == 9
            reopened.Close(False)
    finally:
        try:
            excel.Quit()
        except Exception:
            pass
        time.sleep(0.4)


def _b1_title(sheet_xml: str) -> str:
    match = re.search(r'<c r="B1"[^>]*>.*?</c>', sheet_xml, re.DOTALL)
    assert match is not None
    text = re.search(r"<t[^>]*>([^<]*)</t>", match.group(0))
    cached = re.search(r"<v>([^<]*)</v>", match.group(0))
    if text:
        return text.group(1)
    if cached:
        return cached.group(1)
    return ""


def test_company_title_rules_and_published_month_range() -> None:
    from dfip_web.report_format import (
        report_title_formula,
        report_workbook_title,
    )

    eureka_rows = [
        _published_fact(month_label="Jun-25", month_start="2025-06-01"),
        _published_fact(month_label="Sep-25", month_start="2025-09-01"),
    ]
    other_rows = [_published_fact(month_label="Sep-25", month_start="2025-09-01")]
    assert (
        report_workbook_title("Eureka Forbes", eureka_rows)
        == "Eureka Forbes | Web Engage Report | Month Jun-25 to Sep-25"
    )
    assert (
        report_workbook_title("Eureka Forbes2", eureka_rows)
        == "Eureka Forbes2 | Report | Month Jun-25 to Sep-25"
    )
    assert (
        report_workbook_title("Pratham Patil", other_rows)
        == "Pratham Patil | Report | Month Sep-25"
    )
    assert "Web Engage" not in report_workbook_title("Pratham Patil", other_rows)
    start_only = [_published_fact(month_label="", month_start="2025-10-01")]
    assert report_workbook_title("Acme", start_only) == "Acme | Report | Month Oct-25"
    formula = report_title_formula("Eureka Forbes", eureka_rows)
    assert "PublishedFacts!J:J" in formula
    assert "[$-409]MMM-YY" in formula
    assert "filename" not in formula.lower()

    eureka = render_client_report_xlsx(
        eureka_rows,
        published_at=None,
        client_id="a0000000-0000-4000-8000-000000000001",
        client_name="Eureka Forbes",
        client_code="EF",
        publication_id="pub-eureka-title",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    other = render_client_report_xlsx(
        other_rows,
        published_at=None,
        client_id="b0000000-0000-4000-8000-000000000002",
        client_name="Pratham Patil",
        client_code="PP",
        publication_id="pub-patil-title",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    with ZipFile(io.BytesIO(eureka)) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        parts = _sheet_part_map(workbook_xml, rels)
        titles = []
        for name in REPORT_SHEET_NAMES:
            xml = archive.read(parts[name]).decode("utf-8")
            titles.append(_b1_title(xml))
            assert 'mergeCell ref="B1:X1"' not in xml
            assert "PublishedFacts!J:J" in xml
            assert "<f>" in xml
            assert "Web Engage Daily Report" not in xml
        drawings = [
            n for n in archive.namelist() if n.startswith("xl/drawings/drawing") and n.endswith(".xml")
        ]
        assert len(drawings) == 9
        for part in drawings:
            drawing = archive.read(part).decode("utf-8")
            assert 'name="Rectangle 1"' in drawing
            assert 'textlink="B1"' in drawing
            assert "Eureka Forbes | Web Engage Report | Month Jun-25 to Sep-25" in drawing
            assert "<sle:slicer" in drawing
        query = archive.read("xl/queryTables/queryTable1.xml").decode("utf-8")
        assert 'backgroundRefresh="0"' in query
        caches = [
            n for n in archive.namelist() if n.startswith("xl/slicerCaches/") and n.endswith(".xml")
        ]
        assert len(caches) == 35
        charts = [n for n in archive.namelist() if n.startswith("xl/charts/")]
        assert charts == []
        assert_native_pivot_package(eureka)
    assert set(titles) == {"Eureka Forbes | Web Engage Report | Month Jun-25 to Sep-25"}
    with ZipFile(io.BytesIO(other)) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        parts = _sheet_part_map(workbook_xml, rels)
        overall = archive.read(parts[OVERALL]).decode("utf-8")
    assert _b1_title(overall) == "Pratham Patil | Report | Month Sep-25"
    assert "Web Engage" not in overall
    assert "PublishedFacts!J:J" in overall
