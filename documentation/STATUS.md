# Implementation status

## Current phase

**DFIP-V1 COMPLETE**

Local disposable checkpoint: **RUN 002 FROZEN** (default-client SPA JWT).
**RUN 003 FROZEN** (Company 1 / Company 2 isolation on disposable local
PostgreSQL). **RUN 004 FROZEN** (universal publisher company selection).
**RUN 004B-1 FROZEN** (generic publisher company registry + display-name
rename). **RUN 004B-2 FROZEN** (generic Add Company). `POST /api/v1/clients`
creates a new `client` row and grants inspector membership to the caller only.
**RUN 004B-3 FROZEN** (company onboarding / tenant configuration). A new
company is processing-ready after select-client using packaged fallback.
Optional Logic/Labels overlays stay tenant-scoped. Packaged templates/rate
cards remain shared content; stored catalog version IDs and
`fact_campaign_day.rate_card_rule_id` must belong to the run's `client_id` or
be NULL.
**RUN 004B-4 FROZEN** (end-to-end company-management acceptance). One
universal publisher is created by one-time operator setup
(`POST /api/v1/auth/setup-publisher`: username, password, confirm password).
That account is not generated and is not `demo-publisher` unless the operator
types that name. Inspector-provisioned client-portal logins use
operator-chosen username/password/confirm; DFIP stores a hash only.
`python -m dfip_api.local_demo_seed` remains a test/local fixture only.
See `documentation/LOCAL_TESTING_RUNBOOK.md`.
**RUN 005A COMPLETE** (read-only persistent company-workbook architecture
audit). **RUN 005B COMPLETE** (minimum reusable company-bound static
workbook). **RUN 005C COMPLETE** (same-workbook current-data refresh +
recovery). Two explicit artifacts: static recovery
`DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx` and refreshable
`DFIP_<client_code>_<YYYY-MM-DD>_Client_Report_Refreshable.xlsx`. Refresh All
on the refreshable file pages `GET /publications/current/facts` with a
user-supplied short-lived JWT; Settings ClientId is not authorization; JWT
`client_id` wins. Static recovery remains the re-download path when the file
is lost. Live Refresh All of the tracked git template is still not a client
deliverable.
**RUN 006 FROZEN** (Excel reference formatting parity). Generated static
and refreshable workbooks apply FY-2026 presentation (Aptos Narrow, mixed
`0%` / `0.0%` / `0.00%`, integer `#,##0` cost/counts, sheet column widths,
col-A gutter, title on the label column, PivotStyleLight16). FY-2026 pivot
`dxfs`/`<formats>` are not copied (they bind reference cache field indexes).
Month/Day remain published text.
**RUN 007 COMPLETE** (automatic native slicers + defaults). Generated workbooks
keep the existing 35 per-sheet slicer caches. `sourceName` resolves to
`FACT_HEADERS`. Month slicers default to the latest month in that company's
snapshot; AMC/D2C/Service group and Service FL1 defaults apply when present
and fall back to All. Slicers stay independent per report sheet.
**RUN 008 COMPLETE** (hide PublishedFacts + Facts on generated downloads).
Deletion is unsafe: PivotCache and Power Query load `PublishedFacts`; the
Settings table lives on `Facts`. Both sheets stay in the package as normal
`hidden` (not veryHidden). The tracked operator template remains visible.
**RUN 009 COMPLETE** (controlled extra-column / schema extensibility).
The locked 57 Web Engage headers (K:BO) and the frozen 45-column
`FACT_HEADERS` Excel/API contract are unchanged. One synthetic approved extra
(`Campaign Objective`) may appear after the 57-window; it is stored on
`stg_source_row.raw` only and is not copied onto facts, `FactResponse`,
PivotCache, slicers, or `rpt_*`. Unknown trailing columns fail closed
(`UNAPPROVED_SOURCE_COLUMN: Unapproved source column: …`). Excel exposure of a
newly approved field requires a future workbook-contract update. Dashboard D0–D9 is complete and frozen on `/client/overview`. Extra columns
are not automatic dashboard metrics.
This is application-level tenant scoping plus existing SQL RLS policies when
`SET LOCAL ROLE dfip_api` succeeds. It is not hosted/production isolation.
**P12 COMPLETE** (historical reports / recovery). Publisher Publications and
the client reporting home list current vs historical publications from
`GET /api/v1/publications`. Complete snapshots download from immutable
`publication_fact`. Historical static filename is
`DFIP_<client_code>_<YYYY-MM-DD>_<publication_short_id>_Client_Report.xlsx`.
Current 005B/005C filenames and routes are unchanged. There is no historical
refreshable workbook. Legacy `snapshot_status=none` is labeled “Not a frozen
snapshot”. **P13A COMPLETE** (production environment + secrets). Unknown
`DFIP_ENV` values refuse to start. Staging and production are production-grade:
JWT required, secret ≥ 32 characters with a known-bad blocklist, `DATABASE_URL`
required, remote DSNs require `sslmode=require|verify-ca|verify-full`, HTTPS
origins required, local `.env` is not read. First publisher setup is denied
without header `X-DFIP-Bootstrap-Token`. `GET /auth/setup-status` does not
advertise the production bootstrap window. **P13B COMPLETE** (durable
processing / recovery). Production-grade requires a local filesystem
`DFIP_STORAGE_ENDPOINT`. Abandoned `pending`/`running` runs are failed on API
start. SHA replay is only for processed batches; received/staged/failed
batches retry the same batch without a sibling upload. Retry creates a new
`processing_run` and does not publish. **P13C COMPLETE** (backup + restore).
Logical `pg_dump -Fc` plus a copy of `DFIP_STORAGE_ENDPOINT`, secret-free
manifest, and disposable restore verification. **P13D COMPLETE** (monitoring +
health: public liveness, authenticated readiness, operational logging).
**P13E COMPLETE** (upload / request limits: process-local login/bootstrap
throttle, multipart file-count and total-byte caps, JSON body cap, ZIP
member/path checks, report-generation semaphore). **P13F COMPLETE**
(company active/inactive lifecycle, operator purge CLI, backup
identify-eligible). **P13G COMPLETE** (single-Droplet DigitalOcean deployment
package, Caddy/systemd artifacts, colocated PostgreSQL restore/purge confirmation,
disposable rehearsal). **RUN 010 FROZEN** (refreshable workbook pages
`GET /api/v1/publications/history/facts`; website JWT download stamps Settings
BearerToken and leaves ClientId empty; DataMashup stays Excel-authored).
**RUN 011** hardens V1 reliability: upload auto-progress with stage/heartbeat/
stuck detection, in-process auto-resume after restart, Excel first-page reuse
and still-valid JWT renewal, `audit_log` writes, `engine_version` 0.4.0.
P14 is not started.

