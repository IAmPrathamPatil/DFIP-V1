// Dimension: variation from the published slice.
// Empty variation_id_key is a valid OPEN-A6 member. Do not replace it.

let
    Source = PostgreSQL.Database(PgServer, PgDatabase, [CreateNavigationProperties = false]),
    Schema = Source{[Name = "public", Kind = "Schema"]}[Data],
    Dim = Schema{[Name = "rpt_dim_variation", Kind = "Table"]}[Data],
    WithKey = Table.AddColumn(
        Dim,
        "variation_key",
        each Text.From([client_id]) & "|" & [campaign_id] & "|" & [variation_id_key],
        type text
    )
in
    WithKey
