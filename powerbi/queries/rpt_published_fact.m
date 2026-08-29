// DFIP Phase 2B — PostgreSQL DirectQuery / Import source.
// Bind only public.rpt_* objects. Do not navigate to working-set tables.
// Create parameters PgServer, PgDatabase before using this query.

let
    Source = PostgreSQL.Database(
        PgServer,
        PgDatabase,
        [
            CreateNavigationProperties = false,
            CommandTimeout = #duration(0, 0, 4, 0)
        ]
    ),
    Schema = Source{[Name = "public", Kind = "Schema"]}[Data],
    Allowed = {"rpt_published_fact", "rpt_dim_date", "rpt_dim_client", "rpt_dim_campaign", "rpt_dim_variation"},
    Tables = Table.SelectRows(Schema, each List.Contains(Allowed, [Name])),
    Fact = Schema{[Name = "rpt_published_fact", Kind = "Table"]}[Data],
    WithCampaignKey = Table.AddColumn(
        Fact,
        "campaign_key",
        each Text.From([client_id]) & "|" & [campaign_id],
        type text
    ),
    WithVariationKey = Table.AddColumn(
        WithCampaignKey,
        "variation_key",
        each Text.From([client_id]) & "|" & [campaign_id] & "|" & [variation_id_key],
        type text
    )
in
    WithVariationKey
