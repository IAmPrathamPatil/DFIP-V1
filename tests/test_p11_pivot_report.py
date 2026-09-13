"""P11 native PivotTable, slicer, and publication-bound cache contracts."""

from __future__ import annotations

import io
import re
import shutil
import time
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest
from dfip_web.client_report_download import FACT_VALUE_FIELDS, render_client_report_xlsx
from dfip_web.client_workbook import XLSX_PATH
from dfip_web.daily_report import (
    AMC_GROUP,
    D2C_GROUP,
    REPORT_CONTRACTS,
    REPORT_SHEET_NAMES,
    SERVICE,
    SERVICE_FILTER_LOGIC_1,
    aggregate_published_rows,
    attach_daily_report_sheets,
)
from dfip_web.pivot_report import (
    PIVOT_CACHE_ID,
    PIVOT_CACHE_PART,
    PIVOT_TABLE_NAMES,
    SLICER_PIVOT_CACHE_ID,
    apply_page_filter_defaults_excel,
    apply_page_filter_defaults_xml,
    assert_native_pivot_package,
    bind_pivot_cache_for_snapshot,
    bindings_from_workbook,
    cache_field_index,
    calculated_cache_fields,
    page_filter_uniques_from_rows,
    pivot_cache_definition_xml,
)

from test_p7_publication import FORBIDDEN_TEMPLATE_TOKENS

ROOT = Path(__file__).resolve().parents[1]


def _fact(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Channel": "WhatsApp",
        "Month": "Aug-25",
        "Day": "Aug 1",
        "Filter Logic 1": SERVICE_FILTER_LOGIC_1,
        "Filter Logic 2": "Email One off",
        "Campaign Name": "Camp A",
        "filter_logic_1_group": "Group2",
        "Sent": 10,
        "Failed": 0,
        "Delivered": 10,
        "Unique Impressions": 8,
        "Unique Clicks": 4,
        "Unique Conversions": 1,
        "Unique Click-Through Conversions": 1,
        "Total Cost": "2",
        "Revenue (INR)": "4",
        "Click-Through Revenue (INR)": "3",
        "AMC Device Category -  Filter Logic 4": "",
        "AMC Product Cat -  Filter Logic 5": "",
    }
    row.update(overrides)
    return row


