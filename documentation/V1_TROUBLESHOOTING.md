# DFIP V1 — Troubleshooting

For each failure: symptom → likely cause → verify → safe recovery → what not to do.

---

## Invalid source / header contract

**Symptom:** Upload 422; ingest error; 0 staged.  
**Cause:** Sheet/headers ≠ `expected_source_headers()`; wrong file type; not xlsx ZIP.  
**Verify:** Compare to `packages/core/dfip_core/ingest/headers.py` / SCHEMA.md.  
**Recovery:** Export native Web Engage workbook; do not trim headers.  
**Do not:** Manually rename columns in a way that breaks exact-match contract.

## Workbook too large

**Symptom:** 413 or 422 uncompressed cap.  
**Cause:** Multipart file > `DFIP_UPLOAD_MAX_BYTES` (default 10 MiB; local demo may be 50 MiB), more than `DFIP_UPLOAD_MAX_FILES` (5) parts, total multipart > `DFIP_UPLOAD_MAX_TOTAL_BYTES` (default 20 MiB; local demo may be 100 MiB), JSON body > `DFIP_JSON_MAX_BODY_BYTES` (256 KiB), ZIP uncompressed > 512 MiB (`Workbook uncompressed size exceeds the allowed limit.`), or more than 1024 ZIP members / traversal names.  
**Verify:** File size vs settings; `MAX_UNCOMPRESSED_BYTES` / `MAX_ZIP_MEMBERS` in `upload_service.py`.  
**Recovery:** Raise env only with operator approval; or split/compress source **without** breaking headers.  
**Do not:** Disable ZIP checks.

## Too many login or setup attempts

**Symptom:** 429 `Too many requests.` after several generic 401s.  
**Cause:** Process-local 5 failures / 10 minutes by direct peer IP or username (login and publisher bootstrap).  
**Verify:** Wait for the window; a successful login clears buckets. `X-Forwarded-For` is ignored unless the TCP peer is loopback (Caddy).  
**Do not:** Treat 429 as proof a username exists. There is no database lockout.

## Authentication failure / expired JWT

**Symptom:** 401; Excel mashup error; SPA kicked to credentials.  
**Cause:** Missing Bearer; wrong secret; `exp` passed (leeway 0); `ver` mismatch after logout; `alg=none` rejected.  
**Verify:** `GET /session` with same token; check `DFIP_AUTH_MODE` / secret on **the running API process**.  
**Recovery:** Login/refresh; update Settings Bearer on **live** template only.  
**Do not:** Commit tokens; disable `verify_exp`.

## 403 inspector / publish

**Symptom:** Client role hitting `/facts` or `/uploads`.  
**Cause:** `can_inspect` / `can_publish` false (`roles.py`).  
**Verify:** `GET /session` role.  
**Recovery:** Use admin/publisher token.  
**Do not:** Grant platform-wide rights via empty client_id in production.

## API / CORS / download filename

**Symptom:** Browser blocks download; missing `Content-Disposition`.  
**Cause:** `DFIP_WEB_ORIGIN` mismatch; API not restarted after CORS change.  
**Verify:** Preflight; `expose_headers` includes Content-Disposition (`app.py`).  
**Recovery:** Align origin; restart API.  
**Do not:** Open CORS to `*`.

## Persistence unavailable (503)

**Symptom:** 503 `PERSISTENCE_UNAVAILABLE`.  
**Cause:** Postgres down; bad DSN; pool error (`DatabaseUnavailableError`).  
**Verify:** authenticated `GET /api/v1/ops/ready` (`database.status`); `DATABASE_URL`; **not** public `/health` (health does not connect).  
**Recovery:** Fix DSN/sslmode; restart. In-memory mode if DSN empty (data loss).  
**Do not:** Point pytest postgres URL at production.

Unexpected 500s return `An unexpected error occurred.` Server logs `unexpected-error type=… route=…` without Authorization, Cookie, DSN, or request bodies.

Abandoned processing: API start logs `abandoned-run-sweep marked=N`. Filter `GET /api/v1/processing-runs?status=failed`. Retry is unchanged P13B behavior.

## Publication refused

