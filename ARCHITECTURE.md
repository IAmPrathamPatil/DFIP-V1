# DFIP Architecture

A readable system overview for engineers and reviewers. The exhaustive
engineering record — every component, table, policy, and route — is in
[`documentation/V1_ARCHITECTURE.md`](documentation/V1_ARCHITECTURE.md) and the
documents indexed by
[`documentation/V1_MASTER_DOCUMENTATION.md`](documentation/V1_MASTER_DOCUMENTATION.md).

---

## 1. System overview

DFIP turns an operator-supplied spreadsheet of campaign performance data into a
company-scoped, published, auditable dataset that clients read through an
authenticated API and a browser workspace, and that is also delivered as Excel
workbooks.

It is a two-process, single-host system:

- **`dfip_api`** — FastAPI application under `/api/v1`. Owns authentication,
  authorization, tenant scoping, the data pipeline, publication, analytics, and
  workbook generation.
- **`dfip_web`** — a static origin server. Serves the single-page application and
  a public configuration document. It does not proxy the API, open the database,
  or hold secrets.

PostgreSQL 16 runs on the same host, bound to loopback. A TLS-terminating reverse
proxy is the only public entry point. Both services bind loopback and are managed
by systemd.

There is no message broker, cache server, or separate worker service. Background
work runs in thread pools inside the API process.

## 2. Major components

| Component | Location | Responsibility |
|---|---|---|
| Ingestion | `packages/core/dfip_core/ingest` | Read the source workbook against the header contract; stage rows or record rejections |
| Transformation | `packages/core/dfip_core/transform` | Deterministic mapping of staged rows onto the fact grain |
| Reconciliation | `packages/core/dfip_core/transform` | Independently recompute derived values and compare |
| Configuration | `packages/config/dfip_config` | Settings plus versioned labels, template groups, rate cards, membership groups |
| Persistence | `packages/db/dfip_db` | PostgreSQL adapters, RLS context binding, migrations |
| API | `packages/api/dfip_api` | Routes, services, authentication, authorization, uploads, publications, analytics, workbooks, backup and purge commands |
| Analytics | `packages/analytics/dfip_analytics` | KPI, QA, and grain calculations over published data |
| Reporting | `packages/web/dfip_web` | Workbook generation and OOXML mutation; static origin server |
| Frontend | `apps/web/static` | Single-page application: router, views, API client, styling |

## 3. Request and data flow

### 3.1 Upload to publication

```
   operator .xlsx
        |
        |  POST /api/v1/uploads          bearer required; admin/publisher
        v
   size / count / archive validation     -> 413 or 422 on violation
        |
        v
   source_file  (SHA-256 of file bytes)
        |
        v
   background transform, one serialized worker
        |
        v
   header contract check                 -> exact window; unapproved extra column fails closed
        |
        +--> accepted rows  --> stg_source_row
        +--> malformed rows --> stg_rejected_row
        |
        v
   deterministic transform, versioned configuration bound per run and data day
        |
        v
   fact_campaign_day                     working set; prior grain copied to history on restatement
        |
        v
   QA evaluation                          -> qa_finding; verdict pass | warn | fail | unavailable
        |
        |  POST /api/v1/publications     explicit; admin/publisher; publishable verdict required
        v
   publication row + immutable fact snapshot + publication_current pointer
```

The upload is idempotent on file identity: SHA-256 of the bytes identifies the
source file, and a replay returns the existing result rather than creating a
second import. Work that is staged or received but not finished is recovered on
the same batch instead of being reported as complete.

### 3.2 Reading published data

`publication_current` holds exactly one current publication pointer per company.
It is what `/current/` means throughout the API. It is not a date, not the
working set, and not a draft state.

Reads resolve the caller's company from the principal, check the company is
active, resolve the current pointer, and then serve the immutable snapshot.
Client and reader principals can reach this path; they cannot reach the working
set.

## 4. Analytics flow

Analytics reads published data only, never the working set.

```
   published facts / reporting views
        |
        v
   KPI, QA, grain calculations           aggregate first, then apply the operation
        |
        v
   analytics service                     company-scoped, period + comparison
        |
        v
   /api/v1/analytics/*                   overview, trends, explorer, drilldown,
                                         insights, anomalies, ask, saved, CSV
        |
        v
   Analytics Studio  /client/studio      server-computed, rendered client-side
```

Ratio metrics are never averaged across days. An undefined ratio remains null
rather than becoming zero. Native rate columns from the source platform are
treated as evidence only and are rejected as calculation inputs.

Contextual Ask maps a question onto an allowlisted analytical operation and
explains the structured result. The language model does not execute SQL, define
KPIs, or widen company scope.

## 5. Authentication and authorization

```
   bearer credential
        |
        v
   credential type                       session JWT | Excel access JWT | bootstrap/dev token
        |
        v
   principal                             user, role, company scope
        |
        v
   role check + company resolution        publisher/admin: working set + publish
                                         client/reader:  own published data only
        |
        +--> application SQL predicates   client_id filters applied independently
        |
        +--> transaction-local RLS context set for tenant tables
        |
        v
   company data
```

