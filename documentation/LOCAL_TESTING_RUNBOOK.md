# Local testing and demo runbook

Permanent operator guide for running DFIP locally and walking a **safe**
manual demo. Commands, ports, paths, and environment names are taken from this
repository (`README.md`, `.env.example`, `packages/api`, `packages/web`,
`apps/web/README.md`, `documentation/*`). Do not treat this file as a place to
store secrets.

**P12 COMPLETE.** Historical reports / recovery reuse the existing publication
catalog. **P13A COMPLETE**. **P13B COMPLETE**. **P13C COMPLETE**.
**P13D COMPLETE**. **P13E COMPLETE**. **P13F COMPLETE**. **P13G COMPLETE**. P14 is not started. Do not use this runbook as a reason to change August,
`publication_current`, migrations, facts, QA, Logic/Labels, or the tracked
`excel/Client_Report.xlsx` business/report logic.

**RUN 009:** existing 57-column source files still upload. An approved extra
header after K:BO (`Campaign Objective`) is optional staging-only. An unknown
trailing header fails with `Unapproved source column: …` on the batch error
summary. Generated Excel workbooks stay on the frozen 45-column contract.

---

## ONE-CLICK START

Windows (repository root):

1. Double-click `START_DFIP_DEMO.bat`
2. The launcher checks local `.env` **names**, prepares disposable local Postgres
   (compose `v2-db` on port 5433 when that DSN is used), applies migrations if
   needed, and upserts `demo-publisher` / `demo-client` via existing
   `local_demo_seed` (idempotent; refuses hosted/production DSNs)
3. Wait until the launcher reports API health and website checks
4. The browser opens `http://127.0.0.1:3000`
5. Login as `demo-publisher` (password from `DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD`;
   never printed). Run the demo (Upload → Process → QA → Publish → Excel) using
   **local** fixtures only

Related files (do not embed secrets in them):

| File | Purpose |
|---|---|
| `START_DFIP_DEMO.bat` | Local-only prepare (`scripts/dfip_demo_prepare.py`: migrate + existing `local_demo_seed`), set `PYTHONPATH`, start `python -m dfip_api` and `python -m dfip_web` in separate windows, wait for health, open the browser |
| `CHECK_DFIP_DEMO.bat` | Safe PASS/FAIL checks (Python, paths, modules, auth **variable names**, optional reachability). Never prints secrets |
| `STOP_DFIP_DEMO.bat` | Stops listeners on ports 8000/3000 **only** if the process command line contains `dfip_api` or `dfip_web` |

If `DFIP_AUTH_MODE=dev_token`, `DFIP_DEV_AUTH_TOKEN` must be set locally (empty is not a bypass). If `jwt`, `DFIP_AUTH_SECRET` must be set. With `dev_token` and a non-empty `DATABASE_URL`, `DFIP_DEV_AUTH_CLIENT_ID` is required. The launcher prints **variable names only**.

The launcher seeds `demo-publisher` / `demo-client` through the existing
`python -m dfip_api.local_demo_seed --confirm-local-only` path (via
`scripts/dfip_demo_prepare.py`). It does **not** create Company 2. It refuses
`DFIP_ENV=production` and hosted `DATABASE_URL` values. Passwords stay in
`.env` / the process environment and are never printed. Manual seed remains
valid for terminals.

Manual two-terminal start remains below.

---

## Local E2E bootstrap (default client only)

Repeatable disposable local loop. **Does not** create Company 2 unless
`--company-2` is passed (see **Company 1 / Company 2 isolation** below).
**Does not** touch hosted/Supabase/August data. **Not** a SQL migration.

Default client (already seeded by migrations):
`a0000000-0000-4000-8000-000000000001` (`default`).

Usernames after seed (not passwords): `demo-publisher`, `demo-client`.

### 1. Local PostgreSQL 16

Host port **5433** maps to container 5432 (`docker-compose.yml` profile `v2-db`):

```powershell
docker compose --profile v2-db up -d dfip_db
```

Set **local** `DATABASE_URL` in `.env` (trust auth example — do not commit a
real password; do not use a supabase.co URI):

```text
DATABASE_URL=postgresql://postgres@127.0.0.1:5433/dfip
DFIP_ENV=development
DFIP_AUTH_MODE=jwt
DFIP_AUTH_SECRET=[SET LOCALLY — DO NOT COMMIT]
```

### 2. Migrations

```powershell
python -m dfip_db
```

### 3. Explicit identity seed

Passwords are **environment-only**. They are never printed and must not be
committed.

```powershell
$env:DFIP_LOCAL_DEMO_SEED="1"
$env:DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD="[SET LOCALLY — DO NOT COMMIT]"
$env:DFIP_LOCAL_DEMO_CLIENT_PASSWORD="[SET LOCALLY — DO NOT COMMIT]"
python -m dfip_api.local_demo_seed --confirm-local-only
```

The command refuses empty `DATABASE_URL`, `DFIP_ENV=production`, missing
`--confirm-local-only`, missing `DFIP_LOCAL_DEMO_SEED=1`, and hosted markers
(including `supabase` and transaction pooler port 6543). Re-running upserts
the same subjects (idempotent for disposable reset).

### 4. Start API + SPA

`START_DFIP_DEMO.bat`, or `python -m dfip_api` and `python -m dfip_web`.

SPA login: username `demo-publisher` or `demo-client` and the passwords you
set at seed time. Login does not ask for `client_id`. After `--company-2`,
`demo-publisher` selects a company in the SPA. `demo-client` does not.

### 5. Basic flow (publisher then client)

1. Sign in as `demo-publisher`.
2. Admin Upload Center: upload a local Web Engage `.xlsx` (does **not** publish).
3. Wait until the batch/run succeeds; review QA.
4. Publications: explicit publish of that processing run.
5. Sign out. Sign in as `demo-client`.
6. Client facts: published slice only. Inspector working-set routes return 403.
7. Download Company Workbook from the published-current button. Filename
   `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx`.

Do not Refresh All the tracked live template for client delivery. Use the
static download.

---

## Company 1 / Company 2 isolation (RUN 003)

