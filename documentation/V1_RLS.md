# DFIP V1 — PostgreSQL RLS (exact predicates)

**Source (VERIFIED SQL text):** `supabase/migrations/20260823000013_v2_rls_roles.sql`, `20260823000014_v2_analytics_reporting.sql`, `20260829000018_publication_fact_snapshot.sql`.  
**Python (VERIFIED):** `packages/db/dfip_db/rls.py`, `connection.py` `transaction()`, `identity.py` (`rls=None`).

**Live database policies:** **UNKNOWN / NOT VERIFIED.** This document is the migration text. Hosted `DATABASE_URL` was empty in `.env` at audit time (boolean check only; no secret printed).

**Comment in 000013 (VERIFIED):** “HTTP authorization remains the API's responsibility. These policies are defense in depth. No PostgREST grants. No passwords. No BYPASSRLS for `dfip_api`.”

---

## Application `client_id` filtering vs PostgreSQL RLS

| Layer | What it is | When it applies |
|---|---|---|
| **FastAPI authorization** | Role checks (`can_inspect`, `can_publish`, client vs inspector). JWT/`dev_token` principal. | Every HTTP request. **Primary isolation.** |
| **Application SQL filters** | Extra `WHERE client_id = %s` / `client_id = ANY(%s)` in `PostgresReadRepository._client_clause`, publication `get_current(client_id)`, ingest SHA lookup with `client_id`, fact upsert keys, snapshot copy filter. | Always in those methods, **even if RLS is bypassed**. |
| **PostgreSQL RLS** | `ENABLE` + `FORCE` on listed tables. Policies evaluate `dfip_*` helpers that read **transaction-local GUCs**. | Only after `SET LOCAL ROLE dfip_api` **and** GUCs set. Skipped when `transaction(..., rls=None)` or when `apply_rls_settings` cannot `SET ROLE` (owner/BYPASSRLS login still sets GUCs; FORCE RLS + BYPASSRLS **sees all rows** — comment in `rls.py`). |

**Isolation that exists (when DSN set, login cannot BYPASSRLS, role `dfip_api`, GUCs populated):**

- Inspector working-set tables: `dfip_inspector_client(client_id)` — role in `admin`/`publisher` **and** (platform admin **or** client in `dfip.client_ids`).
- Publication pointer / publication SELECT / `publication_fact` SELECT / view `published_fact_campaign_day` member filter: `dfip_member_client(client_id)` — platform admin **or** client in GUC list. **Readers** can SELECT published data without inspector role.
- Child catalog rows: EXISTS parent version with inspector client.
- `qa_finding`: inspector client (FOR ALL).

**Isolation that does NOT exist:**

- **No RLS** on `app_user`, `client_membership`, `rpt_dim_date` (no `ENABLE ROW LEVEL SECURITY` in migrations). Identity lookups use `transaction(pool, rls=None)` so they **do not** `SET LOCAL ROLE dfip_api`.
- `we_source_column` / `kpi_definition`: RLS enabled but `USING (true)` — any `dfip_api`/`dfip_worker` SELECT sees **all** catalog KPI/source-column rows.
- Unset GUCs: `dfip_client_ids()` is empty array; `dfip_inspector_client` / `dfip_member_client` false unless `dfip.platform_admin='true'`. Reporting logins with empty GUCs see **no** tenant fact rows (by design).
- `dfip_worker` is **NOLOGIN** and unused by Python (`apps/worker` has no runner).
- In-memory mode (`DATABASE_URL` empty): **no PostgreSQL RLS at all**; isolation is process memory + FastAPI only.
- Superuser / table owner / BYPASSRLS connections: policies do not hide rows.
- Excel mashup / static file: **not** Postgres; JWT or static cells.

### RUN 003 (disposable local Postgres only)

Two-tenant HTTP proof uses Company 1 (`default`) and Company 2 (`company-2`) with
separate client identities. `tests/test_company2_isolation.py` asserts FastAPI
scope, SQL `publication_current` pointers, and `SET LOCAL ROLE dfip_api` hiding
the other tenant's `publication_fact`. That is **not** a claim that hosted
production isolation is proven. Identity tables remain without RLS.

### RUN 004 (company selection)

Does not change RLS policies. A multi-membership publisher JWT is fail-closed
(`client_ids` empty in `rls_context_for`) until `POST /auth/select-client`
binds one client. That is still application-level isolation, not a new RLS
model.