Excel access is separate from session authentication. A workbook refresh uses a
short-lived, client-scoped, revocable grant, so a workbook token can be
re-minted or invalidated without disturbing the operator's session.

Publisher sessions are single-active: establishing a new one invalidates the
previous.

Authorization is applied before any cache consultation, so a cache hit never
bypasses an authorization decision.

## 6. PostgreSQL and Supabase

The schema is defined by 30 versioned SQL migrations under
`supabase/migrations/`. Migrations are additive and idempotent; there are no down
migrations.

Three cluster roles exist: a migrator role, an API role, and a worker role that
is `NOLOGIN`. The API login must not be granted `BYPASSRLS`.

Row-level security is enabled and forced on tenant tables, with policies that
evaluate helpers reading transaction-local tenant context. This is defense in
depth behind the application checks, not a replacement for them. Tables that
intentionally have no RLS, and the conditions under which policies do not apply,
are enumerated in [`documentation/V1_RLS.md`](documentation/V1_RLS.md).

The migrations also define published-only reporting views consumed by the
Power BI source package in `powerbi/`.

**Persistence is optional in development.** With no database configured, the API
uses in-memory stores and the application runs entirely in process memory. This is
how the default test suite runs. Production requires the database.

## 7. Frontend and backend separation

The browser is a rendering surface, not a compute surface. The SPA is plain ES
modules with no build step and no framework, served as static files.

Analytics values are computed on the server and rendered on the client. The
browser holds a bearer token in client-side storage and sends it as an
authorization header; it never receives a database credential, and the web origin
process holds no secrets.

The frontend receives only the public API location and the public role names
through a configuration document. Route guards in the client improve navigation
but are not a security boundary — every route is enforced server-side.

## 8. Workbook and report generation

Workbooks are generated from committed templates. Generation and mutation happen
at the OOXML level: openpyxl for cell and style work, plus direct manipulation
of workbook parts for PivotCache bindings, pivot data fields, and conversion
formulas.

Three artifacts are produced from the same template:

| Artifact | Data | Refresh model |
|---|---|---|
| Refreshable | Current publication | Power Query against the API; needs Excel refresh |
| Static company workbook | Current publication snapshot | Server-rendered cells; no refresh needed |
| Consolidated company workbook | Entire published history, newest-wins | Server-rendered cells; no refresh needed |

The consolidated workbook is expensive relative to a request, so the rendered
artifact is cached per company under a fingerprint of the eligible published
history and rebuilt only when that history changes. Cache warming runs on a
dedicated single-worker pool, separate from the serialized upload worker, so a
long build cannot delay ingest. A download that arrives before the artifact is
ready receives `202` with `Retry-After` rather than holding a connection open.

See [`documentation/CONSOLIDATED_WORKBOOK.md`](documentation/CONSOLIDATED_WORKBOOK.md).

## 9. Background processing and concurrency

Two thread pools exist inside the API process, and they are deliberately
separated.

| Pool | Concurrency | Work |
|---|---|---|
| Upload pool | 1 | Transform work. Serialized on purpose so a run cannot race itself. |
| Warm pool | 1 | Consolidated workbook cache warming. Separate so a multi-second render cannot occupy the upload worker. |

Both are process-local. There is no cross-process coordination, which is
consistent with the locked single-instance topology: exactly one API process
runs, so in-process state is sufficient.

Detached pool threads inherit no request context, so a warming thread binds its
own tenant RLS context before reading. Warming is idempotent per company and
fail-open: a failed warm is logged and never fails the publish that triggered it.

## 10. Deployment model

Single Linux host on Oracle Cloud. API and web origin as systemd services,
PostgreSQL colocated on loopback, reverse proxy terminating TLS, persistent
volume for the source archive, systemd timer for backup.

Deployment is recreate-in-place and not zero-downtime: the services are stopped,
the working tree is moved to the target revision, the application environment is
refreshed, and the services are started. A pre-deployment evidence directory
records the outgoing revision, the working-tree state, and checksums of the
artifacts about to be replaced.

`deploy/scripts/deploy.sh` documents the intended release-based flow. The live
host was provisioned with an in-place working tree, so operators should confirm
the actual layout before following any script in `deploy/scripts/`.

Deployment topology and procedure: [`documentation/V1_DEPLOYMENT.md`](documentation/V1_DEPLOYMENT.md).
Operator commands: [`documentation/V1_OPERATIONS_RUNBOOK.md`](documentation/V1_OPERATIONS_RUNBOOK.md).

## 11. Design constraints worth knowing

- **One API process.** Several design choices — in-process pools, process-local
  rate limiting, in-memory mode — assume a single instance. Horizontal scaling
  would require externalizing those.
- **No broker.** Queue depth is bounded by process memory, not by a durable queue.
- **RLS is conditional.** A login that can bypass RLS, or that owns the tables,
  is not constrained by the policies. Application checks remain the primary
  mechanism.
- **Excel refresh is the fragile path.** A Power Query refresh rebuilds the
  shared pivot cache and drops calculated fields; the renderer compensates, but
  this is inherent to refreshing pivots against a live query.