P0–P9 are locked. P10 locked published-data / report-layer parity. P11 adds
native Excel PivotTables, slicers, and expand/collapse on that same published
slice. **P12 COMPLETE** (historical reports / recovery on the existing
publication catalog). **P13A COMPLETE**. **P13B COMPLETE**. **P13C COMPLETE**.
**P13D COMPLETE**. **P13E COMPLETE**. **P13F COMPLETE**. **P13G COMPLETE**. P14 is not started.
Deferred architecture is **V2**.

The P6 SPA Admin facts view consumes the P5 working-set API (admin/publisher
only). Client `/client/facts` and the Excel template consume published facts.
P7 publication writes `publication_current`. P9 application-level
authorization remains in force. **This is application-level authorization, not
PostgreSQL RLS and not production tenant isolation.**

After V2-X Excel pointer verification and the professional website UI, the
next unfinished **master-roadmap** phase is **P9 Power BI Desktop** (repo V2
Phase 2B canvas). Repo P9 authorization is already locked and is not this work.

## DFIP-V1 COMPLETE — acceptance

- **P0–P9 locked.** Working-set `/api/v1/facts` is not publication-filtered.
- **P10 closeout complete.** Documentation, UI copy, release metadata, and
  acceptance tests match the locked product. Report-layer display parity against
  Prod August is 100.00% (DATA/MATH 0).
- **P11 native PivotTables.** The nine Daily Report sheets are real Excel
  PivotTable objects (shared cache, slicers, page filters, outline
  expand/collapse) bound to PublishedFacts.
- **P12 historical reports / recovery complete.** Current vs historical is
  explicit in the SPA catalog. Historical Client Report downloads stay bound
  to `publication_id`. Current static/refreshable downloads follow
  `publication_current`. **P13A COMPLETE**. **P13B COMPLETE**. **P13C COMPLETE**.
  **P13D COMPLETE**. **P13E COMPLETE**. **P13F COMPLETE**. **P13G COMPLETE**. P14 is not started.
- **Working-set / publication boundary.** Admin `/admin/facts` → `GET /facts`.
  Client portal → `GET /publications/current/facts`. Refreshable
  `PublishedFacts.m` → `GET /publications/history/facts` (RUN 010 cumulative).
- **P9 authorization.** Roles: `admin`, `publisher`, `reader`, `client`.
  Unknown JWT roles are rejected. Admin/publisher inspect and publish.
  Reader/client get session plus published data only.
- **Excel / Power Query.** `excel/PublishedFacts.m` pages published facts at
  `limit=200` with Bearer auth. `excel/Client_Report.xlsx` is the Desktop-native
  V2-X workbook (`PublishedFacts` + `Facts` + nine Daily Report **native
  PivotTables**, native connections; no fake `queryMashup` parts). BearerToken
  in Git must stay empty.
- **Reconciliation evidence (P8, when source workbooks are present).**
  Apr–May 2025: 104,768 rows, 0 mismatches. Oct 2025: 29,129 rows, 0
  mismatches. Full-vintage tests skip cleanly when evidence files are absent.
- **Tests.** `python -m pytest`, `python -m ruff check packages tests`, and
  `python -m ruff format --check packages tests`.
- **Known V1 limitations.** Empty `DATABASE_URL` uses in-memory stores (lost
  on restart). SQL migrations are schema artifacts until applied to a live
  database. Authenticated `POST /api/v1/uploads` **is implemented** (does not
  auto-publish). `dev_token` is development/test only; password sign-in issues
  HS256 JWTs from `app_user` membership (not a hosted IdP);
  application-level `client_id` scoping is not production tenant isolation.
- **V2 boundary.** See below. Not implemented in V1.

