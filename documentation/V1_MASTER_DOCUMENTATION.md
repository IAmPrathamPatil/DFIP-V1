# DFIP V1 — Master technical documentation

**Authoritative V1 engineering record** for repository `DFIP-V1` as inspected 2026-08-30.

**Method:** inspect implementation. Status tags: VERIFIED, PARTIALLY VERIFIED, INFERRED, UNKNOWN, NOT IMPLEMENTED, LEGACY, KNOWN LIMITATION.

**Do not modify production.** This file is documentation only.

**Companion files (same folder):**

| File | Contents |
|---|---|
| [V1_ARCHITECTURE.md](V1_ARCHITECTURE.md) | Diagrams, component flow, `/current/` |
| [V1_SOURCE_INVENTORY.md](V1_SOURCE_INVENTORY.md) | Python modules, signatures, pipeline functions |
| [V1_DATABASE.md](V1_DATABASE.md) | Tables, views, migrations |
| [V1_RLS.md](V1_RLS.md) | Every RLS table, policy predicates, helpers, roles; app vs Postgres |
| [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md) | Postgres\* methods: SQL, params, returns, tx, callers, errors |
| [V1_TEST_INVENTORY.md](V1_TEST_INVENTORY.md) | Tests + pytest run (2026-08-30) |
| [V1_API_REFERENCE.md](V1_API_REFERENCE.md) | Every HTTP route |
| [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md) | 45 report fields + complete 67-column A:BO mapping |
| [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md) | Live vs static, ZIP listing, PivotCache, helpers |
| [V1_POWER_BI.md](V1_POWER_BI.md) | Package vs missing pbix |
| [V1_OPERATIONS_RUNBOOK.md](V1_OPERATIONS_RUNBOOK.md) | Commands, new-month |
| [V1_DEPLOYMENT.md](V1_DEPLOYMENT.md) | P13G DigitalOcean single-Droplet package |
| [V1_TROUBLESHOOTING.md](V1_TROUBLESHOOTING.md) | Failure catalog |
| [V1_SECURITY.md](V1_SECURITY.md) | Exposure, residual risk |
| [V1_COMPLETION_AUDIT.md](V1_COMPLETION_AUDIT.md) | Authoritative documentation audit |

Also: [SCHEMA.md](SCHEMA.md) (column contract; some P7/P4 sentences **contradict later migrations/code** — see audit §14).

Older docs (`STATUS.md`, `ANALYTICS.md`, `SCHEMA.md`, `HTTP_WORKFLOW.md`, `DAILY_REPORT.md`, `DESKTOP_ACCEPTANCE.md`, `PUBLICATION.md`, `WEBSITE.md`, `DATA_HANDLING.md`, `LOCAL_TESTING_RUNBOOK.md`) remain useful. **Where they conflict, prefer this V1_* set + source.** `STATUS.md` known-limitations bullet was corrected (HTTP upload exists). The heading `## V2 (not implemented)` still contains legacy tokens required by `test_p10_release.py` — see that file’s forensic note.

Package version: `pyproject.toml` **1.0.0**. API OpenAPI version string **0.5.0**. Web FastAPI **0.6.0**.

---

## 1. Repository inventory

### Top-level (VERIFIED glob)

| Path | Role | Status |
|---|---|---|
| `packages/api` | FastAPI | Active |
| `packages/core` | Ingest + transform | Active |
| `packages/config` | Settings + catalogs JSON | Active |
| `packages/db` | Postgres adapters, migrate | Active when DSN set |
| `packages/web` | SPA server + Excel ZIP | Active |
| `packages/analytics` | KPI, QA, grains | Active |
| `packages/shared` | Minimal shared | Active / thin |
| `apps/web/static` | SPA JS/CSS | Active |
| `apps/worker` | README only | NOT IMPLEMENTED runner |
| `supabase/migrations` | SQL | Schema artifact |
| `excel/` | Client_Report.xlsx + PublishedFacts.m | Active native |
| `powerbi/` | M/DAX/JSON/SQL examples | Spec; no pbix |
| `tests/` | pytest | Active |
| `deploy/` | P13G systemd/Caddy/env/scripts | Package only; no live provision |
| `documentation/` | Docs including this set | Active |
| `source/` | README | Not source data in git |
| `scripts/` | PowerShell demo helpers | Local ops |
| `docker-compose.yml`, `Dockerfile`, `.dockerignore` | Dev/CI image | Active |
| `.github/workflows/ci.yml` | Lint/test | Active |
| `.env.example` | Config template | Active |
| `pyproject.toml` | Packaging | Active |
| `STOP_DFIP_DEMO.bat` | Local stop | Demo |
| `tmp/` | gitignored local artifacts | Generated / secrets — **do not commit** |