def _published_fact(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {field: None for field in FACT_VALUE_FIELDS}
    row.update(
        {
            "filter_logic_1": SERVICE_FILTER_LOGIC_1,
            "filter_logic_2": "Email One off",
            "total_cost": "2",
            "month_label": "Aug-25",
            "day": "Aug 1",
            "campaign_name": "Camp A",
            "channel": "WhatsApp",
            "sent": 10,
            "failed": 0,
            "delivered": 10,
            "unique_impressions": 8,
            "unique_clicks": 4,
            "unique_conversions": 1,
            "unique_click_through_conversions": 1,
            "revenue_inr": "4",
            "click_through_revenue_inr": "3",
            "filter_logic_1_group": "Group2",
        }
    )
    row.update(overrides)
    return row


def test_shared_cache_and_nine_named_pivots() -> None:
    body = XLSX_PATH.read_bytes()
    assert_native_pivot_package(body)
    with ZipFile(XLSX_PATH) as archive:
        names = set(archive.namelist())
        assert "xl/calcChain.xml" not in names
        types = archive.read("[Content_Types].xml").decode("utf-8")
        assert "/xl/calcChain.xml" not in types
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        assert f'cacheId="{PIVOT_CACHE_ID}"' in workbook
        assert 'cacheSource type="worksheet"' in cache
        assert 'sheet="PublishedFacts"' in cache
        assert "connectionId" not in cache
        assert f'pivotCacheId="{SLICER_PIVOT_CACHE_ID}"' in cache
        assert 'caption="Filter Logic 1_2"' in cache
        assert 'recordCount="0"' in cache
        assert "minValue" not in cache
        assert "containsNumber" not in cache
        assert "Group7" in cache
        assert "Service | FMS" in cache
        assert cache.count("<sharedItems/>") == 43
        formulas = dict(calculated_cache_fields())
        assert formulas["Failed Rate SM"] == "Failed/Sent"
        assert formulas["Actual Sent"] == "Sent-Failed"
        assert formulas["Overall ROAS"] == "'Revenue (INR)'/'Total Cost'"
        for _name, formula in formulas.items():
            assert f'formula="{formula}"' in cache
        for index, contract in enumerate(REPORT_CONTRACTS, start=1):
            table = archive.read(f"xl/pivotTables/pivotTable{index}.xml").decode("utf-8")
            assert f'name="{PIVOT_TABLE_NAMES[contract.name]}"' in table
            assert f'cacheId="{PIVOT_CACHE_ID}"' in table
            assert 'outline="1"' in table
            assert 'indent="0"' in table
            for header in contract.display_fields:
                assert f'<field x="{cache_field_index(header)}"/>' in table
            for header in ("Total Cost", "Revenue (INR)", "Overall ROAS", "Failed Rate SM"):
                assert f'fld="{cache_field_index(header)}"' in table
            order = re.findall(
                r"<(location|pivotFields|rowFields|rowItems|colFields|colItems|"
                r"pageFields|dataFields)\b",
                table,
            )
            assert order.index("rowItems") == order.index("rowFields") + 1
            assert order.index("colItems") == order.index("colFields") + 1
            assert "r:id=" in table
            row_items = re.search(r"<rowItems.*?</rowItems>", table)
            assert row_items is not None
            assert 't="grand"' in row_items.group(0)
            assert 't="data"' not in row_items.group(0)


def test_slicers_are_independent_and_connected() -> None:
    with ZipFile(XLSX_PATH) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        bindings = bindings_from_workbook(workbook, rels)
        assert len(bindings) == 9
        seen_caches: set[str] = set()
        for binding in bindings:
            slicer = archive.read(binding.slicer_part).decode("utf-8")
            drawing = archive.read(binding.drawing_part).decode("utf-8")
            parent, name = binding.sheet_part.rsplit("/", 1)
            sheet_rels = archive.read(f"{parent}/_rels/{name}.rels").decode("utf-8")
            assert "../pivotTables/" in sheet_rels
            assert "../slicers/" in sheet_rels
            assert "../drawings/" in sheet_rels
            for field in binding.contract.slicers:
                assert 'cache="Slicer_' in slicer
                assert field in slicer
                assert f'name="{field} {binding.pivot_name}"' in drawing
                seen_caches.add(f"{binding.pivot_name}:{field}")
        assert len(seen_caches) == 35
        for index in range(1, 36):
            cache = archive.read(f"xl/slicerCaches/slicerCache{index}.xml").decode("utf-8")
            assert f'pivotCacheId="{SLICER_PIVOT_CACHE_ID}"' in cache
            assert "tabId=" in cache
            assert any(name in cache for name in PIVOT_TABLE_NAMES.values())


def test_page_fields_match_reference_placement() -> None:
    with ZipFile(XLSX_PATH) as archive:
        service = archive.read("xl/pivotTables/pivotTable9.xml").decode("utf-8")
        amc = archive.read("xl/pivotTables/pivotTable5.xml").decode("utf-8")
        d2c = archive.read("xl/pivotTables/pivotTable8.xml").decode("utf-8")
        overall = archive.read("xl/pivotTables/pivotTable1.xml").decode("utf-8")
    assert f'fld="{cache_field_index("Filter Logic 1")}"' in service
    assert f'fld="{cache_field_index("Filter Logic 2")}"' in service
    assert "pageFields" in service
    service_pages = re.search(r"<pageFields.*?</pageFields>", service)
    assert service_pages is not None
    assert f'fld="{cache_field_index("Filter Logic 1")}" item="0"' in service_pages.group(0)
    assert 'type="captionEqual"' in service
    assert "Service | FMS" in service
    assert 'type="captionEqual"' in amc
    assert "Group7" in amc
    assert f'fld="{cache_field_index("Filter Logic 1_2")}"' in amc
    amc_pages = re.search(r"<pageFields.*?</pageFields>", amc)
    assert amc_pages is not None
    assert 'item="6"' in amc_pages.group(0)
    assert 'type="captionEqual"' in d2c
    assert "Group5" in d2c
    assert f'fld="{cache_field_index("Filter Logic 1_2")}"' in d2c
    d2c_pages = re.search(r"<pageFields.*?</pageFields>", d2c)
    assert d2c_pages is not None
    assert 'item="4"' in d2c_pages.group(0)
    assert "<pageFields" not in overall
    assert REPORT_CONTRACTS[8].name == SERVICE
    assert REPORT_CONTRACTS[8].default_filter_logic_1 == SERVICE_FILTER_LOGIC_1
    assert REPORT_CONTRACTS[4].default_group == AMC_GROUP
    assert REPORT_CONTRACTS[7].default_group == D2C_GROUP


def test_p8_snapshot_rebinds_cache_to_publishedfacts_cells() -> None:
    body = render_client_report_xlsx([], published_at=None)
    assert_native_pivot_package(body)
    with ZipFile(io.BytesIO(body)) as archive:
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
        connections = archive.read("xl/connections.xml").decode("utf-8")
        assert not any(name.startswith("xl/queryTables/") for name in archive.namelist())
    assert 'cacheSource type="worksheet"' in cache
    assert 'sheet="PublishedFacts"' in cache
    assert 'keepAlive="0"' in connections
    assert 'refreshOnLoad="0"' in connections
    assert 'refreshOnLoad="0"' in cache
    with ZipFile(XLSX_PATH) as archive:
        live = archive.read(PIVOT_CACHE_PART).decode("utf-8")
    rebound = bind_pivot_cache_for_snapshot(live, 50286)
    assert "A1:AS50287" in rebound
    assert "connectionId" not in rebound
    assert rebound.count("</cacheSource>") == 1
    from dfip_web.pivot_report import bind_pivot_cache_for_refreshable

    growing = bind_pivot_cache_for_refreshable(live)
    assert 'name="ExternalData_1"' in growing
    assert "worksheetSource ref=" not in growing
    assert "A1:AS1048576" not in growing
    assert 'refreshOnLoad="0"' in growing
    assert 'recordCount="0"' in growing
    from dfip_web.pivot_report import bind_refreshable_query_defined_name

    named = bind_refreshable_query_defined_name(
        '<definedNames><definedName name="ExternalData_1" localSheetId="0">'
        "PublishedFacts!$A$1</definedName></definedNames>",
        29129,
    )
    assert "PublishedFacts!$A$1:$AS$29130" in named
    assert "PublishedFacts!$A$1</definedName>" not in named


def test_p10_reconstruction_still_matches_client_kpis() -> None:
    overall = REPORT_CONTRACTS[0]
    amc_split = REPORT_CONTRACTS[5]
    d2c = REPORT_CONTRACTS[7]
    service = REPORT_CONTRACTS[8]
    sub = REPORT_CONTRACTS[3]
    rows = [
        _fact(),
        _fact(Day="Aug 2", Sent=5, Delivered=5, **{"Unique Clicks": 1}),
        _fact(
            **{
                "Filter Logic 1": "TAMC | D2C AMC|Manual Campaign",
                "filter_logic_1_group": AMC_GROUP,
                "Campaign Name": "AMC Camp",
            }
        ),
        _fact(
            **{
                "Filter Logic 1": "D2C Product| CLTV | Campaigns",
                "filter_logic_1_group": D2C_GROUP,
                "Filter Logic 2": "renewal t-02",
            }
        ),
    ]
    overall_kpis = aggregate_published_rows(rows, overall)
    assert overall_kpis["actual_sent"] == Decimal("35")
    service_kpis = aggregate_published_rows(rows, service)
    assert service_kpis["actual_sent"] == Decimal("15")
    amc_kpis = aggregate_published_rows(rows, amc_split)
    assert amc_kpis["actual_sent"] == Decimal("10")
    d2c_kpis = aggregate_published_rows(rows, d2c)
    assert d2c_kpis["actual_sent"] == Decimal("10")
    sub_kpis = aggregate_published_rows(rows, sub)
    assert sub_kpis["actual_sent"] == Decimal("35")


def test_attach_is_idempotent_and_token_free(tmp_path: Path) -> None:
    first = tmp_path / "p11a.xlsx"
    second = tmp_path / "p11b.xlsx"
    attach_daily_report_sheets(XLSX_PATH, first)
    attach_daily_report_sheets(first, second)
    assert_native_pivot_package(first.read_bytes())
    assert_native_pivot_package(second.read_bytes())
    text = second.read_bytes().decode("latin-1")
    for token in FORBIDDEN_TEMPLATE_TOKENS:
        assert token not in text
    assert "DFIP_DEV_AUTH_TOKEN" not in text
    assert "DFIP_AUTH_SECRET" not in text


def test_documentation_describes_native_pivots() -> None:
    docs = (ROOT / "documentation" / "DAILY_REPORT.md").read_text(encoding="utf-8")
    assert "PivotTable" in docs
    assert "slicer" in docs.lower()
    status = (ROOT / "documentation" / "STATUS.md").read_text(encoding="utf-8")
    assert "P11" in status
    assert "no P12" in status.lower() or "P12" in status


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


def test_desktop_excel_opens_nine_pivots_and_reopens(tmp_path: Path) -> None:
    """Object-model acceptance: Excel opens the template as nine PivotTables.

    Does not call Refresh All. Ribbon Analyze/Design is the UI for these
    objects; a click-check on the local acceptance copy remains Prompt 2.
    """
    copy = tmp_path / "Client_Report.xlsx"
    shutil.copyfile(XLSX_PATH, copy)
    excel = _dispatch_excel()
    try:
        workbook = excel.Workbooks.Open(str(copy.resolve()), UpdateLinks=0, ReadOnly=False)
        names = []
        slicer_shapes = 0
        for sheet_name in REPORT_SHEET_NAMES:
            sheet = workbook.Worksheets(sheet_name)
            tables = sheet.PivotTables()
            assert tables.Count == 1, sheet_name
            pivot = tables.Item(1)
            names.append(str(pivot.Name))
            assert pivot.PivotFields().Count >= 1, sheet_name
            slicer_shapes += int(sheet.Shapes.Count)
        assert set(names) == set(PIVOT_TABLE_NAMES.values())
        assert slicer_shapes >= 9
        saved = tmp_path / "Client_Report_reopen.xlsx"
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


def test_p8_snapshot_excel_opens_with_publication_bound_cache(tmp_path: Path) -> None:
    body = render_client_report_xlsx([_fact()], published_at=None)
    dest = tmp_path / "snapshot.xlsx"
    dest.write_bytes(body)
    excel = _dispatch_excel()
    try:
        workbook = excel.Workbooks.Open(str(dest.resolve()), UpdateLinks=0, ReadOnly=True)
        assert workbook.Worksheets.Count == 11
        assert sum(int(ws.PivotTables().Count) for ws in workbook.Worksheets) == 9
        workbook.Close(False)
    finally:
        try:
            excel.Quit()
        except Exception:
            pass
        time.sleep(0.4)


def test_generated_cache_seeds_only_page_filter_items() -> None:
    cache = pivot_cache_definition_xml()
    assert 'recordCount="0"' in cache
    assert "minValue" not in cache
    assert "containsNumber" not in cache
    assert "Group7" in cache
    assert "Service | FMS" in cache
    assert cache.count("<sharedItems/>") == 43
    assert 'databaseField="0"' in cache


def test_snapshot_page_fields_use_first_seen_item_indexes() -> None:
    rows = [
        _published_fact(),
        _published_fact(
            filter_logic_1="TAMC | D2C AMC|Manual Campaign",
            filter_logic_1_group=AMC_GROUP,
            sent=40,
        ),
        _published_fact(
            filter_logic_1="D2C Product| CLTV | Campaigns",
            filter_logic_1_group=D2C_GROUP,
            sent=30,
        ),
    ]
    groups, fl1 = page_filter_uniques_from_rows(rows)
    assert groups.index(AMC_GROUP) == 1
    assert groups.index(D2C_GROUP) == 2
    assert fl1[0] == SERVICE_FILTER_LOGIC_1
    body = render_client_report_xlsx(rows, published_at=None)
    with ZipFile(io.BytesIO(body)) as archive:
        amc = archive.read("xl/pivotTables/pivotTable6.xml").decode("utf-8")
        d2c = archive.read("xl/pivotTables/pivotTable8.xml").decode("utf-8")
        service = archive.read("xl/pivotTables/pivotTable9.xml").decode("utf-8")
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
    assert f'item="{groups.index(AMC_GROUP)}"' in amc
    assert f'item="{groups.index(D2C_GROUP)}"' in d2c
    assert f'fld="{cache_field_index("Filter Logic 1")}"' in service
    assert f'fld="{cache_field_index("Filter Logic 1")}" item="' not in service
    assert "Service | FMS" in service
    assert "LMS | Campaigns" in service
    assert AMC_GROUP in cache
    assert D2C_GROUP in cache
    assert apply_page_filter_defaults_xml(body, []) == body


def test_desktop_excel_keeps_page_defaults_after_refresh(tmp_path: Path) -> None:
    """Excel COM acceptance: page-filter CurrentPage survives cache refresh.

    Portable suites (Linux/Docker) skip. Windows + Microsoft Excel runs this.
    """
    import sys

    if sys.platform != "win32":
        pytest.skip("Excel COM acceptance requires Windows and Microsoft Excel")
    pytest.importorskip("win32com.client")
    dest = tmp_path / "page_defaults.xlsx"
    dest.write_bytes(
        render_client_report_xlsx(
            [
                _published_fact(sent=100),
                _published_fact(
                    filter_logic_1="TAMC | D2C AMC|Manual Campaign",
                    filter_logic_1_group=AMC_GROUP,
                    campaign_name="AMC Camp",
                    sent=40,
                ),
                _published_fact(
                    filter_logic_1="D2C Product| CLTV | Campaigns",
                    filter_logic_1_group=D2C_GROUP,
                    sent=30,
                ),
            ],
            published_at=None,
        )
    )
    apply_page_filter_defaults_excel(dest)
    excel = _dispatch_excel()
    try:
        workbook = excel.Workbooks.Open(str(dest.resolve()), UpdateLinks=0, ReadOnly=False)
        amc = workbook.Worksheets(REPORT_SHEET_NAMES[5]).PivotTables(1)
        d2c = workbook.Worksheets(REPORT_SHEET_NAMES[7]).PivotTables(1)
        service = workbook.Worksheets(REPORT_SHEET_NAMES[8]).PivotTables(1)
        assert str(amc.PageFields.Item(1).CurrentPage) == AMC_GROUP
        assert str(d2c.PageFields.Item(1).CurrentPage) == D2C_GROUP
        assert str(service.PageFields.Item(1).CurrentPage) == SERVICE_FILTER_LOGIC_1
        assert str(amc.GetPivotData("  Sent")) in {"40", "40.0"}
        assert str(d2c.GetPivotData("  Sent")) in {"30", "30.0"}
        assert str(service.GetPivotData("  Sent")) in {"100", "100.0"}
        workbook.Worksheets(REPORT_SHEET_NAMES[0]).PivotTables(1).PivotCache().Refresh()
        assert str(amc.PageFields.Item(1).CurrentPage) == AMC_GROUP
        assert str(d2c.PageFields.Item(1).CurrentPage) == D2C_GROUP
        assert str(service.PageFields.Item(1).CurrentPage) == SERVICE_FILTER_LOGIC_1
        saved = tmp_path / "page_defaults_reopen.xlsx"
        workbook.SaveAs(str(saved.resolve()))
        workbook.Close(False)
        reopened = excel.Workbooks.Open(str(saved.resolve()), UpdateLinks=0, ReadOnly=False)
        reopened.Worksheets(REPORT_SHEET_NAMES[0]).PivotTables(1).PivotCache().Refresh()
        amc2 = reopened.Worksheets(REPORT_SHEET_NAMES[5]).PivotTables(1)
        d2c2 = reopened.Worksheets(REPORT_SHEET_NAMES[7]).PivotTables(1)
        svc2 = reopened.Worksheets(REPORT_SHEET_NAMES[8]).PivotTables(1)
        assert str(amc2.PageFields.Item(1).CurrentPage) == AMC_GROUP
        assert str(d2c2.PageFields.Item(1).CurrentPage) == D2C_GROUP
        assert str(svc2.PageFields.Item(1).CurrentPage) == SERVICE_FILTER_LOGIC_1
        assert str(amc2.GetPivotData("  Sent")) in {"40", "40.0"}
        assert sum(int(ws.PivotTables().Count) for ws in reopened.Worksheets) == 9
        assert sum(int(ws.Shapes.Count) for ws in reopened.Worksheets) >= 9
        reopened.Close(False)
    finally:
        try:
            excel.Quit()
        except Exception:
            pass
        time.sleep(0.4)


def test_desktop_excel_refreshes_populated_snapshot(tmp_path: Path) -> None:
    """Populated worksheet-backed cache must refresh without killing Excel."""
    dest = tmp_path / "populated_snapshot.xlsx"
    dest.write_bytes(render_client_report_xlsx([_published_fact()], published_at=None))
    excel = _dispatch_excel()
    try:
        workbook = excel.Workbooks.Open(str(dest.resolve()), UpdateLinks=0, ReadOnly=False)
        assert workbook.Worksheets.Count == 11
        assert sum(int(ws.PivotTables().Count) for ws in workbook.Worksheets) == 9
        slicer_shapes = sum(int(ws.Shapes.Count) for ws in workbook.Worksheets)
        assert slicer_shapes >= 9
        pivot = workbook.Worksheets(REPORT_SHEET_NAMES[0]).PivotTables(1)
        pivot.PivotCache().Refresh()
        assert str(pivot.GetPivotData("  Sent")) in {"10", "10.0"}
        assert sum(int(ws.PivotTables().Count) for ws in workbook.Worksheets) == 9
        saved = tmp_path / "populated_snapshot_reopen.xlsx"
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
