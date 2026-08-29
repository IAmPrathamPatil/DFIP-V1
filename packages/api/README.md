# packages/api

P5 FastAPI application (`dfip_api`) plus P7 publication routes and P9
application-level authorization. P10 is documentation/release closeout only.

- Working-set GET routes for existing P3 ingest and P4 fact stores
  (admin/publisher only)
- P7 `POST /api/v1/publications` (admin/publisher only) and published-facts GET
  (any allowlisted role, with existing publication scoping)
- `/health` is public and does not open a database
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
  files, returns **202** with a received batch (or `UploadGroupResponse`
  when several files are sent), and runs `ingest_workbook` then
  `run_transformation` off the API event loop. It does not publish. See
  `documentation/HTTP_WORKFLOW.md`.
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
  Client Report for the current or a historical authorized publication.
  Complete snapshots stay bound to that publication. Downloads do not
  process or publish.
- Production refuses `dev_token` and refuses a missing `DATABASE_URL`.
- Production disables `/docs`, `/redoc`, and `/openapi.json`.
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
