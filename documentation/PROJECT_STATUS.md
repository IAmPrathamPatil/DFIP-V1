# Project Status

Current state of DFIP-V1, written for someone evaluating the repository.

**Last reviewed:** 2026-10-03, against `master` at the merge of pull request #4.

The detailed chronological record of implementation work is preserved in
[`STATUS.md`](STATUS.md) and the phase-by-phase engineering record in
[`V1_MASTER_DOCUMENTATION.md`](V1_MASTER_DOCUMENTATION.md). This page is the
summary; those remain the audit trail.

---

## 1. Deployment state

**Deployed to production.** The application runs on Oracle Cloud as two systemd
services — the API and the web origin — with PostgreSQL 16 colocated on loopback
behind a TLS-terminating reverse proxy.

Verified on the running host after the most recent deployment:

| Check | Result |
|---|---|
| Deployed revision matches the target merge | Yes |
| `dfip-api` service | active, enabled, no restarts since start |
| `dfip-web` service | active, enabled, no restarts since start |
| `GET /health` | `200`, application `dfip-api`, environment `production` |
| Startup log | clean, no import or startup errors |
| Error and traceback log scan since restart | none |
| Report and workbook routes, unauthenticated | `401` — registered and protected |
| Untracked operational artifacts and stashes | preserved |

**Authenticated end-to-end verification of the consolidated workbook against live
production data has not been performed.** It requires an authorized production
account, which was not available for the deployment verification. The feature is
covered by the test suite, but production behaviour of that specific download has
not been exercised with real credentials. This is the most significant open item.

## 2. Implemented areas

Each of the following exists in the code and is exercised by tests.

**Data pipeline**
- Authenticated `.xlsx` upload with SHA-256 file identity and replay semantics.
- Closed schema contract: an unapproved trailing source column fails ingest with a
  publisher-facing message rather than being ignored.
- Staged rows and a rejection table for malformed input.
- Deterministic transformation onto a `campaign × variation × day` fact grain, with
  versioned configuration bound per processing run and per data day.
- Cost derivation from delivered volume and a versioned rate card.
- An independent reconciliation module that recomputes derived values rather than
  trusting the pipeline.
- Explicit run state machine with recovery of staged or received work.

**Persistence**
- 30 versioned additive SQL migrations; no down migrations.
- PostgreSQL 16 with connection pooling; row-level security enabled and forced on
  tenant tables, driven by transaction-local tenant context.
- Three cluster roles, with the API login explicitly not granted `BYPASSRLS`.
- Published-only reporting views.
- In-memory store implementations, which are the default in development and tests.

**Publication**
- Explicit, authorized publication gated on a publishable QA verdict.
- Immutable fact snapshot plus a single current pointer per company.
- Cumulative publication history with newest-wins union per grain, so republished
  months replace rather than duplicate.
- Historical catalog and historical report downloads.

**Analytics**
- Published-only KPI, QA, and grain calculation.
- Analytics API: overview, trends, drilldown, explorer, insights, anomalies,
  contextual Ask, saved analyses, CSV export.
- Analytics Studio at `/client/studio`.
- Deterministic insights and anomalies; Ask bounded to allowlisted operations.

**Web application**
- Single-page application with no build step: routing, role-aware navigation, upload
  centre, publications, fact views, company management, downloads, analytics.
- Publisher session handling with single-active-session enforcement.
- Admin/Publisher and Client surfaces with separate route guards.

**Reporting**
- Refreshable company workbook bound to the API.
- Static company workbook for the current publication.
- Consolidated company workbook for the entire published history, with a per-company
  fingerprint cache, background warming on a dedicated single-worker pool, and a
  `202` plus `Retry-After` contract instead of a long blocking render.
- Click-through and overall conversion sections populated on all three artifacts.
- Native Excel PivotTables, slicers, page filters, and calculated fields, generated
  and mutated at the OOXML level.

