# supabase

Home of DFIP PostgreSQL/Supabase migrations (schema artifact).

```
supabase/migrations/
  20260823000001_p1_extensions.sql
  20260823000002_p1_tables.sql
  20260823000003_p1_indexes.sql
  20260823000004_p1_seed.sql
  20260823000005_p2_config_versions.sql
  20260823000006_p2_campaign_v1.sql
  20260823000007_p2_campaign_v2.sql
  20260823000008_p2_templates.sql
  20260823000009_p2_client_kpis.sql
  20260823000010_p3_batch_ingest_metadata.sql
  20260823000011_v2_identity.sql
  20260823000012_v2_tenant_denorm.sql
  20260823000013_v2_rls_roles.sql
  20260823000014_v2_analytics_reporting.sql
```

V1 files `20260823000001`–`20260823000010` are locked. Phase 1 appends identity,
per-client SHA uniqueness, tenant `client_id` denormalization, database roles,
and row-level security as defense in depth.

These files are the schema. They do not require a live Supabase project to be
reviewed. Default V1 tests inspect SQL without connecting. Apply them to
PostgreSQL 16 when `DATABASE_URL` is configured:

```text
python -m dfip_db
```

`python -m dfip_db` records applied filenames in `dfip_schema_migration` and
skips them on re-run. It is the same schema hosted Supabase applies from this
folder (`supabase db push` / `supabase migration up`). Point `DATABASE_URL` at
the project's Direct or Session-pooler URI. Do not commit that URI.

This repository does not create a supabase.co project. Operator supplies the
DSN locally (`.env`, never git).

V1 migrations do **not** enable RLS. Phase 1 policies are not a hosted
identity-provider runtime and are not PostgREST. There is still no FK to
`auth.users`. Persistence does not use the service-role key.