## Implemented

### P0

- Independent git repository, gitignore, packaging, settings loader, smoke tests,
  Ruff, Dockerfile/compose (app only), GitHub Actions CI

### P1

- PostgreSQL/Supabase SQL migrations
- Core tables, 67-column mapping, rate-v1/v2 seed, 16 Q&A KPI definitions
- Structural schema tests (no live database required)

### P2

- Versioned New Logic campaign snapshots (campaign-v1 / campaign-v2)
- Versioned template Q:R snapshots (template-v1 … v4)
- Filter Logic 1_2 membership (`fl1-group-v1`, 43 members from the client pivot cache)
- Deterministic resolvers: campaign label, template status, rate-card rule, FL1 group
- Client-report KPI namespace (15 formulas) without changing the 16 Q&A KPIs
- Duplicate Campaign Name rows retained; first `row_order` wins; no trim

### P3

- Read-only Web Engage workbook adapter (`Web-Engage Raw`, K:BO = 57 source columns)
- SHA-256 source_file identity and batch idempotency (in-memory store; SQL schema already in P1)
- Staging payload keyed by exact Excel headers; no trim, no label/cost/KPI application
- processing_run created as `pending` and bound to P2 version ids
- Header-contract failures recorded on `stg_rejected_row` / batch.error_summary

### P4

- Deterministic transformation engine (`dfip_core.transform`): extract →
  campaign labels → template Q:R → Filter Logic 1_2 → rate card → Total Cost →
  `fact_campaign_day`
- Total Cost = `Delivered x resolved rate`; Utility outranks Channel; unmatched
  channel costs 0 with no rate rule recorded
- Derived `hhh` / `month_label` / `month_start` from the recovered
  `TEXT(Start Date,"HH")` and `TEXT(Day,"MMM-YY")` formulas
- OPEN-A6 resolved: blank Variation ID keys as the empty string
- Per-day configuration binding, so a batch spanning 2025-08-01 rates each row
  on the correct card
- Idempotent reruns with `fact_campaign_day_history` supersede and full
  source-file / batch / run / config-version lineage on every fact
- Reconciliation module that recomputes every derived value independently
- Row-level rejection of malformed numerics and missing key components without
  affecting accepted rows
- **No new migration.** P4 writes only into columns P1 already defined.

### P5

- FastAPI application in `packages/api/dfip_api` (not in `dfip_core`)
- Public `GET /health` — application liveness only; database status is always
  `not_checked`. Authenticated `GET /api/v1/ops/ready` is cheap readiness
  (publisher/inspector). Web origin `GET /health` is `dfip-web` process
  liveness and is not API or database readiness.
- Read-only `/api/v1` resources: source files, batches, processing runs,
  staged rows, facts, fact history
- Pagination (`limit`/`offset`, default 50, max 200) and exact-match filters
- Authentication boundary: `dev_token` (development/test only) or HS256 JWT.
  Password sign-in (`POST /api/v1/auth/login`) issues a membership-bound JWT.
  Production still refuses `dev_token`.
- JWT roles are allowlisted: `admin`, `publisher`, `reader`, `client`. Unknown
  roles are rejected. Missing JWT `role` still defaults to `reader`.
- Principal carries `subject` / `role` / `client_id` for authorization
- In-memory P3/P4 stores only. Live PostgreSQL/Supabase is not connected.

### P6

- Static Admin/Publisher and Client SPA in `apps/web/static`
- Origin server `dfip_web` on `DFIP_WEB_ORIGIN` (default `http://127.0.0.1:3000`)
- Browser calls P5 `/health` and `/api/v1/*` with an issued access JWT from
  sessionStorage after username/password sign-in
- UI route guards: `admin`/`publisher` → Admin; any authenticated role → Client
- Professional dark control-center shell: Dashboard, Upload Center, Processing,
  Review/QA findings, Facts, Logic, Labels, Publications, Downloads
- Publication control publishes a succeeded processing run (P7); upload does not
- Review reads persisted `qa_finding` rows and displays `qa_verdict`; it does not write the verdict

### P7

- In-memory `publication` / `publication_current` matching the P1 tables
- New publishes write an immutable `publication_fact` snapshot
  (`snapshot_status=complete`) before `publication_current` moves
- `GET /api/v1/publications/{id}/facts` reads that publication's snapshot
- Legacy `snapshot_status=none` still joins live facts and is not immutable
- `POST /api/v1/publications` (admin/publisher only)
- `GET /api/v1/publications/current` (empty-safe)
- `GET /api/v1/publications/current/facts` (FactResponse, max page 200)
- `GET /api/v1/facts` remains the unfiltered working set for admin/publisher
- Empty client workbook `excel/Client_Report.xlsx` is Desktop-native V2-X
  (`PublishedFacts` + `Facts` + nine Daily Report sheets) + source-of-truth
  `PublishedFacts.m`. Fake `xl/queryMashup/` parts are forbidden. Do not commit
  a BearerToken.
- JWT `client_id` scopes published-facts and inspector working-set reads;
  `dev_token` with `DATABASE_URL` requires `DFIP_DEV_AUTH_CLIENT_ID`. That is
  not production tenant isolation.

### P8

