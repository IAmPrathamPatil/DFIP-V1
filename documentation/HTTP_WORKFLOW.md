# HTTP ingest and published download

Authenticated workbook ingest and published-slice download wrap the existing
P3/P4/P7 libraries. They do not replace those libraries, do not auto-publish,
and do not change `GET /api/v1/facts` or `GET /api/v1/publications/current/facts`.

Status of each surface:

| Surface | State |
|---|---|
| `POST /api/v1/uploads` | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** (live API 2026-08-26) |
| `GET /api/v1/publications/current/facts.csv` | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** |
| `GET /api/v1/publications/current/facts.xlsx` | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** (Excel Desktop opened the files) |
| `GET /api/v1/publications/current/client-report.xlsx` | **IMPLEMENTED** / **AUTOMATED**. Static recovery workbook. Filename `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx`. 404 if none published. |
| `GET /api/v1/publications/current/refreshable-client-report.xlsx` | **IMPLEMENTED** / **AUTOMATED** / **DESKTOP VERIFIED** (RUN 005C / RUN 010). Same-file Refresh All pages history/facts. Filename `…_Client_Report_Refreshable.xlsm`. Website/API JWT downloads stamp the short-lived session token into Settings BearerToken. History stays authenticated. ClientId stays empty. |
| `GET /api/v1/publications/{publication_id}/client-report.xlsx` | **IMPLEMENTED** / **AUTOMATED**. Nine-sheet Client Report bound to that publication snapshot. Filename `DFIP_<client_code>_<YYYY-MM-DD>_<publication_short_id>_Client_Report.xlsx`. Not refreshable. |
| Tenant isolation on those routes | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** (API + Excel-opened downloads) |
| Explicit `POST /api/v1/publications` | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** |
| `GET /api/v1/publications` | **IMPLEMENTED** / **AUTOMATED**. Publication history for the scoped client (newest first). |
| Excel Desktop refresh via `PublishedFacts.m` | **MANUALLY VERIFIED** (V2-X complete). Local `Client_Report_pointer_test.xlsx` Refresh All followed the pointer (Unique Clicks 30 → 40). Do not commit a BearerToken. |
| Power BI Desktop / `.pbix` | **P9 / NOT VERIFIED**. Package under `powerbi/`. No `.pbix` saved. Desktop parked. |
| Durable private source-file custody | **IMPLEMENTED** / **AUTOMATED** |
| Object-storage signed-URL download of originals | **NOT IMPLEMENTED** |
| Azure AD → `dfip.client_ids` mapping | **BLOCKED**. Power BI / Postgres session identity only (parked with Power BI). Needs an Entra ID tenant, app registration, and Azure AD authentication to PostgreSQL. JWT `client_id` + `app_user` membership already scopes the API and Excel. Do not fake GUCs. |
| SPA sign-in (`POST /api/v1/auth/login`) | **IMPLEMENTED** / **AUTOMATED**. Password sign-in issues a membership-bound JWT. Not a developer-token paste flow. |
| SPA sign-out (`POST /api/v1/auth/logout`) | **IMPLEMENTED** / **AUTOMATED**. Increments `app_user.token_version`. |
| SPA upload form | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** (browser, 2026-08-27). Upload Center now accepts `POST /api/v1/uploads` **202** (poll batch/run) or **200** replay. Banner: published is false. Working set contained `spa-camp-1`; published slice did not, until explicit publish. |
| SPA published download | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** (browser, 2026-08-27). `/client/facts` Download CSV / Download XLSX created blobs; live `GET .../facts.csv` and `.xlsx` **200**. Client Report uses `GET .../client-report.xlsx`. |
| Auto-publish after ingest | **NOT IMPLEMENTED** (by design) |
| Unapproved extra source column | **IMPLEMENTED** / **AUTOMATED**. Ingest fails; `batch.error_summary` is `UNAPPROVED_SOURCE_COLUMN: Unapproved source column: {header}`. Does not publish. |
| `GET /api/v1/processing-runs/{id}/qa-findings` | **IMPLEMENTED** / **AUTOMATED**. Inspector Review of persisted `qa_finding` rows plus a run-level summary. Does not publish. |
| `POST /api/v1/processing-runs/{id}/qa` | **IMPLEMENTED** / **AUTOMATED**. Re-evaluate QA for an existing run without reprocessing facts. Does not publish. |
| Logic/Labels catalog upload | **IMPLEMENTED** / **AUTOMATED**. `POST /api/v1/catalogs/{logic,labels}` stores a **draft**. |
| Logic/Labels activation | **IMPLEMENTED** / **AUTOMATED**. Explicit `POST .../activate`. Invalid files never activate. |
| Existing-batch reprocess | **IMPLEMENTED** / **AUTOMATED**. `POST /api/v1/batches/{batch_id}/process` creates a **new** processing run. Staged/processed batches transform existing staged rows. Received or failed-without-staging recover the same batch from the source archive. Does not publish. |
| Abandoned-run sweep | **IMPLEMENTED** / **AUTOMATED**. API start with `DATABASE_URL` marks leftover `pending`/`running` runs `failed`. Retry is a new run. |

