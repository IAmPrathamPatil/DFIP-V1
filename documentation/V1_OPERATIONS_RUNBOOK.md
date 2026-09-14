# DFIP V1 — Operations runbook

Commands below are those **declared in the repository**. Local demo helpers under `tmp/start_api.cmd` are **gitignored** and not the source of truth.

See `documentation/V1_DEPLOYMENT.md` for the P13G DigitalOcean single-Droplet package. Do not treat local demo scripts as production.

---

## Prerequisites

- Python **≥ 3.11** (`pyproject.toml`).
- Optional: PostgreSQL 16; Docker Desktop for compose.
- Excel Desktop only if using the **live** template (not required to **generate** client xlsx).

Install:

```text
python -m pip install -e ".[dev]"
```

Copy env template:

```text
copy .env.example .env
```

Fill secrets locally only.

**Local/demo:** `.env` may be used. `dev_token` is allowed only in
`development` and `test`. Demo seed is an explicit local command.

**Production-grade (`staging` / `production`):** secrets come from the
process or container environment. A local `.env` is not read. Require
`DFIP_AUTH_MODE=jwt`, `DFIP_AUTH_SECRET` of at least 32 characters (not a
known-bad/example value), `DATABASE_URL`, HTTPS `DFIP_WEB_ORIGIN` and
`DFIP_API_BASE_URL`. Remote `DATABASE_URL` hosts must use
`sslmode=require|verify-ca|verify-full`. `sslmode=require` encrypts
transport but does not authenticate the server the way `verify-full` does.

**JWT rotation:** replace `DFIP_AUTH_SECRET` and restart. Existing HS256
tokens become invalid; users must sign in again. Dual-key rotation is not
implemented.

**Database rotation:** replace `DATABASE_URL` and restart.

**First publisher (production-grade):** denied unless `DFIP_BOOTSTRAP_TOKEN`
is set and the caller sends header `X-DFIP-Bootstrap-Token`. Never put the
token in a query string. After a publisher/admin exists, setup returns 409.
Remove the bootstrap token from the environment after setup.
`GET /auth/setup-status` does not advertise the production window.

---

## Windows demo scripts (VERIFIED files)

| Script | Behavior |
|---|---|
| `START_DFIP_DEMO.bat` | Local-only migrate + existing `local_demo_seed`, then API + SPA. Refuses production/hosted DSNs. Does not print passwords. |
| `STOP_DFIP_DEMO.bat` | Stops demo processes (inspect before use). |
| `CHECK_DFIP_DEMO.bat` | Non-secret presence checks for Python, packages, health. |
| `scripts/dfip_demo_port.ps1` | Port 8000/3000 helpers. |

These are **local convenience**, not Docker production.

## Start processes

API (`packages/api/dfip_api/__main__.py`):

```text
python -m dfip_api
```

Default bind `127.0.0.1:8000`.

Web:

```text
python -m dfip_web
```

Default `127.0.0.1:3000`. Browser uses `/config.json` for API base.

Empty `DATABASE_URL` ⇒ **in-memory** stores (data lost on restart; SPA login
directory is empty). Hosted/local Postgres: set `DATABASE_URL` (session pooler
5432, not transaction 6543 — `.env.example`). Local compose `dfip_db` is host
**5433**.

Migrations (Postgres):

```text
python -m dfip_db
```

Local demo identities (disposable loopback/compose only; **not** a migration):

```text
python -m dfip_api.local_demo_seed --confirm-local-only
```

