# DFIP V1 — Completion audit (final documentation pass)

**Date:** 2026-08-30 (final forensic documentation pass).  
**Git:** `5eebfa3` only — feature chronology **not** in VCS.  
**Production code:** **not modified** this pass. Markdown under `documentation/` only (plus this audit). Temporary inspect script was deleted after use.

**DOCUMENTATION STATUS: COMPLETE WITH VERIFIED GAPS**

The V1_* set plus companions is intended to answer “what is implemented in this repository” without opening the tree. Remaining gaps below are **named**, not silent.

---

## 1. Repository coverage

Inspected: `packages/`, `apps/`, `supabase/migrations/` (19 files), `excel/`, `powerbi/`, `tests/`, `documentation/`, Docker/CI, README/STATUS.

**V1 documents:**

| File | Role |
|---|---|
| [V1_MASTER_DOCUMENTATION.md](V1_MASTER_DOCUMENTATION.md) | Index + implementation-status matrix |
| [V1_ARCHITECTURE.md](V1_ARCHITECTURE.md) | Components, `/current/` |
| [V1_API_REFERENCE.md](V1_API_REFERENCE.md) | HTTP |
| [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md) | 45 report fields + **67 A:BO** copy from SCHEMA.md |
| [V1_DATABASE.md](V1_DATABASE.md) | Tables, views, indexes, migrations |
| [V1_RLS.md](V1_RLS.md) | Every policy predicate, helpers, roles, app vs RLS |
| [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md) | Every Postgres\* method: SQL, tx, callers, errors |
| [V1_SOURCE_INVENTORY.md](V1_SOURCE_INVENTORY.md) | Python modules |
| [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md) | Live vs static, ZIP listing, helpers, Refresh All |
| [V1_POWER_BI.md](V1_POWER_BI.md) | Spec-only |
| [V1_OPERATIONS_RUNBOOK.md](V1_OPERATIONS_RUNBOOK.md) | Commands |
| [V1_TROUBLESHOOTING.md](V1_TROUBLESHOOTING.md) | Failures |
| [V1_SECURITY.md](V1_SECURITY.md) | Authn/z, residual |
| [V1_TEST_INVENTORY.md](V1_TEST_INVENTORY.md) | Tests + pytest run record |
| [V1_COMPLETION_AUDIT.md](V1_COMPLETION_AUDIT.md) | This file |

Older `SCHEMA.md`, `STATUS.md`, `DAILY_REPORT.md`, etc. remain. **Where they conflict with source or this set, both sides are recorded** — they are not silently merged.

---

## 2. Source-code coverage

Public pipeline/HTTP: `V1_SOURCE_INVENTORY.md`. Postgres adapters: `V1_POSTGRES_STORES.md`. Excel helpers that bind PivotCache: `V1_EXCEL_REPORTING.md`.

**Still not signature-complete:** every `InMemory*` method; all 16 `mapping.py` / 20 `resolve.py` functions (module-level coverage only); every `daily_report._*` XML string builder.

---

## 3. API coverage

**36** `/api/v1` routes + `GET /health` — `V1_API_REFERENCE.md` (includes company create/rename).

---

## 4. Database coverage

19 migrations inventoried. RLS copied in `V1_RLS.md`. Store SQL in `V1_POSTGRES_STORES.md`.

**Live DB:** UNKNOWN (empty DSN at original audit). **`audit_log` writes:** login/upload/publish (RUN 011). **`dfip_worker_concurrency`:** in-process upload executor cap (1–4).

---

## 5. Data-pipeline coverage

`ingest_workbook` → transform → `evaluate_qa` → `PublicationService.create` → pointer + `publication_fact` → JSON/CSV/XLSX.

**RUN 011:** HTTP `ingest_workbook(..., engine_version=ENGINE_VERSION)` uses transform **`"0.4.0"`**. Reprocess uses the same constant.

---

## 6. Excel coverage

