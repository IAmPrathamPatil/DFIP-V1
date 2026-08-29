# DFIP website

The Publisher/Admin control center is the existing vanilla SPA in
`apps/web/static`, restyled as a dark enterprise shell. It talks only to the
P5/P7 API. It does not proxy the API, open a database, or change publication,
QA, Logic/Labels, Excel, or export semantics.

## Authentication

Sign-in uses username and password against `app_user`. The API issues an HS256
access JWT bound to that user's `client_membership`. The SPA stores the issued
token in `sessionStorage` for this tab, sends it as `Authorization: Bearer …`,
and refreshes it while the tab is in use. Sign out increments the server
`token_version` and clears the tab key.

Clients do not paste a developer token. `DFIP_DEV_AUTH_TOKEN` remains a
local/admin Bearer path for curl and tests only; it is not exposed in this UI.

Unauthorized admin URLs render the forbidden page. The API remains the
security boundary. PostgreSQL RLS stays defense in depth.

## Navigation

Publisher (`admin` / `publisher`):

| Route | Screen |
|---|---|
| `/admin` | Dashboard (live API summaries only) |
| `/admin/upload` | Upload Center (Raw Data, Logic, Labels) |
| `/admin/processing-runs` | Processing runs |
| `/admin/processing-runs/:id` | Run detail, QA findings, publish |
| `/admin/review` | Review / QA (optional `?processing_run_id=`) |
| `/admin/facts` | Working-set facts (`GET /api/v1/facts`) |
| `/admin/logic` | Logic versions |
| `/admin/labels` | Labels versions |
| `/admin/catalogs?kind=` | Same catalog screens (compat) |
| `/admin/publications` | Current publication, publish, history |
| `/admin/downloads` | Published Client Report, CSV/XLSX, and catalog downloads |
| `/admin/source-files` | Source file lineage |
| `/admin/batches` | Batch lineage |
| `/admin/history` | Working-set fact history |

Client / reader (any authenticated role):

| Route | Screen |
|---|---|
| `/client` | Published reporting overview |
| `/client/facts` | Published slice (`GET /api/v1/publications/current/facts`) |

## Major workflows

1. Sign in → Dashboard
2. Upload Center → Raw Data `.xlsx` (`POST /api/v1/uploads` returns **202**;
   poll `GET /batches/{id}` and Processing. Published remains false.)
3. Processing run → Review / QA (`GET /api/v1/processing-runs/{id}/qa-findings`)
4. Working-set facts
5. Explicit Publish Run (`POST /api/v1/publications`)
6. Publication history and Downloads

Logic/Labels uploads store drafts. Activate / Deactivate are confirmed and do
not publish.

## Local development

Unchanged: `python -m dfip_api` and `python -m dfip_web`, then open
`http://127.0.0.1:3000`. See `apps/web/README.md`.