Requires `DFIP_LOCAL_DEMO_SEED=1` and publisher/client password env vars.
Optional `--company-2` upserts a second local tenant (`company-2`) with
`demo-publisher-2` / `demo-client-2` and grants `demo-publisher` a Company 2
membership. SPA publishers with two inspector memberships select a company
(`POST /api/v1/auth/select-client`) before upload/publish. Publishers may list
authorized companies (`GET /api/v1/clients`) and rename display names
(`POST /api/v1/clients/{client_id}/rename`) without changing `client_id`.
They may also create a tenant (`POST /api/v1/clients`) which grants membership
to the caller only. Stored catalog version IDs must belong to the run's
`client_id`; packaged fallback must not masquerade as a different tenant's
catalog version. Packaged templates/rate cards remain shared content; non-default
tenants persist NULL `rate_card_rule_id`. The product publisher is created by
one-time operator setup (`POST /api/v1/auth/setup-publisher`). Production-grade
setup requires header `X-DFIP-Bootstrap-Token`. Local demo
seed is a test fixture only. Publishers provision client-portal
logins with operator-chosen username/password/confirm
(`POST /api/v1/clients/{client_id}/users`); hash only; minimum 12 characters. Clients stay
company-scoped and do not see the selector. Refuses hosted URLs and
`DFIP_ENV=production`.
See `LOCAL_TESTING_RUNBOOK.md`.

(`packages/db/dfip_db/__main__.py` / `migrate.py`). Ledger table `dfip_schema_migration`. **Do not** point `DFIP_TEST_DATABASE_URL` at live data (`pytest -m postgres` drops `public`).

Docker:

```text
docker compose build
docker compose run --rm app
docker compose --profile v2-db up -d dfip_db
```

DB published at host **5433**. Compose `app` command is pytest, not a long-running API.

CI: `.github/workflows/ci.yml` — ruff + pytest; postgres job separate.

Tests:

```text
python -m pytest
python -m ruff check packages tests
python -m ruff format --check packages tests
```

---

## Actual operator workflow (HTTP path)

This is the implemented path (`HTTP_WORKFLOW.md`, SPA `apps/web/static/js/app.js`). It is **not** “run a worker then a CLI publish”.

1. **Catalogs (if labels/logic changed)**  
   Admin `/admin/logic` `/admin/labels` → `POST /catalogs/{kind}` → activate. Packaged JSON still used if no upload. Does not publish facts.

2. **Prepare source**  
   Web Engage `.xlsx` with sheet/header contract (`Web-Engage Raw`, K:BO). Size: multipart ≤ `DFIP_UPLOAD_MAX_BYTES` (default 64 MiB); uncompressed ZIP ≤ **512 MiB**.

3. **Authenticate**  
   SPA `/` credential view: password `POST /auth/login`. Role must be `admin`
   or `publisher` to upload. A publisher with two inspector memberships gets an
   unbound JWT and must `POST /auth/select-client` (SPA company picker) before
   upload/process/QA/publish. JWT `client_id` then wins; request `client_id`
   cannot widen it. Clients never see the picker.

4. **Upload**  
   `/admin/upload` → `POST /api/v1/uploads`. **Does not publish.** Poll batch + processing-run (SPA uses ~2s poll).

5. **Validate processing**  
   Run `status=succeeded`. Inspect rejected count, staged vs transformed. `/admin/review` QA findings. Verdict `warn` is **publishable**; `fail` is not.

6. **Inspect working set (optional)**  
   `/admin/facts` → `GET /facts` (not what clients see).

7. **Publish**  
   `/admin/publications` → `POST /publications` with `processing_run_id`, `client_id`, optional period, `fact_scope` (usually `processing_run`). Replaces `publication_current`.

8. **Confirm current**  
   `GET /publications/current` — publication_id and timestamps. SPA publications view.

9. **Validate API slice**  
   `GET /publications/current/facts?limit=200`. Check `pagination.total`.

10. **Generate client report**  
    `GET /publications/current/client-report.xlsx` (SPA **Download Company Workbook**).
    Filename `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx`. **Static recovery.**
    Same-file refresh uses `GET /publications/current/refreshable-client-report.xlsx`
    (**Download Refreshable Workbook**). The download stamps the session JWT into
    Settings BearerToken so Refresh All can call history/facts. Token expiry
    requires a re-download.
    No current publication returns 404.