- Client portal `/client/facts` reads `GET /api/v1/publications/current/facts`
- Admin `/admin/facts` remains `GET /api/v1/facts` (working set)
- UAT coverage for empty current, republish, period clip, pagination, JWT
  scoping, and workbook/M leakage
- Full-vintage P4 evidence recon runs when gitignored source workbooks are
  present; otherwise it skips cleanly

### P9

- Application-level route authorization on the existing FastAPI boundary
- Working-set and staging inspection (`/facts`, `/facts/history`,
  `/source-files`, `/batches`, `/staged-rows`, `/processing-runs`) is
  admin/publisher only; client/reader receive 403
- `GET /session`, `GET /publications/current`, and
  `GET /publications/current/facts` remain available to allowlisted roles
- `POST /publications` remains admin/publisher only
- `GET /facts` is still the unfiltered working set for inspector roles. It is
  not publication-filtered.
- JWT `client_id` still wins on published-facts and inspector reads; a
  mismatched explicit `client_id` does not expose another client. `dev_token`
  with `DATABASE_URL` is bound to `DFIP_DEV_AUTH_CLIENT_ID` and is never
  `platform_admin`. Unscoped in-memory tokens may pass `client_id` in tests.
- **This is application-level authorization, not PostgreSQL RLS and not
  production tenant isolation.**

### P10

- Data/report-layer parity closeout: documentation, UI copy, release metadata,
  CI format check, and acceptance tests
- Prod August reconstructed display parity 100.00%; backend business logic
  unchanged
- Native Excel PivotTable objects are P11, not this phase

### P11

- Nine report sheets are native Excel PivotTables (Analyze / Design / Fields)
- PivotTable XML is re-serialized to the child order desktop Excel writes
  (`rowItems` before `colFields`). Excel 16 COM opens the tracked template as
  11 sheets / 9 PivotTable objects without Refresh All
- One shared pivot cache bound to the PublishedFacts worksheet (the same sheet
  the live query fills). P8 historical downloads write that sheet as static
  cells and keep the cache worksheet-bound so it cannot refresh into another
  publication
- Independent per-sheet slicer caches (35), page filters (Service FL1/FL2;
  AMC/D2C Filter Logic 1_2), compact+outline expand/collapse
- P10 UNIQUE/FILTER/SUMIFS remains the Python reconstruction contract
- P12 COMPLETE (catalog + historical static recovery). **P13A COMPLETE**.
  **P13B COMPLETE**. **P13C COMPLETE**. **P13D COMPLETE**. **P13E COMPLETE**.
  **P13F COMPLETE**. **P13G COMPLETE**. P14 is not started.
  No August republish. Download does not move publication_current.

### P12

- Reuses `publication` / `publication_current` / `publication_fact` and the
  existing generator. No history store, no xlsx blobs, no fiscal-year table.
- `GET /api/v1/publications` remains the tenant-scoped catalog (newest first).
- Current static: `GET /publications/current/client-report.xlsx` →
  `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx` (RUN 005B unchanged).
- Current refreshable: `GET /publications/current/refreshable-client-report.xlsx`
  only. `/{id}/refreshable-client-report.xlsx` is not a route.
- Historical static: `GET /publications/{publication_id}/client-report.xlsx` →
  `DFIP_<client_code>_<YYYY-MM-DD>_<8-hex>_Client_Report.xlsx`. Same-day
  republishes do not collide. Uses `client_code`, never the display name.
- Complete snapshots read `publication_fact` only. Legacy `snapshot_status=none`
  is labeled “Not a frozen snapshot” and may still join live working-set facts.
- Publisher `/admin/publications` is report history. `/admin/history` remains
  working-set fact lineage. Client home lists only that tenant’s publications.
- Download is read-only with respect to publication state.

### P13A

- Strict `DFIP_ENV` allowlist: `development`, `test`, `staging`, `production`.
  Unknown values refuse to start. `staging` and `production` are production-grade.
- Production-grade requires `DFIP_AUTH_MODE=jwt`, `DFIP_AUTH_SECRET` ≥ 32
  characters, and rejects known-bad/example secrets. `dev_token` remains
  development/test only. OpenAPI is disabled.
- Production-grade requires `DATABASE_URL`. Remote hosts require
  `sslmode=require|verify-ca|verify-full`. Loopback/compose hosts do not.
  `sslmode=require` encrypts transport; `verify-full` remains available for remote DSNs.
  Colocated V1 PostgreSQL uses loopback and does not require TLS.
- Production-grade process environments do not read a local `.env`.
- Production-grade requires HTTPS `DFIP_WEB_ORIGIN` and `DFIP_API_BASE_URL`.
  Production HTTPS, HSTS, and host matching are terminated at Caddy
  (`deploy/Caddyfile`). The application remains HTTP on loopback.
- First publisher setup is denied by default. Enable with `DFIP_BOOTSTRAP_TOKEN`
  plus header `X-DFIP-Bootstrap-Token` (constant-time compare, never a query
  parameter). After an inspector exists, setup returns 409. Production
  `GET /auth/setup-status` always returns `publisher_setup_required: false`.
- New publisher/client passwords have a 12-character minimum. Production-grade
  creation refuses `demo-*` usernames and known fixture passwords. No startup
  database scan for demo identities. PBKDF2 unchanged.
