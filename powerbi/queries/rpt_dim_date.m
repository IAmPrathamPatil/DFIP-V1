// Dimension: calendar. Mark as date table on [day]. Not tenant-owned.

let
    Source = PostgreSQL.Database(PgServer, PgDatabase, [CreateNavigationProperties = false]),
    Schema = Source{[Name = "public", Kind = "Schema"]}[Data],
    Dim = Schema{[Name = "rpt_dim_date", Kind = "Table"]}[Data]
in
    Dim
