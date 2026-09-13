# DFIP V1 — Architecture and data flow

**Status of this document:** VERIFIED against source in this repository as of 2026-08-30, except where labeled otherwise.

`POST /api/v1/uploads` is implemented (`upload_routes.py`). `STATUS.md` known-limitations no longer says “no HTTP upload”. The `## V2 (not implemented)` list is **legacy wording** locked by `test_p10_release.py`; see the note under that heading.

Cross-references: [V1_MASTER_DOCUMENTATION.md](V1_MASTER_DOCUMENTATION.md), [V1_API_REFERENCE.md](V1_API_REFERENCE.md), [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md), [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md), [V1_POWER_BI.md](V1_POWER_BI.md), [SCHEMA.md](SCHEMA.md), [HTTP_WORKFLOW.md](HTTP_WORKFLOW.md), [PUBLICATION.md](PUBLICATION.md).

---

## Naming collision (read first)

This repository uses **two different “V2” meanings**:

| Label in repo | What it actually is |
|---|---|
| Product phases **P0–P11** | Excel/API “V1 complete” client-reporting product |
| **V2 Phase 1 / 2A / 2B** in README and tests | PostgreSQL persistence, `rpt_*` analytics, Power BI *source package* **already in this tree** |
| `apps/worker` “V2” | **NOT IMPLEMENTED** job runner |
| Operator “V2 roadmap” | Future product: production IdP, hosted Power BI canvas, live mashup-safe pivots, etc. |

This architecture document describes **what is in the DFIP-V1 git tree**, including persistence and HTTP ingest that README calls “V2”.

---

## ASCII architecture (actual)

```
  Web Engage .xlsx (sheet "Web-Engage Raw", columns K:BO = 57 headers)
           + optional Logic/Labels catalog .xlsx
                          |
                          |  POST /api/v1/uploads  (admin/publisher)
                          |  UploadService ThreadPoolExecutor (max_workers=1)
                          v
  SHA-256 identity  -->  source_file (+ optional object store)
                          |
                          v
  P3 ingest library       stg_source_row  /  stg_rejected_row
  (dfip_core.ingest)      batch, processing_run (pending → succeeded|failed)
                          |
                          v
  P4 transform            extract → P2 labels/templates/FL1 group/rate card
  (dfip_core.transform)   → Total Cost → fact_campaign_day (+ history on restate)
                          |
                          v
  QA evaluate_qa          qa_finding; run.qa_verdict = pass|warn|fail|unavailable
  (dfip_analytics.qa)     publish allowed only for pass|warn
                          |
                          |  POST /api/v1/publications  (admin/publisher)
                          v
  publication row         + publication_fact snapshot (when snapshot_status=complete)
  publication_current     one pointer per client_id  ← THIS IS "/current/"
                          |
          +---------------+----------------+--------------------+
          |               |                |                    |
          v               v                v                    v
   GET /facts        GET /publications/   SPA Admin           SPA Client
   working set       current/facts        /admin/*            /client/*
   admin/publisher   JSON page 200 max    (working set)       (published)
                          |
                          +-- facts.csv / facts.xlsx  (row cap DFIP_DOWNLOAD_MAX_ROWS)
                          +-- client-report.xlsx      (static PublishedFacts + pivots)
                          |
                          v
   LIVE AUTHORING         excel/Client_Report.xlsx + PublishedFacts.m
   (operator Excel)       Web.Contents → /publications/current/facts
                          PivotCache worksheet range A1:AS2 until Refresh All
                          KNOWN LIMITATION: Refresh All can destroy pivot layouts
                          |
                          v
   STATIC CLIENT FILE     render_client_report_xlsx clone
                          cells A1:AS{n}, query tables stripped, cache rebound
                          Settings credentials cleared

  OPTIONAL (DATABASE_URL set)
                          PostgreSQL 16 / Supabase
                          RLS GUCs (defense in depth; API still app-level authz)
                          rpt_published_fact + rpt_dim_*  (Power BI package only)
                          NO committed .pbix  — Power BI Desktop canvas NOT VERIFIED
```

**This flow is not a separate worker process.** `apps/worker` is empty of code. Ingest/transform run **inside the API process** off the event loop.

---

## Persistence modes