Observed live-API evidence: `documentation/DESKTOP_ACCEPTANCE.md`.

## Complete workflow

```
CLIENT XLSX
  → POST /api/v1/uploads          (admin/publisher; .xlsx only)
  → header/file validation
  → ingest_workbook (P3)
  → run_transformation (P4)
  → evaluate_qa + persist qa_finding + qa_verdict (inspector-only; does not publish)
  → fact_campaign_day working set
  → GET /processing-runs/{id}/qa-findings  (admin/publisher Review)
  → POST /api/v1/batches/{id}/process     (optional; new run, current Logic/Labels)
  → POST /api/v1/publications     (admin/publisher; explicit)
  → publication + publication_current
  → GET /publications             (history for that client)
  → rpt_* / GET .../facts
  → Excel PublishedFacts.m        (manual Desktop refresh)
  → Power BI rpt_*                (manual Desktop build)
  → GET .../facts.csv|xlsx        (published slice only)
  → GET .../client-report.xlsx    (nine-sheet Client Report; no reprocess)
```

Publication is never implied by upload.

## Upload endpoint

`POST /api/v1/uploads`

Multipart form:

| Field | Required | Notes |
|---|---|---|
| `file` | one of `file` / `files` | Single `.xlsx`. ZIP magic `PK` plus workbook parts. Keeps the existing `UploadResponse`. |
| `files` | one of `file` / `files` | Repeatable `.xlsx` parts for one logical period/update. One file still returns `UploadResponse`. Two or more return `UploadGroupResponse`. |
| `client_id` | when JWT `client_id` is null | UUID. JWT `client_id` always wins. |
| `force` | no | Default `false`. Re-ingest a known SHA. |

Authentication: Bearer. Authorization: `admin` / `publisher` only (`reader` /
`client` → 403). Unauthenticated → 401.

A client cannot submit another client's `client_id` (403). A development token
bound with `DFIP_DEV_AUTH_CLIENT_ID` uses that client (a mismatched form
`client_id` is 403). Unscoped in-memory tokens must pass `client_id`; that is
not production tenant isolation. `dev_token` with `DATABASE_URL` requires the
bound client id and is never a platform-wide RLS identity.

Limits:

- Default max size per file: `DFIP_UPLOAD_MAX_BYTES` (10 MiB production/P13E default; local demo may set 50 MiB / `52428800`)
- Max files per multipart request: `DFIP_UPLOAD_MAX_FILES` (5)
- Max total multipart bytes: `DFIP_UPLOAD_MAX_TOTAL_BYTES` (20 MiB production/P13E default; local demo may set 100 MiB / `104857600` so a single 50 MiB workbook is not rejected. 0 means twice the per-file cap)
- Claimed ZIP uncompressed total: `MAX_UNCOMPRESSED_BYTES` (512 MiB). Over this is 422 `Workbook uncompressed size exceeds the allowed limit.` Member count 1024 and `..`/absolute ZIP names stay rejected.
- Non-multipart JSON/body: `DFIP_JSON_MAX_BODY_BYTES` (256 KiB)
- Temp files use a sanitized basename; path traversal filenames are rejected
- Responses never include filesystem paths or `storage_uri`

