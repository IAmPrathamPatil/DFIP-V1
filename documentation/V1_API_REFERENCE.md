# DFIP V1 — API reference

Prefix default: `/api/v1` (`Settings.dfip_api_prefix`). All resource routes require `Authorization: Bearer …` unless noted.

**Pagination (JSON list endpoints):** `limit` default 50, max **200** (`DEFAULT_PAGE_LIMIT`, `MAX_PAGE_LIMIT` in `packages/api/dfip_api/schemas.py`). Response envelope: `{ items, pagination: { limit, offset, total } }`.

**Error envelope:** `{ error: { code, message } }` via `ErrorResponse`. Common: 401 auth, 403 authz, 404, 413 size, 422 validation, 429 too many requests, 503 persistence.

**Decimals** on facts: JSON **strings**. NULL and `""` are distinct.

**Authz (application-level, not RLS):**

| Role | Working-set inspector routes | Publish / upload / catalogs | Session + published facts/downloads |
|---|---|---|---|
| `admin`, `publisher` | Yes (`can_inspect`) | Yes (`can_publish` for publications; uploads/catalogs use `InspectorDep`) | Yes |
| `reader`, `client` | 403 | 403 | Yes (scoped by JWT `client_id`) |
| unknown JWT role | 401 (`Invalid authentication credentials`) | — | — |

JWT `client_id` always wins over query/form `client_id` when the token has a client. Unscoped `dev_token` may pass `client_id` query/form — **not** production isolation.

`ClientResponse` / session client rows include `lifecycle_status`, `deactivated_at`, and `purge_eligible_after`. Inactive companies block client-role login, select-client, refresh of a bound token, upload, process/retry, QA write, publish, catalog activation/update, current client report, and refreshable current workbook. Publisher historical `{publication_id}` downloads remain available during retention. Deactivation is application-layer; it does not change RLS or `rpt_*`. Permanent purge is `python -m dfip_api.purge`, not HTTP.

---

## Compact contract table