~230 tracked files in workspace listing; tests ~61 files; 19 SQL migrations.

### Important Python modules (active V1)

See §32–33. Generated: none committed except Excel binary. Unused: worker app. LEGACY: Python `build_client_report()` must not overwrite native xlsx.

---

## 2. Project history

**Git:** `git log` shows **one** commit: `5eebfa3 Initial DFIP V1 release`.

Cannot verify staged P0–P11 chronology from git. Product phases are documented in README/`STATUS.md` as narrative, **not** independently dated in VCS.

**INFERRED from code comments and docs only (not git):** P5 API → P7 publication → V2-X native mashup → P8 static download → P9 authz → P10 formula contract → P11 native pivots → upload HTTP → publication_fact snapshots → password login. Treat as documentation, not forensic git.

---

## 3–4. Architecture and technology stack

Architecture: [V1_ARCHITECTURE.md](V1_ARCHITECTURE.md).

### Languages actually present

| Language | Where |
|---|---|
| Python 3.11 | packages, tests |
| SQL | supabase/migrations, powerbi/sql |
| JavaScript (no TypeScript in apps/web) | `apps/web/static/js` |
| M (Power Query) | `excel/PublishedFacts.m`, `powerbi/queries/*.m` |
| DAX | `powerbi/dax/measures.dax` |
| XML | OOXML inside xlsx (connections, pivotCache, queryTables) |
| JSON | config data, semantic-model, pages, theme |
| YAML | compose, GitHub Actions |
| Batch/PowerShell | STOP bat, `scripts/*.ps1` |
| VBA | **NOT FOUND** |

### Declared Python dependencies (`pyproject.toml`) — versions **not pinned** (lower bounds only)

| Package | Constraint | Used for |
|---|---|---|
| pydantic | ≥2.0 | schemas/settings |
| pydantic-settings | ≥2.0 | Settings |
| openpyxl | ≥3.0 | xlsx read/write (not DataMashup) |
| fastapi | ≥0.115 | API + web |
| python-multipart | ≥0.0.9 | uploads |
| uvicorn | ≥0.30 | ASGI |
| httpx | ≥0.27 | web api_client / tests |
| PyJWT | ≥2.8 | JWT |
| psycopg[binary,pool] | ≥3.1 | Postgres |
| pytest | ≥8.0 (dev) | tests |
| ruff | ≥0.6 (dev) | lint |
| PyYAML | ≥6.0 (dev) | tests |

Exact installed versions: **version not verified** (no lockfile in inventory).

### Services / DB / containers

- PostgreSQL **16** (compose image `postgres:16`; CI same).  
- Supabase: migrations + optional hosted DSN; PostgREST/Auth **not** the Excel path.  
- Docker: app image pytest; db profile `v2-db` port 5433.

---

## 5. Configuration

Loaded by `dfip_config.settings.Settings` from environment and optional `.env`. Extra env keys ignored.

