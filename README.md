# DFIP V1 — DataFlow Intelligence Platform

DFIP replaces a manual Excel / Power Query workflow for Web Engage campaign reporting. Publishers upload source data; DFIP labels, costs, validates, and publishes a narrow authorized dataset that the client workbook refreshes over a controlled API.

This repository is **DFIP-V1 COMPLETE**.

P0–P9 are locked. P10 locked published-data / report-layer parity. P11 adds
native Excel PivotTables on that slice. There is no P12, P13, or P14.
Deferred architecture is V2, not a later V1 phase.

## Current implementation status

| Phase | Name | Status |
|---|---|---|
| M1.1–M1.4 | Source discovery, reverse-engineering, contract lock | Complete (evidence, not code) |
| M2.1 | Architecture specification | Complete (design, not code) |
| **P0** | Repository & environment foundation | Complete |
| **P1** | Database foundation | Complete |
| **P2** | Configuration / business logic | Complete |
| **P3** | Ingestion | Complete |
| **P4** | Transformation / reconciliation | Complete |
| **P5** | API / authentication foundation | Complete |
| **P6** | Admin / Publisher + Client web application | Complete |
| **P7** | Publication + Excel / Power Query client workbook | Complete |
| **P8** | UAT / full reconciliation | Complete |
| **P9** | Authorization hardening | Complete |
| **P10** | Report-layer data parity | **Complete** |
| **P11** | Native Excel PivotTables + slicers | **Complete — presentation objects** |
| **V2 Phase 1** | PostgreSQL persistence foundation | Complete |
| **V2 Phase 2A** | Analytics / published `rpt_*` | Complete |
| **V2 Phase 2B** | Power BI reporting package | Complete — local Desktop `.pbix` remaining |
| **V2 HTTP ingest** | Authenticated `.xlsx` upload + published download | Complete — API, SPA, and V2-X Excel pointer Refresh All verified locally. **P9 Power BI Desktop** remaining. See `documentation/DESKTOP_ACCEPTANCE.md` |


### Implemented (P0 through P11)

- Independent Git repository at `DFIP-V1`
- `.gitignore` that excludes client workbooks, CSV/raw data, secrets, caches, and virtualenvs
- Python packages (`dfip_core`, `dfip_db`, `dfip_shared`, `dfip_config`, `dfip_api`, `dfip_web`)
- `.env.example` with documented placeholders and no real credentials
- smoke tests, configuration-load tests, P1 schema tests, P2 configuration tests, P3 ingest tests, P4 transformation and reconciliation tests, P5 API tests, P6 web tests, P7 publication and workbook tests, P8 UAT tests, P9 authorization tests, P10 release tests, P11 PivotTable tests
- Ruff lint configuration
- Minimal Dockerfile / docker-compose (app image; optional `dfip_db` on profile `v2-db`)
- GitHub Actions workflow that installs, lints, and tests
- PostgreSQL/Supabase SQL migrations (tables, indexes, catalogs, KPI/rate-card seed, P2 config loads, P3 batch metadata)
- Versioned New Logic campaign/template snapshots and Filter Logic 1_2 membership
- Deterministic resolvers (campaign label, template Q:R, rate-card rule, FL1 group)
- Read-only ingestion of the 57 Web Engage source columns into the P1 staging model
- Excel A:BO ↔ database column mapping documented in `documentation/SCHEMA.md`
- Deterministic transformation engine producing `fact_campaign_day` records with Total Cost, derived labels, and full configuration lineage
- Reconciliation module that independently recomputes every derived value
- Read-only FastAPI application (`dfip_api`) under `/api/v1` with pagination, validated filters, and an authentication boundary
- Public `GET /health` that never claims database connectivity
- Admin/Publisher and Client SPA (`apps/web/static`) served by `python -m dfip_web` on port 3000, consuming `/api/v1` with UI route guards. Upload Center uploads `.xlsx` (does not publish). Publications publishes a succeeded run. Client facts download the published CSV/XLSX slice. See `documentation/WEBSITE.md`.
- P7 publication pointer (`publication` / `publication_current`) with `POST /api/v1/publications` and published-facts GET
- Empty client workbook `excel/Client_Report.xlsx` (`PublishedFacts` + `Facts`
  + nine Daily Report **native PivotTables** (shared cache, slicers, page
  filters), native Excel connection/query artifacts). Power
  Query source of truth is `excel/PublishedFacts.m` (published facts, limit
  200). Fake `xl/queryMashup/` parts are forbidden. Do not commit a BearerToken.
  See `documentation/DAILY_REPORT.md`.
