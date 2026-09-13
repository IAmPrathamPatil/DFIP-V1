"""Restore 22 native PivotTable measures after Power Query cache rebuild.

Excel PivotCache.Refresh from a query table drops calculated dataFields
(22 → 10 additive sums). Unique CTC / Unique Conversions survive but shift
left, so the Click-Through / Overall banners at O8 / T8 sit over blanks.
A Workbook_SheetPivotTableUpdate handler re-adds MEASURE_HEADERS in
reference order. Refreshable downloads are therefore .xlsm; static
snapshots stay .xlsx and do not include VBA.
"""

from __future__ import annotations

from pathlib import Path

from dfip_web.client_workbook import EXCEL_DIR
from dfip_web.daily_report import (
    MEASURE_HEADERS,
    MEASURE_OPS,
    SERVICE,
    SERVICE_FILTER_LOGIC_1,
    _next_relationship_id,
    mutate_xlsx,
)
from dfip_web.pivot_report import (
    calculated_cache_fields,
    measure_datafield_display_name,
)
from dfip_web.report_format import datafield_numfmt

VBA_PROJECT_PART = "xl/vbaProject.bin"
VBA_SOURCE_PATH = EXCEL_DIR / "restore_pivot_measures.bas"
VBA_PROJECT_PATH = EXCEL_DIR / "vbaProject.bin"
VBA_REL_TYPE = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
MACRO_WORKBOOK_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
VBA_PROJECT_TYPE = "application/vnd.ms-office.vbaProject"
OPENXML_WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"

_EXCEL_NUMBER_FORMAT = {
    2: "0.00",
    3: "#,##0",
    4: "#,##0.00",
    9: "0%",
    10: "0.00%",
    164: "0.0%",
}


def measure_restore_vba_source() -> str:
    """Standard-module VBA that re-adds the 22 reference dataFields."""
    calc = dict(calculated_cache_fields())
    add_calc: list[str] = []
    add_data: list[str] = []
    for header, kind, *_rest in MEASURE_OPS:
        if kind != "sum":
            formula = calc[header]
            add_calc.append(
                f"    On Error Resume Next\n"
                f'    Target.CalculatedFields.Add "{_vba_str(header)}", "={_vba_str(formula)}"\n'
                f"    On Error GoTo Fail"
            )
        display = measure_datafield_display_name(header)
        fmt = datafield_numfmt(header)
        number_format = _EXCEL_NUMBER_FORMAT.get(fmt or 0, "General") if fmt else None
        add_data.append(
            f"    Set field = Nothing\n"
            f"    On Error Resume Next\n"
            f'    Set field = Target.PivotFields("{_vba_str(display)}")\n'
            f'    If field Is Nothing Then Set field = Target.PivotFields("{_vba_str(header)}")\n'
            f"    On Error GoTo Fail\n"
            f"    If Not field Is Nothing Then\n"
            f"        Target.AddDataField field\n"
            + (
                f'        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "{number_format}"\n'
                if number_format
                else ""
            )
            + f"    End If"
        )
    calc_block = "\n".join(add_calc)
    data_block = "\n".join(add_data)
    return f'''Attribute VB_Name = "DFIPRestoreMeasures"
Option Explicit

Public Restoring As Boolean
Public AppHandler As DFIPAppEvents

Public Sub Auto_Open()
    Set AppHandler = New DFIPAppEvents
    AppHandler.Init
    DisableBackgroundQuery
    On Error Resume Next
    Application.OnTime Now + TimeSerial(0, 0, 2), "DFIPRestoreMeasures.DisableBackgroundQuery"
    On Error GoTo 0
End Sub

Public Sub Auto_Close()
    Set AppHandler = Nothing
End Sub

Public Sub DisableBackgroundQuery()
    Dim cn As Object
    Dim ws As Object
    Dim qt As Object
    Dim lo As Object
    Dim cache As Object
    On Error Resume Next
    ThisWorkbook.Connections("Query - PublishedFacts").OLEDBConnection.BackgroundQuery = False
    ThisWorkbook.Connections("Query - PublishedFacts").OLEDBConnection.RefreshOnFileOpen = False
    For Each cn In ThisWorkbook.Connections
        cn.OLEDBConnection.BackgroundQuery = False
        cn.OLEDBConnection.RefreshOnFileOpen = False
    Next
    For Each ws In ThisWorkbook.Worksheets
        For Each qt In ws.QueryTables
            qt.BackgroundQuery = False
            qt.RefreshOnFileOpen = False
        Next
        For Each lo In ws.ListObjects
            lo.QueryTable.BackgroundQuery = False
            lo.QueryTable.RefreshOnFileOpen = False
        Next
    Next
    For Each cache In ThisWorkbook.PivotCaches
        cache.BackgroundQuery = False
    Next
End Sub

Public Sub RefreshPivotCaches()
    Dim cache As Object
    Dim ws As Object
    Dim pt As Object
    On Error Resume Next
    Application.CalculateUntilAsyncQueriesDone
    For Each cache In ThisWorkbook.PivotCaches
        cache.BackgroundQuery = False
        cache.Refresh
    Next
    For Each ws In ThisWorkbook.Worksheets
        For Each pt In ws.PivotTables
            RestorePivot pt
        Next
    Next
End Sub

Public Sub RestorePivot(ByVal Target As PivotTable)
    Dim field As Object
    Dim xlHidden As Long
    If Restoring Then Exit Sub
    If Target Is Nothing Then Exit Sub
    On Error GoTo Fail
    If Target.DataFields.Count >= {len(MEASURE_HEADERS)} Then
        PinService Target
        Exit Sub
    End If
    Restoring = True
    xlHidden = 0
    Do While Target.DataFields.Count > 0
        Target.DataFields.Item(1).Orientation = xlHidden
    Loop
{calc_block}
{data_block}
    PinService Target
    Restoring = False
    Exit Sub
Fail:
    Restoring = False
End Sub

Private Sub PinService(ByVal Target As PivotTable)
    On Error Resume Next
    If Target.Parent.Name = "{_vba_str(SERVICE)}" Then
        Target.PageFields("Filter Logic 1").CurrentPage = "{_vba_str(SERVICE_FILTER_LOGIC_1)}"
    End If
End Sub
'''