**Symptom:** 422 on `POST /publications`.  
**Cause:** Run not `succeeded`; QA `fail`/`unavailable`/missing; client mismatch; bad period; bad `fact_scope`.  
**Verify:** `GET /processing-runs/{id}` `qa_verdict`.  
**Recovery:** Fix data/QA; `warn` is allowed.  
**Do not:** Bypass QA in code.

## Invalid / empty current publication

**Symptom:** `items=[]`, `total=0`; header-only CSV; empty pivots.  
**Cause:** No `publication_current`; wrong JWT client; unpublished.  
**Verify:** `GET /publications/current`.  
**Recovery:** Publish succeeded run.  
**Do not:** Point Excel at `GET /facts`.

## Duplicate / restatement confusion

**Symptom:** History rows; counts change.  
**Cause:** Same grain restated; `fact_campaign_day_history`.  
**Verify:** `/facts/history`.  
**Recovery:** Expected. Publish the run you intend.  
**Do not:** Delete history tables ad hoc on live DB.

## Missing metrics in pivots

**Symptom:** Blank values; `#NAME?` on calculated fields.  
**Cause:** PivotCache failure after Refresh All; or field not in `MEASURE_HEADERS`.  
**Verify:** cache fields named vs Field2…; `cacheId`.  
**Recovery:** Re-download `client-report.xlsx`. Restore live template from git.  
**Do not:** Refresh All to “fix” it.

## PivotCache corruption / blank PivotTables

See [V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md) § failure.  
**Do not:** Ship the live template after Refresh All.  
**Do not:** Trust “29k rows loaded” as pivot health.

## Query table / ExternalData_1

**Symptom:** Cache source name `ExternalData_1`.  
**Cause:** Live mashup load path.  
**Recovery:** Static generator path.  
**Do not:** Manually remap FieldN in a client file as a process — regenerate.

## Incorrect row counts

**Symptom:** API total ≠ Excel rows.  
**Cause:** Pagination not fully consumed in M; download cap; snapshot vs working set mix-up; period filter on publish.  
**Verify:** `pagination.total` vs sheet last row − 1; `snapshot_row_count`.  
**Recovery:** Fix M paging (template) or re-download static; check `fact_scope`/period.  
**Do not:** Compare `GET /facts` to client file.

## QA thousands of warnings

**Symptom:** UI shows large warning count.  
**Cause:** Often `QA-RATIO-GT-ONE` + some `QA-IMPOSSIBLE-FAILED`, not thousands of Failed>Sent. UI may show **total** findings not `by_rule`.  
**Verify:** `GET .../qa-findings` breakdown.  
**Recovery:** Warnings are publishable; investigate source quality separately.  
**Do not:** Assume transform invented Failed>Sent (Sent/Failed are 1:1).

## Power BI refresh failure

**Symptom:** No rows / connect error.  
**Cause:** Wrong role (`dfip_api` NOLOGIN); unset RLS GUCs; pointing at `fact_campaign_day`; no `.pbix` built.  
**Verify:** `reporting_login.example.sql`; `validation_kpis.sql`.  
**Recovery:** Follow `powerbi/README.md`.  
**Do not:** Embed service-role in Desktop.

## SPA cannot reach API

**Symptom:** Network error in browser.  
**Cause:** API down; `config.json` `apiBaseUrl` wrong; mixed bind 127.0.0.1 vs localhost cookies (usually CORS).  
**Verify:** `GET http://127.0.0.1:8000/health` (API liveness), authenticated
`GET /api/v1/ops/ready` (readiness), and `http://127.0.0.1:3000/config.json`.  
**Recovery:** Start both; match `.env`. Public `/health` does not prove Postgres.

## Login always invalid

**Symptom:** 401 on `/auth/login`.  
**Cause:** In-memory identity empty; user not seeded; wrong hash iterations.  
**Verify:** Identity store / `app_user` migration `20260829000019_app_user_password.sql`.  
**Recovery:** Seed user on Postgres; or use jwt/dev_token for local.  
**Do not:** Log passwords.

## Abandoned processing after API restart

