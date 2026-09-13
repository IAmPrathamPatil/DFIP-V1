# DFIP V1 — Test inventory

**pytest was executed** 2026-08-30 on this machine. Tests were **not modified**.

RUN 011: `pyproject.toml` `[tool.pytest.ini_options]` uses `-ra --tb=short`. CI writes
`pytest-report.xml` so pass/skip counts survive a Windows Excel COM dump on stdout.

---

## Run record

| Item | Value |
|---|---|
| Command | `python -m pytest C:\Users\PRATHAM\Downloads\DFIP-V1\tests -q --tb=line` |
| Working dir / interpreter | Windows; Python 3.11 (Anaconda + user site-packages pytest) |
| Exit code | **0** |
| Wall duration | **263041 ms** (~4 m 23 s) |
| `--collect-only -q` | **652** collected items (sum of per-file counts; parametrize expands `def test_`) |
| Official footer `N passed, M skipped` | **NOT EMITTED** — stdout ends at pytest **warnings summary** after a **Windows fatal exception `0x80010108`** (COM/thread dump) around **11%** progress; tests **continued to 100%** |
| Parsed remaining progress glyphs | **588** `.` and **52** `s` on `[nn%]` lines (**640** of **652**). **12** items **UNKNOWN** (first progress block overwritten by the COM dump) |
| Failed | **not printed**. Exit 0 ⇒ **INFERRED 0 failures**; not independently confirmed by a footer |
| Errors (pytest `ERROR` status) | **none printed** |
| Skipped | **at least 52** visible `s`; true skip count **UNKNOWN** if some skips were in the dumped block |
| `DFIP_TEST_DATABASE_URL` | **unset** (postgres-marked tests skip) |
| `DATABASE_URL` | process unset; `.env` present with **empty** `DATABASE_URL` (boolean only) |
| Hosted DB | **not used** |

`def test_` grep ≈ **549** functions vs **652** collected items: **not a code bug** — pytest items include parametrize. Prior inventory counted functions only.

Warnings observed: Starlette/`httpx` TestClient deprecation; openpyxl slicer-list unsupported; openpyxl no default style (`test_v2_upload_async`).

---

## By category