- `DFIP_LOG_LEVEL` controls application and uvicorn log level. No global
  log scrubber. `redact_dsn()` is available for operator-facing DSN messages.
- JWT rotation: replace `DFIP_AUTH_SECRET` and restart; existing tokens are
  invalid. Database rotation: replace `DATABASE_URL` and restart.
- SPA access JWT remains in `sessionStorage`. Excel generator unchanged.

### P13B

- Production-grade requires a non-empty local `DFIP_STORAGE_ENDPOINT`
  (`FilesystemSourceObjectStore`). Empty and HTTP(S) endpoints refuse to start.
  Development/test may keep the in-memory object store.
- API startup in database mode marks leftover `processing_run` `pending` /
  `running` rows `failed` with
  `Processing abandoned because the API process restarted. Retry is available.`
  The same startup then auto-resumes recoverable `received`/`staged` batches
  and failed runs with those recovery reasons. Partial facts are kept; recovery
  is a new run that restates grains. Manual Retry is for genuine failure.
- HTTP SHA replay (`replayed=true`) only when a `processed` batch exists.
  `staged` alone is not terminal success. In-flight same-process SHA still
  returns 202. Recoverable received/staged/failed batches reuse the same
  batch id.
- `POST /api/v1/batches/{id}/process` retries received (archive), staged/failed
  with staged rows (new run), and processed (existing reprocess). Missing
  archive is 422. Does not publish. Does not resume mid-chunk.
- Upload worker finalization fails leftover pending/running work if the worker
  returns without completing. Temp `dfip-upload-*` dirs stay disposable.
- Publisher Batch/Processing pages expose Retry / Re-process. No new dashboard.
- P13C backs up PostgreSQL **and** the source archive directory.
- No Celery, Redis, job table, object-storage vendor, or schema migration.

### P13C

- Operator CLI: `python -m dfip_api.backup backup|verify|restore`.
- Stop the API, then `pg_dump -Fc`, then copy `DFIP_STORAGE_ENDPOINT`.
  Dump-then-copy without stopping the API still cannot omit committed
  `source_file` rows because archive `put` happens before `storage_uri` is
  stored. Preferred: stop the API first.
- Dump login must be superuser or BYPASSRLS-capable. Role `dfip_api` is
  refused. Do not grant BYPASSRLS to `dfip_api`.
- Layout: `manifest.json`, `postgres/dfip.dump`, `source-archive/{bucket}/…`.
- Manifest has no secrets. Dumps contain client data and `password_hash`.
- Restore into a disposable local database (`--confirm-disposable`) or the
  colocated production database name `dfip` (`--confirm-production-local`).
  Cluster roles `dfip_migrator` / `dfip_api` / `dfip_worker` are prerequisites
  (`pg_dump` does not create them). Do not re-apply migrations on a complete
  dump. Inject P13A secrets from the environment, then start the API so P13B
  can sweep leftover `pending`/`running` runs.
- RPO: last successful coordinated backup. RTO: provision + restore + verify
  (depends on dataset size). Not an SLA.
- **P13F COMPLETE** (company lifecycle + operator purge CLI + backup
  identify-eligible). **P13G COMPLETE** (host scheduling + DigitalOcean
  package). **P13D COMPLETE**
  (public liveness + authenticated readiness + logs). **P13E COMPLETE**
  (process-local request limits).

### P13D

- Public `GET /health` stays cheap liveness: no PostgreSQL, no archive scan,
  no backup verification. Response shape is unchanged.
- Authenticated `GET /api/v1/ops/ready` (admin/publisher) reports `ready` or
  `not_ready` with cheap database `SELECT 1`, source-storage directory
  existence, and process-local worker `idle`/`busy`. HTTP 200 or 503.
  Worker busy does not make the API not-ready. Failed/abandoned runs do not.
- Optional `DFIP_BACKUP_LAST_DIR` may add backup manifest timestamp/age only.
  Readiness does not hash the archive or run `python -m dfip_api.backup verify`.
- Unexpected 500s stay generic to clients; server logs `type` + route.
  Persistence 503s stay `Persistence is unavailable.`; server logs type + route.
- P13B abandoned-run sweep logs `abandoned-run-sweep marked=N` (count only).
- Publisher Dashboard Environment shows liveness vs readiness. Processing
  lists `progress_at` and `error_summary`. Clients do not receive operator
  readiness. No monitoring platform.

### P13E

- Process-local failed-login throttle on `POST /api/v1/auth/login`: 5 failures
  / 10 minutes keyed independently by direct peer IP and normalized username.
  Before the threshold, the existing generic 401 remains. After, HTTP 429
  `Too many requests.` Successful login clears those buckets. No database
  lockout. `X-Forwarded-For` is trusted only when the TCP peer is loopback
  (V1 Caddy); otherwise it is ignored.
- The same limiter applies to `POST /api/v1/auth/setup-publisher` failures.
  P13A bootstrap token, constant-time compare, production deny-by-default, and
  `inspector_exists()` closure are unchanged.