**Symptom:** Batch/run stuck `pending`/`running` after the API process stopped.  
**Cause:** Worker died with the process; leftover rows are not resumed mid-chunk.  
**Verify:** After restart, abandoned runs are marked
`Processing abandoned because the API process restarted. Retry is available.`
Startup then auto-resumes recoverable `received`/`staged` batches. Poll
`GET /api/v1/batches/{id}` shows `stage` and `stuck`.  
**Recovery:** Wait for auto-resume. Publisher Retry /
`POST /api/v1/batches/{id}/process` is only for genuine failure or when
`stuck` remains true. Recovery creates a new `processing_run`. Then explicit
QA and explicit publish.  
**Do not:** Kill a live operator API to prove this. Disposable proof is
`tests/test_p13b_recovery.py` and `tests/test_run011_reliability.py`. Do not
publish from retry.

## Missing source archive

**Symptom:** Retry 422 `Source archive is missing. Upload the workbook again.`  
**Cause:** `received`/unstaged recovery needs the filesystem object; it was never
written or the directory is gone.  
**Verify:** `DFIP_STORAGE_ENDPOINT` on the API process; production-grade refuses
empty/HTTP endpoints.  
**Recovery:** Upload the workbook again (same SHA may recover once archived).  
**Do not:** Expect PostgreSQL to hold the xlsx bytes.

If the live archive directory was lost, restore the P13C `source-archive/` tree
from a verified backup into `DFIP_STORAGE_ENDPOINT` (disposable/local target
only; never into hosted/`dfip`). Then retry. Do not flatten object keys.

---

## Backup / restore verification failed

**Symptom:** `python -m dfip_api.backup verify` reports missing files, SHA
mismatch, size mismatch, malformed `storage_uri`, missing dump, or dump
checksum mismatch.  
**Cause:** Incomplete archive copy; file mutated after dump; dump as `dfip_api`
(RLS-empty); wrong backup root.  
**Verify:** Manifest inventory vs `source_file.storage_uri`; dump SHA-256.  
**Recovery:** Recreate the backup after stopping the API. Restore only into a
disposable local database with `--confirm-disposable`.  
**Do not:** Restore into `dfip`, hosted, or August. Do not delete archive
orphans except via the P13F company-purge CLI after eligibility. Do not re-run migrations on a complete dump.

## Inactive company / blocked live operations

**Symptom:** 403 `This company is inactive.` on login, select-client, upload, process, QA, publish, catalog mutate, or current report.  
**Cause:** `client.lifecycle_status=inactive`. Application-layer guard.  
**Verify:** Companies page status chip; `GET /api/v1/clients`.  
**Recovery:** Publisher `POST /clients/{id}/reactivate`. Historical publisher publication download still works during retention while the JWT remains bound to that company. The SPA does not call a successful refresh in that state; it reuses `GET /session`.  
**Do not:** Expect `rpt_*` / Power BI to hide the tenant. Do not purge from the SPA. Do not treat a refresh 403 as a sign-out if the message is `This company is inactive.`

## Company purge refused

**Symptom:** `python -m dfip_api.purge` JSON `ok: false`.  
**Cause:** Missing `--confirm-purge` / `--confirm-disposable` (or `--confirm-production-local` for colocated `dfip`); company still active; `purge_eligible_after` not reached or NULL; hosted DSN; default/packaged owner; unknown code.  
**Verify:** Dry-run `--code` identity (id/code/status/timestamps).  
**Recovery:** Deactivate and wait until eligibility, or use a disposable local database.  
**Do not:** Type the company display name as confirmation. Do not grant DELETE to `dfip_api` for company tables.

## Purge archive leftover

**Symptom:** Database company gone; `{archive}/{bucket}/{client_id}/` still exists; CLI `ok: false` / exit 2.  
**Cause:** Filesystem delete after SQL commit failed.  
**Verify:** Path is tenant-prefixed.  
**Recovery:** Remove that tenant directory only. Do not claim cryptographic erasure.  
**Do not:** Delete other companies' archive trees. Host journal retention is operator policy (`documentation/V1_DEPLOYMENT.md`).

---

## Debugging procedures

- API logs: uvicorn stdout / journald; `DFIP_LOG_LEVEL`. Host journal retention is operator policy.  
- OpenAPI (non-prod): `http://127.0.0.1:8000/docs`.  
- Publication audit: `publication` row `published_at`, `published_by`, `notes`, `snapshot_row_count`.  
- No dedicated APM in repo. **NOT IMPLEMENTED:** distributed tracing.