Original XLSX bytes are archived to private object storage **after** ZIP/size/name
validation and **before** ingest/transform (`store-after-validation-before-process`).
The object key is `{client_id}/{source_file_id}/{sha256}.xlsx`. The URI is stored
on `source_file.storage_uri` (`dfip-source://{bucket}/…`) and is never returned
by the API. A local temp copy is used only for processing and is deleted
afterwards. Processing failure does not delete the archive. Same-client SHA
replay does not create a duplicate object; a missing object is backfilled from
the uploaded bytes. Empty `DFIP_STORAGE_ENDPOINT` uses an in-memory adapter
(development/test only; not durable across restart). Production-grade
environments require a private local directory. HTTP(S) endpoints are not
implemented. Workbook blobs are not stored in PostgreSQL.

Same-client SHA replay (`replayed=true`, HTTP 200) is only for a **processed**
batch. A `staged` batch after a crash is not terminal success: the same SHA
retries that batch. A `received` batch is recovered from the archive using the
same batch id. In-process in-flight SHA still returns 202.

The handler validates ZIP/size/name on the request path, archives the original, creates a `received`
batch, and runs `ingest_workbook` then `run_transformation` on a dedicated
upload thread so `/health`, `/api/v1/ops/ready`, `/session`, catalogs, and status routes stay
responsive. After a new transform it runs `evaluate_qa` (with `reconcile_run`
when a single rate-card label is known) and replaces `qa_finding` rows for that
processing run and writes `processing_run.qa_verdict`. It does not write
`fact_campaign_day` itself and does not publish. Replayed uploads skip transform and skip
QA. Header-contract failures create a failed batch and do not create a
processing run or QA rows.

Multiple files in one request are **not** concatenated into one workbook. Each
file keeps its own `source_file`, SHA-256, object archive, `batch`, and
`processing_run`. Files are processed independently in a stable
`(filename.lower(), sha256)` order through the existing pipeline, so multipart
part order cannot change facts. Overlapping grains in that request are restated
by the later file in that order. Duplicate SHA parts replay. Integer/decimal
source values are not averaged or rewritten. Same-client SHA replay remains
idempotent. Later uploads (including corrections) restate only matching fact
grains; untouched grains and prior source objects remain. Cumulative reporting
uses existing `POST /publications` `fact_scope=client_current`. Each
`processing_run` records the Logic/Labels versions bound for that file's
`observed_day_min`.

Poll existing lifecycle endpoints while a batch is in flight:

- `GET /api/v1/batches/{batch_id}` (`received` → `staged` / `failed` / `processed`)
- `GET /api/v1/processing-runs?batch_id=` (`pending` → `running` → `succeeded` / `failed`)

### Response (202 accepted, 200 replayed)

`UploadResponse`:

- `source_file_id`, `original_filename`, `sha256`, `byte_size`, `client_id`
- `replayed`, `published` (always `false`), `accepted` (always `true` on 2xx)
- `batch` (existing `BatchResponse`; new work is `received` on 202)
- `processing_run` (null on 202 acknowledgement; present after processing)
- `transform` (null until processing completes)
- `rejections` (reason_code / source_row_number, capped; empty on 202)

`UploadGroupResponse` (two or more files):

- `client_id`, `file_count`, `replayed` (true only when every item replayed)
- `published` (always `false`), `accepted` (always `true` on 2xx)
- `duration_ms`, `staged_row_count`, `fact_count` (instrumentation; 202 counts
  may still be zero until each item is polled)
- `items`: one `UploadResponse` per file, in processing order

Header contract failure still creates a failed batch and **does not** create a
succeeded `processing_run`. Transform rejections keep `INVALID_NUMERIC`,
`DUPLICATE_GRAIN_KEY`, and `MISSING_CAMPAIGN_ID`.

### Upload error behavior

| Case | Status | Code |
|---|---|---|
| Missing/invalid Bearer | 401 | `AUTHENTICATION_FAILED` |
| Reader/client, or wrong client | 403 | `AUTHORIZATION_FAILED` |
| Oversize | 413 | `PAYLOAD_TOO_LARGE` |
| Not `.xlsx`, empty, bad ZIP, path traversal, missing `client_id`, no workbook | 422 | `VALIDATION_ERROR` |

