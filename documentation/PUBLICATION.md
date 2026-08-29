# Publication, client portal, and client workbook

P7 added the **publication pointer**. P8 wires the **client portal** to that
same published slice. P9 adds **application-level authorization** on the
existing API boundary. P10 locked data/report parity. P11 is native Excel
PivotTable presentation of that same published slice. It does not add
PostgreSQL RLS, a KPI engine, a QA engine, a live database, or an HTTP upload
pipeline. P12/P13/P14 are not started.

P3 ingestion and P4 transformation are libraries. V2 adds authenticated
multipart ingest (`POST /api/v1/uploads`) that calls those libraries and does
not publish. `python -m dfip_api` still starts with empty in-memory stores and
does not ingest an XLSX on startup. SQL migrations exist; PostgreSQL is used
only when `DATABASE_URL` is set. See `documentation/HTTP_WORKFLOW.md`.

## Publisher flow

1. Upload a Web Engage `.xlsx` from Upload Center or with
   `POST /api/v1/uploads` (admin/publisher), or call `ingest_workbook` /
   `run_transformation` as libraries.
2. Confirm `processing_run.status = succeeded`.
3. On Publications (or Review), publish that run for the scoped client.
4. `POST /api/v1/publications` writes `publication`, writes an immutable
   `publication_fact` snapshot of the candidate slice, and replaces
   `publication_current` for that client. Earlier publication rows remain as
   history and are listed by `GET /api/v1/publications`. Omit `fact_scope` (or
   send `processing_run`) to keep the published slice run-scoped. Send
   `fact_scope=client_current` to publish all current facts for that client,
   still clipped by `period_start` / `period_end` when set. Later restatements
   do not change a complete snapshot. `GET /api/v1/publications/{id}/facts`
   reads that publication's snapshot.

   Legacy rows with `snapshot_status=none` (including August until this
   migration is applied and a new publish occurs) still join live
   `fact_campaign_day` and are **not** immutable. Do not fabricate values for
   those rows.

Upload does not publish. Only `admin` and `publisher` may publish. `reader` /
`client` receive 403.

## Authorization matrix

| Surface | Who | Meaning |
|---|---|---|
| Working set (`GET /facts`, `/facts/history`, source files, batches, staged rows, processing runs) | admin / publisher | Inspection of unpublished working state. Client / reader → 403. |
| Published set (`GET /publications/current`, `GET /publications/current/facts`, `GET /publications/current/facts.csv`, `GET /publications/current/facts.xlsx`, `GET /publications/current/client-report.xlsx`, `GET /publications/{id}/facts`, `GET /publications/{id}/client-report.xlsx`) | client / reader / admin / publisher | Current pointer, current published facts/downloads, historical publication facts, and the nine-sheet Client Report for an authorized publication. |
| Workbook ingest (`POST /uploads`) | admin / publisher | `.xlsx` → P3 ingest → P4 transform. Does not publish. Client / reader → 403. |
| Publication history (`GET /publications`) | client / reader / admin / publisher | Prior publication rows for the scoped client. JWT `client_id` wins. |
| Publication write (`POST /publications`) | admin / publisher | Client / reader → 403. |

**This is application-level authorization, not PostgreSQL RLS and not production tenant isolation.**

## Working set vs published slice

| Surface | Endpoint | Meaning |
|---|---|---|
| Admin `/admin/facts` | `GET /api/v1/facts` | P6 **working set**. Publication does not filter it. Admin/publisher only. |
| Client `/client/facts` | `GET /api/v1/publications/current/facts` | **Published slice** for `publication_current`. |
| Excel `Client_Report.xlsx` | `GET /api/v1/publications/current/facts` | Same published slice, paged at 200. The committed file is **Desktop-native V2-X** (`PublishedFacts` + `Facts` + nine Daily Report sheets, native connections/query tables). Fake `xl/queryMashup/` parts are forbidden. Refresh All was verified on a local pointer-test workbook. Do not commit a BearerToken. See `documentation/DESKTOP_ACCEPTANCE.md` and `documentation/DAILY_REPORT.md`. |
| Published download | `GET /api/v1/publications/current/facts.csv` and `.xlsx` | Same published slice as a file. **IMPLEMENTED**. |
| Client Report download | `GET /api/v1/publications/current/client-report.xlsx` and `GET /api/v1/publications/{id}/client-report.xlsx` | Nine-sheet `Client_Report.xlsx` bound to that publication's snapshot. No token is embedded. **IMPLEMENTED**. |