| Category | Files (primary) | What they actually assert (from names + sampled bodies) |
|---|---|---|
| Unit / contract | `test_smoke`, `test_structure`, `test_config`, `test_p1_*`, `test_p2_config`, `test_p3_ingest`, `test_p4_transform`, `test_daily_report` (formulas), `kpis` via `test_v2_phase2a_kpis`, `test_automatic_qa` | Imports, schema SQL shape, P2 resolvers, ingest headers, transform math, sheet contracts, KPI specs, QA rules |
| API (TestClient, in-memory) | `test_p5_api`, `test_p7_publication`, `test_p7_client_auth`, `test_p9_authz`, `test_security_hardening`, `test_v2_http_*`, `test_v2_upload_async`, `test_v2_batch_reprocess`, `test_v2c_catalog` | HTTP status, envelopes, authz, upload/reprocess/QA/catalogs, OpenAPI presence in dev |
| Database / integration | `test_v2_phase1_*`, `test_v2_phase2a_*` (postgres), `test_v2_phase2b_postgres`, `test_v2c_postgres`, `test_publication_snapshots` (incl postgres case), `test_qa_finding_persist`, `test_fact_persist_chunks` | Stores, RLS, migrations, snapshots, 50k chunks — **skipped without test DSN** |
| Excel / OOXML | `test_p8_client_report`, `test_p11_pivot_report`, `test_p12_historical_reports`, `test_run005c_refreshable_workbook`, `test_run006_excel_format`, `test_run007_slicers`, `test_run008_sheet_visibility`, `test_run009_schema_extensibility`, `test_p9_publishedfacts_scale`, `test_p9_row_calculation`, `test_p10_report_identity`, `test_p10_cache_shared_items`, `test_daily_report` workbook ZIP, `test_company_workbook` | Native pivots, no fake mashup, snapshot rebind, slicers, page fields, company-bound filename/provenance, RUN 006 fonts/numFmts/widths, RUN 007 slicer sourceName/defaults, RUN 008 hidden PublishedFacts/Facts, RUN 009 frozen 45-col Excel plus approved-extra staging, P12 historical filename / immutability / isolation; **some COM desktop tests skip without Excel** |
| Publication | `test_p7_publication`, `test_publication_fact_scope`, `test_publication_snapshots`, `test_incremental_monthly`, `test_v2_phase1_publication`, `test_v2_phase1_publish_integrity`, `test_company2_isolation`, `test_publisher_company_selection`, `test_company_registry`, `test_company_create`, `test_p12_historical_reports` | Pointer, isolation, scopes, immutability, Company 1/2 JWT isolation, universal publisher select-client, generic registry + rename, Add Company, P12 current vs historical catalog |
| Catalog identity | `test_catalog_version_identity` | Stored version IDs and `rate_card_rule_id` owned by run `client_id` or NULL; packaged fallback vs Logic/Labels overlay; new-tenant process+publish; mismatch NULL on disposable Postgres |
| Company management | `test_company_management_acceptance` | Operator publisher setup; confirm-password client login; isolation; new-company process+publish+client report; SPA setup + provision forms |
| Security | `test_security_hardening`, `test_p9_authz`, `test_p5_api` leak tests, `test_p13a_production` | OpenAPI off in prod, scoped tokens, no secret leak in errors, production-grade fail-closed, bootstrap header, dotenv isolation |
| Durable recovery | `test_p13b_recovery` | Production storage gate, SHA replay vs staged recovery, abandoned pending/running sweep, same-batch received recovery, missing archive 422, no publish on retry, A/B isolation, worker finalize, fact restate |
| Monitoring / health | `test_p13d_health` | Public `/health` liveness (no DB, no secrets); authenticated `/ops/ready` 401/403/200/503; storage dir check; worker idle/busy; no archive walk; no backup verify; 500/503 logs; abandoned-sweep log count |
| Upload / request limits | `test_p13e_limits` | Login/bootstrap 5/10-minute throttle and 429; no username enumeration; multipart file-count and total-byte 413; JSON body 413; ZIP member/path; pagination 200/201; P13B 202/retry; 75k row cap; report semaphore; cheap `/health`; JWT `client_id` wins |
| Backup / restore | `test_p13c_backup` | Secret-free manifest, archive inventory, missing/SHA/size/malformed URI detection, dump checksum, restore refuses `dfip`/hosted, disposable Postgres dump→destroy→restore preserves current/historical publications, A/B isolation, workbook regen, P13B sweep |
| Company lifecycle / purge | `test_p13f_lifecycle`, `test_p13f_purge` | Active/inactive persist; deactivate blocks login/select/refresh/upload/process/QA/publish/catalog/current reports; historical publisher download remains; publication_current kept; reactivate restores; SPA keeps the existing JWT via GET /session when refresh is blocked for an inactive company; purge CLI dry-run/eligibility/confirmation/default-owner/hosted refusal; FK order; no CASCADE; A gone B intact; rollback; incomplete archive cleanup; identify-eligible never selects the only verified set |
| Deployment (P13G) | `test_p13g_deploy` | systemd/Caddy/env artifacts exist; no secrets in deploy files; loopback bind; restore/purge `--confirm-production-local` still refuses hosted URLs and disposable `dfip`; X-Forwarded-For trusted only from loopback |
| Acceptance / e2e | `test_p8_uat`, `test_v2_http_workflow`, `test_v2_operational_e2e`, `test_v2c_e2e`, `test_v2_web_ui`, `test_p6_web` | Full HTTP loops, SPA strings, UAT |
| Docs/release | `test_p10_release` | README/STATUS/PUBLICATION/SPA **string contracts** (locks stale V2 heading tokens) |
| Power BI spec | `test_v2_phase2b_model` | JSON/M/DAX files exist and match contract — **not Desktop** |
| Storage | `test_source_file_storage` | Object store adapters |
| Paging | `test_staged_row_paging`, `test_fact_run_paging` | Limits/offsets |

Vintage reconciliation `test_p4_workbook_reconciliation.py` **skips** without source workbooks (`STATUS.md`).

Every `test_*` **body** is still not line-audited (file/category inventory).

---

## Helper modules

| File | Role |
|---|---|
| `conftest.py` | shared fixtures |
| `postgres_support.py` | migrate + pool for postgres tests; **drops `public`** |
| `catalog_support.py` | catalog xlsx fixtures |
| `http_ingest_support.py` | multipart upload helpers |