Local-only proof that two tenants can share disposable Postgres without sharing
published facts. RUN 004 then grants ``demo-publisher`` a Company 2 membership
so one publisher identity can select either tenant. Clients stay single-membership.
**Does not** claim hosted/production RLS isolation.

| Tenant | `client_id` | `client.code` | Client username | Publisher username |
|---|---|---|---|---|
| Company 1 | `a0000000-0000-4000-8000-000000000001` | `default` | `demo-client` | `demo-publisher` (also Company 2 after `--company-2`) |
| Company 2 | `a0000000-0000-4000-8000-000000000002` | `company-2` | `demo-client-2` | `demo-publisher` and leftover `demo-publisher-2` |

`--company-2` grants `demo-publisher` a publisher membership on both companies.
`demo-client` stays Company 1 only. `demo-client-2` stays Company 2 only.
`demo-publisher-2` remains a Company-2-only leftover from RUN 003; it is not
the universal publisher path.

### Seed Company 2 (same disposable DSN as RUN 002)

```powershell
$env:DFIP_LOCAL_DEMO_SEED="1"
$env:DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD="[SET LOCALLY — DO NOT COMMIT]"
$env:DFIP_LOCAL_DEMO_CLIENT_PASSWORD="[SET LOCALLY — DO NOT COMMIT]"
$env:DFIP_LOCAL_DEMO_PUBLISHER2_PASSWORD="[SET LOCALLY — DO NOT COMMIT]"
$env:DFIP_LOCAL_DEMO_CLIENT2_PASSWORD="[SET LOCALLY — DO NOT COMMIT]"
python -m dfip_api.local_demo_seed --confirm-local-only --company-2
```

All four passwords must differ. The command still upserts Company 1 identities
first. It is **not** a migration and is refused against hosted/production DSNs.

Publishing Company 2 can use leftover `demo-publisher-2` (JWT already bound)
or the universal `demo-publisher` path in RUN 004.

### What this unit proves vs what it does not

| Layer | Proven here | Not proven |
|---|---|---|
| FastAPI authorization | JWT `client_id` wins; query/form override is 403; client cannot inspect/publish | Hosted IdP |
| Application SQL `client_id` filters | `publication_current` / published facts / downloads scoped per client | Template/rate-card tenant overlay (Logic/Labels overlay exists; packaged JSON still supplies content when no overlay) |
| PostgreSQL RLS | `SET LOCAL ROLE dfip_api` + GUCs hide the other tenant's `publication_current` / `publication_fact` on disposable DB | Production isolation when the login is table owner / `BYPASSRLS` |
| Identity lookup | `app_user` / `client_membership` have **no RLS**; Python uses `transaction(..., rls=None)` | Database-enforced identity hiding |

Tests: `tests/test_company2_isolation.py` (in-memory plus `DFIP_TEST_DATABASE_URL`).

---

## Universal publisher company selection (RUN 004)

One publisher identity (`demo-publisher`) operates Company 1 and Company 2
from the same SPA. **Does not** add a `platform_admin` role. **Does not**
create Company 3. **Does not** fix per-tenant Logic/Labels/rate-card catalogs.

### Auth design

Password login for a publisher/admin whose **every** membership is inspector
and who has **two or more** such memberships may omit `client_id`. The issued
JWT is **unbound** (`client_id` omitted). Resource routes fail closed until
`POST /api/v1/auth/select-client` re-issues a JWT bound to one authorized
inspector membership.

Why this is safest:

- Existing rule remains: JWT `client_id` always wins; a conflicting request
  `client_id` is 403.
- An unbound JWT that kept both memberships on inspector list endpoints would
  leak both tenants. Fail-closed until select avoids that.
- Switching replaces the token, so server context cannot keep a stale company.
- Single-membership client login is unchanged. A client with two *client*
  memberships still gets 403 without explicit `client_id`.
- Request/form `client_id` cannot substitute for select-client and cannot
  widen a bound context.

Clients never see the selector. `POST /auth/select-client` requires
`admin`/`publisher`. Client callers are 403.

### Publisher SPA flow

1. Sign in as `demo-publisher` (no `client_id`).
2. Company picker is shown (admin routes only; hidden from `demo-client`).
3. Select Company 1 or Company 2.
4. Active company is shown in the top bar. Upload / process / QA / publish
   use the bound JWT company.
5. Switch the top-bar selector to the other authorized company. Pending
   upload state is cleared.

Tests: `tests/test_publisher_company_selection.py`.

Catalog identity (RUN 004B-3): stored catalog version IDs must belong to the
run's `client_id`; packaged fallback must not masquerade as a different
tenant's catalog version. Company 2 and new tenants still process packaged
JSON content; they store NULL version IDs instead of the default client's
UUIDs. Default Company 1 still stores its own packaged identifiers.

### RUN 004B-1 — generic company registry + safe rename

Publishers/admins list authorized tenants from `client` records
(`GET /api/v1/clients`) and rename display `name` only
(`POST /api/v1/clients/{client_id}/rename`). `client.id` and `client.code`
do not change. Publications, `publication_current`, facts, and memberships
stay on the same `client_id`. Clients/readers receive 403. SPA:
`/admin/companies`. Do not use this UI for adding companies (RUN 004B-2).

Tests: `tests/test_company_registry.py`.

### RUN 004B-2 — Add Company

Publishers/admins create a tenant with `POST /api/v1/clients` `{name}`.
`client.id` is a new UUID (`code` is that same UUID text to satisfy
`UNIQUE(code)` without colliding with existing codes). Display names are not
unique. The caller receives an inspector membership; other users do not.
SPA: `+ Add Company` on `/admin/companies`. Does not onboard catalogs,
clone Logic/Labels, or create client users.

Tests: `tests/test_company_create.py`.

### RUN 004B-3 — company onboarding / tenant configuration

A newly created company is **processing-ready** after the publisher selects
it: upload → process → QA → publish uses packaged JSON for Logic, Labels,
templates, and rate cards. Optional tenant Logic/Labels remain the existing
upload/activate overlay (`/admin/logic`, `/admin/labels`) for the selected
company. There is no clone wizard and no copy of Company 1 catalogs.

**Tenant-owned:** `client`, memberships, facts, publications, uploaded
Logic/Labels versions.