## Existing-batch reprocess / recovery

`POST /api/v1/batches/{batch_id}/process?client_id=`

Admin/publisher only. Always a **new** `processing_run` when work starts.
Does not publish. JWT `client_id` always wins. Unscoped principals must pass
`client_id`.

| Batch state | Recovery |
|---|---|
| `processed` with staged rows | Existing reprocess: new run from staged rows |
| `staged` with staged rows | New run from durable staged rows |
| `failed` with staged rows | New run from durable staged rows |
| `received`, or `failed` without completed staging | Re-ingest the **same** batch from the source archive |
| Missing archive | 422 `Source archive is missing. Upload the workbook again.` |
| Active `pending`/`running` run with a live worker | 202 the existing active run |
| Zombie `pending`/`running` with no worker | Fail the zombie run and start recovery |

202 means the new run is pending/running; poll
`GET /api/v1/processing-runs/{processing_run_id}` until `succeeded` or
`failed`. 200 is returned only when the executor is not configured (tests).

On API process start with `DATABASE_URL`, leftover `pending`/`running` runs are
marked `failed` with
`Processing abandoned because the API process restarted. Retry is available.`
The same startup then auto-resumes recoverable `received`/`staged` work and
those abandoned failures. Manual Retry is only required for genuine validation
or archive failures.

Active `pending`/`running` runs that still have a local worker return 202.
Zombie runs with no worker are failed and rescheduled.

### Reprocess error behavior

| Case | Status | Code |
|---|---|---|
| Missing/invalid Bearer | 401 | `AUTHENTICATION_FAILED` |
| Reader/client | 403 | `AUTHORIZATION_FAILED` |
| Bound JWT vs other client query | 403 | `AUTHORIZATION_FAILED` |
| Other client's batch | 404 | `NOT_FOUND` |
| Missing `client_id` (unscoped) | 422 | `VALIDATION_ERROR` |
| Not recoverable / missing archive | 422 | `VALIDATION_ERROR` |

## Download endpoint

`GET /api/v1/publications/current/facts.csv`
`GET /api/v1/publications/current/facts.xlsx`

Same tenant rules as `GET /api/v1/publications/current/facts`. JWT `client_id`
wins. A development token must pass `client_id`.

The file is the **current published slice only**:

- Never `fact_campaign_day` working-set rows from another run
- Never another client's data
- Never unpublished runs
- Empty current pointer → 200 with headers only
- Columns are `FactResponse` field order
- Filename: `published-facts-{client_id}-{publication_id}.{csv|xlsx}`
- Row cap: `DFIP_DOWNLOAD_MAX_ROWS` (default 75_000). Exceeding it is 422.
- HTTP `MAX_PAGE_LIMIT = 200` is unchanged on JSON list routes

`GET /api/v1/publications/current/client-report.xlsx`
`GET /api/v1/publications/{publication_id}/client-report.xlsx`

Same tenant rules as the matching facts routes. The file is the nine-sheet
`Client_Report.xlsx` for that publication's snapshot (complete P6 snapshots
stay immutable). Filename is `Client_Report.xlsx`. No Bearer token is
embedded. Downloads do not process, publish, or move `publication_current`.
Empty current pointer returns the nine-sheet workbook with headers only.
The same `DFIP_DOWNLOAD_MAX_ROWS` cap applies.

## Processing lifecycle

1. Upload validates type/size/name and returns **202** with a `received` batch
   (or **200** for a successful SHA replay). Ingest/transform run off the API
   event loop.
2. P3 stages rows or records a header rejection (no succeeded run).
3. P4 transforms staged rows into the working set.
4. `evaluate_qa` (with `reconcile_run` when a single rate-card label is known)
   replaces `qa_finding` for that run and writes `processing_run.qa_verdict`
   (`pass` / `warn` / `fail`, or `unavailable` if QA itself fails). Findings do
   not auto-publish. `POST /api/v1/publications` requires status `succeeded` and
   a publishable verdict (`pass` or `warn`). `fail`, `unavailable`, and a null
   verdict cannot be published. Re-evaluate without reprocessing via
   `POST /api/v1/processing-runs/{id}/qa`.