| Mode | When | Behavior |
|---|---|---|
| In-memory | `DATABASE_URL` empty; tests injecting stores | `InMemoryIngestStore`, `InMemoryFactStore`, `InMemoryPublicationStore`, `InMemoryCatalogStore`, `InMemoryIdentityStore`. Lost on process restart. **VERIFIED** `create_app` in `packages/api/dfip_api/app.py`. |
| PostgreSQL | `DATABASE_URL` set and no injected stores | psycopg pool; Postgres* store classes. Production **requires** this (`validate_persistence_settings`). |
| Hybrid tests | Injected stores win even if DSN present | Test isolation. |

`GET /health` sets `database.configured` from whether the DSN is non-empty. `database.status` is always `not_checked` — health **does not** open PostgreSQL. Authenticated `GET /api/v1/ops/ready` (admin/publisher) performs a cheap `SELECT 1`, a source-storage directory check, and process-local worker idle/busy. Web origin `/health` is `dfip-web` process liveness only. **VERIFIED.**

---

## Component catalog

### 1. Source workbooks

- **Purpose:** Native Web Engage export plus New Logic / Labels catalogs.
- **Technology:** Excel `.xlsx` (ZIP).
- **Ingestion:** `dfip_core.ingest.reader` + `headers.py` (exact 57-header K:BO contract, no trim of source cells for staging payload). Optional RUN 009 approved extras may follow that window (`APPROVED_EXTRA_SOURCE_COLUMNS`). Unknown trailing headers fail closed.
- **Location:** Operator-supplied files; `source/` is documentation-only in git. Raw bytes may land in `DFIP_STORAGE_ENDPOINT` if configured.
- **Failure:** Header mismatch / unapproved extra → ingest error; ZIP/size → HTTP 422/413.

### 2. P3 ingest (`packages/core/dfip_core/ingest`)

- **Input:** Workbook bytes or path.
- **Output:** `source_file`, `batch`, `stg_source_row`, `processing_run` bound to P2 version ids. Approved extras stay on `stg_source_row.raw` JSON keys; they are not fact columns.
- **Idempotency:** SHA-256 of file; HTTP 200 replay only after a **processed** batch. Staged/received/failed work is recovered on the same batch, not treated as complete.
- **Does not:** apply labels, cost, KPIs, or publish.

### 3. P2 configuration (`packages/config`)

- **Input:** Packaged JSON under `dfip_config/data/` and/or uploaded Logic/Labels versions.
- **Output:** Versioned campaign labels, template Q:R, rate cards, FL1 groups.
- **Bound per processing run / day** via `select_version_for_day` / `bind_configuration`.

### 4. P4 transform (`packages/core/dfip_core/transform`)

- **Input:** Staged rows + catalog.
- **Output:** `FactRecord` grains `(client_id, campaign_id, variation_id_key, day)`.
- **Engine version constant:** `ENGINE_VERSION = "0.4.0"`.
- **Persist chunk:** `FACT_PERSIST_CHUNK_SIZE = 500`.
- **Cost:** `calculate_total_cost` — Delivered × rate card; unmatched → `0.0000`, never NULL.
- **Reconciliation:** `reconcile.py` independently recomputes derived fields (P8 tests).

### 5. QA (`packages/analytics/dfip_analytics/qa.py`)

- **Input:** Facts + run context.
- **Output:** `qa_finding` rows; verdict `pass` / `warn` / `fail` / `unavailable`.
- **Publishable:** `PUBLISHABLE_QA_VERDICTS = {pass, warn}`.
- **Important:** `QA-IMPOSSIBLE-FAILED` is **warning** (Failed > Sent). `QA-RATIO-GT-ONE` is **warning**. A large warning count does not block publish.

### 6. Publication (`PublicationService`)

- **Input:** Succeeded run with publishable QA.
- **Output:** `publication` + replace `publication_current` for that `client_id`; optional immutable `publication_fact` snapshot.
- **`/current/` meaning:** the publication_id stored in `publication_current` for the JWT-scoped client. Empty pointer → empty facts / header-only files.
- **Replacement:** new publish **replaces the pointer**; prior `publication` rows remain (list history). Rollback of pointer is **NOT IMPLEMENTED** as a dedicated API (INFERRED: operator would publish an older run again if that run still exists).
- **Fact scopes:** `processing_run` (default) or `client_current` (`ALLOWED_FACT_SCOPES`).

