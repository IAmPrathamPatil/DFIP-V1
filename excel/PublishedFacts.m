// DFIP P7 — published facts for the client workbook.
// Excel is a reporting + refresh layer, not the processing engine.
// This query calls GET /api/v1/publications/current/facts with Bearer auth.
// Page size is the API maximum of 200. Power Query pages until total is consumed.
// JWT client_id scopes the result. A development token has no client_id; set
// ClientId in the Settings table only for local testing. That is not RLS.

let
    SettingsTable = Excel.CurrentWorkbook(){[Name="Settings"]}[Content],
    SettingsKeyed = Table.TransformColumnNames(SettingsTable, Text.Trim),
    SettingValue = (name as text) as text =>
        let
            Matched = Table.SelectRows(SettingsKeyed, each Text.From([Parameter]) = name),
            Raw = if Table.RowCount(Matched) = 0 then "" else Matched{0}[Value],
            TextValue = if Raw = null then "" else Text.Trim(Text.From(Raw))
        in
            TextValue,
    ApiBaseUrl = Text.TrimEnd(SettingValue("ApiBaseUrl"), "/"),
    BearerToken = SettingValue("BearerToken"),
    ClientId = SettingValue("ClientId"),
    PageLimit = 200,
    QueryRecord = [
        limit = Text.From(PageLimit)
    ],
    QueryWithClient = if ClientId = "" then QueryRecord else Record.AddField(QueryRecord, "client_id", ClientId),

    FetchPage = (offset as number) as record =>
        let
            Query = Record.AddField(QueryWithClient, "offset", Text.From(offset)),
            Bytes = Web.Contents(
                ApiBaseUrl,
                [
                    RelativePath = "/api/v1/publications/current/facts",
                    Query = Query,
                    Headers = [
                        Authorization = "Bearer " & BearerToken,
                        Accept = "application/json"
                    ]
                ]
            ),
            Document = Json.Document(Bytes)
        in
            Document,

    ItemsFrom = (payload as record) as list =>
        if payload = null then {}
        else if Record.HasFields(payload, "items") and payload[items] <> null then payload[items]
        else {},

    TotalFrom = (payload as record) as number =>
        try Number.From(payload[pagination][total]) otherwise 0,

    FirstPage = FetchPage(0),
    Total = TotalFrom(FirstPage),
    PageIndexes =
        if Total = 0 then {}
        else {0..Number.RoundDown((Total - 1) / PageLimit)},
    CombinedItems =
        if Total = 0 then {}
        else if Total <= PageLimit then ItemsFrom(FirstPage)
        else List.Combine(List.Transform(PageIndexes, each ItemsFrom(FetchPage(_ * PageLimit)))),

    DecimalFields = {
        "total_cost",
        "revenue_inr",
        "impression_through_revenue_inr",
        "click_through_revenue_inr"
    },
    HeaderMap = [
        filter_logic_1 = "Filter Logic 1",
        filter_logic_2 = "Filter Logic 2",
        template_status = "Template Status",
        amc_status_filter_logic_3 = "AMC Status - Filter Logic 3",
        amc_device_category_filter_logic_4 = "AMC Device Category -  Filter Logic 4",
        amc_product_cat_filter_logic_5 = "AMC Product Cat -  Filter Logic 5",
        manual_or_automated = "Manual Or Automated",
        total_cost = "Total Cost",
        hhh = "HHH",
        month_label = "Month",
        day = "Day",
        campaign_name = "Campaign Name",
        campaign_id = "Campaign ID",
        variation_name = "Variation Name",
        variation_id = "Variation ID",
        channel = "Channel",
        type_of_campaign = "Type of Campaign",
        start_date = "Start Date",
        sent = "Sent",
        failed = "Failed",
        delivered = "Delivered",
        unique_impressions = "Unique Impressions",
        unique_clicks = "Unique Clicks",
        unique_conversions = "Unique Conversions",
        unique_impression_through_conversions = "Unique Impression-Through Conversions",
        unique_click_through_conversions = "Unique Click-Through Conversions",
        revenue_inr = "Revenue (INR)",
        impression_through_revenue_inr = "Impression-Through Revenue (INR)",
        click_through_revenue_inr = "Click-Through Revenue (INR)",
        template_name_whatsapp = "Template Name (WhatsApp)",
        client_id = "client_id",
        variation_id_key = "variation_id_key",
        month_start = "month_start",
        filter_logic_1_group = "filter_logic_1_group",
        label_match_status = "label_match_status",
        template_match_status = "template_match_status",
        rate_card_rule_id = "rate_card_rule_id",
        processing_run_id = "processing_run_id",
        batch_id = "batch_id",
        campaign_label_version_id = "campaign_label_version_id",
        template_label_version_id = "template_label_version_id",
        rate_card_version_id = "rate_card_version_id",
        label_group_version_id = "label_group_version_id",
        first_seen_at = "first_seen_at",
        last_seen_at = "last_seen_at"
    ],
    ParseDecimal = (value as any) as any =>
        if value = null or value = "" then value
        else try Number.FromText(Text.From(value)) otherwise value,

    Facts =
        if List.Count(CombinedItems) = 0 then
            #table(Record.FieldValues(HeaderMap), {})
        else
            let
                AsTable = Table.FromList(CombinedItems, Splitter.SplitByNothing(), {"Row"}),
                Expanded = Table.ExpandRecordColumn(AsTable, "Row", Record.FieldNames(AsTable{0}[Row])),
                Decimals = List.Accumulate(
                    DecimalFields,
                    Expanded,
                    (state, field) =>
                        if Table.HasColumns(state, {field}) then
                            Table.TransformColumns(state, {{field, ParseDecimal, type any}})
                        else state
                ),
                RenamePairs = List.Select(
                    Record.FieldNames(HeaderMap),
                    each Table.HasColumns(Decimals, {_})
                ),
                Renamed = Table.RenameColumns(
                    Decimals,
                    List.Transform(RenamePairs, each {_, Record.Field(HeaderMap, _)})
                )
            in
                Renamed
in
    Facts