### RUN 004B-1 (company registry / rename)

Does not change RLS policies. Display-name updates use the same
`transaction(..., rls=None)` pattern as identity lookups because `dfip_api`
has SELECT-only on `client`. Authorization is membership + inspector role.
`client.id` is not rewritten.

### RUN 004B-2 (Add Company)

Does not change RLS policies. Inserts into `client` and `client_membership`
use `transaction(..., rls=None)` because `dfip_api` has SELECT-only grants.
The creating inspector is the only membership granted. No catalog rows.

### RUN 004B-3 (company onboarding / catalog identity)

Does not change RLS policies. `processing_run` version-id FKs remain PK-only.
Application bind plus INSERT/UPDATE subqueries require
`version.client_id = processing_run.client_id` before a UUID is stored.
Packaged fallback content is still allowed; foreign packaged UUIDs become
NULL. `fact_campaign_day.rate_card_rule_id` is likewise NULL unless the rule's
`rate_card_version.client_id` is the run tenant (packaged rules belong to the
default client). **Invariant:** Stored catalog version IDs must belong to the
run's `client_id`; packaged fallback must not masquerade as a different
tenant's catalog version.

### RUN 004B-4 (client-portal user provisioning)

Does not change RLS policies. Inserts into `app_user` and `client_membership`
use `transaction(..., rls=None)` like company create, including one-time
publisher setup. Application authorization is the control. This is not
production identity isolation.

---

## Roles (000013)

| Role | Attributes | Purpose (SQL comments) |
|---|---|---|
| `dfip_migrator` | `NOLOGIN` | DDL. Not an application login. |
| `dfip_api` | `NOLOGIN` `NOBYPASSRLS` | DML after `SET LOCAL ROLE`. |
| `dfip_worker` | `NOLOGIN` `NOBYPASSRLS` | “Prepared for a later worker runtime. Not used in Phase 1.” |

`REVOKE ALL ON SCHEMA public FROM PUBLIC`. Grants: USAGE+CREATE schema to migrator; USAGE to api/worker.

---

## Helper functions (exact bodies)

```sql
dfip_is_platform_admin()  -- STABLE
  SELECT COALESCE(current_setting('dfip.platform_admin', true), '') = 'true'

dfip_current_role()
  SELECT COALESCE(current_setting('dfip.role', true), '')

dfip_client_ids()
  SELECT COALESCE(
    string_to_array(NULLIF(COALESCE(current_setting('dfip.client_ids', true), ''), ''), ','),
    ARRAY[]::text[]
  )

dfip_client_allowed(p_client_id uuid)
  SELECT p_client_id IS NOT NULL AND p_client_id::text = ANY (dfip_client_ids())

dfip_inspector_client(p_client_id uuid)
  SELECT dfip_current_role() IN ('admin', 'publisher')
     AND (dfip_is_platform_admin() OR dfip_client_allowed(p_client_id))

dfip_member_client(p_client_id uuid)
  SELECT dfip_is_platform_admin() OR dfip_client_allowed(p_client_id)
```

`EXECUTE` granted to `dfip_api` and `dfip_worker` only (revoked from PUBLIC).

Python sets GUCs with `set_config(..., true)` (transaction-local): `dfip.user_id`, `dfip.role`, `dfip.client_ids` (comma-joined), `dfip.platform_admin` (`true`/`false`), `dfip.subject`.

---

## RLS-enabled tables

All of the following have `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY` unless noted.

| Table | Migration | FORCE? |
|---|---|---|
| `client` | 000013 | yes |
| `source_file`, `batch`, `processing_run` | 000013 | yes |
| `stg_source_row`, `stg_rejected_row` | 000013 | yes |
| `fact_campaign_day`, `fact_campaign_day_history` | 000013 | yes |
| `publication`, `publication_current` | 000013 | yes |
| `campaign_label_version`, `campaign_label_row` | 000013 | yes |
| `template_label_version`, `template_label_row` | 000013 | yes |
| `rate_card_version`, `rate_card_rule` | 000013 | yes |
| `label_group_version`, `label_group_member` | 000013 | yes |
| `we_source_column`, `kpi_definition`, `audit_log` | 000013 | yes |
| `qa_finding` | 000014 | yes |
| `publication_fact` | 000018 | yes |

