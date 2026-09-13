Attribute VB_Name = "DFIPRestoreMeasures"
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
    If Target.DataFields.Count >= 22 Then
        PinService Target
        Exit Sub
    End If
    Restoring = True
    xlHidden = 0
    Do While Target.DataFields.Count > 0
        Target.DataFields.Item(1).Orientation = xlHidden
    Loop
    On Error Resume Next
    Target.CalculatedFields.Add "Failed Rate SM", "=Failed/Sent"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Actual Sent", "=Sent-Failed"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Delivery Rate", "=Delivered/Sent"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Delivered to Imp. rate", "='Unique Impressions'/Delivered"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "CTR (Del to Clicks)", "='Unique Clicks'/Delivered"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "CTR ( Impr. to Click )", "='Unique Clicks'/'Unique Impressions'"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Cost/ UCT conversion", "='Total Cost'/'Unique Click-Through Conversions'"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "UCT conversion rate", "='Unique Click-Through Conversions'/'Unique Clicks'"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Unique Click Through Conv ROAS", "='Click-Through Revenue (INR)'/'Total Cost'"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Cost/Unique Conversion", "='Total Cost'/'Unique Conversions'"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Delivered Thru Conv. rate", "='Unique Conversions'/Delivered"
    On Error GoTo Fail
    On Error Resume Next
    Target.CalculatedFields.Add "Overall ROAS", "='Revenue (INR)'/'Total Cost'"
    On Error GoTo Fail
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Total Cost")
    If field Is Nothing Then Set field = Target.PivotFields("Total Cost")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Sent")
    If field Is Nothing Then Set field = Target.PivotFields("Sent")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Failed")
    If field Is Nothing Then Set field = Target.PivotFields("Failed")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Failed Rate SM")
    If field Is Nothing Then Set field = Target.PivotFields("Failed Rate SM")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Actual Sent")
    If field Is Nothing Then Set field = Target.PivotFields("Actual Sent")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Delivered")
    If field Is Nothing Then Set field = Target.PivotFields("Delivered")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Delivery Rate")
    If field Is Nothing Then Set field = Target.PivotFields("Delivery Rate")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Unique Impressions")
    If field Is Nothing Then Set field = Target.PivotFields("Unique Impressions")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Delivered to Imp. rate")
    If field Is Nothing Then Set field = Target.PivotFields("Delivered to Imp. rate")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Unique Clicks")
    If field Is Nothing Then Set field = Target.PivotFields("Unique Clicks")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("CTR (Del to Clicks)")
    If field Is Nothing Then Set field = Target.PivotFields("CTR (Del to Clicks)")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0.00%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("CTR ( Impr. to Click )")
    If field Is Nothing Then Set field = Target.PivotFields("CTR ( Impr. to Click )")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0.00%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Unique Click-Through Conversions")
    If field Is Nothing Then Set field = Target.PivotFields("Unique Click-Through Conversions")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Click-Through Revenue (INR)")
    If field Is Nothing Then Set field = Target.PivotFields("Click-Through Revenue (INR)")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Cost/ UCT conversion")
    If field Is Nothing Then Set field = Target.PivotFields("Cost/ UCT conversion")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("UCT conversion rate")
    If field Is Nothing Then Set field = Target.PivotFields("UCT conversion rate")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0.0%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Unique Click Through Conv ROAS")
    If field Is Nothing Then Set field = Target.PivotFields("Unique Click Through Conv ROAS")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0.00"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Unique Conversions")
    If field Is Nothing Then Set field = Target.PivotFields("Unique Conversions")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("  Revenue (INR)")
    If field Is Nothing Then Set field = Target.PivotFields("Revenue (INR)")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Cost/Unique Conversion")
    If field Is Nothing Then Set field = Target.PivotFields("Cost/Unique Conversion")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "#,##0"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Delivered Thru Conv. rate")
    If field Is Nothing Then Set field = Target.PivotFields("Delivered Thru Conv. rate")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0.00%"
    End If
    Set field = Nothing
    On Error Resume Next
    Set field = Target.PivotFields("Overall ROAS")
    If field Is Nothing Then Set field = Target.PivotFields("Overall ROAS")
    On Error GoTo Fail
    If Not field Is Nothing Then
        Target.AddDataField field
        Target.DataFields.Item(Target.DataFields.Count).NumberFormat = "0.00"
    End If
    PinService Target
    Restoring = False
    Exit Sub
Fail:
    Restoring = False
End Sub

Private Sub PinService(ByVal Target As PivotTable)
    On Error Resume Next
    If Target.Parent.Name = "Service Campaigns" Then
        Target.PageFields("Filter Logic 1").CurrentPage = "Service | FMS & LMS | Campaigns"
    End If
End Sub