11. **Open Excel (client file)**  
    Confirm PublishedFacts last row = 1 + total; headers = `FACT_HEADERS`; Settings blank; **do not Refresh All**.

12. **Spot-check pivots**  
    Overall Daywise expand Month; totals vs known vintage (P8/P10 tests when evidence files present).

13. **Deliver**  
    Send the downloaded xlsx. Do not send the git template with a live token.

Optional: `facts.csv` / `facts.xlsx` for raw published slice without pivots.

---

## New month (critical)

| Question | Answer (VERIFIED unless noted) |
|---|---|
| Does the client need a new Excel download? | **No** if they have the refreshable workbook and can paste a current JWT. **Yes** for static recovery when the file is lost. |
| Can the existing client workbook refresh to the new month? | **Refreshable artifact:** Refresh All loads `publication_current` for that JWT's company (processing-run snapshot). **Static artifact:** query tables stripped; re-download. Settings ClientId cannot switch companies. |
| Which workbook is for clients? | Refreshable for same-file updates. Static for recovery. Not the tracked live template. |
| What happens when current publication changes? | Pointer row updates. JSON/CSV/XLSX current endpoints and **live** M query follow the new id. **Old static files do not change.** |
| How is old data replaced? | New snapshot for the new publication. Prior publication rows remain in `GET /publications` history. Historical `/{id}/client-report.xlsx` still binds that snapshot if `snapshot_status=complete`. |
| How is current selected? | Last successful `POST /publications` for that `client_id`. |
| PivotTables on static file | Bound to **that file’s** cells. Unchanged until user downloads again. |
| Filters / slicers | Local to the file; new download uses template defaults (Group7/5, Service FL1, etc.). |
| Cached data | Static cells + rebound cache. Live template cache is the dangerous path. |

**INFERRED:** There is no “append October under August” unless the published fact set actually contains both months (e.g. `client_current` scope or a run that restated a multi-month working set). Inspect `snapshot_row_count` and day min/max.

---

## Auth operations

- JWT TTL default **3600s** (production). Local demo may set `DFIP_AUTH_TOKEN_TTL_SECONDS=43200` (12 hours). Tokens still expire. Excel live query fails with 401 when expired — mint a new token (`/auth/login` or `/auth/refresh`). **Never** put tokens in git.
- Logout increments `app_user.token_version`; old `ver` claims fail.
- `dev_token` refused in production.

---

## Storage of originals

Production-grade requires `DFIP_STORAGE_ENDPOINT` as a private local directory
(`FilesystemSourceObjectStore`). Empty endpoint is in-memory and is refused in
staging/production. HTTP(S) object-storage vendors are not configured.
V1 production uses a local directory on a persistent volume
(`documentation/V1_DEPLOYMENT.md`).
Never put storage credentials in Excel.

## Backup and restore

P13C operator CLI (not imported by API startup; not a client UI):

```text
python -m dfip_api.backup backup --output DIR
python -m dfip_api.backup verify --backup DIR
python -m dfip_api.backup restore --backup DIR --target-database-url URL --target-archive-root DIR --confirm-disposable
python -m dfip_api.backup restore --backup DIR --target-database-url URL --target-archive-root DIR --confirm-production-local
```

Uses `DATABASE_URL` and `DFIP_STORAGE_ENDPOINT` (or `--database-url` /
`--archive-root`). `pg_dump -Fc` must run as a superuser or BYPASSRLS-capable
account, never `dfip_api`. Do not grant BYPASSRLS to `dfip_api`. Passwords stay
in the process environment, not in scripts. Do not put `.env` in the backup.

**Coordinated order (preferred V1):** stop the API → `pg_dump -Fc` → copy the
complete `DFIP_STORAGE_ENDPOINT` tree → write `manifest.json` → verify →
restart the API. Host example timer: `deploy/systemd/dfip-backup.timer`.
P13F backup identify-eligible never deletes backup sets.