**Not RLS-enabled (VERIFIED grep):** `app_user`, `client_membership`, `rpt_dim_date`. Views inherit underlying table RLS.

---

## Policies (exact USING / WITH CHECK)

Roles on every policy below: `TO dfip_api, dfip_worker`.

### Tenant / working set

| Policy | Command | USING | WITH CHECK |
|---|---|---|---|
| `client_select` | SELECT | `dfip_member_client(id)` | — |
| `source_file_inspector` | ALL | `dfip_inspector_client(client_id)` | same |
| `batch_inspector` | ALL | `dfip_inspector_client(client_id)` | same |
| `processing_run_inspector` | ALL | `dfip_inspector_client(client_id)` | same |
| `stg_source_row_inspector` | ALL | `dfip_inspector_client(client_id)` | same |
| `stg_rejected_row_inspector` | ALL | `dfip_inspector_client(client_id)` | same |
| `fact_campaign_day_inspector` | ALL | `dfip_inspector_client(client_id)` | same |
| `fact_campaign_day_history_inspector` | ALL | `dfip_inspector_client(client_id)` | same |

### Publication

| Policy | Command | USING | WITH CHECK |
|---|---|---|---|
| `publication_select` | SELECT | `dfip_member_client(client_id)` | — |
| `publication_write` | INSERT | — | `dfip_inspector_client(client_id)` |
| `publication_current_select` | SELECT | `dfip_member_client(client_id)` | — |
| `publication_current_insert` | INSERT | — | `dfip_inspector_client(client_id)` |
| `publication_current_update` | UPDATE | `dfip_inspector_client(client_id)` | same |
| `publication_fact_select` | SELECT | `dfip_member_client(client_id)` | — |
| `publication_fact_insert` | INSERT | — | `dfip_inspector_client(client_id)` |

**No `publication` UPDATE/DELETE policy** in these migrations. UPDATE/DELETE would fail for `dfip_api` unless other policies exist (**NONE in later files**). Grants still include UPDATE/DELETE on `publication` — RLS would deny non-matching commands.

### Catalog versions (parent)

Inspector ALL with `dfip_inspector_client(client_id)` USING and WITH CHECK:  
`campaign_label_version_inspector`, `template_label_version_inspector`, `rate_card_version_inspector`, `label_group_version_inspector`.

### Catalog children (EXISTS parent)

USING and WITH CHECK identical:

```sql
EXISTS (
  SELECT 1 FROM <parent_version> AS v
  WHERE v.id = <child>.version_id
    AND dfip_inspector_client(v.client_id)
)
```

Parents: `campaign_label_version`, `template_label_version`, `rate_card_version`, `label_group_version`.  
Children: `campaign_label_row`, `template_label_row`, `rate_card_rule`, `label_group_member`.

### World-readable (to api/worker)

| Policy | Command | USING |
|---|---|---|
| `we_source_column_select` | SELECT | `true` |
| `kpi_definition_select` | SELECT | `true` |

### Audit

| Policy | Command | Predicate |
|---|---|---|
| `audit_log_select` | SELECT USING | `dfip_is_platform_admin() OR (client_id IS NOT NULL AND dfip_inspector_client(client_id))` |
| `audit_log_insert` | INSERT WITH CHECK | same |

API Python **does not INSERT** `audit_log` (grep). Table unused by app path.

### QA

| Policy | Command | USING / WITH CHECK |
|---|---|---|
| `qa_finding_inspector` | ALL | `dfip_inspector_client(client_id)` both |

---

## View filter (not a POLICY)

`published_fact_campaign_day` (current definition **000018**, replacing 000013/000017) includes `WHERE ... dfip_member_client(p.client_id)` plus snapshot vs live UNION (see `V1_DATABASE.md`). Readers query this view when `PostgresFactStore.list_published_slice` is not `working_set` and `current_rls()` is not None.

---

## Python application of RLS

`apply_rls_settings(conn, context)`:

1. If `context is None`: **return immediately** (no SET ROLE, no GUCs). Used by identity lookups.
2. Else `SET LOCAL ROLE dfip_api` inside a savepoint; on `InsufficientPrivilege`, keep current role and still set GUCs.
3. `set_config` for identity GUCs.

`Postgres*Store._tx()` → `transaction(self._pool, current_rls())` → `conn.transaction()` then `apply_rls_settings`.

`current_rls()` is a `ContextVar` set by `bind_rls` on the request/worker path.
