"""RUN 007: native slicer bindings and snapshot-scoped defaults."""

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
from dfip_web.client_workbook import FACT_HEADERS
from dfip_web.daily_report import (
    AMC_GROUP,
    AMC_SPLIT,
    REPORT_CONTRACTS,
    REPORT_SHEET_NAMES,
    SERVICE_FILTER_LOGIC_1,
)
from dfip_web.slicer_defaults import (
    assert_slicer_package,
    drawing_anchor_boxes,
    latest_month_label,
    month_sort_key,
    selected_slicer_values,
    AMC_SPLIT_PIVOT_NAME,
)
from dfip_web.pivot_report import (
    PIVOT_CACHE_PART,
    PIVOT_TABLE_NAMES,
    REFERENCE_SLICER_GEOMETRY,
    assert_native_pivot_package,
)

from test_p11_pivot_report import _published_fact


def _rows_company(*, month: str, start: str, group: str, channel: str = "WhatsApp"):
    return [
        _published_fact(
            month_label=month,
            month_start=start,
            filter_logic_1_group=group,
            channel=channel,
        )
    ]


def _workbook(rows, *, artifact: str = ARTIFACT_STATIC, client_id: str, code: str):
    return render_client_report_xlsx(
        rows,
        published_at=None,
        client_id=client_id,
        client_name=code,
        client_code=code,
        publication_id=f"pub-{code}",
        artifact=artifact,
        api_base_url="http://127.0.0.1:8000" if artifact == ARTIFACT_REFRESHABLE else None,
    )


def _slicer_caches(body: bytes) -> list[str]:
    with ZipFile(io.BytesIO(body)) as archive:
        names = sorted(
            n for n in archive.namelist() if n.startswith("xl/slicerCaches/") and n.endswith(".xml")
        )
        return [archive.read(n).decode("utf-8") for n in names]


def _shared_items(body: bytes, field: str) -> list[str]:
    with ZipFile(io.BytesIO(body)) as archive:
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
    match = re.search(
        rf'<cacheField name="{re.escape(field)}"[^>]*>\s*<sharedItems[^>]*>(.*?)</sharedItems>',
        cache,
        re.DOTALL,
    )
    if match is None:
        return []
    return re.findall(r'<s v="([^"]*)"/>', match.group(1))


def _month_shared(body: bytes) -> list[str]:
    return _shared_items(body, "Month")


def _selected_indexes(xml: str) -> list[int]:
    selected = []
    for item in re.findall(r"<i ([^/]*)/>", xml):
        if 's="1"' not in item:
            continue
        x = re.search(r'x="(\d+)"', item)
        if x:
            selected.append(int(x.group(1)))
    return selected