**Dump-then-copy without stopping the API:** archive `put` happens before
`source_file.storage_uri` is stored, so a committed dump row cannot point at a
file that was never written. Concurrent uploads after the dump can still
produce extra archive files that are not in that dump (orphans, not a restore
blocker). Preferred: stop the API first.

Artifacts (no company display names; no secrets in filenames):

```text
backup-root/
    manifest.json
    postgres/dfip.dump
    source-archive/{bucket}/{client_id}/{source_file_id}/{sha256}.xlsx
```

Manifest includes timestamp, git revision when available, migration ledger,
dump filename + SHA-256, archive inventory (relative path, size, SHA-256),
source_file mapping results, tool versions. It must not contain `DATABASE_URL`,
passwords, `DFIP_AUTH_SECRET`, bootstrap/dev tokens, or bearer tokens.
`app_user.password_hash` remains in the database dump as durable application
data.

`verify` is read-only: it does not delete files, mutate publications, or
change `publication_current`. It reports missing files, SHA/size mismatches,
malformed `storage_uri`, dump checksum failures, and orphan count (orphans are
not deleted). Optional dump inspection uses `pg_restore --list`.

**Restore order:**

1. Provision an empty PostgreSQL cluster/database (never `dfip`, hosted, or August).
2. Ensure cluster-level roles exist: `dfip_migrator`, `dfip_api`, `dfip_worker`.
   `pg_dump` of a database does not recreate arbitrary cluster roles.
3. Restore the logical dump with privileged credentials (`pg_restore`).
4. Restore the source archive tree with the same relative paths.
5. Verify the migration ledger against the repository revision. Do **not**
   re-apply `python -m dfip_db` on a complete schema-containing dump.
6. Verify every required `source_file` ↔ archive mapping.
7. Set P13A production environment secrets/configuration separately.
8. Start the API only after both durable stores exist.
9. Let the P13B startup sweep mark leftover `pending`/`running` runs failed.
10. Run post-restore verification (`python -m dfip_api.backup verify`).
11. Verify current publications and history (`publication` /
    `publication_fact` / `publication_current`). Do not rebuild history from
    working facts.
12. Verify tenant isolation.
13. Verify workbook regeneration from restored snapshots.
14. Verify processing/retry if a recoverable run exists.

Restore is destructive to the **target** database. `--confirm-disposable`
refuses `dfip` / `postgres` / templates / hosted URLs. Colocated production
database name `dfip` requires `--confirm-production-local` (still refuses
hosted URLs). Acceptance uses a newly created disposable database only.

Backup artifacts are confidential client data including password hashes;
restrict filesystem access; do not serve them over HTTP. Encryption-at-rest
is a host/filesystem concern. Retention is P13F. Scheduling example is P13G
(`deploy/systemd/dfip-backup.timer`). Production procedure:
`documentation/V1_DEPLOYMENT.md`.

### Monitoring / health (P13D)

Public `GET /health` is **liveness** (process alive). It does not open PostgreSQL,
scan source storage, or run backup verification.

Authenticated `GET /api/v1/ops/ready` (admin/publisher) is **readiness**:
- database: cheap `SELECT 1` → `ok` / `unavailable`
- storage: configured filesystem root exists and is a directory → `ok` /
  `unavailable` / `not_configured`
- worker: process-local `idle` / `busy` (ThreadPoolExecutor; no external queue)
- optional `DFIP_BACKUP_LAST_DIR`: manifest timestamp/age only

HTTP 200 `ready` or 503 `not_ready`. Database or storage unavailability makes
the API not-ready. Worker busy and failed/abandoned runs do not. Clients
receive 403. Paths, DSNs, and tenant inventories are omitted.
Readiness authenticates the bearer locally (JWT/dev_token role) so a database
outage still returns this structured body; token-version directory lookup
stays on data routes.