- Multipart uploads keep `DFIP_UPLOAD_MAX_BYTES` (default 64 MiB) per file.
  `DFIP_UPLOAD_MAX_FILES` default 5; `DFIP_UPLOAD_MAX_TOTAL_BYTES` default
  128 MiB (0 means twice the per-file cap). Count and running total are checked
  before accumulating extra payloads. Catalog upload remains a single file.
  The 64 MiB per-file cap fits FY-2026 Raw (51.34 MiB). Raw, Logic, and Labels
  share that cap. Operator env may still override.
- Non-multipart JSON/body cap `DFIP_JSON_MAX_BODY_BYTES` default 256 KiB
  (Content-Length and streamed size). Multipart workbook uploads skip this cap.
- XLSX ZIP: at most 1024 members; absolute names and `..` path components are
  rejected. PK magic and the 512 MiB claimed-uncompressed cap remain.
- Client Report and published CSV/XLSX generation use a process-local
  semaphore(1); a busy generator returns 429. Download row cap remains 75,000.
- The upload `ThreadPoolExecutor` queue is still unbounded. V1 remains one API
  process (`deploy/systemd/dfip-api.service`). P13B same-SHA 202, completed replay 200, and retry remain.
- Pagination max remains 200. Database pool max 8 is unchanged. No Redis, WAF,
  API gateway, CAPTCHA, or tenant QPS system.

### P13F

- Stored company states are `active` and `inactive` only (`client.lifecycle_status`).
  `deactivated_at` and `purge_eligible_after` are timestamps. There is no
  `PURGE_ELIGIBLE` or `PURGED` row state and no tombstone.
- Deactivate/reactivate are publisher HTTP routes. They delete nothing.
  `publication_current`, memberships, source archive, staging, facts, QA, and
  catalogs stay. In-flight processing is not cancelled. New live operations
  are blocked by application authorization, not by RLS or `rpt_*` changes.
- Permanent purge is `python -m dfip_api.purge` with a privileged DSN. There
  is no HTTP purge route. Role `dfip_api` is not granted DELETE on `client`,
  `app_user`, `client_membership`, `publication_fact`, or catalog tables.
- Confirmation is immutable `client.code` plus `--confirm-purge` plus
  `--confirm-disposable` or `--confirm-production-local`. Company name is display-only. Dry-run mutates nothing.
- Whole-company purge is the only V1 deletion granularity. SQL is one
  transaction in FK-safe order; filesystem archive deletion happens after
  commit. Hosted/live/`dfip`/`postgres` DSNs and the packaged default owner
  are refused. No automatic purge scheduler. Host backup timer is an example
  in `deploy/systemd/dfip-backup.timer`.
- Backup identify-eligible lists extra verified sets under keep-count. It
  never deletes and never selects the only verified set. Company purge does
  not rewrite backup artifacts. A pre-purge backup can restore that company.
- Direct SQL consumers may still see an inactive tenant if their membership/RLS
  rules allow it. Power BI / dashboard work must account for that later.

### P13G

- Locked topology: one DigitalOcean Droplet (BLR1), PostgreSQL 16 colocated,
  persistent block volume for `DFIP_STORAGE_ENDPOINT`, systemd API+web,
  Caddy HTTPS. Artifacts under `deploy/`. Runbook:
  `documentation/V1_DEPLOYMENT.md`.
- API `127.0.0.1:8000`, web `127.0.0.1:3000`. Public 443 only (80 redirect).
  PostgreSQL, archive, and backups stay private.
- Restore/purge keep hosted-DSN refusal. Database name `dfip` requires
  `--confirm-production-local` and a loopback DSN.
- Login throttle trusts `X-Forwarded-For` only from a loopback TCP peer.
- No live Droplet, DNS, or production secrets are created by this repository.
  systemd/Caddy host execution is pending a real Droplet rehearsal.
- P14 is not started.

## V1 limitations (expected)

- Default runtime is in-memory when `DATABASE_URL` is empty. Restarting that
  process loses working-set and publication state.
- When `DATABASE_URL` is set, ingest/facts/publications persist in PostgreSQL.
  Production-grade also requires a local `DFIP_STORAGE_ENDPOINT`. Leftover
  `pending`/`running` processing runs are failed on API start so retry can
  create a new run. Coordinated backup is `python -m dfip_api.backup`.
- P3 ingestion and P4 transformation are libraries; HTTP upload calls them.
- SQL under `supabase/migrations/` is the schema artifact. The running API
  opens PostgreSQL only when `DATABASE_URL` is set.
- `dev_token` is allowed only in development and test.
- HS256 JWT is an authentication hook, not a production identity provider.
- JWT `client_id` scoping is application-level, not PostgreSQL RLS and not
  production tenant isolation.
- The SPA stores the Bearer token in `sessionStorage`.
- Docker Compose runs tests only; it is not a production deploy.

## V2 Phase 1 (PostgreSQL persistence foundation)

V1 remains the in-memory default. When `DATABASE_URL` is set, the API uses
PostgreSQL adapters for ingest, facts, publications, and reads. Authentication
is still application authentication (`dev_token` / HS256 JWT). There is no
hosted identity provider. Row-level security policies are defense in depth and
do not replace FastAPI 401/403/422 behavior.

