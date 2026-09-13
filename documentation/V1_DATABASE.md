# DFIP V1 — Database completeness

**Source:** `supabase/migrations/*.sql` plus `dfip_db.migrate`. **Live database:** **UNKNOWN / NOT VERIFIED** (process env `DATABASE_URL` unset; `.env` `DATABASE_URL` empty boolean — no connection attempted, no secrets printed). Object lists are **VERIFIED from SQL text**. Whether a hosted project has applied every file is **UNKNOWN**.

Store methods: [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md). RLS predicates: [V1_RLS.md](V1_RLS.md).

**Apply:** `python -m dfip_db` requires `DATABASE_URL` (`packages/db/dfip_db/__main__.py` → `apply_migrations`). Ledger: `dfip_schema_migration`. Baseline cutoff in migrate.py: `20260823000014` / view `rpt_published_fact` (re-apply safety — **VERIFIED** constants).

**API usage:** Postgres adapters only when `DATABASE_URL` is set. Empty DSN ⇒ in-memory stores; SQL objects unused at runtime.

---

## Migration timeline (current vs historical)

| File | Additive change | Historical vs current |
|---|---|---|
| 000001 | extensions | current |
| 000002 | core tables | current (later ALTERs) |
| 000003 | indexes | current |
| 000004 | seed catalogs/KPIs/client | current seed |
| 000005–000009 | config versions, campaign v1/v2, templates, client KPIs | current |
| 000010 | batch ingest metadata columns | current |
| 000011 | `app_user`, `client_membership` | current |
| 000012 | tenant denorm `client_id` on staging/run + indexes | current |
| 000013 | roles, RLS helpers, policies, `published_fact_campaign_day` | current roles/policies; **view replaced** in 000017 |
| 000014 | `qa_finding`, `rpt_dim_date`, `rpt_*` views, QA RLS | current |
| 000015 | catalog write grants | current |
| 000016 | `processing_run.error_summary`, `progress_at` | current |
| 000017 | `publication.fact_scope`; **REPLACE** `published_fact_campaign_day` | current view definition |
| 000018 | `snapshot_status`, `snapshot_row_count`, `publication_fact` | current |
| 000019 | `app_user` password hash columns | current |

`published_fact_campaign_day` in 000013 is a **historical definition**; 000017 is the live SQL.

---

## Tables (purpose / PK / V1 use)

| Table | Purpose | PK | Used by V1 app path |
|---|---|---|---|
| `client` | Tenant | `id` uuid | Yes (FK root; seed demo id) |
| `we_source_column` | A:BO catalog | `excel_position` | Schema tests / docs; not HTTP |
| `source_file` | File identity SHA-256 unique | `id` | Yes ingest |
| `batch` | Staging job | `id` | Yes |
| `campaign_label_version` / `campaign_label_row` | New Logic snapshots | version + rows | Yes P2/P4 |
| `template_label_version` / `template_label_row` | Q:R templates | | Yes |
| `rate_card_version` / `rate_card_rule` | Cost rates | | Yes `calculate_total_cost` |
| `label_group_version` / `label_group_member` | FL1 groups | | Yes |
| `processing_run` | Transform run + QA verdict | `id` | Yes; + `error_summary`, `progress_at` (000016) |
| `stg_source_row` | Raw header JSON | `id` | Yes |
| `stg_rejected_row` | Rejects | `id` | Yes |
| `fact_campaign_day` | Working-set grain | `(client_id, campaign_id, variation_id_key, day)` | Yes `GET /facts` |
| `fact_campaign_day_history` | Superseded grains | `id` | Yes `GET /facts/history` |
| `publication` | Publish event | `id`; unique `(id, client_id)` added 000018 | Yes |
| `publication_current` | Pointer | `client_id` PK **VERIFIED 000002** | Yes `/current/` |
| `publication_fact` | Immutable snapshot | composite publication+grain **VERIFIED 000018** | Yes when `snapshot_status=complete` |
| `kpi_definition` | QA+client formula registry | `id` | Seed; runtime uses `kpis.py` |
| `audit_log` | Intended audit | `id` | **NOT USED** by API Python (only schema catalog list) |
| `qa_finding` | Inspector QA | composite uniqueness in app | Yes inspector QA |
| `rpt_dim_date` | Date table | `day` | Power BI / SQL; not HTTP |
| `app_user` | Password login | `id` | Yes when identity store + 000019 hash |
| `client_membership` | User↔client role | | Yes login |

`publication.fact_scope`: `processing_run` \| `client_current` (000017).  
`publication.snapshot_status`: `none` \| `complete` (000018).

### `fact_campaign_day` columns (VERIFIED 000002)

Types: uuid/text/date/timestamptz/bigint/`numeric(18,4)` as in SQL. CHECK `label_match_status` / `template_match_status` ∈ `{matched, blank_key, unmatched}` or NULL. FKs to client, rate_card_rule, processing_run, batch, four version tables.

`rpt_dim_variation` comment (000014) still says empty `variation_id_key` is a valid **OPEN-A6** key. Python `derive.variation_id_key`: `None` → `""`, otherwise returns the original string. SCHEMA.md describes `COALESCE(variation_id, '')`. For NULL vs empty this matches. Whitespace-only WE values are **not** trimmed in Python.

---

## Views

| View | Introduced | Purpose | Used by |
|---|---|---|---|
| `published_fact_campaign_day` | 000013, **replaced** 000017 | Join current pointer to facts (and snapshot-aware in 000017) | `rpt_published_fact`; RLS reporting |
| `rpt_published_fact` | 000014 | Power BI fact | Desktop spec; not Excel HTTP |
| `rpt_dim_client` | 000014 | Client dim | spec |
| `rpt_dim_campaign` | 000014 | Campaign dim | spec |
| `rpt_dim_variation` | 000014 | Variation dim | spec |

---

## Indexes (000003 + 000012 + 000014 + 000018)

Campaign/template/rate/label lookups; batch status; processing_run batch; staging keys; fact day/batch/run/channel; history key; publication client; audit_log time; kpi namespace; denorm client indexes; `fact_campaign_day_published_slice_idx`; `qa_finding` client/run/rule/severity; `publication_fact_pub_day_idx`, `publication_fact_client_idx`.

---

## Roles / functions / RLS (000013 + later policies)

**Roles (NOLOGIN):** `dfip_migrator`, `dfip_api` (NOBYPASSRLS), `dfip_worker` (comment: prepared for later worker — **NOT used** by Python worker app).

**Functions:** `dfip_is_platform_admin()`, `dfip_current_role()`, `dfip_client_ids()`, `dfip_client_allowed(uuid)`, `dfip_inspector_client(uuid)`, `dfip_member_client(uuid)` — read GUCs `dfip.platform_admin`, `dfip.role`, `dfip.client_ids`.

**Python:** `dfip_db.rls.bind_rls` SET LOCAL in request/worker (`qa_workflow.processing_rls`).

**Policies:** full USING/WITH CHECK text is in [V1_RLS.md](V1_RLS.md) (copied from SQL, not paraphrased away).

**HTTP still authorizes in FastAPI first.** Unset GUCs ⇒ no tenant fact rows for reporting logins (powerbi README). Identity SQL uses `rls=None` (no `dfip_api` role).

---

## Important queries (application)

Implemented in the Postgres adapters listed in [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md). There is **no** `PostgresIdentityStore` class (`identity.py` functions only).

Reporting validation: `powerbi/sql/validation_kpis.sql`, `inspector_not_in_model.sql`. Example logins: `reporting_login.example.sql` (not a migration).