**Shared/global for V1:** packaged template and rate-card **content** (date
windows). KPI/QA/source-column catalogs. Packaged campaign/label **content**
until a tenant overlay is activated.

**Identity:** Stored catalog version IDs must belong to the run's `client_id`;
packaged fallback must not masquerade as a different tenant's catalog
version. `fact_campaign_day.rate_card_rule_id` follows the same rule: packaged
P1 rule UUIDs persist only for the default client; other tenants keep Total
Cost from packaged rates and store NULL rule ids.

**Client-ready (004B-3):** Publishing is tenant-scoped. Client-portal login
provisioning for a company is RUN 004B-4 (`POST /api/v1/clients/{id}/users`).

SPA `/admin/companies` states this lifecycle. Tests:
`tests/test_catalog_version_identity.py` plus frozen 003/004/004B-1/004B-2
suites.

### RUN 004B-4 — end-to-end company-management acceptance

**Product path:** one-time publisher setup. `GET /api/v1/auth/setup-status`
is true only when no publisher/admin identity exists **in development/test**.
The SPA then shows Create the publisher account (username, password, confirm
password). `POST /api/v1/auth/setup-publisher` stores a PBKDF2 hash, grants
publisher membership on every existing company, and never returns the password.
A second setup is 409. Production-grade setup is denied unless header
`X-DFIP-Bootstrap-Token` matches `DFIP_BOOTSTRAP_TOKEN`; production
`setup-status` always returns false. The operator then signs in with those exact
credentials and uses company selection. This is not public registration
and not a per-company publisher.

**Test/local fixture only:** `python -m dfip_api.local_demo_seed
--confirm-local-only` still inserts `demo-publisher` / `demo-client` (and
optional leftover `demo-publisher-2` / `demo-client-2`) for frozen isolation
tests. That is not the product credential path.

`POST /api/v1/clients/{client_id}/users`
`{username, password, confirm_password, role: "client"}`
inserts `app_user` + one `client` membership. Password is hashed (PBKDF2);
the API never returns the password. Usernames are not generated. Clients
login without a company picker. They cannot inspect, publish, or manage
companies. Cross-tenant `client_id` is 403.

SPA: Create Client Account on `/admin/companies` (username, password,
confirm). Tests: `tests/test_company_management_acceptance.py`.

Browser file injection for `/admin/upload` remains a CDP limitation
(same as earlier runs). Fixture upload uses the SPA's
`POST /api/v1/uploads` endpoint. That is a test-method limit, not a
product upload defect. The rest of publisher/client SPA acceptance is
done in the local website. Application authorization (403) is what
these tests prove; they do not prove production PostgreSQL RLS.

---

---

## 5-MINUTE LOCAL START

Prerequisites (from `README.md`): Python **3.11+**, pip, Git. From the
**repository root**, create a venv and install the editable package:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
copy .env.example .env
```

Leave secret fields in `.env` empty, or set them **locally** (never commit).

**Terminal 1 — API** (repository root; after venv activate). Password sign-in
for the SPA requires a non-empty `DFIP_AUTH_SECRET` (`apps/web/README.md`).
The values below are placeholders only:

```powershell
$env:DFIP_AUTH_SECRET="[SET LOCALLY — DO NOT COMMIT]"
$env:DFIP_AUTH_MODE="jwt"
python -m dfip_api
```

Host/port defaults: `127.0.0.1:8000` (`DFIP_API_HOST` / `DFIP_API_PORT`).
Public health: `http://127.0.0.1:8000/health`. OpenAPI (non-production):
`http://127.0.0.1:8000/docs`.

**Terminal 2 — website** (separate process; API does not serve the SPA):

```powershell
python -m dfip_web
```

Open `http://127.0.0.1:3000` (`DFIP_WEB_HOST` / `DFIP_WEB_PORT`,
`DFIP_WEB_ORIGIN`).

**Login:** username/password against `app_user` (`POST /api/v1/auth/login`).
The SPA does **not** accept `DFIP_DEV_AUTH_TOKEN`.

Seed local demo users with `python -m dfip_api.local_demo_seed --confirm-local-only`
(see **Local E2E bootstrap** above). Migrations still do **not** insert a demo
login. The in-memory API starts with an **empty** user directory unless tests
inject users. Do not put passwords in this document.

Then (publisher/admin session, local fixtures only): Upload Raw → optional
Logic/Labels draft + activate → Process / Review QA → explicit Publish →
download `Client_Report.xlsx` → open Excel Desktop on a **local copy**.

---

## 1. Project overview

DFIP replaces a manual Excel / Power Query workflow for Web Engage campaign
reporting. Publishers upload source workbooks; DFIP labels, costs, validates
(QA findings), and **explicitly publishes** a narrow authorized slice. Clients
consume that slice through the website and the Excel Client Report
(`README.md`, `documentation/PUBLICATION.md`).

| Surface | What it is (this repo) |
|---|---|
| **API** | FastAPI `dfip_api`, prefix `/api/v1`, default `http://127.0.0.1:8000`. Working-set routes, catalogs, uploads, publications. Public `GET /health` is process liveness and does **not** open PostgreSQL. Authenticated `GET /api/v1/ops/ready` is cheap readiness (publisher/inspector). Web `GET /health` is the website process only. |
| **Website** | Static SPA in `apps/web/static`, served by `python -m dfip_web` on port **3000**. Does not proxy `/api/v1` (`packages/web/README.md`). Browser uses `/config.json` for `apiBaseUrl` / `apiPrefix`. |
| **PostgreSQL / Supabase** | Optional. Empty `DATABASE_URL` → in-memory stores. Set `DATABASE_URL` for persistence (PostgreSQL 16 local or Supabase URI, session pooler **5432**, not transaction pooler **6543**). SQL lives under `supabase/migrations/`. |
| **Object storage** | Original source files. Empty `DFIP_STORAGE_ENDPOINT` → in-memory adapter (development/test; not durable across restart). Production-grade requires a local directory. Bucket name `DFIP_STORAGE_BUCKET` (default `dfip-source-files`). Server-side only (`.env.example`). |
| **Excel Client Report** | Tracked `excel/Client_Report.xlsx` plus `excel/PublishedFacts.m`. Native DataMashup; fake `xl/queryMashup/` parts forbidden. Git BearerToken must stay empty. |
| **Nine report sheets** | FY-2026 Daily Report tabs as native PivotTables on a shared cache bound to PublishedFacts (`documentation/DAILY_REPORT.md`). |
| **Native PivotTables** | P11: Analyze/Design, Fields pane, slicers, page filters, outline expand/collapse. Historical downloads rebind cache to that publication’s static cells (`refreshOnLoad=0` for P8 snapshots). |
| **Publication workflow** | Upload does **not** publish. `POST /api/v1/publications` writes snapshot facts and moves `publication_current`. History: `GET /api/v1/publications`. `fact_scope=processing_run` (default) vs `client_current` (cumulative). |