### 7. API (`dfip_api`)

- **Framework:** FastAPI + uvicorn.
- **Prefix:** `DFIP_API_PREFIX` default `/api/v1`.
- **Auth:** Bearer `dev_token` or HS256 JWT. Password login issues JWT.
- **CORS:** only if `DFIP_WEB_ORIGIN` set; methods GET HEAD OPTIONS POST; expose `Content-Disposition`.
- **OpenAPI:** `/docs` `/redoc` `/openapi.json` **disabled in production**.
- **Retry:** none at HTTP layer for uploads (single-thread executor; poll batch/run).

### 8. Web SPA (`dfip_web` + `apps/web/static`)

- **Purpose:** Admin/publisher operations + client published facts UI.
- **Does not:** proxy API, open DB, or mint secrets.
- **Config:** `GET /config.json` → `apiBaseUrl`, `apiPrefix`, `adminRoles`.
- **Auth in browser:** Bearer stored client-side (`auth.js`); not httpOnly cookie (VERIFIED pattern from SPA; tokens must not be committed).

### 9. Excel live template

- Tracked `excel/Client_Report.xlsx` + `excel/PublishedFacts.m`.
- Live query to **current publication JSON API**, page size 200.
- Native PivotTables share one cache. See [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md).

### 10. Static client report

- `render_client_report_xlsx` clones the template ZIP, writes static cells, strips query tables, disables mashup keep-alive/background/refreshOnLoad, rebinds PivotCache to `PublishedFacts!A1:AS{n}`, blanks Settings credentials.

### 11. Power BI package

- Source-only: M queries, DAX, semantic-model JSON, theme, pages spec.
- **No `.pbix` in repo.** Desktop canvas **NOT VERIFIED** (`documentation/DESKTOP_ACCEPTANCE.md`).

### 12. Docker / CI

- `Dockerfile`: Python 3.11-slim, `pip install -e ".[dev]"`, default CMD pytest.
- `docker-compose.yml`: `app` runs tests; `dfip_db` postgres:16 on profile `v2-db`, host port **5433**.
- GitHub Actions: ruff + pytest; optional postgres job with `DFIP_TEST_DATABASE_URL` (**drops public** in postgres tests — never point at live data).

### 13. Backup / restore (`python -m dfip_api.backup`)

- **Purpose:** Coordinated logical PostgreSQL dump (`pg_dump -Fc`) plus a copy of `DFIP_STORAGE_ENDPOINT`.
- **Privilege:** Superuser or BYPASSRLS-capable dump login. Role `dfip_api` is refused. Do not grant BYPASSRLS to `dfip_api`.
- **Layout:** `manifest.json` (no secrets), `postgres/dfip.dump`, `source-archive/{bucket}/{client_id}/{source_file_id}/{sha256}.xlsx`.
- **Restore:** Disposable local database (`--confirm-disposable`) or colocated production name `dfip` (`--confirm-production-local`). Cluster roles `dfip_migrator` / `dfip_api` / `dfip_worker` are prerequisites. Do not re-apply migrations on a complete dump. Inject P13A secrets from the environment, then start the API so P13B can sweep leftover `pending`/`running` runs.
- **Does not:** delete old backups or provision cloud resources. Example host schedule: `deploy/systemd/dfip-backup.timer`. P13F `identify-eligible` lists extra verified sets only. P13D monitoring is public liveness + authenticated readiness + logs; backup verify stays this CLI.
- **Not imported by** `create_app`.

### 14. Company lifecycle / purge (`python -m dfip_api.purge`)

- Application-layer `active`/`inactive` on `client`. HTTP deactivate/reactivate. No RLS/`rpt_*` change.
- Operator purge CLI uses a privileged DSN. SQL then `{root}/{bucket}/{client_id}/` archive. No HTTP purge. No automatic scheduler. Production-local database name `dfip` requires `--confirm-production-local`.

### 15. Production deployment package (`deploy/`)