Fresh ZIP listing of tracked `excel/Client_Report.xlsx`: **113 parts**, cache **A1:AS2**, 9 pivots, query table `ExternalData_1`, connection `Query - PublishedFacts`, DataMashup props without plaintext M. Helpers inventoried.

**Still not line-audited:** DataMashup binary in `customXml/item1.xml`; Excel table display names inside `table1.xml`/`table2.xml`; every slicer cache XML body.

---

## 7. Power BI coverage

Specified, not runnable. No PBIX. Canvas NOT VERIFIED.

---

## 8. Test coverage

See `V1_TEST_INVENTORY.md`. Suite **ran** (exit 0); official passed/skipped footer **missing** due to Windows COM dump.

---

## 9. Configuration / deployment

Settings in master + `.env.example`. P13G artifacts under `deploy/` and
`V1_DEPLOYMENT.md`. No Kubernetes. This repository does not provision DigitalOcean.

---

## 10. Security

JWT, roles, Excel residual mashup, identity `rls=None`, RLS vs app filters: `V1_SECURITY.md` + `V1_RLS.md`.

---

## Feature / implementation-status matrix

| Topic | Expected | Actual in repo | Status | Evidence | Limitation |
|---|---|---|---|---|---|
| What V1 is | Product in this tree | FastAPI + SPA + ingest/transform/QA/publish/Excel | PASS | packages/, README | Single git commit |
| Architecture | Layered pipeline | VERIFIED | PASS | V1_ARCHITECTURE | |
| HTTP upload | Multipart ingest | `POST /uploads` | PASS | upload_routes.py | Does not publish; `force` form |
| QA engine | Findings | `evaluate_qa` | PASS | qa.py | STATUS V2 list still says unimplemented (**test lock**) |
| KPI engine | Sum-then-divide | `kpis.py` | PASS | | same STATUS lock |
| SQL RLS | Policies | 000013+ | PARTIAL | V1_RLS.md | Defense in depth; not production isolation |
| App `client_id` filters | Tenant WHERE | Read repo / stores | PASS | V1_POSTGRES_STORES | Distinct from RLS |
| `/current/` pointer | `publication_current` | Yes | PASS | publication_store | No rollback API |
| Live Refresh All + pivots | Safe | Unsafe | FAIL / KNOWN LIMITATION | Excel ZIP + prior desktop | Use static download |
| Static client xlsx | Snapshot | `render_client_report_xlsx` | PASS | client_report_download.py | Mashup binary may remain |
| Power BI pbix | Report | Absent | NOT IMPLEMENTED | glob 0 pbix | |
| audit_log writes | Audit | login/upload/publish INSERTs | PASS (best-effort) | dfip_api/audit.py | No secrets; swallowed errors |
| Signed URLs | Source download | Private filesystem archive | N/A V1 | DFIP_STORAGE_ENDPOINT | Not an HTTP object store |
| Worker queue | Async jobs | In-process ThreadPool (1–4) | PASS (min-safe) | app.py upload executor | No Celery/Redis; transform serialized |
| RECON-09 | Unknown spec | **No module** | NOT IMPLEMENTED | grep | |
| Rate limit login | Abuse control | 5 failures / 10 min | PASS | limits.py AttemptLimiter | Process-local |
| Hosted Postgres contents | Inspect | — | UNKNOWN | empty DSN | |
| pytest footer counts | Exact pass/skip | Incomplete stdout | PARTIAL | V1_TEST_INVENTORY | COM dump |

---

## 11. Known limitations

- Live Excel Refresh All vs intact PivotTables.
- P10-locked STATUS `## V2 (not implemented)` tokens vs implemented upload/KPI/QA/RLS.
- RLS not production isolation; identity bypasses `dfip_api` role.
- No Power BI canvas; no separate worker process.

---

## 12. NOT IMPLEMENTED (product)

