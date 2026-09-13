# packages/api

P5 FastAPI application (`dfip_api`) plus P7 publication routes and P9
application-level authorization. P10 is documentation/release closeout only.

- Working-set GET routes for existing P3 ingest and P4 fact stores
  (admin/publisher only)
- P7 `POST /api/v1/publications` (admin/publisher only) and published-facts GET
  (any allowlisted role, with existing publication scoping)
- `/health` is public liveness and does not open a database
- `GET /api/v1/ops/ready` is authenticated cheap readiness (admin/publisher).
  It does not hash the source archive or run `python -m dfip_api.backup verify`.
- Website `GET /health` (`dfip-web`) is a different process and is not API
  or database readiness.
- `/api/v1/*` requires a configured Bearer credential
- JWT roles are `admin`, `publisher`, `reader`, `client`. Unknown roles are
  rejected. `dev_token` remains development/test only. HS256 JWT is an
  authentication hook, not a production identity provider.
- GET `/facts` is the unfiltered working set for inspector roles. It is not
  publication-filtered.
- Default runtime is empty in-memory P3/P4/P7 stores when `DATABASE_URL` is
  empty. When `DATABASE_URL` is set, PostgreSQL adapters are used. API startup
  does not ingest an XLSX.
- `POST /api/v1/uploads` (admin/publisher) accepts one or more `.xlsx`
  files (max 5 parts, 10 MiB each, 20 MiB total by default; local demo may
  raise per-file to 50 MiB and total to 100 MiB via env), returns **202**
  with a received batch (or `UploadGroupResponse`
  when several files are sent), and runs `ingest_workbook` then
  `run_transformation` off the API event loop. It does not publish. See
  `documentation/HTTP_WORKFLOW.md`.
- `POST /api/v1/batches/{id}/process` retries a recoverable batch: archive
  recovery for `received`, a new `processing_run` for staged/processed/failed
  with staged rows. It does not publish. Missing archive is 422.
- Operator backup/restore is `python -m dfip_api.backup` (not imported by
  `create_app`). Dump with a superuser or BYPASSRLS login, never `dfip_api`.
  Restore requires `--confirm-disposable` (refuses `dfip` / hosted URLs) or
  `--confirm-production-local` (colocated local database name `dfip` only).
  `identify-eligible` lists extra verified sets and never deletes.
- Operator company purge is `python -m dfip_api.purge` (not imported by
  `create_app`). Dry-run then `--confirm-purge` plus `--confirm-disposable` or
  `--confirm-production-local`, with `--code`. Privileged DSN. No HTTP purge.
- HTTP SHA replay (`replayed=true`) only after a processed batch.
- `POST /api/v1/catalogs/{logic,labels}` (admin/publisher) accepts a Logic or
  Labels `.xlsx`, validates it, and stores a **draft** version. Activation is
  `POST /api/v1/catalogs/{kind}/{id}/activate`. Invalid files return 422 and
  do not write a version. Processing uses the client's active uploaded
  version when one exists; otherwise packaged JSON. See
  `documentation/HTTP_WORKFLOW.md`.
- `GET /api/v1/publications` lists publication history for the scoped client.
- `GET /api/v1/publications/{id}/facts` returns that publication's snapshot.
  Complete snapshots do not follow later restatements. Legacy `none` still
  joins live facts.
- `GET /api/v1/publications/current/facts.csv` and `.xlsx` download the
  current published slice only.
- `GET /api/v1/publications/current/client-report.xlsx` and
  `GET /api/v1/publications/{id}/client-report.xlsx` download the nine-sheet
  company workbook for the current or a historical authorized publication.
  Filename is `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx`.
  Current download with no publication returns 404. Complete snapshots stay
  bound to that publication. Downloads do not
  process or publish.
- Production-grade environments refuse `dev_token`, require a strong JWT
  secret, require `DATABASE_URL`, require a local `DFIP_STORAGE_ENDPOINT`
  directory, require HTTPS origins, and disable
  `/docs`, `/redoc`, and `/openapi.json`.
- Production-grade first publisher setup requires header
  `X-DFIP-Bootstrap-Token`. Development/test setup stays unauthenticated
  until a publisher exists. Failed login and bootstrap attempts are throttled
  in-process (5 / 10 minutes by IP and username) with 429 `Too many requests.`
- Non-multipart JSON bodies are capped at 256 KiB (`DFIP_JSON_MAX_BODY_BYTES`).
- `dev_token` with `DATABASE_URL` requires `DFIP_DEV_AUTH_CLIENT_ID` and is
  never a platform-wide RLS identity. JWT `client_id` is authoritative on
  inspector working-set reads as well as publication.
- **This is application-level authorization, not a replacement for FastAPI
  gates.** PostgreSQL RLS is defense in depth when database mode is enabled.

```powershell
python -m dfip_api
```

Persistence is in-memory unless `DATABASE_URL` is configured. `/health` does
not claim live database connectivity (`database.status` remains `not_checked`).
Use `GET /api/v1/ops/ready` for cheap operator readiness.