`publication_current` is one pointer per client. Republishing replaces the
pointer. Empty current publication returns `items: []` and `total: 0` — the
client portal does not fall back to `/facts`. Current and historical fact
reads use `publication_fact` when `snapshot_status=complete`. Excel
`PublishedFacts.m` still calls `GET /publications/current/facts` and does not
need a path change.

## Immutable snapshots

New publishes copy the candidate fact set into `publication_fact` in the same
transaction as the publication row and `publication_current` pointer. Chunk
size is 500, the same family as fact persistence. Approximate write batches:
50k → 100, 100k → 200, 250k → 500 statements in one transaction.

`fact_campaign_day_history` is working-set restatement lineage. It is not a
publication snapshot and is not used to reconstruct old publishes.

## Client workbook flow

Distinguish three states. Do not collapse them:

- **A. Python structure scaffold** — `build_client_report()` writes Settings
  (`Parameter` / `Value`: `ApiBaseUrl`, `BearerToken`, `ClientId`) and an empty
  Facts table (45 headers). It is not Office DataMashup.
- **B. Native Excel DataMashup** — tracked `excel/Client_Report.xlsx` has
  sheets `PublishedFacts` and `Facts`, the nine Daily Report sheets, plus Excel
  connections / query tables / the DataMashup customXml package. Fake
  `xl/queryMashup/` parts are absent.
- **C. Refresh All verified** — local Desktop pointer-test workbook on
  2026-08-27. See `documentation/DESKTOP_ACCEPTANCE.md`. Presence of native
  zip parts does not by itself prove Refresh All on every machine.

1. Keep `excel/Client_Report.xlsx` as the native V2-X workbook (empty BearerToken).
2. For a refreshable Desktop workbook: Settings table headers must be
   **Parameter** / **Value**; paste unchanged `excel/PublishedFacts.m`; Ignore
   Privacy Levels (CurrentWorkbook + Web.Contents).
3. Do not leave a Bearer token in the committed template.
4. Do not run `build_client_report()` over a Desktop-native file; that rebuild
   is structure-only and would strip mashup.

Power Query calls `GET /api/v1/publications/current/facts` with Bearer
authentication and pages `limit=200` until `total` is consumed. It does not
call working-set `GET /api/v1/facts`.

## Authentication notes

- JWT roles are allowlisted: `admin`, `publisher`, `reader`, `client`. Unknown
  roles are rejected; they are not mapped to `reader`.
- A JWT with `client_id` is scoped to that client on published-facts reads and
  inspector working-set reads. JWT `client_id` always wins. A mismatched
  explicit `client_id` does not expose another client's data.
- `dev_token` with `DATABASE_URL` requires `DFIP_DEV_AUTH_CLIENT_ID` and is
  bound to that client. It is never a platform-wide RLS identity. Unscoped
  in-memory tokens may still pass `client_id` explicitly. **That is not
  production tenant isolation.**
- **This is application-level authorization, not PostgreSQL RLS and not
  production tenant isolation.** HS256 JWT is an authentication hook, not a
  production identity provider. `dev_token` is development/test only.
  Production identity-provider runtime and RLS are **V2**, not additional V1
  phases.

## Excel is not the processing engine

The workbook must not contain database URLs, signing secrets, source/raw files,
or staging endpoints. Labels, cost, and QA stay on the backend.