**Security and operations**
- JWT authentication; role-based authorization with unknown roles rejected.
- Company scoping applied in the data access layer independently of RLS.
- Short-lived, revocable, client-scoped Excel access grants.
- Login and setup rate limiting per IP and username.
- Upload count, size, and archive validation.
- Coordinated backup, verify, and restore commands with explicit confirmation gates.
- Company deactivate/reactivate over HTTP; purge as an operator-only command.
- Public liveness endpoint that does not claim database connectivity, plus an
  authenticated readiness diagnostic.
- Production disables interactive API documentation.

**Delivery**
- GitHub Actions: lint, format check, full test suite against a PostgreSQL service
  container, and the PostgreSQL-marked suite as a separate job.

## 3. Verification state

### 3.1 The repository does not have a clean baseline

This is stated plainly because it is easy to misread a badge otherwise. There are
pre-existing lint findings, format findings, test failures, and
PostgreSQL-marked failures that exist on `master` independently of recent feature
work. They were measured by comparing the base revision against the
consolidated-workbook changes in isolated worktrees with identical dependency
versions, so the comparison is attributable to code rather than environment.

| Check | Base revision | With consolidated-workbook changes | New issues |
|---|---|---|---|
| `ruff check packages tests` | 240 findings | 237 findings | 0 new (3 pre-existing fixed) |
| `ruff format --check packages tests` | 76 files | 76 files | 0 new |
| `pytest` (full suite) | 15 failed / 1394 passed / 29 skipped | 15 failed / 1446 passed / 29 skipped | 0 new |
| `pytest -m postgres` | 11 failed / 89 passed / 2 skipped | 11 failed / 89 passed / 2 skipped | 0 new |

The consolidated-workbook work added 52 passing tests and introduced no new
failure in either suite. Three format regressions that the work did introduce were
fixed in a follow-up commit.

Measurements were taken on Windows with fresh virtual environments. GitHub Actions
runs on Linux, so skip and pass counts differ slightly; the failure names and the
new-issue counts are what carry over.

### 3.2 The pre-existing failures

Fifteen test failures, of which eleven are also the PostgreSQL-marked failures.
They are dominated by two causes:

- **Database test-environment residue.** A test login role still owns objects, so
  the role cannot be dropped between runs. This is local-environment state, not a
  code defect, and it does not reproduce on a fresh CI database.
- **Two scope-guard assertions and one config assertion** that do not match current
  behaviour.

None of these are related to the consolidated workbook, ingestion, publication, or
analytics.

### 3.3 Test structure

| Layer | How it runs | Notes |
|---|---|---|
| In-memory | `python -m pytest` | Stores injected as in-memory doubles; no database required |
| PostgreSQL | `python -m pytest -m postgres` | Requires a disposable database via `DFIP_TEST_DATABASE_URL`. These tests **drop and recreate the `public` schema** — never point them at live data |
| Workbook and report | part of the default suite | Asserts generated OOXML directly |
| CI | GitHub Actions | Lint, format, full suite, PostgreSQL suite |

127 test files. 12 carry the `postgres` marker.

## 4. Known limitations

### 4.1 Repository and verification

- Pre-existing lint, format, and test failures, as measured in section 3.
- Two files carry local mixed line endings that Ruff would reformat. Git normalizes
  them, so they are clean in a fresh checkout and are not a code defect.
- No `LICENSE` file, so there is no license badge and no stated reuse grant.

### 4.2 Architecture and operations

- **Single API process by design.** In-process thread pools, process-local rate
  limiting, and in-memory mode all assume one instance. Horizontal scaling would
  require externalizing those.
- **No durable queue.** Background depth is bounded by process memory.
- **Excel refresh is the fragile path.** A Power Query refresh rebuilds the shared
  pivot cache and drops calculated fields. The renderer compensates, but this is
  inherent to refreshing pivots against a live query.
- **The documented release layout does not match the live host.** `deploy/scripts/`
  documents a release-directory plus symlink flow. The host was provisioned with an
  in-place working tree. Operators should confirm the actual layout before running
  any script in that directory.
- **RLS is conditional.** A login that can bypass RLS, or that owns the tables, is
  not constrained by the policies. Several tables intentionally have no RLS. The
  application authorization layer is the primary mechanism.