---

## 2. Prerequisites

Facts from `README.md`, `pyproject.toml`, CI, Excel docs:

| Requirement | Repository fact |
|---|---|
| Python | `requires-python = ">=3.11"`; CI uses **3.11** |
| Node / Vite / React | **Not used.** SPA is static HTML/JS (`apps/web/README.md`) |
| Packages | `python -m pip install -e ".[dev]"` (runtime + pytest + ruff) |
| Excel | Excel **Desktop** for PivotTable objects, ribbon Analyze/Design/Fields, slicers, and Refresh All (`documentation/DESKTOP_ACCEPTANCE.md`, `excel/README.md`). Python `build_client_report()` is structure-only and must **not** overwrite the native tracked workbook. |
| Local services | API + web as two processes. PostgreSQL only if `DATABASE_URL` is set. Docker optional (`docker compose`); default compose runs pytest, not the API. Profile `v2-db` starts Postgres 16 as `dfip_db` on host **5433**. |
| Folders | Repository root working directory; `source/` for local Web Engage drops (gitignored except README); `excel/` for the template; gitignored `/tmp/` for local acceptance copies (`tmp/desktop_acceptance/` in desktop docs). |

---

## 3. Environment variables

Loaded by `dfip_config.settings.Settings` from the process environment and
optional `.env` (`env_file=".env"`). **Never put real secret values in this
markdown.** For secrets use **[SET LOCALLY — DO NOT COMMIT]**.

Copy template: `copy .env.example .env` (Windows) / `cp .env.example .env`.