def test_required_slicers_bind_to_fact_headers() -> None:
    body = _workbook(
        _rows_company(month="Aug-25", start="2025-08-01", group="Group2"),
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    assert_native_pivot_package(body)
    assert_slicer_package(body)
    caches = _slicer_caches(body)
    assert len(caches) == 35
    names = []
    sources = []
    pivots = []
    for xml in caches:
        names.append(re.search(r' name="([^"]+)"', xml).group(1))
        sources.append(re.search(r'sourceName="([^"]+)"', xml).group(1))
        pivots.append(re.search(r'name="(Pivot[^"]+)"', xml).group(1))
        assert "Field" not in sources[-1] or not re.match(r"Field\d+$", sources[-1])
        assert sources[-1] in FACT_HEADERS
        assert sources[-1] != "Field2"
        assert xml.count("<pivotTable ") == 1
    assert len(set(names)) == 35
    assert set(pivots) <= set(PIVOT_TABLE_NAMES.values())
    assert sources.count("Filter Logic 1") == 9
    assert sources.count("Channel") == 9
    assert sources.count("filter_logic_1_group") == 9
    assert sources.count("Month") == 6
    assert sources.count("AMC Device Category -  Filter Logic 4") == 1
    assert sources.count("AMC Product Cat -  Filter Logic 5") == 1
    assert len(FACT_VALUE_FIELDS) == 45
    assert len(FACT_HEADERS) == 45
    with ZipFile(io.BytesIO(body)) as archive:
        assert "Calibri" not in archive.read("xl/styles.xml").decode("utf-8")
        assert len([n for n in archive.namelist() if n.startswith("xl/slicers/")]) == 9
        rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        cache_files = {
            n.rsplit("/", 1)[-1]
            for n in archive.namelist()
            if n.startswith("xl/slicerCaches/") and n.endswith(".xml")
        }
        assert set(re.findall(r'Target="slicerCaches/([^"]+)"', rels)) == cache_files
        for index, name in enumerate(PIVOT_TABLE_NAMES.values(), start=1):
            table = archive.read(f"xl/pivotTables/pivotTable{index}.xml").decode("utf-8")
            assert f'name="{name}"' in table


def test_month_slicer_selects_all_snapshot_months() -> None:
    company_a = _workbook(
        [
            *_rows_company(month="Jul-25", start="2025-07-01", group="Group2"),
            *_rows_company(month="Aug-25", start="2025-08-01", group="Group2"),
        ],
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    company_b = _workbook(
        _rows_company(month="Jun-25", start="2025-06-01", group="Group5", channel="RCS"),
        client_id="b0000000-0000-4000-8000-000000000002",
        code="C2",
    )
    months_a = _month_shared(company_a)
    months_b = _month_shared(company_b)
    assert months_a == ["Jul-25", "Aug-25"]
    assert months_b == ["Jun-25"]
    month_caches_a = [xml for xml in _slicer_caches(company_a) if 'sourceName="Month"' in xml]
    month_caches_b = [xml for xml in _slicer_caches(company_b) if 'sourceName="Month"' in xml]
    assert month_caches_a and month_caches_b
    for xml in month_caches_a:
        selected = _selected_indexes(xml)
        assert selected == list(range(len(months_a)))
    for xml in month_caches_b:
        selected = _selected_indexes(xml)
        assert selected == [0]
    with ZipFile(io.BytesIO(company_a)) as archive:
        custom = archive.read("docProps/custom.xml").decode("utf-8")
    with ZipFile(io.BytesIO(company_b)) as archive:
        custom_b = archive.read("docProps/custom.xml").decode("utf-8")
    assert "a0000000-0000-4000-8000-000000000001" in custom
    assert "b0000000-0000-4000-8000-000000000002" in custom_b
    assert "a0000000-0000-4000-8000-000000000001" not in custom_b


def test_month_slicer_items_are_chronological() -> None:
    body = _workbook(
        [
            *_rows_company(month="Oct-25", start="2025-10-01", group="Group2"),
            *_rows_company(month="Aug-25", start="2025-08-01", group="Group2"),
            *_rows_company(month="Sep-25", start="2025-09-01", group="Group2"),
        ],
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    assert _month_shared(body) == ["Aug-25", "Sep-25", "Oct-25"]
    assert month_sort_key("Aug-25") < month_sort_key("Sep-25") < month_sort_key("Oct-25")
    with ZipFile(io.BytesIO(body)) as archive:
        overall = archive.read("xl/pivotTables/pivotTable1.xml").decode("utf-8")
        amc = archive.read("xl/pivotTables/pivotTable6.xml").decode("utf-8")
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
    month_cache = re.search(r'<cacheField name="Month"[^>]*>', cache)
    assert month_cache is not None
    assert 'numFmtId="17"' in month_cache.group(0)
    assert 'missingItemsLimit="0"' in cache
    assert 'sortType="manual"' in overall
    assert 'numFmtId="17"' in overall
    assert f'name="{AMC_SPLIT_PIVOT_NAME}"' in amc
    assert '<rowFields count="4"><field x="0"/><field x="1"/><field x="9"/><field x="10"/></rowFields>' in amc
    day_fields = re.findall(
        r"<pivotField\b[^>]*/>|<pivotField\b[^>]*>.*?</pivotField>",
        re.search(r"<pivotFields\b[^>]*>.*?</pivotFields>", amc, re.DOTALL).group(0),
        re.DOTALL,
    )
    assert 'axis="axisRow"' in day_fields[10]
    grains = {contract.name: contract.grain for contract in REPORT_CONTRACTS}
    assert grains[AMC_SPLIT] == ("Filter Logic 1", "Filter Logic 2", "Month")


def test_slicers_follow_fy2026_geometry() -> None:
    body = _workbook(
        [
            *_rows_company(month="Aug-25", start="2025-08-01", group="Group2"),
            *_rows_company(month="Sep-25", start="2025-09-01", group="Group2"),
        ],
        artifact=ARTIFACT_REFRESHABLE,
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    boxes = drawing_anchor_boxes(body)
    assert len(boxes) == 35
    by_part: dict[str, list[tuple[int, int, int, int]]] = {}
    for part, from_col, from_row, to_col, to_row in boxes:
        by_part.setdefault(part, []).append((from_col, from_row, to_col, to_row))
        assert from_col >= 26, part
        assert to_col >= 29, part
        assert from_col < to_col
        assert from_row < to_row
    drawings = sorted(by_part, key=lambda name: int(re.search(r"drawing(\d+)", name).group(1)))
    assert len(drawings) == 9
    for sheet_name, part in zip(REPORT_SHEET_NAMES, drawings, strict=True):
        wanted = [(box[0], box[1], box[2], box[3]) for box in REFERENCE_SLICER_GEOMETRY[sheet_name]]
        assert by_part[part] == wanted, sheet_name
    assert_slicer_package(body)
    assert_native_pivot_package(body)


def test_missing_default_falls_back_to_all() -> None:
    rows = _rows_company(month="Aug-25", start="2025-08-01", group="Group2")
    assert AMC_GROUP not in {row["filter_logic_1_group"] for row in rows}
    selected = selected_slicer_values(
        "filter_logic_1_group",
        contract_name="AMC DayWise",
        uniques=[AMC_GROUP, "Group2"],
        present=["Group2"],
        latest_month="Aug-25",
    )
    assert selected == (AMC_GROUP, "Group2")
    selected_ok = selected_slicer_values(
        "Filter Logic 1",
        contract_name="Service Campaigns",
        uniques=[SERVICE_FILTER_LOGIC_1, "Other"],
        present=[SERVICE_FILTER_LOGIC_1],
        latest_month="Aug-25",
    )
    assert selected_ok == (SERVICE_FILTER_LOGIC_1, "Other")
    assert latest_month_label(rows) == "Aug-25"
    body = _workbook(
        rows,
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    amc_group = [
        xml
        for xml in _slicer_caches(body)
        if 'sourceName="filter_logic_1_group"' in xml and "PivotAmcDaywise" in xml
    ]
    assert amc_group
    selected = _selected_indexes(amc_group[0])
    items = re.findall(r"<i ([^/]*)/>", amc_group[0])
    assert selected == list(range(len(items)))


def test_company_slicer_values_are_tenant_scoped() -> None:
    company_a = _workbook(
        _rows_company(month="Aug-25", start="2025-08-01", group="Group2", channel="WhatsApp"),
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    company_b = _workbook(
        _rows_company(month="Jun-25", start="2025-06-01", group="Group5", channel="RCS"),
        client_id="b0000000-0000-4000-8000-000000000002",
        code="C2",
    )
    new_company = _workbook(
        _rows_company(month="Sep-25", start="2025-09-01", group="Group2", channel="Email"),
        client_id="c0000000-0000-4000-8000-000000000003",
        code="NEWCO",
    )
    assert_slicer_package(company_a)
    assert_slicer_package(company_b)
    assert_slicer_package(new_company)
    assert _shared_items(company_a, "Channel") == ["WhatsApp"]
    assert _shared_items(company_b, "Channel") == ["RCS"]
    assert _shared_items(new_company, "Channel") == ["Email"]
    assert _shared_items(new_company, "Month") == ["Sep-25"]
    months_n = [xml for xml in _slicer_caches(new_company) if 'sourceName="Month"' in xml]
    assert months_n
    assert all(_selected_indexes(xml) == [0] for xml in months_n)


def test_static_and_refreshable_share_slicer_defaults() -> None:
    rows = [
        *_rows_company(month="Jul-25", start="2025-07-01", group="Group2"),
        *_rows_company(month="Aug-25", start="2025-08-01", group="Group2"),
    ]
    static = _workbook(
        rows, artifact=ARTIFACT_STATIC, client_id="a0000000-0000-4000-8000-000000000001", code="C1"
    )
    refreshable = _workbook(
        rows,
        artifact=ARTIFACT_REFRESHABLE,
        client_id="a0000000-0000-4000-8000-000000000001",
        code="C1",
    )
    assert_slicer_package(static)
    assert_slicer_package(refreshable)
    months_s = [xml for xml in _slicer_caches(static) if 'sourceName="Month"' in xml]
    months_r = [xml for xml in _slicer_caches(refreshable) if 'sourceName="Month"' in xml]
    assert [_selected_indexes(xml) for xml in months_s] == [
        _selected_indexes(xml) for xml in months_r
    ]
    with ZipFile(io.BytesIO(refreshable)) as archive:
        names = set(archive.namelist())
        assert "xl/queryTables/queryTable1.xml" in names
        settings = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert "BearerToken" not in settings
    assert "local-publisher-pass" not in settings


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


def _slicer_cache_by_name(workbook, name: str):
    for index in range(1, workbook.SlicerCaches.Count + 1):
        cache = workbook.SlicerCaches.Item(index)
        if str(cache.Name) == name:
            return cache
    raise AssertionError(f"missing slicer cache {name}")


def _select_slicer_item(cache, wanted: str) -> None:
    items = cache.SlicerItems
    names = [str(items.Item(index).Name) for index in range(1, items.Count + 1)]
    assert wanted in names, names
    items.Item(names.index(wanted) + 1).Selected = True
    for index, name in enumerate(names, start=1):
        if name != wanted:
            items.Item(index).Selected = False
    selected = [
        str(items.Item(index).Name)
        for index in range(1, items.Count + 1)
        if bool(items.Item(index).Selected)
    ]
    assert selected == [wanted], selected


def _pivot_sent(pivot) -> float:
    names = [
        str(pivot.DataFields.Item(index).Name) for index in range(1, pivot.DataFields.Count + 1)
    ]
    sent_name = next((name for name in names if name.strip() == "Sent"), None)
    if sent_name is None:
        raise AssertionError(f"Sent data field not readable: {names}")
    cell = pivot.GetPivotData(sent_name)
    value = getattr(cell, "Value2", None)
    if value is None:
        value = cell.Value
    return float(value)


def test_desktop_excel_slicers_save_reopen(tmp_path) -> None:
    """Open, filter a native slicer, PivotCache.Refresh, save/reopen. Not live Refresh All."""
    static_path = tmp_path / "run007_static.xlsx"
    refresh_path = tmp_path / "run007_refreshable.xlsm"
    rows = [
        *_rows_company(month="Jul-25", start="2025-07-01", group="Group2", channel="WhatsApp"),
        *_rows_company(month="Aug-25", start="2025-08-01", group="Group2", channel="RCS"),
    ]
    rows[1] = dict(rows[1])
    rows[1]["sent"] = 90
    static_path.write_bytes(
        _workbook(rows, client_id="a0000000-0000-4000-8000-000000000001", code="C1")
    )
    refresh_path.write_bytes(
        _workbook(
            rows,
            artifact=ARTIFACT_REFRESHABLE,
            client_id="a0000000-0000-4000-8000-000000000001",
            code="C1",
        )
    )
    excel = _dispatch_excel()
    try:
        for path in (static_path, refresh_path):
            workbook = excel.Workbooks.Open(str(path.resolve()), UpdateLinks=0, ReadOnly=False)
            assert workbook.Worksheets.Count == 11
            assert workbook.SlicerCaches.Count == 35
            assert sum(int(ws.PivotTables().Count) for ws in workbook.Worksheets) == 9
            overall = workbook.Worksheets(REPORT_SHEET_NAMES[0]).PivotTables(1)
            overall.PivotCache().Refresh()
            assert workbook.SlicerCaches.Count == 35
            channel = _slicer_cache_by_name(workbook, "Slicer_Channel_PivotOverall")
            assert channel.PivotTables.Count == 1
            _select_slicer_item(channel, "WhatsApp")
            whatsapp = _pivot_sent(overall)
            _select_slicer_item(channel, "RCS")
            rcs = _pivot_sent(overall)
            assert whatsapp == 10
            assert rcs == 90
            sub_split = _slicer_cache_by_name(workbook, "Slicer_Channel_PivotSubSplit")
            selected_sub = [
                str(sub_split.SlicerItems.Item(index).Name)
                for index in range(1, sub_split.SlicerItems.Count + 1)
                if bool(sub_split.SlicerItems.Item(index).Selected)
            ]
            assert set(selected_sub) == {"WhatsApp", "RCS"}
            overall.PivotCache().Refresh()
            assert workbook.SlicerCaches.Count == 35
            _select_slicer_item(
                _slicer_cache_by_name(workbook, "Slicer_Channel_PivotOverall"),
                "WhatsApp",
            )
            saved = path.with_name(path.stem + "_reopen" + path.suffix)
            workbook.SaveAs(str(saved.resolve()))
            workbook.Close(False)
            reopened = excel.Workbooks.Open(str(saved.resolve()), UpdateLinks=0, ReadOnly=True)
            assert reopened.SlicerCaches.Count == 35
            assert sum(int(ws.PivotTables().Count) for ws in reopened.Worksheets) == 9
            reopened_channel = _slicer_cache_by_name(reopened, "Slicer_Channel_PivotOverall")
            assert reopened_channel.SlicerItems.Count >= 1
            reopened.Close(False)
    finally:
        try:
            excel.Quit()
        except Exception:
            pass
        time.sleep(0.4)