| Name | Type | Default | Required | Read | Affects | Sensitivity |
|---|---|---|---|---|---|---|
| DFIP_ENV | str | development | no | Settings | production auth/OpenAPI/DSN rules | low |
| DFIP_LOG_LEVEL | str | INFO | no | documented; uvicorn | logging | low |
| DFIP_API_HOST | str | 127.0.0.1 | no | __main__ | bind | low |
| DFIP_API_PORT | int | 8000 | no | __main__ | bind | low |
| DFIP_API_BASE_URL | str | http://127.0.0.1:8000 | no | web config.json | SPA + Excel Settings default | low (URL) |
| DFIP_API_PREFIX | str | /api/v1 | no | app routers | paths | low |
| DFIP_WEB_ORIGIN | str | http://127.0.0.1:3000 | no | CORS | browser | low |
| DFIP_WEB_HOST/PORT | str/int | 127.0.0.1 / 3000 | no | web __main__ | bind | low |
| DFIP_WORKER_CONCURRENCY | int | 1 | no | settings only | **no worker** | low |
| DATABASE_URL | str | empty | **prod yes** | create_app | memory vs Postgres | **SECRET** |
| DFIP_TEST_DATABASE_URL | str | unset | postgres tests | pytest | **drops public** | SECRET |
| SUPABASE_URL / ANON / SERVICE_ROLE | str | empty | no for Excel | Settings | reserved; not Excel | **SECRET** |
| DFIP_STORAGE_BUCKET | str | dfip-source-files | no | object store | prefix | low |
| DFIP_STORAGE_ENDPOINT | str | empty | no | source_storage | durable files | path/SECRET |
| DFIP_AUTH_MODE | str | dev_token | prod jwt | auth | mode | med |
| DFIP_DEV_AUTH_TOKEN | str | empty | if dev_token | auth | Bearer | **SECRET** |
| DFIP_DEV_AUTH_ROLE | str | reader | no | Principal | role | med |
| DFIP_DEV_AUTH_CLIENT_ID | UUID str | empty | if dev_token+DSN | Principal | scope | med |
| DFIP_AUTH_SECRET | str | empty | jwt / prod | JWT | HS256 | **SECRET** |
| DFIP_AUTH_ISSUER / AUDIENCE | str | empty | no | JWT verify if set | iss/aud | low |
| DFIP_AUTH_TOKEN_TTL_SECONDS | int | 3600 | no | issue_access_token | exp | low |
| DFIP_PASSWORD_PBKDF2_ITERATIONS | int | 210000 | no | password hashes | | low |
| DFIP_UPLOAD_MAX_BYTES | int | 67108864 | no | uploads | 413 | 64 MiB; FY-2026 Raw is 53832336 |
| DFIP_UPLOAD_MAX_FILES | int | 5 | no | uploads | 413 | keep 5 |
| DFIP_UPLOAD_MAX_TOTAL_BYTES | int | 134217728 | no | uploads | 413 | 128 MiB; 0 means 2× per-file |
| DFIP_DOWNLOAD_MAX_ROWS | int | 75000 | no | downloads | 422 if over | low |

Excel Settings sheet (live): `ApiBaseUrl`, `BearerToken` **SECRET**, `ClientId`.

Compose `dfip_db`: `POSTGRES_HOST_AUTH_METHOD: trust` — **local profile only**, not production.

---

## 6–8. Data model, pipeline, publication

See [V1_ARCHITECTURE.md](V1_ARCHITECTURE.md), [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md), [PUBLICATION.md](PUBLICATION.md).

**Ingest:** `sha256_file` → `source_file` unique sha256 → `batch` → `iter_source_rows` → `stg_source_row`; rejects to `stg_rejected_row`. Headers: `match_header_rows` / `HeaderContractError`.

**Transform:** `extract_source_fields` → `bind_configuration` → `calculate_total_cost` → `FactRecord` persist chunks of 500.

**Publish:** `PublicationService.create` → store `create` with `snapshot_facts` → pointer replace. `/current/` = `publication_current` for scoped client.

---

## 9–10. API and authentication

[V1_API_REFERENCE.md](V1_API_REFERENCE.md), [V1_SECURITY.md](V1_SECURITY.md).

SPA routes (JS, not API): `/`, `/admin`, `/admin/upload`, source-files, batches, processing-runs, review, facts, history, logic, labels, catalogs, publications, downloads, `/client`, `/client/facts`. Access flags in `app.js`.

---

## 11–21. Excel, facts, pivots, metrics, M

[V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md), [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md), [DAILY_REPORT.md](DAILY_REPORT.md).

---

## 22. Power BI

[V1_POWER_BI.md](V1_POWER_BI.md).

---

## 23. Testing inventory