- P8 client portal `/client/facts` reads the published slice; Admin `/admin/facts` remains the working set
- P9 application-level authorization: admin/publisher inspect the working set and publish; client/reader may read session plus published data only. Unknown JWT roles are rejected. **This is application-level authorization, not PostgreSQL RLS and not production tenant isolation.**
- P10 V1 data/report-layer closeout: documentation, UI copy, release metadata, and acceptance tests. Native PivotTables are P11.
- P11 native Excel PivotTables, independent slicers, page filters, and outline expand/collapse on the nine report sheets. P8 historical downloads rebind the shared cache to that publication's static PublishedFacts cells. P12/P13/P14 are not started.
- V2 HTTP ingest: `POST /api/v1/uploads` accepts a workbook and runs existing P3/P4 libraries off the API event loop (202 + poll batch/run). It does not publish. The Admin SPA posts that multipart. Published-slice download: `GET /api/v1/publications/current/facts.csv` and `.xlsx`, including Client SPA buttons. See `documentation/HTTP_WORKFLOW.md`.

### Runtime honesty (V1)

- P3 ingestion and P4 transformation remain **libraries**. Authenticated
  `POST /api/v1/uploads` (V2) calls those libraries off the API event loop; it does not auto-publish.
- `python -m dfip_api` starts with **empty in-memory** P3/P4/P7 stores. It does
  not ingest an XLSX on startup.
- SQL migrations under `supabase/migrations/` exist as the schema artifact. The
  running API opens PostgreSQL only when `DATABASE_URL` is set.
- `dev_token` is development/test only. HS256 JWT is an authentication hook, not a production identity provider.
- Application-level `client_id` scoping is not PostgreSQL RLS. V1 is not production tenant isolated.

### V2 (not implemented)

Later V2 phases remain unimplemented: QA rule engine, production
identity-provider runtime / Supabase Auth, object-storage HTTP upload,
signed URLs, KPI engine, RECON-09, OData/PostgREST,
and worker/queue runtime. These are not additional V1 phases.

V2 Phase 1 adds optional PostgreSQL persistence when `DATABASE_URL` is set
(local PostgreSQL 16 or hosted Supabase Free-tier URI, Direct/Session pooler).
Default `python -m pytest` still uses in-memory stores. Authentication remains
application authentication. PostgreSQL RLS is defense in depth and does not
replace FastAPI authorization. A supabase.co project is not created by this
repository; the operator supplies `DATABASE_URL` locally and never commits it.

V2 Phase 2A adds `dfip_analytics`, `qa_finding`, and published-only `rpt_*`
views. Phase 2B is the version-controlled Power BI package in `powerbi/`; no
committed `.pbix`. Authenticated multipart ingest is `POST /api/v1/uploads`;
published CSV/XLSX download is `GET /api/v1/publications/current/facts.csv`
(and `.xlsx`). See `documentation/HTTP_WORKFLOW.md`. Excel Desktop refresh
(V2-X) was verified on a local pointer-test workbook. Power BI Desktop
(P9) remains parked.

## Repository structure

```
DFIP-V1/
├── apps/
│   ├── web/          # P6 Admin/Client SPA (static origin)
│   └── worker/       # planned job runner; P4 logic is a library in packages/core
├── packages/
│   ├── core/         # P3 ingestion + P4 transformation/reconciliation
│   ├── api/          # P5 FastAPI application
│   ├── web/          # P6 static origin server + test API client
│   ├── db/           # P1 catalog + SQL inspection + V2 Postgres adapters
│   ├── analytics/    # V2 Phase 2A KPI / QA calculator
│   ├── shared/       # planned shared types
│   └── config/       # P0 settings + P2 versioned config resolvers + P5 auth settings
├── powerbi/          # V2 Phase 2B DAX / model / pages (no committed .pbix)
├── supabase/         # P1/P2/P3 SQL migrations (P4 adds none)
├── excel/            # Native V2-X Client_Report.xlsx + PublishedFacts.m + 9-sheet report
├── tests/            # P0–P10 tests
├── documentation/    # status, schema, local testing runbook, publication, daily report
├── source/           # local source-data drop folder (gitignored except README)
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

Local Web Engage `.xlsx` files may sit in this folder for forensic work. They are **gitignored** and must stay that way. Do not move, rename, or edit them.

## Prerequisites

- Python 3.11 or later
- pip
- Git
- Docker Desktop (optional; used only if you want to build the P0 image)

## Local setup

```powershell
cd C:\Users\PRATHAM\Downloads\DFIP-V1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows one-click local demo (after the venv install and a local `.env`):
double-click `START_DFIP_DEMO.bat`. Use `CHECK_DFIP_DEMO.bat` and
`STOP_DFIP_DEMO.bat` as documented in `documentation/LOCAL_TESTING_RUNBOOK.md`.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Environment setup