def app_events_vba_source() -> str:
    """Class module: Application events survive copying vbaProject.bin."""
    return """Public WithEvents App As Application
Public WithEvents QT As QueryTable

Public Sub Init()
    Set App = Application
    On Error Resume Next
    Set QT = ThisWorkbook.Worksheets("PublishedFacts").ListObjects(1).QueryTable
    If QT Is Nothing Then Set QT = ThisWorkbook.Worksheets("PublishedFacts").QueryTables(1)
    On Error GoTo 0
    DFIPRestoreMeasures.DisableBackgroundQuery
End Sub

Private Sub QT_BeforeRefresh(ByVal Cancel As Boolean)
    DFIPRestoreMeasures.DisableBackgroundQuery
End Sub

Private Sub App_SheetPivotTableUpdate(ByVal Sh As Object, ByVal Target As PivotTable)
    DFIPRestoreMeasures.RestorePivot Target
End Sub

Private Sub QT_AfterRefresh(ByVal Success As Boolean)
    If Success Then DFIPRestoreMeasures.RefreshPivotCaches
End Sub
"""


def thisworkbook_vba_source() -> str:
    return app_events_vba_source()


def _vba_str(value: str) -> str:
    return value.replace('"', '""')


def write_vba_source(path: Path | None = None) -> Path:
    target = path or VBA_SOURCE_PATH
    target.write_text(measure_restore_vba_source(), encoding="utf-8", newline="\r\n")
    return target


def embed_measure_restore_vba_into(parts: dict[str, bytes]) -> None:
    """Add the measure-restore VBA project to an in-memory package."""
    if not VBA_PROJECT_PATH.is_file():
        raise ValueError("Client report VBA project is missing.")
    vba = VBA_PROJECT_PATH.read_bytes()
    if len(vba) < 1000:
        raise ValueError("Client report VBA project is invalid.")
    types = parts["[Content_Types].xml"].decode("utf-8")
    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    types = types.replace(OPENXML_WORKBOOK_TYPE, MACRO_WORKBOOK_TYPE)
    if VBA_PROJECT_PART not in types and 'PartName="/xl/vbaProject.bin"' not in types:
        types = types.replace(
            "</Types>",
            f'<Override PartName="/{VBA_PROJECT_PART}" ContentType="{VBA_PROJECT_TYPE}"/></Types>',
        )
    if VBA_REL_TYPE not in rels:
        rid = _next_relationship_id(rels)
        rels = rels.replace(
            "</Relationships>",
            f'<Relationship Id="rId{rid}" Type="{VBA_REL_TYPE}" '
            f'Target="vbaProject.bin"/></Relationships>',
        )
    parts["[Content_Types].xml"] = types.encode("utf-8")
    parts["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")
    parts[VBA_PROJECT_PART] = vba


def embed_measure_restore_vba(body: bytes) -> bytes:
    """Turn a refreshable xlsx zip into a macro-enabled workbook with restore VBA."""
    return mutate_xlsx(body, embed_measure_restore_vba_into)
