// DFIP P7 — published facts for the client workbook.
// Excel is a reporting + refresh layer, not the processing engine.
// This query calls GET /api/v1/publications/history/facts.csv with Prefer dfip-bearer.
// The result is the company's cumulative published snapshots: later months
// are added on Refresh All; previously published months remain. Newest
// publication wins per campaign/variation/day grain (republish).
// One CSV response replaces JSON paging. JSON GET /history/facts stays available.
// JWT client_id scopes the result. A development token has no client_id; set
// ClientId in the Settings table only for local testing. That is not RLS.
// Result columns are HeaderMap order (FACT_HEADERS). Extra CSV fields are dropped.

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
    RefreshPayload =
        if BearerToken = "" then null
        else try Json.Document(
            Web.Contents(
                ApiBaseUrl,
                [
                    RelativePath = "/api/v1/auth/refresh",
                    Headers = [
                        Prefer = "dfip-bearer=" & BearerToken,
                        Accept = "application/json",
                        #"Content-Type" = "application/json"
                    ],
                    Content = Text.ToBinary("{}"),
                    ManualStatusHandling = {401, 403},
                    Timeout = #duration(0, 0, 0, 30)
                ]
            )
        ) otherwise null,
    AccessToken =
        if RefreshPayload <> null
           and Value.Is(RefreshPayload, type record)
           and Record.HasFields(RefreshPayload, "access_token")
           and RefreshPayload[access_token] <> null
           and Text.Trim(Text.From(RefreshPayload[access_token])) <> ""
        then Text.From(RefreshPayload[access_token])
        else BearerToken,
    QueryWithClient = if ClientId = "" then [] else [client_id = ClientId],
    Bytes = Web.Contents(
        ApiBaseUrl,
        [
            RelativePath = "/api/v1/publications/history/facts.csv",
            Query = QueryWithClient,
            Headers = [
                Prefer = "dfip-bearer=" & AccessToken,
                Accept = "text/csv"
            ],
            Timeout = #duration(0, 0, 1, 30),
            ManualStatusHandling = {401, 403}
        ]
    ),
    JsonProbe = try Json.Document(Bytes) otherwise null,
    Checked =
        if JsonProbe <> null
           and Value.Is(JsonProbe, type record)
           and Record.HasFields(JsonProbe, "error")
        then error Error.Record(
            "AuthenticationFailed",
            "Session expired. Download a new Refreshable Workbook from the website.",
            null
        )
        else Bytes,
    Source = Csv.Document(Checked, [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
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
    CanonicalKeys = Record.FieldNames(HeaderMap),
    DisplayHeaders = Record.FieldValues(HeaderMap),
    Facts =
        if Table.RowCount(Promoted) = 0 then
            #table(DisplayHeaders, {})
        else
            let
                Decimals = List.Accumulate(
                    DecimalFields,
                    Promoted,
                    (state, field) =>
                        if Table.HasColumns(state, {field}) then
                            Table.TransformColumns(state, {{field, ParseDecimal, type any}})
                        else state
                ),
                Renamed = Table.RenameColumns(
                    Decimals,
                    List.Transform(CanonicalKeys, each {_, Record.Field(HeaderMap, _)})
                ),
                Ordered = Table.ReorderColumns(Renamed, DisplayHeaders),
                Chrono = Table.ReorderColumns(
                    Table.RenameColumns(
                        Table.RemoveColumns(
                            Table.AddColumn(
                                Ordered,
                                "MD",
                                each try Date.From([month_start]) otherwise [Month],
                                type date
                            ),
                            {"Month"}
                        ),
                        {{"MD", "Month"}}
                    ),
                    Table.ColumnNames(Ordered)
                )
            in
                Chrono
in
    Facts