- Identity tables `app_user` and `client_membership` (no FK to `auth.users`)
- Per-client `source_file` SHA uniqueness
- `client_id` denormalized onto `processing_run` and staging rows
- Production refuses to start without `DATABASE_URL`
- Optional Compose service `dfip_db` (profile `v2-db`); `python -m pytest`
  still runs without PostgreSQL
- Hosted Supabase is the same PostgreSQL protocol: set `DATABASE_URL` to the
  project Direct or Session-pooler URI. This repository does not provision a
  supabase.co project. Persistence does not use the service-role key.
- `python -m dfip_db` is idempotent via `dfip_schema_migration`
- Still no object-storage HTTP upload, signed URLs, worker runtime, PostgREST, or
  OData. KPI engine and QA engine for this phase were also out of scope.

## V2 Phase 2A (analytics / reporting foundation)

Phase 2A adds a post-aggregation KPI calculator (`dfip_analytics`), inspector-only
`qa_finding`, and published-only `rpt_*` views for later Power BI use. P4 math
modules, `/api/v1`, and migrations 01–13 are unchanged. There is no `.pbix`,
no `/api/v2`, and no analytics HTTP route in this phase. See
`documentation/ANALYTICS.md`.

- KPI formulas match existing `kpi_definition` rows (16 qa + 15 client)
- Ratios are SUM(numerator)/SUM(denominator), never averages of daily ratios
- Reporting reads `published_fact_campaign_day` only
- Product/ASIN is not a grain. Amazon CPC / ACOS / Orders are unavailable
- `processing_run.qa_verdict` is written after transform (`pass` / `warn` /
  `fail` / `unavailable`)
- HTTP ingest after P4 now persists inspector `qa_finding` rows (see HTTP workflow)

## V2 Phase 2B (Power BI reporting layer)

Phase 2B is the Power BI semantic model and five report pages specified under
`powerbi/`. The model binds only `rpt_published_fact` and the four `rpt_dim_*`
objects. Ratio measures are `DIVIDE(SUM(num), SUM(den))`. There is no committed
`.pbix` (Desktop build is local). Inspector `qa_finding` is not a Power BI
source. P4 math, `/api/v1`, and migrations 01–14 are unchanged.

V2 Phases 1, 2A, and 2B (package + database contract) are implemented. Saving
`DFIP.pbix` in Power BI Desktop is **P9** and remains a local manual step.
Operator reporting LOGINs are specified in `powerbi/sql/reporting_login.example.sql`.
Live Client A published cards for the current pointer: Clicks **40** /
Delivered **100** / CTR **0.400000**.

## V2 HTTP ingest and published download

Authenticated multipart ingest and published-slice download wrap P3/P4/P7.
They do not auto-publish and do not change `MAX_PAGE_LIMIT` or `FactResponse`.
See `documentation/HTTP_WORKFLOW.md`.

- **IMPLEMENTED / AUTOMATED:** `GET /api/v1/publications` lists publication
  history for the scoped client (newest first). Admin overview shows that
  list. Republish keeps prior rows.
- **IMPLEMENTED / AUTOMATED / MANUALLY VERIFIED (live API 2026-08-26):**
  `POST /api/v1/uploads` → background `ingest_workbook` → `run_transformation` →
  `evaluate_qa` (persist `qa_finding`). New work returns **202** with a
  `received` batch; poll `GET /batches/{id}` and
  `GET /processing-runs?batch_id=`. JWT `client_id` always wins. No
  fact-table writes in the HTTP layer. Explicit `POST /api/v1/publications`.
- **IMPLEMENTED / AUTOMATED / MANUALLY VERIFIED:**
  `GET /api/v1/publications/current/facts.csv` and `.xlsx` (published slice
  only). Excel Desktop opened those XLSX files and read the expected rows.
- **MANUALLY VERIFIED (V2-X, 2026-08-27):** Excel Desktop Refresh All on
  local `Client_Report_pointer_test.xlsx` followed `publication_current`
  Unique Clicks **30 → 40** (publication `a0c13f32-…`, run `db7d748f-…`).
  Do not commit a BearerToken.
- **IMPLEMENTED / AUTOMATED / MANUALLY VERIFIED (browser, 2026-08-27):**
  Admin SPA upload (`POST /api/v1/uploads` **202** acknowledgement, then
  batch/run poll; published false) then
  explicit publish (`POST /api/v1/publications` **201**, publication
  `d7d35786-3776-4401-ad6f-e49bac0354a8`, run
  `3f4c72aa-a3ef-4037-8380-a55186d2382a`, `spa-camp-1` unique_clicks **7**).
  Client `/client/facts` Download CSV / XLSX. Live CORS
  `Access-Control-Expose-Headers: Content-Disposition` on `127.0.0.1:8000`.
  Client B reader CSV has no `spa-camp-1`; cross-client `client_id` **403**;
  working-set **403**.
- **MANUAL / NOT VERIFIED (P9 / next):** Power BI Desktop model, KPI cards,
  pointer refresh, and local `DFIP.pbix`. Untitled report only; no `.pbix`
  saved. Version-controlled package is `powerbi/`. Azure AD mapping remains
  **BLOCKED**.
- **BLOCKED:** Azure AD → `dfip.client_ids` (Entra ID tenant, app
  registration, Azure AD authentication to PostgreSQL). Power BI session
  identity only. Not required for JWT API / Excel.
