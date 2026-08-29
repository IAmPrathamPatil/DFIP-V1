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
  `GET /api/v1/publications/current/facts` (`limit=200`). Does not call the
  unpublished working-set `GET /api/v1/facts`.

Three distinct states:

| State | Meaning |
|---|---|
| A. Python structure scaffold | Settings + Facts tables. What `build_client_report()` writes. Not DataMashup. |
| B. Native Excel DataMashup | Excel Desktop connections / query tables / DataMashup package. **This is the tracked** `excel/Client_Report.xlsx`. No fake `queryMashup` zip parts. |
| C. Refresh All verified | Excel Desktop actually refreshed against a live published pointer. Verified on a **local** workbook (2026-08-27), not claimed for every clone of this file. |

Do not treat A as B or C. Presence of `PublishedFacts.m` does not mean Refresh
All works.

Authenticated clients download a publication-bound copy via
`GET /api/v1/publications/current/client-report.xlsx` or
`GET /api/v1/publications/{publication_id}/client-report.xlsx`. That copy
injects the publication snapshot into `PublishedFacts` and does not embed a
Bearer token. It is not a substitute for Desktop Refresh All of the tracked
template. The download row cap is `DFIP_DOWNLOAD_MAX_ROWS` (default 75,000).

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