5. Publisher reviews working-set facts and
   `GET /processing-runs/{id}/qa-findings`, then calls
   `POST /api/v1/publications` with that `processing_run_id`. Optional
   `fact_scope` defaults to `processing_run` (run-scoped slice). Cumulative
   publishes set `fact_scope=client_current`. The write copies the candidate
   facts into `publication_fact` and only then moves `publication_current`.
6. `publication_current` points at the publication. `GET /publications` lists prior
   rows for that client (newest first). `GET /publications/{id}/facts` returns
   that publication's snapshot. `rpt_*` and current downloads follow the pointer.
7. Republish replaces the pointer. Complete snapshots stay frozen after later
   restatement. Legacy `snapshot_status=none` publications still join live
   `fact_campaign_day` and are not immutable. `rpt_*` follows `publication_current`.

## Logic and labels

Versioned New Logic (campaign A:M), template Q:R, rate cards, and Filter Logic
1_2 groups are **processing inputs**, not decorative metadata.

Packaged JSON under `packages/config/dfip_config/data/` remains the default.
Publisher Logic/Labels `.xlsx` uploads create a new **draft** version in the
existing `campaign_label_*` / `label_group_*` tables (in-memory overlay when
`DATABASE_URL` is empty). Activation is explicit (`draft` → `active`; the
previous uploaded active version becomes `superseded`). Processing
(`bind_versions_for_day` / P4) uses the client's active uploaded version when
one exists; otherwise the packaged date-window catalogs. Historical
`processing_run` rows keep the version ids they originally bound.

Admin/publisher UI: `/admin/catalogs`. JWT `client_id` always wins.
Reader/client receive 403. Invalid files return 422 and do not write a
version.

Template Q:R and rate cards are unchanged in this phase. Signed-URL download of
original source files remains unimplemented. Durable private custody of uploaded
Raw `.xlsx` files is implemented (see upload lifecycle above).

## Tenant isolation

Application-level JWT/`client_id` scoping plus PostgreSQL RLS when
`DATABASE_URL` is set. RLS does not replace 401/403. Azure AD mapping to
`dfip.client_ids` is **BLOCKED** (Entra ID / Azure AD → PostgreSQL identity).
It is not required for the JWT API or Excel `PublishedFacts.m` flow.

## E2E test procedure

Automated:

```powershell
python -m pytest tests/test_v2_http_ingest.py
python -m pytest tests/test_v2_operational_e2e.py
python -m pytest tests/test_v2c_catalog.py tests/test_v2c_e2e.py
python -m pytest tests/test_v2_http_workflow.py -m postgres
```

`tests/test_v2_http_workflow.py` uploads Client A, publishes, checks `rpt_*`
and KPIs, downloads, isolates Client B, then rejects malformed / invalid
numeric / duplicate-grain workbooks and verifies republish.

## Manual Excel refresh procedure

**A. Python structure scaffold:** `build_client_report()` writes Settings + Facts
only. It does not create DataMashup.

**B. Native Excel DataMashup:** tracked `excel/Client_Report.xlsx` has
`PublishedFacts` + `Facts` + nine Daily Report sheets, native
connections/query tables, and the DataMashup customXml package. Fake
`xl/queryMashup/` parts are forbidden. See `documentation/DAILY_REPORT.md`.

**C. Refresh All:** verified on a local pointer-test workbook that loads
unchanged `excel/PublishedFacts.m`. Observed 2026-08-27: pointer Unique Clicks
30 → 40. See `documentation/DESKTOP_ACCEPTANCE.md`.

Do not leave a Bearer token in `excel/Client_Report.xlsx`.

## Manual Power BI Desktop procedure

**P9 / NOT VERIFIED.** Model, KPI cards, pointer refresh, and local
`DFIP.pbix` were not built in this pass. `dfip_api` is NOLOGIN. Apply
`powerbi/sql/reporting_login.example.sql` as a privileged owner, then follow
`powerbi/README.md` and `documentation/DESKTOP_ACCEPTANCE.md`. Expected Client
A cards for the current pointer: Clicks **40**, Delivered **100**, CTR
**0.400000**. Do not connect to `fact_campaign_day`. Do not commit a `.pbix`.