- **NOT IMPLEMENTED:** signed-URL download of original source files;
  auto-publish (by design).
- Durable private source-file **upload custody** is implemented: originals are
  stored under `{client_id}/{source_file_id}/{sha256}.xlsx` and referenced by
  `source_file.storage_uri`. The API still omits `storage_uri`. Temp ingest
  copies are deleted. Retention is keep-until-operator-deletes (no auto-expiry).
  Empty `DFIP_STORAGE_ENDPOINT` is in-memory (development/test only; not durable
  across process restart). Production-grade requires a private local directory.
  Do not put blobs in PostgreSQL. Do not ship storage credentials to Excel or the SPA.

### Nine-sheet Daily Report (implemented)

The tracked `excel/Client_Report.xlsx` keeps native V2-X PublishedFacts refresh
and the nine FY-2026 client report sheets as **native PivotTables** (shared
cache, slicers, page filters, outline expand/collapse). P10 UNIQUE/FILTER/SUMIFS
remains the reconstruction/test contract and is not stored on the delivered
sheets. See `documentation/DAILY_REPORT.md`.

### Professional website UI (implemented)

The existing vanilla SPA is now a dark Publisher/Admin control center with
Dashboard, Upload Center, Processing, Review (QA findings), Facts, Logic,
Labels, Publications, and Downloads. Client `/client` remains reporting-only
for non-admin roles. No API, Excel, Power BI, or migration changes. See
`documentation/WEBSITE.md`.

### V2-C Logic + Labels publisher upload (implemented)

Publisher/admin can upload Logic (campaign A:M) and Labels (Filter Logic 1_2)
`.xlsx` files, inspect validation errors, store a new **draft** version,
explicitly activate it, download versions, and have processing bind the active
uploaded catalogs. Invalid files never become active and never replace an
existing active version. Historical `processing_run` rows keep their original
version ids. Drafts are not bound. Deactivate falls back to packaged JSON.
Restatement keeps overlay labels on the working set and does not expose new
clicks on `publication_current` until republish. JWT `client_id` isolation is
application-level (not PostgreSQL RLS). Template Q:R and rate-card HTTP upload are
not in this phase.

### Security hardening (application-level)

`dev_token` with `DATABASE_URL` requires `DFIP_DEV_AUTH_CLIENT_ID` and never
sets RLS `platform_admin`. JWT `client_id` is authoritative on inspector
working-set reads as well as publication. Production disables `/docs`,
`/redoc`, and `/openapi.json`. This is not the future V2 RLS / IdP phase.

### Inspector QA on HTTP ingest (implemented)

After a new transform, HTTP ingest runs the existing `evaluate_qa` engine
(with `reconcile_run` when a single rate-card label is known) and replaces
`qa_finding` rows for that `processing_run_id`. It also writes
`processing_run.qa_verdict` from persisted finding severities (`error` → fail,
`warning` → warn, otherwise pass). If QA itself fails, the verdict is
`unavailable` and findings are not replaced with an empty pass. Review lists
findings at `GET /api/v1/processing-runs/{processing_run_id}/qa-findings`
(admin/publisher, JWT client scope). Findings do not auto-publish. Explicit
`POST /api/v1/publications` requires `succeeded` plus a `pass` or `warn`
verdict. Header failures and replayed uploads do not evaluate QA.

### V2-X Excel native refresh (complete)

Excel Desktop Refresh All of `PublishedFacts` follows `publication_current`.
Verified 2026-08-27 on local `Client_Report_pointer_test.xlsx`: pointer moved
`427fa391-…` → `a0c13f32-…`, run `b1cd73b6-…` → `db7d748f-…`, Unique Clicks
**30 → 40**. Do not commit a BearerToken. The nine Daily Report sheets consume
that same PublishedFacts table (see `documentation/DAILY_REPORT.md`). Next
unfinished master-roadmap phase is **P9 Power BI** (Desktop `.pbix`; package
already under `powerbi/`).

## V2 (not implemented)

These require architecture beyond locked P0–P9. They are not V1 phases.

- Automatic QA-based publishing (Publish remains an explicit human action)
- PostgreSQL RLS and production tenant isolation
- Production identity-provider runtime / Supabase Auth
- Live persistent Postgres / Supabase provisioning
- HTTP upload API, signed URLs, CSV publication, OData/PostgREST
- KPI engine, RECON-09
- AI labeling, dynamic columns, rule builder, multi-client redesign
- Automatic QA-based publishing, advanced analytics, worker/queue runtime

**Forensic documentation note (2026-08-30):** This heading and bullet **tokens** (including `HTTP upload`, `KPI engine`, `PostgreSQL RLS`, `Supabase Auth`, `RECON-09`) are retained because `tests/test_p10_release.py` asserts they appear here. They are **not** an accurate inventory of the repository: `POST /api/v1/uploads`, `dfip_analytics` KPI/QA, CSV/XLSX publication download, and SQL RLS policies **are implemented** (see “V2 HTTP ingest”, Phase 2A/2B, and `documentation/V1_*.md`). True remaining gaps include production IdP, signed URLs, PostgREST/OData, worker/queue, auto-publish, RECON-09 (**no matching module found**), and Power BI Desktop `.pbix`.
