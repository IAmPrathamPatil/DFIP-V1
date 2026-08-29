// Dimension: campaign from the published slice. Add campaign_key for the relationship.

let
    Source = PostgreSQL.Database(PgServer, PgDatabase, [CreateNavigationProperties = false]),
    Schema = Source{[Name = "public", Kind = "Schema"]}[Data],
    Dim = Schema{[Name = "rpt_dim_campaign", Kind = "Table"]}[Data],
    WithKey = Table.AddColumn(
        Dim,
        "campaign_key",
        each Text.From([client_id]) & "|" & [campaign_id],
        type text
    )
in
    WithKey