Website `GET /health` is the web process only (`application: dfip-web`).

Backup health remains `python -m dfip_api.backup verify`. Do not call it from
HTTP health/readiness. Host scheduling is the example systemd timer.

RPO: last successful coordinated backup. RTO: time to provision/restore
database + source archive + configuration + verification; depends on dataset
size and infrastructure. No numeric production SLA.

---

## Company lifecycle and purge (P13F)

Stored states: `active` and `inactive`. Deactivate stamps `deactivated_at` and
`purge_eligible_after` from `DFIP_COMPANY_PURGE_MIN_AGE_DAYS` (unset = not
clock-eligible; `0` = immediately eligible). Reactivate clears both timestamps.
Deactivate does not delete data and does not cancel in-flight processing.
`POST /auth/refresh` is refused while the JWT is bound to an inactive company.
The SPA keeps the existing token via `GET /session` so Companies and
Publications history remain usable for publisher historical recovery. Live
routes (upload, process, publish, current workbook) still require an active
company. `select-client` cannot bind an inactive company.

Publisher HTTP:

```text
POST /api/v1/clients/{client_id}/deactivate
POST /api/v1/clients/{client_id}/reactivate
```

Permanent purge is operator CLI only (privileged DSN, same safety pattern as
backup). There is no SPA purge control and no HTTP DELETE company route.

```text
python -m dfip_api.purge dry-run --code CODE --confirm-disposable
python -m dfip_api.purge purge --code CODE --confirm-purge --confirm-disposable
python -m dfip_api.purge dry-run --code CODE --confirm-production-local
python -m dfip_api.purge purge --code CODE --confirm-purge --confirm-production-local
python -m dfip_api.purge stale-uploads --max-age-hours 24
python -m dfip_api.backup identify-eligible --parent DIR --keep-count N
```

Dry-run mutates nothing. Actual purge requires inactive + eligibility +
`--confirm-purge` + (`--confirm-disposable` or `--confirm-production-local`) +
unique `client.code`. Company name is not a confirmation token. The
packaged/default owner cannot be purged. Hosted URLs are refused.
`--confirm-disposable` also refuses database name `dfip`.
`--confirm-production-local` allows only colocated local `dfip`.

SQL deletion is one transaction in FK-safe order. The tenant archive tree
`{DFIP_STORAGE_ENDPOINT}/{bucket}/{client_id}/` is removed after commit.
A filesystem failure after SQL commit is incomplete cleanup, not success.
Company B is untouched. Multi-company users and platform admins are retained.
Backup sets are not rewritten. A restore from a pre-purge backup can restore
the company. This is not cryptographic erasure.

Host log rotation is operator journald policy. Backup scheduling example:
`deploy/systemd/dfip-backup.timer`. See `documentation/V1_DEPLOYMENT.md`.

---

## Worker

`DFIP_WORKER_CONCURRENCY` exists in settings. **No worker app.** HTTP ingest uses
`ThreadPoolExecutor(max_workers=1)`. The in-process queue is unbounded. V1
runs exactly one API process. Client Report generation uses a process-local
semaphore(1) and returns 429 when busy.

On API start with `DATABASE_URL`, leftover `pending`/`running` processing runs
are marked failed so the publisher can retry. Startup logs
`abandoned-run-sweep marked=N`. Authenticated readiness reports process-local
worker `idle`/`busy`; durable status remains `processing_run`.

---

## Stop

Local demo: kill the two Python processes (`STOP_DFIP_DEMO.bat`). Production:
`systemctl stop dfip-api dfip-web` (`documentation/V1_DEPLOYMENT.md`).

---

## Logging while operating

See [V1_TROUBLESHOOTING.md](V1_TROUBLESHOOTING.md). API uses uvicorn logging; `DFIP_LOG_LEVEL`. Public `/health` does not prove Postgres. Use authenticated `/api/v1/ops/ready`.