Power BI Desktop report, Entra mapping, separate worker app, HTTP signed URLs, PostgREST/OData, auto-publish, production IdP, compose long-running API service, pointer rollback API, safe live pivot refresh.

**Named V2 in folders/tests** (`v2_*`, “V2 Phase 1” SQL comments) **are in this V1 tree** (Postgres, HTTP ingest, analytics). That naming is **not** “future V2 only.”

---

## 13. NOT VERIFIED / UNKNOWN

- Hosted DB object list, row counts, applied migrations.
- pytest official passed/skipped integers (footer missing); 12 of 652 collect items not in parsed glyphs.
- DataMashup bytecode / full `PublishedFacts.m` equality vs customXml.
- Excel COM desktop Refresh All on this workstation this pass.
- `table1.xml` / `table2.xml` display names.
- Whether every postgres test would pass with a live DSN.
- uvicorn production header logging.

---

## 14. Contradictions (explicit — not reconciled in code)

| Location A | Location B | Conflict |
|---|---|---|
| `SCHEMA.md` § P7 “no schema change” / “in-memory store. No new migration.” | Migrations `000017` `fact_scope`, `000018` `publication_fact` | Publication **did** gain schema after SCHEMA.md P7 text |
| `SCHEMA.md` P4 “Facts are never overwritten in place.” | `PostgresFactStore._upsert_many_once` **UPDATE** `fact_campaign_day` after copying prior row to history | Restate **is** an in-place UPDATE of the current grain; history holds the previous image |
| `SCHEMA.md` “`UNIQUE (sha256)`” on `source_file` | `000012` drops `source_file_sha256_key`, adds `UNIQUE (client_id, sha256)` | Global vs per-client SHA |
| `SCHEMA.md` / `rpt_dim_variation` “OPEN-A6” | Python `variation_id_key` is empty string, not a named sentinel | Terminology vs implementation; empty string **is** the implementation |
| `ingest_workbook` default | `ENGINE_VERSION` | **Resolved RUN 011** — both `0.4.0` |
| `STATUS.md` `## V2 (not implemented)` bullets (HTTP upload, KPI, RLS, …) | Source implements those | **Locked** by `test_p10_release.py` — forensic note in STATUS; do not delete tokens |
| `DAILY_REPORT.md` / STATUS “Refresh All verified” (mashup data) | PivotCache destruction after Refresh All | Data refresh ≠ intact pivots |
| pyproject **1.0.0** vs OpenAPI **0.5.0** / web **0.6.0** | Same repo | Three version strings |
| `V1_TEST_INVENTORY` older “549 tests” vs collect **652** | parametrize | Count basis differs |
| 000013 `published_fact_campaign_day` definition | 000017 then **000018 REPLACE** | Historical vs current view |

---

## 15. Remaining documentation gaps

1. InMemory store per-method catalog.  
2. DataMashup binary disassembly.  
3. Every pytest body.  
4. `WEBSITE.md` vs `views.js` pixel UI.  
5. Live Postgres inspection.  
6. Exact pytest passed/skipped footer — CI now writes `pytest-report.xml`.

These are **explicitly** not claimed complete.

---

## V1 remaining work vs later product

Unfinished **product**: safe live pivots, Power BI canvas, production isolation, IdP.  
**Do not treat folder name `v2_*` as unimplemented.**

## RUN 011 addendum (2026-09-03)

Minimum-safe hardening without a new worker service:

- Upload auto-progress: Received → Pending → Processing → Validating → Succeeded. Poll `GET /batches/{id}` resumes orphaned work. Retry is recovery-only.
- History facts SQL uses one `DISTINCT ON` materialization plus page `COUNT`.
- Disk `PublishedFacts.m` refreshes a still-valid access JWT and reuses page 0.
- `audit_log` writes for login, upload accept, and publish. Signed URLs remain N/A (private filesystem). RLS remains defense in depth.
- `dfip_worker_concurrency` caps the in-process upload executor at 1–4; transform stays serialized.