| Variable | Required? | Purpose | Local-only? | Secret? |
|---|---|---|---|---|
| `DFIP_ENV` | Default `development` | Named environment. `production` refuses `dev_token`, requires JWT secret + `DATABASE_URL`. | Safe example: `development` | No |
| `DFIP_LOG_LEVEL` | Default `INFO` | Log verbosity | Safe | No |
| `DFIP_API_HOST` | Default `127.0.0.1` | API bind host | Typical local | No |
| `DFIP_API_PORT` | Default `8000` | API bind port | Typical local | No |
| `DFIP_API_BASE_URL` | Default `http://127.0.0.1:8000` | Public API URL (SPA `/config.json`) | Typical local | No |
| `DFIP_API_PREFIX` | Default `/api/v1` | API path prefix | Safe | No |
| `DFIP_WEB_ORIGIN` | Default `http://127.0.0.1:3000` | CORS allow-origin on the API | Must match the browser origin | No |
| `DFIP_WEB_HOST` | Default `127.0.0.1` | Web bind host | Typical local | No |
| `DFIP_WEB_PORT` | Default `3000` | Web bind port | Typical local | No |
| `DFIP_WORKER_CONCURRENCY` | Default `1` | Documented worker concurrency | Safe | No |
| `DATABASE_URL` | Empty OK in development; **required in production** | PostgreSQL connection. Empty = in-memory. | Local or hosted | **Yes** (credentials in URI) — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_TEST_DATABASE_URL` | No (pytest `-m postgres` only) | **Not** read by the API. Suite **drops `public`**. | Test only | **Yes** if set. **Never** a live/acceptance DB |
| `SUPABASE_URL` | Empty OK | Reserved for later Auth/PostgREST; not the API persistence path | — | Treat as sensitive |
| `SUPABASE_ANON_KEY` | Empty OK | Must never grant table SELECT; never ship to browser/Excel | — | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `SUPABASE_SERVICE_ROLE_KEY` | Empty OK | Server-side only | Production-oriented | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_STORAGE_BUCKET` | Default `dfip-source-files` | Logical storage prefix | Safe default | No |
| `DFIP_STORAGE_ENDPOINT` | Empty = in-memory (dev/test) | Production-grade: required local directory | Local path is local-only | Path may be sensitive |
| `DFIP_AUTH_MODE` | Default `dev_token` | `dev_token` or `jwt`. Production must be `jwt`. | `dev_token` is **development/test only** | No (the mode name) |
| `DFIP_DEV_AUTH_TOKEN` | Empty is **not** a bypass | Constant Bearer for curl/tests. SPA never pastes this. | **Development-only credential** | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_DEV_AUTH_ROLE` | Default `reader` | Role for the development token: `reader` \| `client` \| `publisher` \| `admin` | Dev/test | No |
| `DFIP_DEV_AUTH_CLIENT_ID` | **Required** if `dev_token` **and** `DATABASE_URL` is set | UUID binding the dev token to one client | Dev/test | Treat as tenant identifier; do not commit production client ids |
| `DFIP_AUTH_SECRET` | Required for `jwt` mode and for issuing login JWTs; required in production | HS256 signing secret | Local vs production **must not** reuse | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_AUTH_ISSUER` | Default empty | JWT issuer claim when configured | — | Usually not a password |
| `DFIP_AUTH_AUDIENCE` | Default empty | JWT audience when configured | — | Usually not a password |
| `DFIP_AUTH_TOKEN_TTL_SECONDS` | Default `3600` (1 hour). Local demo `43200` (12 hours) | Access JWT lifetime after login | Production default unchanged; tokens still expire; local demo may raise | No |
| `DFIP_PASSWORD_PBKDF2_ITERATIONS` | Default `210000` | Hash iterations for stored verifiers | Tests may lower; do not weaken production | No |
| `DFIP_LOCAL_DEMO_SEED` | Required only for `python -m dfip_api.local_demo_seed` | Must be exactly `1` | Local seed only | No |
| `DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD` | Required only for local seed | Password for `demo-publisher` | Local seed only | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_LOCAL_DEMO_CLIENT_PASSWORD` | Required only for local seed | Password for `demo-client` | Local seed only | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_LOCAL_DEMO_PUBLISHER2_PASSWORD` | Required only with `--company-2` | Password for `demo-publisher-2` | Local seed only | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_LOCAL_DEMO_CLIENT2_PASSWORD` | Required only with `--company-2` | Password for `demo-client-2` | Local seed only | **Yes** — [SET LOCALLY — DO NOT COMMIT] |
| `DFIP_UPLOAD_MAX_BYTES` | Default `10485760` (10 MiB). Local demo `52428800` (50 MiB) | Max multipart `.xlsx` size | Production default unchanged; local demo may raise | No |
| `DFIP_UPLOAD_MAX_FILES` | Default `5` | Max workbook parts per request | Keep 5 | No |
| `DFIP_UPLOAD_MAX_TOTAL_BYTES` | Default `20971520` (20 MiB). Local demo `104857600` (100 MiB) | Max total multipart bytes | Local demo may raise so one 50 MiB file is accepted | No |
| `DFIP_DOWNLOAD_MAX_ROWS` | Default `75000` | Cap for published CSV/XLSX / Client Report | Safe default | No |

**Safe configuration values:** hosts, ports, URLs, `DFIP_ENV=development`,
bucket name, auth **mode** names, numeric caps.

**Sensitive credentials:** `DATABASE_URL`, `DFIP_AUTH_SECRET`,
`DFIP_DEV_AUTH_TOKEN`, Supabase keys, Excel Settings **BearerToken** on a
local workbook copy.

**Development-only:** `DFIP_AUTH_MODE=dev_token`, `DFIP_DEV_AUTH_*`.
Production refuses `dev_token`.

**Production-only expectations (do not use this runbook to configure prod):**
`DFIP_ENV=production`, `DFIP_AUTH_MODE=jwt`, non-empty `DFIP_AUTH_SECRET`,
non-empty `DATABASE_URL`, local `DFIP_STORAGE_ENDPOINT` directory. `/docs`
disabled.

P13B recovery: leftover `pending`/`running` runs are failed on API start.
Publisher Batch/Processing pages expose Retry / Re-process
(`POST /api/v1/batches/{id}/process`). Retry does not publish. Do not kill a
live operator API to prove restart; use `tests/test_p13b_recovery.py` on
`DFIP_TEST_DATABASE_URL`.

P13C backup/restore: `python -m dfip_api.backup`. Stop the API, dump with a
superuser/BYPASSRLS login (never `dfip_api`), copy `DFIP_STORAGE_ENDPOINT`,
verify. Restore only into a newly created disposable local database with
`--confirm-disposable`. Never restore into `dfip`, hosted, or August.
`tests/test_p13c_backup.py` is the acceptance path (`-m postgres` creates a
disposable `dfip_p13c_*` database). Do not put `.env` in a backup set.

---

## 4. Start the API

| Item | Repository fact |
|---|---|
| Working directory | **Repository root** (same as `pip install -e ".[dev]"`) |
| PYTHONPATH | **Not** required after editable install. Pytest sets `pythonpath` to `packages/*` in `pyproject.toml`. Runtime uses installed packages `dfip_api`, `dfip_config`, etc. |
| Command | `python -m dfip_api` (`packages/api/dfip_api/__main__.py` → uvicorn `dfip_api.app:app`) |
| Host / port | `settings.dfip_api_host` / `dfip_api_port` → default **127.0.0.1:8000** |
| Reload | `reload=False` |
| Expected success | Uvicorn binds that host/port (typical log: `Uvicorn running on http://127.0.0.1:8000`). Process stays in the foreground. |
| Health | `GET http://127.0.0.1:8000/health` → `status: ok`, `application: dfip-api`, `database.status: not_checked` (liveness, not readiness) |
| Readiness | Authenticated `GET http://127.0.0.1:8000/api/v1/ops/ready` → `ready`/`not_ready` (200/503). Publisher/inspector only. |
| Docs | `http://127.0.0.1:8000/docs` when `DFIP_ENV` is **not** `production` |

**PowerShell (simplest supported path):** activate `.venv`, then from repo root:

```powershell
python -m dfip_api
```

If password login is needed, set `DFIP_AUTH_SECRET` and typically
`DFIP_AUTH_MODE=jwt` as in `apps/web/README.md` **before** starting (or put
them in gitignored `.env`).

Do not start API and website in the same terminal if you need both: each
`uvicorn.run` blocks.

---

## 5. Start the website

| Item | Repository fact |
|---|---|
| Working directory | Repository root |
| Command | `python -m dfip_web` (`packages/web/dfip_web/__main__.py` → `dfip_web.app:app`) |
| Host / port | Default **127.0.0.1:3000** |
| Result | Serves `apps/web/static`; `/config.json` points the browser at `DFIP_API_BASE_URL` + `DFIP_API_PREFIX` |
| URL | `http://127.0.0.1:3000` |
| Docs | Web process has `docs_url=None` |

**Separate terminal from the API.** CORS allows `DFIP_WEB_ORIGIN` only
(`packages/api/dfip_api/app.py`). If the site cannot call the API, check that
origin and that the API is listening.

---

## 6. Local authentication

**Modes** (`packages/api/dfip_api/auth.py`):

- **`dev_token`:** constant Bearer matching `DFIP_DEV_AUTH_TOKEN`. Allowed only
  when `DFIP_ENV` is `development` or `test`. Empty token is not a bypass.
  With `DATABASE_URL`, `DFIP_DEV_AUTH_CLIENT_ID` is required. Role from
  `DFIP_DEV_AUTH_ROLE`. **Curl/tests only** — the SPA never pastes this token
  (`documentation/WEBSITE.md`).
- **`jwt`:** HS256 with `DFIP_AUTH_SECRET`. Password sign-in issues this JWT.

In development/test, `dev_token` mode still accepts a matching development
token; if `DFIP_AUTH_SECRET` is also set, a valid access JWT is accepted on
the **same** process so curl can use the token while the SPA uses password
login.

**SPA login:** `POST /api/v1/auth/login` with `username` + `password`
(optional `client_id` UUID). Username maps to `app_user.subject`. Password is
checked against `app_user.password_hash`. Membership comes from
`client_membership`. The API issues an HS256 access JWT (`token_type: bearer`,
`expires_in` from TTL). The SPA stores the **issued** JWT in `sessionStorage`
and sends `Authorization: Bearer …`. Logout increments `app_user.token_version`.

**client_id:** bound from membership (single membership auto-selects; multiple
memberships require `client_id` or login returns **403**). Platform admins
follow `apply_identity` rules in `auth_routes.py`. JWT claims cannot exceed
membership (`membership.py`).

**dev_token limitations:** not an IdP; not RLS; not a platform-wide identity
when a database is configured; refused in production; reader/client cannot
hit working-set / upload / publish routes (403).

**Demo users are not in migrations.** Use `python -m dfip_api.local_demo_seed --confirm-local-only` for disposable local Postgres (add `--company-2` for Company 2 identities). In-memory identity is empty at process start. Pytest users such as `alice.client` exist **only** inside tests (`tests/test_p7_client_auth.py`) and are **not** a runtime demo account. Power BI `dfip_desktop_a` / `_b` are **database LOGIN roles** for parked P9 work, stored under gitignored
`tmp/desktop_acceptance/pbi_desktop_logins.json` if created locally — they are
**not** SPA usernames.

If you already have a local PostgreSQL `app_user` row: username is
`app_user.subject`; the password verifier is `app_user.password_hash`;
membership is `client_membership`. This runbook does not record any password.

---

## 7. What each credential means

| Credential / ID | Purpose | Where configured | Client types it? | Secret? |
|---|---|---|---|---|
| `DATABASE_URL` | Server persistence | `.env` / process env | No | Yes |
| JWT secret (`DFIP_AUTH_SECRET`) | Sign/verify access JWTs | API env | No | Yes |
| Dev token (`DFIP_DEV_AUTH_TOKEN`) | Curl/test Bearer | API env | No (SPA forbids paste) | Yes |
| Dev client ID (`DFIP_DEV_AUTH_CLIENT_ID`) | Bind dev token to one tenant when DB is on | API env | Excel ClientId must match if set (`excel/README.md`) | Tenant id; not a password |
| Username | `app_user.subject` | Database or test inject | **Yes** (SPA) | Identifier |
| Password | Verifies `password_hash` | Stored hashed in `app_user`; never in git | **Yes** (SPA) | Yes |
| `client_id` | Tenant scope on API and Excel | Membership; JWT claim; optional login field; Excel Settings ClientId | Sometimes (multi-membership / Excel) | Identifier |
| Storage endpoint/bucket | Source-file custody | API env | No | Endpoint path may be sensitive |
| Excel BearerToken | Power Query `PublishedFacts.m` calls published facts | Settings table on a **local** workbook copy | Operator pastes a **reader/client JWT** or local token — **never** commit | Yes |
| Excel refresh | `PublishedFacts` → `GET /api/v1/publications/current/facts` | `excel/PublishedFacts.m` + Settings | Refresh All in Desktop | Token is secret |

---

## 8. Manual core demo (safe local data)

Use **local** Web Engage `.xlsx` drops (`source/`, gitignored) or other
operator-owned fixtures. Packaged Logic/Labels JSON defaults live in
`packages/config/dfip_config/data/` (campaign/template/FL1 snapshots — not raw
facts). Do **not** reprocess or republish **live August**.

1. **Start API** — section 4. Confirm `GET /health`.
2. **Start website** — section 5 (second terminal).
3. **Open website** — `http://127.0.0.1:3000`.
4. **Login** — publisher or admin `app_user` (not seeded here). Reader/client
   cannot upload or publish (403).
5. **Upload Raw** — `/admin/upload` Raw Data. `POST /api/v1/uploads` returns
   **202** (or **200** SHA replay). Does **not** publish. Poll batch / processing
   run until `succeeded`.
6. **Logic** — optional. Upload Logic `.xlsx` → **draft** → explicit
   **activate** (`/admin/logic` or `/admin/catalogs`). Invalid files 422.
   Otherwise processing uses packaged date-window catalogs.
7. **Labels** — same for Labels (`/admin/labels`). Activation does not publish.
8. **Process** — ingest/transform already ran on upload. Optional
   `POST /api/v1/batches/{id}/process` creates a **new** run with currently
   active catalogs (does not re-upload Raw, does not publish).
9. **Inspect QA** — `/admin/review` or run detail:
   `GET /api/v1/processing-runs/{id}/qa-findings`. Verdict `pass` / `warn`
   required to publish; `fail` / `unavailable` cannot publish.
10. **Review** — working-set `/admin/facts` (`GET /api/v1/facts`) is
    unpublished until step 11.
11. **Publish** — Publications or run detail: `POST /api/v1/publications`.
    Default `fact_scope=processing_run`. Cumulative: `client_current`.
12. **Download Company Workbook** — select the company, then `/admin/companies`,
    `/admin/downloads`, or `/admin/publications`:
    `GET /api/v1/publications/current/client-report.xlsx` (static recovery) or
    `GET /api/v1/publications/current/refreshable-client-report.xlsx`.
    Save under gitignored `tmp/` — do not overwrite tracked `excel/Client_Report.xlsx`.
    No current publication returns 404. Static files do not change after a later publish;
    refreshable files do after Excel Refresh All with a current JWT.
13. **Open Excel Desktop** on the **refreshable download** for Refresh All, or the
    static download for recovery. Do not Refresh All the tracked git template.
14. **Refresh** — refreshable only: Settings `ApiBaseUrl` is pre-filled; paste
    `BearerToken`; leave `ClientId` empty when the JWT has `client_id`. Ignore
    Privacy Levels. Clear BearerToken before save. JWT client_id remains
    authoritative.
15. **Verify nine reports** — section 9. Confirm you did not change live August.
16. **P12 publication history** — `/admin/publications`: current is marked
    Current publication; prior rows are Historical publication. Download Client
    Report on a prior row stays bound to that `publication_id` (short-id
    filename). `/admin/history` is working-set fact lineage, not this catalog.
    Client home lists only that tenant. Do not Refresh All a historical static
    file. Production deployment package is `documentation/V1_DEPLOYMENT.md`; do not start P14.

---

## 9. Excel demo checklist

Specification: `documentation/DAILY_REPORT.md`. Visual expectation: nine
**PivotTable objects** (not formula reconstruction on the sheet). Shared cache
from PublishedFacts. Independent slicers per sheet. Compact+outline so Month
expands to Day.

| Check | What to look for |
|---|---|
| Nine report sheets | Overall Daywise Report; Vertical Level; Channel Wise; Sub-Split; AMC DayWise; AMC Split; AMC Vertical Monthly Split; D2C Vertical; Service Campaigns (names include trailing spaces as in the source spec) |
| PivotTable object | Selecting the table shows **PivotTable Analyze** and **PivotTable Design** |
| Fields pane | PivotTable Fields lists cache fields (labels, day, metrics) |
| Slicers | Per-sheet slicers; Channel on Overall does not filter Sub-Split |
| Page filters | AMC sheets default Filter Logic 1_2 Group7 (D2C AMC Vertical); D2C Vertical Group5; Service Campaigns Filter Logic 1 = `Service \| FMS & LMS \| Campaigns` (P11 pins CurrentPage after populate) |
| Expand/collapse | Outline +/- on Month → day-level rows |
| Day-level detail | Days appear under expanded months on Overall / AMC DayWise / Service Campaigns |
| Totals | Grand/subtotals match published facts for the visible filter — not unpublished working-set rows |
| Report values | Unique clicks, delivered, cost, etc. match `GET .../publications/current/facts` (or historical `{id}/facts` for a historical download) |

Do not commit a token-bearing copy. Do not run `build_client_report()` over
the native tracked file.

---

## 10. Incremental month test (safe local)

Use **two local** Web-Engage Raw workbooks whose days fall in different months
(operator files in `source/`). Packaged campaign catalogs are vintage-windowed
(`campaign_labels_v1.json` Apr–Jul 2025, `v2` Aug–Oct 2025 —
`packages/config/dfip_config/data/README.md`). Prefer months that match those
windows; do not use live August production as the correction target.

1. **January (or first month) file:** upload → wait for succeeded run → QA →
   publish. For a growing published slice use `fact_scope=client_current`
   (`documentation/HTTP_WORKFLOW.md`, `PUBLICATION.md`). Verify Excel /
   `/client/facts` shows only that month’s published grains.
2. **February (or second month) file:** upload → process → QA → publish
   `client_current`. Verify **both** months appear; first month grains remain
   unless restated.
3. **Correction:** upload a restatement for **one** January grain (same
   campaign/day keys). Later uploads **restate matching fact grains**
   (`HTTP_WORKFLOW.md`). Publish again. Verify **only** that grain’s published
   values change; other January and February grains stay. Complete P8
   **snapshots** of earlier publications must **not** change
   (`GET /publications/{id}/facts`).

---

## 11. Historical publication test (safe local)

Do not touch live August.

1. **Publish A** — record one KPI from `GET /api/v1/publications/current/facts`
   (e.g. Unique Clicks for a known campaign/day) and the `publication_id`.
2. **Controlled change** — upload a restatement of that grain; process; **publish B**.
3. **Verify current = B** — `GET /api/v1/publications/current/facts` (and
   current Client Report download) match B.
4. **Verify A recoverable** — `GET /api/v1/publications/{id-of-A}/facts` and
   `GET /api/v1/publications/{id-of-A}/client-report.xlsx` still match A.
   Complete snapshots do not follow later restatement (`PUBLICATION.md`).
   Legacy `snapshot_status=none` still joins live facts — those rows are **not**
   immutable; do not treat them as frozen history.

---

## 12. Troubleshooting

| Symptom | Likely cause | Command / check | Expected result |
|---|---|---|---|
| `localhost:8000` connection refused | API not running or wrong host/port | Start `python -m dfip_api`; GET `/health` | JSON `status: ok` |
| `ModuleNotFoundError` (`dfip_api`, …) | Editable install missing | From repo root: `python -m pip install -e ".[dev]"`; activate `.venv` | `python -c "import dfip_api"` succeeds |
| Missing PYTHONPATH | Running without install; pytest needs package dirs | Use `pip install -e`; pytest already has `pythonpath` in `pyproject.toml` | Tests/API import packages |
| `DFIP_AUTH_MODE` configuration error | Unsupported mode or `dev_token` outside development/test; production without jwt | Align with `.env.example` and `auth.validate_auth_settings` | Process starts; no `AuthConfigurationError` |
| Missing `DFIP_DEV_AUTH_CLIENT_ID` | `dev_token` + non-empty `DATABASE_URL` | Set a UUID for the demo client, or use in-memory (`DATABASE_URL` empty) | Startup succeeds |
| Missing database connection | Bad/empty URL while code path needs Postgres; production without URL | Check `DATABASE_URL`; `/health` `database.configured` true/false **does not** prove connectivity | Persistence errors are 503 `PERSISTENCE_UNAVAILABLE` if pool fails |
| Website cannot reach API | API down; `DFIP_API_BASE_URL` mismatch; CORS origin ≠ `DFIP_WEB_ORIGIN` | Open `/config.json` on :3000; match API URL; restart API after origin change | Browser calls `:8000/api/v1` with CORS |
| Login **401** | Wrong password; no `app_user`; empty in-memory directory; dummy-hash timing | Confirm user exists in DB or that you are not expecting a seeded demo | `Invalid authentication credentials.` |
| Login **403** | No memberships; multiple memberships without `client_id`; **inactive company** client login | Membership rows; pass `client_id` on login; reactivate if lifecycle is inactive | Authorized session JSON |
| Working-set **403** after login | Role is reader/client | Use publisher/admin membership | Upload/facts/publish allowed |
| Excel cannot refresh | Empty BearerToken; wrong `ApiBaseUrl`; privacy firewall; API down; unpublished empty slice | Local Settings; GET published facts with same token; Ignore Privacy Levels | Query returns published rows or empty headers |
| Excel PivotTable crash / repaired file | Fake mashup parts; overwriting native file with `build_client_report()` | Use tracked native file or API download; never fake `queryMashup` | Workbook opens as PivotTable objects |
| Missing PublishedFacts | Wrong download; empty publication pointer | Publish first; use `.../client-report.xlsx` | Sheet present; historical downloads static cells |
| Historical snapshot looks like current | Legacy `snapshot_status=none` or opened **current** download | Use `{publication_id}` routes; complete snapshots only | A frozen; current follows B |

---

## 13. Health check

These are **not interchangeable**:

- `GET http://127.0.0.1:8000/health` — API **process liveness**. `database.status`
  is always `not_checked`. Does not open PostgreSQL, scan storage, or verify
  backups.
- `GET http://127.0.0.1:3000/health` — website **process liveness**
  (`application: dfip-web`). Does not prove the API or the database.
- `GET http://127.0.0.1:8000/api/v1/ops/ready` — authenticated **readiness**
  (admin/publisher). Cheap `SELECT 1`, source-storage directory check, and
  process-local worker idle/busy. HTTP 200 `ready` or 503 `not_ready`.
  Does not hash the archive or run `python -m dfip_api.backup verify`.
- `python -m pytest` from repo root — default in-memory suite (CI). Does not
  require `DATABASE_URL`.
- `python -m ruff check packages tests` — lint, no database.

**Do not treat as a local demo health check:**

- `python -m pytest -m postgres` with `DFIP_TEST_DATABASE_URL` — **drops
  `public`**. Never point at live Supabase / August.
- `python -m dfip_db` — applies SQL migrations to `DATABASE_URL`. Not a
  liveness probe; do not run against production.

Backup verification remains `python -m dfip_api.backup verify` (P13C), not an
HTTP health request.

---

## 14. Safe vs dangerous actions

**SAFE FOR LOCAL DEMO**

- Start local API (`python -m dfip_api`)
- Start local web (`python -m dfip_web`)
- Use local fixtures under `source/` or other gitignored operator files
- Create disposable local acceptance workbooks under gitignored `tmp/`
- Download Client Report / facts for a **local** published pointer
- Deactivate/reactivate a **disposable local** company
- `python -m dfip_api.purge dry-run --code CODE --confirm-disposable` on a disposable local database
- `python -m pytest` (default, in-memory)

**DO NOT**

- Point destructive pytest (`-m postgres` / `DFIP_TEST_DATABASE_URL`) at live Supabase
- Republish or reprocess **August** / live acceptance data
- Alter `publication_current` except via normal local publish on a disposable DB
- Overwrite tracked `excel/Client_Report.xlsx` with a token-bearing copy
- Commit `.env`, tokens, or service-role keys
- Run destructive cleanup / drop-public against anything but an isolated test DB
- Start P14 (final full acceptance against a real Droplet is a later operator step)
- Restore a P13C backup into hosted, August, or any live database. Colocated production `dfip` restore uses `--confirm-production-local` only during documented disaster recovery.
- Run `python -m dfip_api.purge purge` against hosted/live/August/`postgres` or the packaged default owner. Production-local `dfip` purge still requires eligibility + `--confirm-purge` + `--confirm-production-local`.
- Execute permanent purge from the browser/SPA

---

## 15. Common files / paths

| Path | Role |
|---|---|
| `packages/api/dfip_api/` | API application |
| `packages/web/dfip_web/` | Web origin + Client Report download / PivotTable helpers |
| `apps/web/static/` | SPA |
| `packages/config/dfip_config/` | Settings + packaged JSON catalogs |
| `packages/core/` | Ingest / transform libraries |
| `packages/db/` | Postgres adapters; `python -m dfip_db` migrations |
| `tests/` | Automated tests (P0–P13C) |
| `packages/config/dfip_config/data/` | Logic/Labels JSON snapshots |
| `source/` | Local raw drops (gitignored except README) |
| `excel/Client_Report.xlsx` | Tracked native template |
| `excel/PublishedFacts.m` | Power Query source of truth |
| `documentation/` | Status, schema, HTTP workflow, website, publication, daily report, desktop acceptance, this runbook |
| `.env.example` | Environment template |
| `Dockerfile`, `docker-compose.yml` | Optional image / pytest / `v2-db` |
| `supabase/migrations/` | Schema artifact |
| `tmp/` (gitignored) | Local desktop acceptance copies |
| `powerbi/` | Parked P9 package (no committed `.pbix`) |
| `START_DFIP_DEMO.bat` | Windows one-click start (API + web + browser) |
| `CHECK_DFIP_DEMO.bat` | Safe PASS/FAIL demo checks |
| `STOP_DFIP_DEMO.bat` | Stop DFIP listeners on 8000/3000 only |
| `scripts/dfip_demo_env_presence.ps1` | Auth variable presence (no secret values) |
| `scripts/dfip_demo_port.ps1` | Port conflict / DFIP-only stop |

Windows BAT files start `python -m dfip_api` / `python -m dfip_web`; they are not a second startup protocol.

---

## 16. Quick start

See **5-MINUTE LOCAL START** at the top of this file.

---

## 17. Boss demo checklist

- [ ] API running (`GET /health` → `ok`)
- [ ] Website running (`http://127.0.0.1:3000`)
- [ ] Login works (operator `app_user`; demo **not** seeded)
- [ ] Upload works (202; published still false)
- [ ] QA visible on the processing run
- [ ] Publish works (explicit; pointer moves)
- [ ] Excel downloads (`client-report.xlsx`)
- [ ] 9 reports visible
- [ ] PivotTable Analyze/Design visible
- [ ] Fields pane visible
- [ ] Slicer works
- [ ] Expand/collapse works
- [ ] January (or first-month fixture) data visible
- [ ] February cumulative test passes (`client_current` + two local files)
- [ ] No live August changes

---

## 18. No secret storage

- Never put secrets in this markdown file.
- Never commit `.env` files containing secrets.
- Never commit token-bearing Excel acceptance copies.
- Never paste production credentials into screenshots.
- Never grant Excel or the browser a database credential
  (`README.md` data security rules).