Run: `python -m pytest` (pythonpath packages/*). Marker `postgres` needs `DFIP_TEST_DATABASE_URL`.

| File | Purpose (from name + spot checks) |
|---|---|
| test_smoke.py, test_structure.py, test_config.py | Foundation |
| test_p1_schema.py, test_p1_label_contract.py | Schema/labels |
| test_p2_config.py | Catalogs/resolvers |
| test_p3_ingest.py | Ingest |
| test_p4_transform.py, test_p4_workbook_reconciliation.py | Transform/P8 math |
| test_p5_api.py | API |
| test_p6_web.py | Web static |
| test_p7_publication.py, test_p7_client_auth.py | Publication |
| test_p8_uat.py, test_p8_client_report.py | UAT / static xlsx |
| test_p9_authz.py, test_p9_row_calculation.py, test_p9_publishedfacts_scale.py | Authz/scale |
| test_p10_*.py | Report identity / cache items / release |
| test_p11_pivot_report.py | Native pivots |
| test_daily_report.py | Nine-sheet contract |
| test_v2_phase1_* | Postgres stores, RLS, API, facts, publish integrity, migrations, config |
| test_v2_phase2a_* | QA, reporting, KPIs, RLS |
| test_v2_phase2b_* | Power BI **files** + postgres views |
| test_v2_http_* | Upload, QA HTTP, workflow |
| test_v2_web_ui.py, test_v2_upload_async.py, test_v2_batch_reprocess.py | SPA/async/reprocess |
| test_v2_operational_e2e.py, test_v2c_e2e.py | E2E / catalog e2e |
| test_v2c_catalog.py, test_v2c_postgres.py | Catalog write |
| test_publication_snapshots.py, test_publication_fact_scope.py | Snapshots |
| test_incremental_monthly.py | Monthly increment |
| test_fact_persist_chunks.py, test_fact_run_paging.py, test_staged_row_paging.py | Paging/chunks |
| test_qa_finding_persist.py, test_automatic_qa.py | QA |
| test_security_hardening.py | Security |
| test_source_file_storage.py | Object store |
| postgres_support.py, catalog_support.py, http_ingest_support.py | Fixtures |

**Level 3 / operational e2e:** `test_v2_operational_e2e.py` exists. Full vintage reconciliation **skips** if source workbooks absent (`STATUS.md`). Desktop Excel COM Refresh All is **not** unattended CI.

**Power BI tests** do not open Desktop.

---

## 24. Docker / deploy

[V1_ARCHITECTURE.md](V1_ARCHITECTURE.md) + [V1_OPERATIONS_RUNBOOK.md](V1_OPERATIONS_RUNBOOK.md). No k8s manifests found. No production deploy playbook beyond `.env.example` honesty.

---

## 25. Logging / observability

- Framework: stdlib + uvicorn; `DFIP_LOG_LEVEL` in example.  
- Health: liveness only.  
- Publication audit: columns `published_by`, `published_at`, `notes`.  
- Request logging: default uvicorn **UNKNOWN** header redaction.  
- No Sentry/OpenTelemetry in pyproject.

---

## 26–28. Failures, runbook, new month

[V1_TROUBLESHOOTING.md](V1_TROUBLESHOOTING.md), [V1_OPERATIONS_RUNBOOK.md](V1_OPERATIONS_RUNBOOK.md).

---

## 29–30. Security and limitations

[V1_SECURITY.md](V1_SECURITY.md), [V1_COMPLETION_AUDIT.md](V1_COMPLETION_AUDIT.md).

### Known limitations (severity)

| Sev | Item |
|---|---|
| CRITICAL | Live Refresh All can destroy PivotTables (FieldN / ExternalData_1) |
| CRITICAL | Treating empty-DSN or demo JWT as production |
| HIGH | App-level authz ≠ production tenant isolation |
| HIGH | HS256 shared secret; JWT 1h; SPA storage |
| HIGH | Power BI canvas absent |
| MEDIUM | Static xlsx may retain mashup XML; lineage UUIDs on sheet |
| MEDIUM | QA warnings noisy; UI aggregation |
| MEDIUM | STATUS.md drift (HTTP upload) |
| LOW | Compose trust auth on optional db |
| LOW | API vs package version strings |
| COSMETIC | Trailing spaces in sheet names (required for Excel identity) |

---

## 31. Architectural decisions (rationale)

| Decision | Support | Tag |
|---|---|---|
| Publication pointer vs mutating facts for clients | Code + PUBLICATION.md | VERIFIED behavior; INFERRED “so Excel always hits one URL” |
| `/publications/current/facts` not `/facts` | M script + P9 | VERIFIED |
| Static client-report + cache rebind | client_report_download docstring | VERIFIED response to live mashup/pivot failure |
| Strip query tables on download | same | VERIFIED |
| Clear Settings credentials | same | VERIFIED |
| Snapshot `publication_fact` | migration 000018 comments | VERIFIED immutability for complete |
| In-memory default | app.py | VERIFIED local/tests |
| No fake queryMashup zip | tests + DESKTOP_ACCEPTANCE | VERIFIED Excel Repair history |
| RLS as defense in depth | app description | VERIFIED comments |
| DirectQuery for PBI | powerbi README | VERIFIED spec; canvas NOT VERIFIED |
| Sum-then-divide KPIs | kpis.py | VERIFIED |
| Single upload thread | ThreadPoolExecutor max_workers=1 | VERIFIED |

---

## 32. Function inventory (project-specific, condensed)

Full signatures live in source. Below: FILE | FUNCTION | PURPOSE | NOTES.

### API / auth

| File | Function / class method | Purpose |
|---|---|---|
| app.py | `create_app` | Factory, stores, routers |
| app.py | `validate_persistence_settings` | Prod requires DSN |
| app.py | `health` | Public health |
| auth.py | `validate_auth_settings` | Refuse silent no-auth |
| auth.py | `authenticate_bearer` | Parse Bearer |
| auth.py | `_authenticate_jwt` / `_authenticate_dev_token` | Modes |
| auth.py | `issue_access_token` | HS256 |
| auth_routes.py | `login` / `logout` / `refresh` | Session JWT |
| password.py | `verify_password` / hash helpers | PBKDF2 |
| membership.py | `apply_identity` | Bind DB identity to Principal |
| deps.py | `get_principal` / `require_inspector` | FastAPI deps |
| roles.py | `can_publish` / `can_inspect` | Role gates |
| routes.py | list/get source-files, batches, process, staged-rows, runs, qa, facts, history, session | Inspector + session |
| publication_routes.py | create/list/current/facts/downloads/client-report | P7 |
| publication_service.py | `PublicationService.create/get_current/list_*` | Pointer + snapshot load |
| upload_routes.py | `upload_workbook` | Multipart |
| upload_service.py | UploadService + `_assert_xlsx_payload` | Size/ZIP/pipeline off-loop |
| catalog_routes.py | CRUD activate download | Logic/Labels |
| catalog_service.py | import/list/activate | Catalogs |
| published_download.py | `render_published_csv/xlsx` | Slice files |
| service.py | `fact_to_response`, ReadService | Mapping |
| errors.py | handlers | HTTP errors |
| source_storage.py | `build_source_object_store` | Memory vs disk |
| identity_store.py | get_credential, increment_token_version | Users |
| qa_workflow.py | HTTP QA evaluate | Persist findings |

### Core

| File | Symbol | Purpose |
|---|---|---|
| ingest/headers.py | `expected_source_headers`, `match_header_rows` | Contract |
| ingest/reader.py | `inspect_workbook`, `iter_source_rows`, `sha256_file` | Read xlsx |
| ingest/pipeline.py | ingest entry (batch/run) | P3 |
| ingest/store.py | InMemoryIngestStore | Memory |
| transform/engine.py | transform run, `ConfigBinder` | P4 |
| transform/extract.py | `extract_source_fields` | Typing |
| transform/labels.py | `bind_configuration*` | P2 bind |
| transform/cost.py | `calculate_total_cost` | H formula |
| transform/derive.py | `month_label`, `hhh`, `variation_id_key` | Derived |
| transform/reconcile.py | independent recompute | P8 |
| transform/store.py | InMemoryFactStore | Memory |
| transform/fact.py | `FactRecord`, `FactKey` | Grain |

### Config / db / analytics / web

| File | Symbol | Purpose |
|---|---|---|
| settings.py | `load_settings` | Env |
| resolve.py | campaign/template/rate/group resolvers | P2 |
| catalog.py / store.py / workbook.py / rate_cards.py | catalogs | P2 |
| dfip_db/* | Postgres* stores, `migrate`, `bind_rls` | Persistence |
| qa.py | `evaluate_qa`, `verdict_from_findings` | QA |
| kpis.py | `compute_kpis`, `KPI_SPECS` | Metrics |
| divide.py | `safe_divide` | KPI math |
| grains.py / aggregate.py / powerbi_contract.py | analytics helpers | |
| client_workbook.py | `FACT_HEADERS`, `build_client_report` | Scaffold **not** native mashup |
| client_report_download.py | `render_client_report_xlsx` | Client file |
| daily_report.py | `REPORT_CONTRACTS`, `report_formula`, `aggregate_published_rows` | P10/P11 contract |
| pivot_report.py | `bind_pivot_cache_for_snapshot`, `assert_native_pivot_package`, XML builders | P11 |
| published_facts_pages.py | paging helper | |
| app.py (web) | SPA + config.json | P6 |
| api_client.py | server-side HTTP helper | tests/tools |

Callers: HTTP routes → services → stores; upload_service → ingest pipeline → engine → QA; publication → fact snapshot + render xlsx.

Errors: `ApiError` subclasses; `HeaderContractError`; `RowValidationError`; `ConfigurationBindingError`.

---

## 33. Class inventory (project-specific)

| Class | File | Purpose |
|---|---|---|
| Settings | settings.py | Env |
| Principal | auth.py | Caller |
| PublicationService | publication_service.py | Publish |
| UploadService | upload_service.py | Ingest HTTP |
| CatalogService | catalog_service.py | Catalogs |
| ReadService | service.py | Reads |
| FactResponse and page models | schemas.py | HTTP |
| PublicationRecord / CurrentRecord | publication_store.py | Memory rows |
| FactRecord / FactKey / RowRejection / TransformResult / ConfigBinder | transform | P4 |
| HeaderMatch / HeaderContractError | headers.py | Ingest |
| CostResolution | cost.py | Cost |
| KpiSpec | kpis.py | KPI |
| QaFinding / QaContext / QaRule | qa.py | QA |
| RlsContext | rls.py | RLS |
| ReportSheetContract / ReportPivotBinding | daily_report / pivot_report | Excel |
| Identity stores | identity_store.py | Users |
| InMemory* / Postgres* stores | various | Persistence |

---

## 34. Constants (non-secret)

| Name | Value | Impact |
|---|---|---|
| MAX_PAGE_LIMIT | 200 | JSON pages + M PageLimit |
| DEFAULT_PAGE_LIMIT | 50 | API default |
| MAX_UNCOMPRESSED_BYTES | 512 MiB | Upload ZIP claimed total |
| dfip_upload_max_bytes default | 64 MiB | Multipart; FY-2026 Raw is 51.34 MiB |
| dfip_download_max_rows default | 75000 | Downloads |
| dfip_auth_token_ttl_seconds | 3600 | JWT |
| ENGINE_VERSION | 0.4.0 | Transform |
| FACT_PERSIST_CHUNK_SIZE | 500 | Commits |
| SNAPSHOT_CHUNK_SIZE | 500 | publication_fact |
| PIVOT_CACHE_ID | 1 | Excel |
| PageLimit (M) | 200 | Mashup |
| DEFAULT_CLIENT_ID | a0000000-…0001 | Demo/tests |
| FAKE_MASHUP_ZIP_PARTS | queryMashup paths | Must be absent |
| PUBLISHABLE_QA_VERDICTS | pass, warn | Publish gate |
| ADDITIVE_HEADERS / MEASURE_HEADERS / FACT_HEADERS | see source | Reports |
| SERVICE_FILTER_LOGIC_1 | Service \| FMS & LMS \| Campaigns | Service sheet |
| AMC_GROUP / D2C_GROUP / SERVICE_GROUP | Group7 / Group5 / Group2 | Defaults |

---

## 35. SQL / migrations (filenames)

| File | Topic |
|---|---|
| 20260823000001_p1_extensions.sql | Extensions |
| 000002_p1_tables.sql | Core tables |
| 000003_p1_indexes.sql | Indexes |
| 000004_p1_seed.sql | Seed KPI/rate/client |
| 000005–000009 | Config versions, campaign v1/v2, templates, client KPIs |
| 000010 | Batch ingest metadata |
| 000011 | V2 identity |
| 000012 | Tenant denorm |
| 000013 | RLS roles |
| 000014 | Analytics reporting `rpt_*` |
| 000015 | Catalog write grants |
| 000016 | Processing run failure progress |
| 000017 | Publication fact scope |
| 000018 | publication_fact snapshot |
| 000019 | app_user password |

Apply via `dfip_db.migrate`. Views `rpt_published_fact`, dims in 000014. Triggers/functions: see those files (not copied here). Inspector SQL: `dfip_db/sql_inspect.py`.

---

## 36–39. API table, dictionary, traceability, acceptance

See dedicated files + [V1_COMPLETION_AUDIT.md](V1_COMPLETION_AUDIT.md).

---

## 40. V1 vs V2 (product)

**V1 complete in this repo (with limitations above):** ingest, transform, QA, publish pointer + snapshot, JWT/dev_token, SPA, Excel native template, static client-report, tests, SQL migrations, optional Postgres.

**Not V1:** verified Power BI report, Entra, worker queue, safe live pivot refresh, production multi-tenant, lockfile-pinned deps, compose long-running API.

**Already in tree but named V2:** Postgres persistence, `rpt_*`, HTTP upload, catalog HTTP, password JWT, PBI **source package**.

---

## 40b. Final implementation-status matrix

Full PASS/PARTIAL/FAIL table, contradictions, and remaining gaps: **[V1_COMPLETION_AUDIT.md](V1_COMPLETION_AUDIT.md)** (authoritative for this documentation pass).

| Reader question | Where answered |
|---|---|
| What V1 is | This file §§1–4; README |
| Architecture / components | [V1_ARCHITECTURE.md](V1_ARCHITECTURE.md) |
| APIs | [V1_API_REFERENCE.md](V1_API_REFERENCE.md) |
| Authn/z | [V1_SECURITY.md](V1_SECURITY.md), [V1_RLS.md](V1_RLS.md) |
| Database / inventory | [V1_DATABASE.md](V1_DATABASE.md), [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md) |
| Ingest / transform / QA / publication / current pointer | Architecture + API + stores |
| Excel / PivotTables / Power Query / static download | [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md) |
| Power BI | [V1_POWER_BI.md](V1_POWER_BI.md) — **not implemented as pbix** |
| Operations / troubleshooting / deployment / config | Runbook, troubleshooting, this file §41, `.env.example` |
| Testing | [V1_TEST_INVENTORY.md](V1_TEST_INVENTORY.md) |
| Security / limitations / known failures | Security, Excel Refresh All, audit §11–14 |
| Source inventory | [V1_SOURCE_INVENTORY.md](V1_SOURCE_INVENTORY.md) |
| Data dictionary | [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md) |
| Exact completion status / V2 naming | [V1_COMPLETION_AUDIT.md](V1_COMPLETION_AUDIT.md) |

Do not claim Power BI canvas, safe live Refresh All, or production RLS isolation as implemented.

---

## 41. How to reproduce an environment

1. Python 3.11+, `pip install -e ".[dev]"`.  
2. `.env` from example.  
3. `python -m dfip_api` and `python -m dfip_web`.  
4. Optional Postgres + migrate.  
5. Do not expect git history of features.  
6. Do not Refresh All the live xlsx for client delivery.

---

## Duplicate / unused / drift notes

- `GET /facts` vs published facts — **intentional duplicate-looking APIs**.  
- P10 formulas vs P11 pivots — dual report engines; delivered sheets are pivots.  
- `STATUS.md` HTTP upload sentence — **LEGACY documentation**.  
- `apps/worker` — unused.  
- `build_client_report` — test/scaffold; dangerous if run on native file.
