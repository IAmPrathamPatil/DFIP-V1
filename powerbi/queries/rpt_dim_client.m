// Dimension: client. Tenant rows already filtered by dfip_member_client.

let
    Source = PostgreSQL.Database(PgServer, PgDatabase, [CreateNavigationProperties = false]),
    Schema = Source{[Name = "public", Kind = "Schema"]}[Data],
    Dim = Schema{[Name = "rpt_dim_client", Kind = "Table"]}[Data]
in
    Dim
