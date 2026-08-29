# packages/db

P1 database package (`dfip_db`):

- `catalog.py` — locked column/KPI/table names
- `paths.py` — migration file locations
- `sql_inspect.py` — SQL structural parser used by tests

V2 Phase 1 adapters (used only when `DATABASE_URL` is set):

- `connection.py` — psycopg 3 pool, transactions, unavailable-database mapping
- `migrate.py` — apply V1 + Phase 1 SQL to PostgreSQL 16 (idempotent ledger)
- `ingest_store.py` / `fact_store.py` / `publication_store.py` / `read_repository.py`
- `catalog_store.py` — V2-C publisher Logic/Labels writes to existing
  `campaign_label_*` / `label_group_*` tables

Canonical DDL lives in `supabase/migrations/`. P3/P4 calculation modules are
unchanged. There is no SQLAlchemy engine and no PostgREST surface.