- Locked V1 host: one DigitalOcean Droplet (BLR1), colocated PostgreSQL 16, block-volume archive, Caddy HTTPS, systemd API+web.
- **Does not** provision DigitalOcean or DNS. Runbook: `documentation/V1_DEPLOYMENT.md`.

---

## Data lifecycle (grain)

```
Excel row (source_row_number)
  → stg_source_row.raw (header-keyed JSON)
  → extract_source_fields (typed WE measures + ids + day)
  → labels / template / FL1 group / rate / total_cost / derive month, hhh, variation_id_key
  → fact_campaign_day  (current working set)
  → on restatement: prior grain copied to fact_campaign_day_history
  → on publish: copy into publication_fact (if snapshot complete)
  → API FactResponse (decimals as JSON strings)
  → Excel HeaderMap (snake_case → display headers)
  → Pivot cache fields / MEASURE_HEADERS calculated fields
```

Native Web Engage **rate columns** on the source sheet are **not** KPI inputs (`NATIVE_RATE_MEASURES` in `kpis.py`).

---

## What `/current/` is and is not

**IS (VERIFIED):**

- `GET /api/v1/publications/current` → `{ publication, current }` where `current` is the `publication_current` pointer (`client_id`, `publication_id`, `updated_at`).
- `GET /api/v1/publications/current/facts` reads that publication’s snapshot (or live join for legacy `snapshot_status=none`).
- Excel M script **only** calls this facts URL (not `GET /facts`).
- Client SPA `/client/facts` uses the same published slice.

**IS NOT:**

- Not a date named “current month”.
- Not the working set (`GET /facts` is unpublished/in-progress facts for inspectors).
- Not a draft/staged publication state machine. States on the publication row are **snapshot** `none` | `complete`, plus the processing run’s own status. There is **no** `draft` publication entity. **VERIFIED** `SNAPSHOT_STATUS_*` in `publication_store.py`.

---

## Failure and retry (pipeline)

| Stage | Retry |
|---|---|
| Multipart upload | Client retries whole POST; SHA replay only if identical file already **processed** |
| Background transform | Single worker thread; poll `GET /batches/{id}` and `GET /processing-runs` |
| QA | `POST .../qa` can re-evaluate |
| Publish | Explicit POST; failed QA blocks |
| Excel live refresh | Excel mashup; **unsafe for pivots** — see Excel doc |
| Power BI | Operator Desktop refresh; package documents DirectQuery default |
| Coordinated backup | Stop API → `pg_dump -Fc` → copy archive → manifest → verify. Operator CLI only. |

---

## Process topology (local)

```
python -m dfip_api   →  127.0.0.1:8000   (DFIP_API_HOST/PORT)
python -m dfip_web   →  127.0.0.1:3000   (DFIP_WEB_HOST/PORT)
python -m dfip_api.backup  →  operator CLI (stop API first; not a web UI)
Browser              →  SPA origin, XHR to DFIP_API_BASE_URL
Excel Desktop        →  Settings ApiBaseUrl + BearerToken (live template only)
```

Two processes. No reverse-proxy in repo. CORS must allow the web origin.

---

## RUN 009 schema extensibility (controlled)

The product can ingest one **approved** extra source header after the locked 57-column K:BO window. That value stays on `stg_source_row.raw`. Transform, publication, `FactResponse`, Excel `FACT_HEADERS`, PivotCache, slicers, and Power BI `rpt_*` remain the existing canonical contracts.

Future dashboard D0 must **not** treat arbitrary extra columns as metrics/dimensions. A new dashboard field needs explicit semantic registration, the same way Power BI needs an explicit `rpt_*` / DAX change.

Clients cannot define trusted schema. Only the catalog `APPROVED_EXTRA_SOURCE_COLUMNS` list (code, not a client workbook) approves extras. Unapproved trailing headers fail ingest with a publisher-facing `Unapproved source column: …` message.

---

## Related existing docs

Operational HTTP details: [HTTP_WORKFLOW.md](HTTP_WORKFLOW.md). Schema letters: [SCHEMA.md](SCHEMA.md). Website routes: [WEBSITE.md](WEBSITE.md). V1 set: [V1_MASTER_DOCUMENTATION.md](V1_MASTER_DOCUMENTATION.md), [V1_RLS.md](V1_RLS.md), [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md), [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md).