### 4.3 Consolidated workbook

- **Workbook cache location defaults to ephemeral temporary storage** unless
  `DFIP_CONSOLIDATED_CACHE_DIR` is set. System temp directories are subject to
  periodic cleanup. This is self-healing — the next download rebuilds — but it
  costs a rebuild, and the setting is worth configuring for production.
- **A persistent cache-write failure surfaces as repeated `202` responses.** The
  download reports "preparing" whenever no artifact is stored for the current
  fingerprint. If the cache directory were unwritable, a caller would retry until
  its budget was exhausted instead of receiving a render or an error. The cache is
  writable in the current deployment, and warming failures are logged as
  `consolidated cache warm failed`, but the failure mode is worth knowing.

### 4.4 Product scope

- Power BI is a **source package only**. No `.pbix` is committed and the Desktop
  canvas is not verified.
- The consolidated workbook is refused, by design, for a company whose cumulative
  rows exceed the history cap, and for a company with no published history. It
  never returns a truncated or empty workbook in place of an error.
- Analytics Studio does not currently offer a free-form metric or grain picker, a
  matrix builder, drill-down, Ask, CSV export, or saved-analysis management. See
  [`ANALYTICS_STUDIO.md`](ANALYTICS_STUDIO.md).

## 5. Remaining work

Not committed to a schedule; recorded so the boundary is visible.

| Item | Note |
|---|---|
| Authenticated production smoke of the consolidated workbook | Requires an authorized production account |
| Clear the pre-existing lint, format, and test baseline | Would make the CI signal meaningful |
| Repository hygiene files | No `LICENSE`, `CONTRIBUTING`, or GitHub templates at the time of writing — `CONTRIBUTING.md` and `SECURITY.md` have since been added |
| Screenshots and visual documentation | The repository has no image assets; captures must be sanitized and free of client data |
| Set an explicit consolidated cache directory for production | Removes the temp-directory dependency |
| Reconcile the documented release layout with the live in-place layout | Documentation-only correction, or change the scripts to match |
| Two scope-guard test assertions | Fail against current behaviour and need a decision, not a fix |

## 6. Documentation state

**Strong.** The engineering record is unusually complete for a repository of this
size, and its distinguishing feature is honesty: claims are tagged `VERIFIED`,
`PARTIALLY VERIFIED`, `INFERRED`, `UNKNOWN`, `NOT IMPLEMENTED`, `LEGACY`, or
`KNOWN LIMITATION`, and [`V1_RLS.md`](V1_RLS.md) documents what isolation does
*not* exist as carefully as what it does.

Two known documentation issues remain:

- Some early sentences in [`SCHEMA.md`](SCHEMA.md) predate later migrations and
  contradict them. [`V1_MASTER_DOCUMENTATION.md`](V1_MASTER_DOCUMENTATION.md)
  flags this.
- Historical phase documents still describe the deployment target as a
  DigitalOcean Droplet and state that no host was provisioned. Those records are
  preserved as history; the current topology is documented in
  [`V1_DEPLOYMENT.md`](V1_DEPLOYMENT.md) and [`ARCHITECTURE.md`](../ARCHITECTURE.md).

New in this revision: [`ARCHITECTURE.md`](../ARCHITECTURE.md),
[`CONSOLIDATED_WORKBOOK.md`](CONSOLIDATED_WORKBOOK.md),
[`ANALYTICS_STUDIO.md`](ANALYTICS_STUDIO.md), and this page.

## 7. Related

- [`../README.md`](../README.md) — project overview
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — system architecture
- [`V1_MASTER_DOCUMENTATION.md`](V1_MASTER_DOCUMENTATION.md) — authoritative document index
- [`V1_TEST_INVENTORY.md`](V1_TEST_INVENTORY.md) — test inventory
- [`V1_DEPLOYMENT.md`](V1_DEPLOYMENT.md) — deployment topology and procedure
- [`V1_SECURITY.md`](V1_SECURITY.md) — exposure surface and residual risk
- [`V1_RLS.md`](V1_RLS.md) — RLS position and limits