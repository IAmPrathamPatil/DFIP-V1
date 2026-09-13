# excel

Home of the native V2-X client workbook and the Power Query M script.

- `Client_Report.xlsx` — Desktop-native V2-X workbook: `PublishedFacts` +
  `Facts` sheets, Settings table, native connection/query artifacts, the
  DataMashup customXml package, and nine FY-2026 Daily Report **native
  PivotTables** (shared cache, slicers, page filters). Fake `xl/queryMashup/`
  and `customXml/powerQuery.xml` parts must not exist. Do not leave a
  BearerToken in Git. `build_client_report()` still writes a structure-only
  scaffold and must not be run over this file. See
  `documentation/DAILY_REPORT.md`.
- `PublishedFacts.m` — source-of-truth Power Query. Pages
  `GET /api/v1/publications/history/facts` (`limit=200`) so one refreshable
  workbook accumulates later published months for that company. Does not call
  the unpublished working-set `GET /api/v1/facts` or bind a download-time
  publication id. HeaderMap is the frozen 45-column `FACT_HEADERS` contract
  (RUN 009 does not append extras).

Three distinct states:

| State | Meaning |
|---|---|
| A. Python structure scaffold | Settings + Facts tables. What `build_client_report()` writes. Not DataMashup. |
| B. Native Excel DataMashup | Excel Desktop connections / query tables / DataMashup package. **This is the tracked** `excel/Client_Report.xlsx`. No fake `queryMashup` zip parts. |
| C. Refresh All verified | Excel Desktop actually refreshed against a live published pointer. Verified on a **local** workbook (2026-08-27), not claimed for every clone of this file. |

Do not treat A as B or C. Presence of `PublishedFacts.m` does not mean Refresh
All works.

Authenticated clients download:

- Static recovery: `GET /api/v1/publications/current/client-report.xlsx`
- Refreshable: `GET /api/v1/publications/current/refreshable-client-report.xlsx`

Generated static downloads **hide** `PublishedFacts` and `Facts`. Refreshable
downloads hide both technical sheets the same way. Website/API refreshable
downloads write the caller's short-lived access JWT into Settings BearerToken
so Excel Refresh All can call history/facts. That is the session token, not a
permanent secret. Settings ClientId stays empty; JWT `client_id` is
authoritative. Unhide `Facts` or `PublishedFacts` via Excel only if the
operator must inspect Settings or the query sheet. The tracked template in
this folder stays visible for authoring.

Filenames `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx` and
`…_Client_Report_Refreshable.xlsm`. The static copy injects the publication
snapshot and strips query tables. The refreshable copy keeps the native
PublishedFacts query, authors 45 queryTableFields, sets ApiBaseUrl, and
stamps the download session JWT into BearerToken. JWT `client_id` scopes the
result. First-time Excel on each PC: when the Access Web content dialog
appears for the API base URL, choose **Anonymous** (not Windows, Basic, or
Organizational). Auth is the M `Authorization` header, not Excel's
credential store. That choice persists for that URL on that Excel profile;
later Refresh All should not prompt again while the JWT is valid. Token
expiry requires a re-download. Never embed a password or a permanent token.
While the stamped JWT is still valid, Refresh All calls `POST /auth/refresh`
and reuses the first history page instead of fetching page 0 twice.

Do not run `build_client_report()` over a Desktop-built native workbook.
Python must not rewrite the DataMashup package at runtime; Excel skips that
query. `PublishedFacts.m` on disk (canonical 45-column order, 30s timeout) is
pasted in Desktop Excel when rebuilding the tracked template.
Disk `PublishedFacts.m` may include still-valid JWT renewal (`POST /auth/refresh`)
and first-page reuse (`List.Skip`). Apply those only by pasting the query in
Desktop Excel. Do not COM-save over the tracked workbook and do not Python-replace
Section1.m at a different length; that drops `cacheId` or makes Excel skip Refresh All.
Same-length token edits (current→history, Chrono Month typing) are the only Python
template patches that keep the native PivotTable package complete.

This is not where production Web Engage source workbooks live. Those files stay
local and gitignored. Do not copy client data into this folder. Do not leave a
Bearer token in the tracked xlsx.

## First-time setup (Excel Desktop)

1. Copy `excel/Client_Report.xlsx` to the client computer. It already has the
   native `PublishedFacts` query/connection. Do not run `build_client_report()`
   over it.
2. Enter the API base URL and a Bearer token in the Settings table. Leave
   ClientId empty when the JWT already has `client_id`, or when the local
   development token is bound with `DFIP_DEV_AUTH_CLIENT_ID`.
3. If `PublishedFacts` is already in the workbook, Refresh All. Otherwise add
   query `PublishedFacts` from unchanged `PublishedFacts.m`. Ignore Privacy
   Levels (`Excel.CurrentWorkbook()` + `Web.Contents`).
4. Against PostgreSQL, `dev_token` requires `DFIP_DEV_AUTH_CLIENT_ID`. Excel
   ClientId must match that bound client if set. That is not RLS. `dev_token`
   is development/test only. HS256 JWT is not a production identity provider.

See `documentation/DESKTOP_ACCEPTANCE.md`.

Rebuild the tracked **structure** after editing Settings/Facts headers (this
does **not** embed the M script and does **not** create DataMashup):

```powershell
python -c "from dfip_web.client_workbook import build_client_report; build_client_report()"
```

Do not run that builder over a Desktop-built native workbook; it would strip
the mashup and leave structure only.