```powershell
copy .env.example .env
```

`.env` is gitignored. Leave secret fields empty. P0/P1 do not need a live database, Supabase project, or API keys.

See `.env.example` for what each variable is for. Local API/web startup,
auth, Excel demo, and troubleshooting: `documentation/LOCAL_TESTING_RUNBOOK.md`.

## Tests

```powershell
python -m pytest
```

## Lint / format

```powershell
python -m ruff check packages tests
python -m ruff format --check packages tests
```

`ruff format` (without `--check`) rewrites files locally. CI runs `ruff check` and `ruff format --check`.

## Docker

Docker is **not required** for P0 development. Docker Desktop is not assumed to be installed.

When Docker is available:

```powershell
docker compose build
docker compose run --rm app
```

The compose file starts the `app` image and runs tests. It does **not** start
Postgres or Supabase unless you opt in with profile `v2-db` (service
`dfip_db`). P1 schema lives as SQL files under `supabase/migrations/`.

```powershell
docker compose --profile v2-db up -d dfip_db
```

## Data security rules

1. Client Web Engage workbooks are source evidence. Do not edit, rename, overwrite, recalculate, or commit them.
2. Never commit `.env`, API keys, service-role keys, or credential files.
3. Never grant a client or Excel workbook a database credential.
4. Source / raw / upload directories are gitignored. Generated extracts (`*.csv`, `*.pkl`, `*.parquet`) are gitignored.

Details: `documentation/DATA_HANDLING.md`.

## Where source data must and must not live

| Location | Allowed | Tracked by Git |
|---|---|---|
| Project root `*.xlsx` (supplied evidence, left in place) | Local forensic copies only | No |
| `source/` | Future local drops of exports | No (except `source/README.md`) |
| `uploads/`, `data/raw/`, `raw/` | Local only | No |
| `excel/` | Native V2-X `Client_Report.xlsx` + `PublishedFacts.m`; nine Daily Report sheets; empty BearerToken; not production campaign data | Yes (`Client_Report.xlsx` only) |
| Git history / GitHub | Never client data or secrets | — |

## Current phase

**DFIP-V1 COMPLETE.** On Windows you can double-click `START_DFIP_DEMO.bat`
(see `documentation/LOCAL_TESTING_RUNBOOK.md`). Or start the API and the web
origin separately:

```powershell
$env:DFIP_DEV_AUTH_TOKEN="choose-a-local-token"
$env:DFIP_DEV_AUTH_ROLE="publisher"
# When DATABASE_URL is set, also set DFIP_DEV_AUTH_CLIENT_ID to that client's UUID.
python -m dfip_api
python -m dfip_web
```

Default API stores are empty. P3/P4 do not run on API startup; there is no HTTP
upload. Open `http://127.0.0.1:3000` only after you have injected or otherwise
populated in-memory stores (tests do this). Use `DFIP_DEV_AUTH_ROLE=publisher`
or `admin` to inspect the working set; `reader` / `client` receive 403 on
`GET /api/v1/facts` and staging routes.

Admin `/admin/facts` is `GET /api/v1/facts` (working set; admin/publisher only).
Client `/client/facts` and `excel/Client_Report.xlsx` read
`GET /api/v1/publications/current/facts` (published slice). Leave
`DATABASE_URL` empty for the V1 in-memory default. `/health` reports
application liveness only. **This is application-level authorization, not
PostgreSQL RLS and not production tenant isolation.** See
`documentation/PUBLICATION.md`.