| METHOD | PATH | AUTH | PURPOSE | REQUEST | RESPONSE | ERRORS |
|---|---|---|---|---|---|---|
| GET | `/health` | **Public** | Liveness; DSN flag only; no DB/storage/backup I/O | — | `HealthResponse` | 500 |
| GET | `/api/v1/ops/ready` | Inspector | Cheap readiness (SELECT 1 + storage dir + worker idle/busy) | — | `ReadyResponse` | 401, 403, **503** `not_ready` |
| GET | `/docs`, `/redoc`, `/openapi.json` | Public (development/test) | OpenAPI | — | HTML/JSON | **Absent in staging/production** |
| POST | `/api/v1/auth/login` | **Public** | Password → JWT | `LoginRequest` `{username, password, client_id?}` | `LoginResponse` | 401, 403, 413, 422, **429** |
| GET | `/api/v1/auth/setup-status` | **Public** | Development/test: true only if no publisher/admin exists. Production-grade: always `{publisher_setup_required: false}` | — | `{publisher_setup_required}` | — |
| POST | `/api/v1/auth/setup-publisher` | One-time | Operator-chosen publisher username/password/confirm (min 12 chars). Production-grade requires header `X-DFIP-Bootstrap-Token` matching `DFIP_BOOTSTRAP_TOKEN`. Query parameters are ignored. Failed attempts share the process-local 5/10-minute throttle. | `{username,password,confirm_password}` | 201 `{user_id,username,role}` (no password) | 401/409/413/422/**429** |
| POST | `/api/v1/auth/logout` | Bearer | Bump `token_version` | — | 204 | 401 |
| POST | `/api/v1/auth/refresh` | Bearer JWT only | New access JWT | — | `LoginResponse` | 401 if `dev_token` |
| POST | `/api/v1/auth/select-client` | Inspector | Bind JWT to one authorized client | `SelectClientRequest` | `LoginResponse` | 403 clients |
| GET | `/api/v1/session` | Bearer | Current principal | — | `SessionResponse` | 401 |
| GET | `/api/v1/clients` | Inspector | Publisher company registry (membership-scoped) | — | `ClientPage` | 401/403 |
| POST | `/api/v1/clients` | Inspector | Create company; new `client.id`; caller membership only | `{name}` | 201 `ClientResponse` | 401/403/422 |
| POST | `/api/v1/clients/{client_id}/rename` | Inspector | Update `client.name` only; `id`/`code` unchanged | `{name}` | `ClientResponse` | 401/403/404/422 |
| POST | `/api/v1/clients/{client_id}/deactivate` | Inspector | Set `lifecycle_status=inactive`; stamp eligibility; delete nothing | — | `ClientResponse` | 401/403/404 |
| POST | `/api/v1/clients/{client_id}/reactivate` | Inspector | Set `active`; clear `deactivated_at` / `purge_eligible_after` | — | `ClientResponse` | 401/403/404 |
| POST | `/api/v1/clients/{client_id}/users` | Inspector | Operator-chosen client-portal login; hash only; role `client` | `{username,password,confirm_password,role}` | 201 `ClientUserResponse` (no password) | 401/403/404/422 |
| GET | `/api/v1/source-files` | Inspector | List source files | query filters + page | `SourceFilePage` | 401/403/422/503 |
| GET | `/api/v1/source-files/{id}` | Inspector | One source file | — | `SourceFileResponse` | 404 |
| GET | `/api/v1/batches` | Inspector | List batches | filters + page | `BatchPage` | |
| GET | `/api/v1/batches/{id}` | Inspector | One batch | — | `BatchResponse` | 404 |
| POST | `/api/v1/batches/{id}/process` | Inspector | Retry/reprocess a recoverable batch; **new** `processing_run`; archive recovery for received; **does not publish** | query `client_id?` | **202** `ReprocessResponse` (`published: false`) | 403/404/422 |
| GET | `/api/v1/batches/{id}/staged-rows` | Inspector | Staging page | page + filters | `StagedRowPage` | |
| GET | `/api/v1/processing-runs` | Inspector | List runs | `batch_id?` + page | `ProcessingRunPage` | |
| GET | `/api/v1/processing-runs/{id}` | Inspector | One run | — | `ProcessingRunResponse` | |
| GET | `/api/v1/processing-runs/{id}/qa-findings` | Inspector | QA page | page | `QaFindingPage` | |
| POST | `/api/v1/processing-runs/{id}/qa` | Inspector | Evaluate/persist QA | — | `ProcessingRunResponse` | |
| GET | `/api/v1/facts` | Inspector | **Working set** (not publication) | filters + page | `FactPage` | |
| GET | `/api/v1/facts/history` | Inspector | Superseded facts | filters + page | `FactHistoryPage` | |
| POST | `/api/v1/uploads` | Inspector | Ingest xlsx; **does not publish** | multipart `file` and/or `files`; form `client_id?`, **`force` bool default false** | 202 new work `UploadResponse` or `UploadGroupResponse`; **200** SHA replay (`replayed: true`); `published` always false | 413 (size/count/total), 422, 429 |
| POST | `/api/v1/publications` | Publisher | Publish succeeded run | `PublicationCreateRequest` | 201 `PublicationStateResponse` | 403, 422 |
| GET | `/api/v1/publications` | Bearer | Publication history | `client_id?` + page | `PublicationPage` | |
| GET | `/api/v1/publications/current` | Bearer | Pointer | `client_id?` | `PublicationCurrentResponse` | |
| GET | `/api/v1/publications/current/facts` | Bearer | Published JSON | page + `client_id?` | `FactPage` (empty if none) | |
| GET | `/api/v1/publications/{publication_id}/facts` | Bearer | Historical published JSON | page | `FactPage` | 404 |
| GET | `/api/v1/publications/current/facts.csv` | Bearer | CSV of current slice | `client_id?` | attachment | 422 if over row cap |
| GET | `/api/v1/publications/current/facts.xlsx` | Bearer | XLSX facts only | `client_id?` | attachment | |
| GET | `/api/v1/publications/current/client-report.xlsx` | Bearer | Static recovery company workbook for current publication | `client_id?` | `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx` | 404 if no current publication; 422 if over row cap |
| GET | `/api/v1/publications/current/refreshable-client-report.xlsx` | Bearer | Refreshable company workbook for current publication only | `client_id?` | `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report_Refreshable.xlsx` | 404 if none; 422 if over row cap; historical `/{id}/refreshable-…` is **not** a route |
| GET | `/api/v1/publications/{publication_id}/client-report.xlsx` | Bearer | Static snapshot bound to that id | `client_id?` | `DFIP_<client_code>_<YYYY-MM-DD>_<publication_short_id>_Client_Report.xlsx` | 404 |
| POST | `/api/v1/catalogs/{kind}` | Inspector | Upload Logic/Labels draft | multipart, `kind`=`logic`\|`labels` | 201 `CatalogImportResponse` | |
| GET | `/api/v1/catalogs/{kind}` | Inspector | List versions | | `CatalogListResponse` | |
| GET | `/api/v1/catalogs/{kind}/{version_id}` | Inspector | Inspect version | | `CatalogVersionResponse` | |
| POST | `/api/v1/catalogs/{kind}/{version_id}/activate` | Inspector | Activate draft | | version | packaged sentinel rejected |
| POST | `/api/v1/catalogs/{kind}/{version_id}/deactivate` | Inspector | Deactivate | | version | |
| GET | `/api/v1/catalogs/{kind}/{version_id}/download` | Inspector | xlsx bytes | | file | |

Web origin (not API prefix):

| METHOD | PATH | AUTH | PURPOSE |
|---|---|---|---|
| GET | `http://{web}/health` | Public | `{status, application: dfip-web}` |
| GET | `http://{web}/config.json` | Public | API location for SPA (**no secrets**) |
| GET | `http://{web}/`, `/{path}` | Public files | SPA |

---

## Endpoint details

### GET `/health`

**Module:** `dfip_api.app.create_app` inner `health`.

```json
{
  "status": "ok",
  "application": "dfip-api",
  "environment": "development",
  "database": { "configured": false, "status": "not_checked" }
}
```

`configured` true iff `DATABASE_URL` non-empty. Never probes DB.

### GET `/api/v1/ops/ready`

**Module:** `dfip_api.routes.ops_ready` via `dfip_api.ops.build_readiness`.

Admin/publisher only. Authenticates the bearer locally (JWT/dev_token role)
so a database outage still returns this body instead of
`PERSISTENCE_UNAVAILABLE`. Token-version directory lookup stays on data
routes. Cheap checks only: `SELECT 1`, source-storage directory existence,
process-local worker occupancy. Optional `DFIP_BACKUP_LAST_DIR` manifest
timestamp/age. Does **not** hash the archive or run backup verify.

```json
{
  "status": "ready",
  "application": "dfip-api",
  "database": { "status": "ok" },
  "storage": { "status": "ok" },
  "worker": "idle",
  "backup": { "status": "not_configured" }
}
```

HTTP **200** when `status` is `ready`. HTTP **503** when `status` is `not_ready`
(database unavailable or source-storage directory unavailable). Worker `busy`
does not make the service not-ready. Failed/abandoned processing runs do not.
Does not return filesystem paths, DSNs, tenant rows, or publication ids.

Web origin `GET /health` remains `{status, application: dfip-web}` and is not
this endpoint.

### POST `/api/v1/auth/login`

**Module:** `dfip_api.auth_routes.login`.

- Looks up `app_user` via `IdentityStore.get_credential`.
- PBKDF2 verify (`dfip_password_pbkdf2_iterations`, default 210000). Dummy hash on miss to reduce timing leak.
- Issues HS256 JWT via `issue_access_token`. Claims: `sub`, `role`, `iat`, `exp`, optional `client_id`, `ver`, `iss`, `aud`.
- TTL: `dfip_auth_token_ttl_seconds` default **3600**. `verify_exp` true, **leeway 0**.
- Platform admin: unbound token unless `client_id` requested.
- Multi-membership non-admin: must pass `client_id` or 403.

**Does not** accept pasting `DFIP_DEV_AUTH_TOKEN` as username/password.

### GET `/api/v1/facts` vs published facts

| | Working set | Published |
|---|---|---|
| Path | `/facts` | `/publications/current/facts` |
| Authz | Inspector only | Any allowed role with client scope |
| Filter | Query: client, campaign, day range, run, batch, etc. | Publication snapshot / pointer |
| Excel | **Must not** | `PublishedFacts.m` uses this |

### POST `/api/v1/uploads`

**Module:** `upload_routes.upload_workbook` → `UploadService`.

- Caps: `DFIP_UPLOAD_MAX_BYTES` default 64 MiB **per file**; `DFIP_UPLOAD_MAX_FILES` default 5; `DFIP_UPLOAD_MAX_TOTAL_BYTES` default 128 MiB (0 → 2× per-file). Count/total are checked before extra payload accumulation. The 64 MiB per-file cap fits the FY-2026 Raw workbook (53,832,336 bytes). Raw, Logic, and Labels share the per-file cap.
- Uncompressed ZIP payload cap: `MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024` in `upload_service.py` (monthly Web-Engage Raw worksheet XML). Member count cap 1024; absolute/`..` ZIP names rejected. Over the uncompressed cap returns 422 `Workbook uncompressed size exceeds the allowed limit.`
- Non-multipart JSON/body cap: `DFIP_JSON_MAX_BODY_BYTES` default 256 KiB. Login/setup failures: 5 / 10 minutes by IP and username → 429 `Too many requests.`
- 202 + poll; does **not** call `PublicationService.create`.
- JWT client wins.
- Failed ingest `batch.error_summary` is publisher-facing. Unapproved extras:
  `UNAPPROVED_SOURCE_COLUMN: Unapproved source column: {header}`.
- `FactResponse` stays the frozen 45-field contract (`extra="forbid"`). Approved extras may appear only inside inspector `StagedRowResponse.raw`.

### POST `/api/v1/publications`

**Body:**

```json
{
  "client_id": "uuid",
  "processing_run_id": "uuid",
  "period_start": null,
  "period_end": null,
  "notes": null,
  "fact_scope": "processing_run"
}
```

`fact_scope`: `processing_run` | `client_current`.

**Guards (VERIFIED `PublicationService.create`):** run exists; `status==succeeded`; `qa_verdict` in `{pass, warn}`; batch `client_id` matches; period order.

### Downloads

Row cap: `DFIP_DOWNLOAD_MAX_ROWS` default **75000**. Exceeding → validation error (do not silently truncate — VERIFIED intent in settings comments and download helpers).

Client report: `dfip_web.client_report_download.render_client_report_xlsx` via `publication_routes._client_report_download`. Process-local semaphore(1); busy → 429.

CSV/XLSX facts-only: `dfip_api.published_download`.

### Catalogs

`kind` path: `logic` | `labels`. Packaged version id `PACKAGED_SENTINEL` cannot activate/deactivate via HTTP.

---

## Implementation map

| Area | Module |
|---|---|
| App factory, health, CORS, routers | `packages/api/dfip_api/app.py` |
| Working-set routes | `routes.py` (`router` + `inspector_router`) |
| Publications | `publication_routes.py`, `publication_service.py` |
| Uploads | `upload_routes.py`, `upload_service.py` |
| Catalogs | `catalog_routes.py`, `catalog_service.py` |
| Auth HTTP | `auth_routes.py`, `auth.py`, `password.py`, `membership.py` |
| Request limits | `limits.py` (process-local attempt window + JSON body cap) |
| Models | `schemas.py` |
| Deps | `deps.py` (`get_principal`, `require_inspector`) |

---

## Representative published fact item (safe)

Field names match `FactResponse`. Values illustrative, not live data.

```json
{
  "client_id": "a0000000-0000-4000-8000-000000000001",
  "campaign_id": "…",
  "variation_id": null,
  "variation_id_key": "",
  "day": "2025-10-01",
  "month_label": "Oct-25",
  "sent": 0,
  "failed": 0,
  "delivered": 0,
  "unique_impressions": 0,
  "unique_clicks": 0,
  "unique_conversions": 0,
  "unique_click_through_conversions": 0,
  "unique_impression_through_conversions": 0,
  "revenue_inr": "0.0000",
  "total_cost": "0.0000",
  "filter_logic_1": "…",
  "filter_logic_1_group": "Group7",
  "processing_run_id": "…",
  "first_seen_at": "2026-08-29T00:00:00+00:00",
  "last_seen_at": "2026-08-29T00:00:00+00:00"
}
```

Never log or document real JWTs. Bearer header shape: `Authorization: Bearer <token>`.

---

## Inspector query parameters (VERIFIED `routes.py`)

All inspector GETs use `PaginationDep`: `limit` 1–200 default 50, `offset` ≥0.

| Route | Extra query |
|---|---|
| `/source-files` | `client_id` |
| `/batches` | `source_file_id`, `client_id`, `status` |
| `/batches/{id}/staged-rows` | `source_row_number` (≥1), `campaign_id` text, `variation_id` text, `day` |
| `/processing-runs` | `batch_id`, `status` |
| `/processing-runs/{id}/qa` POST | `client_id` |
| `/facts`, `/facts/history` | `client_id`, `batch_id`, `processing_run_id`, `campaign_id` text, `variation_id` text, `day` |

JWT `client_id` wins via `inspector_read_scope`. Unknown query keys are ignored (not SQL injection).

`SourceFileResponse` fields: `source_file_id`, `client_id`, `original_filename`, `sha256`, `byte_size`, `source_kind`, `uploaded_at` — **no** `storage_uri`.

---

## Error envelope codes (`dfip_api.errors`)

`VALIDATION_ERROR` 422, `AUTHENTICATION_FAILED` 401, `AUTHORIZATION_FAILED` 403, `NOT_FOUND` 404, `INVALID_PAGINATION` 422, `PAYLOAD_TOO_LARGE` 413, `TOO_MANY_REQUESTS` 429, `PERSISTENCE_UNAVAILABLE` 503, `INTERNAL_ERROR` 500, `METHOD_NOT_ALLOWED` 405.

Optional `error.details` on validation. No filesystem paths or secrets in production bodies.

---

## Route registration (cross-check)

`create_app` includes, all under `DFIP_API_PREFIX` except health:

1. `auth_public_router` — login  
2. `auth_session_router` — logout, refresh, select-client  
3. `router` — session + **`inspector_router` nested** (`router.include_router(inspector_router)`), including `GET /ops/ready`  
4. `publication_router` — static paths `/publications/current/*` registered **before** `/{publication_id}/…`  
5. `upload_router`  
6. `catalog_router`  
7. `client_router` — list companies, create company, rename display name, deactivate, reactivate. Permanent purge is not an HTTP route.  

Plus app-level `GET /health`. Non-prod OpenAPI at `/docs`, `/redoc`, `/openapi.json`.

**Count:** 1 public health + 1 login + 3 session auth + 1 session GET + 13 inspector + 3 companies + 10 publication + 1 upload + 6 catalog = **38** `/api/v1` endpoints + **1** `/health`.

---

## Upload / reprocess side effects

- Writes `source_file`, `batch`, staging, `processing_run`, facts, optional object-store blob, `qa_finding`.  
- **Does not** write `publication` / `publication_current`.  
- `force=true` skips SHA replay (re-stage into a new batch).
- SHA replay (`replayed=true`) only after a processed batch.
