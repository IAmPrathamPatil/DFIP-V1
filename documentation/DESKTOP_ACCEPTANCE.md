# Desktop acceptance — observed 2026-08-26

This record is only what was physically executed. It does not invent a `.pbix`
and does not replace `excel/PublishedFacts.m`.

Current Client A publication pointer after V2-X (2026-08-27) is
`a0c13f32-910f-4e12-bb1a-a9907c038337` / run
`db7d748f-d59a-4b4e-af73-e4d927e722b0` (`p7-be947c5bc055-camp-a`, unique_clicks
**40**).

## Classification

| Surface | Classification |
|---|---|
| Live API on PostgreSQL | **IMPLEMENTED** / **MANUALLY VERIFIED** |
| HTTP upload / transform / explicit publish | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** |
| CSV / XLSX published download | **IMPLEMENTED** / **AUTOMATED** / **MANUALLY VERIFIED** |
| `excel/Client_Report.xlsx` native DataMashup | **IMPLEMENTED** in the tracked file (`PublishedFacts` + `Facts` + nine Daily Report **native PivotTables**, `xl/connections.xml`, query tables, DataMashup customXml, pivot cache / slicers). Fake `xl/queryMashup/` / `customXml/powerQuery.xml` remain **absent**. BearerToken in Git must stay empty. Refresh All is **MANUALLY VERIFIED** on a local pointer-test copy, not claimed for every checkout. P10 UNIQUE/FILTER/SUMIFS reconstruction is documented in `documentation/DAILY_REPORT.md`. P11 Excel COM opens 9 PivotTable objects and save/reopen (no Refresh All). Ribbon Analyze/Design/Fields/slicer clicks remain a local Excel UI check, not an unattended COM Refresh All loop. |
| Excel Desktop Refresh All via `PublishedFacts.m` (V2-X) | **MANUALLY VERIFIED** (complete). Pointer Unique Clicks **30 → 40**. |
| Excel unpublished restatement → refresh | **MANUALLY VERIFIED** (sheet emptied until publish) |
| Excel publish → refresh | **MANUALLY VERIFIED** (clicks 30 then 40) |
| Power BI Desktop PostgreSQL model, DAX, five pages, cards | **P9 / NOT VERIFIED** |
| Local `DFIP.pbix` | **NOT VERIFIED** (not saved) |
| Azure AD → `dfip.client_ids` | **BLOCKED**. Needs Entra ID + Azure AD auth to PostgreSQL. Not required for JWT API / Excel. Parked with Power BI. |
| Reporting LOGIN that encodes one client (`dfip_desktop_a` / `_b`) | **MANUALLY VERIFIED** against `rpt_*` (not a Power BI canvas) |
| SPA Admin upload (does not publish) | **MANUALLY VERIFIED** (browser, 2026-08-27). `POST /uploads` **201**; published false. |
| SPA explicit publish | **MANUALLY VERIFIED** (browser, 2026-08-27). `POST /publications` **201**. |
| SPA Client CSV / XLSX download | **MANUALLY VERIFIED** (browser, 2026-08-27). |
| Live CORS `Content-Disposition` expose | **MANUALLY VERIFIED** (2026-08-27) after API restart. |

## Why fake mashup zip parts stay forbidden

Three states (do not collapse):

- **A. Python structure scaffold** — Settings + Facts. What `dfip_web.client_workbook`
  writes. Not DataMashup. Not a Refresh All claim.
- **B. Native Excel DataMashup** — Excel Desktop connections / query tables /
  DataMashup customXml. This is the tracked `excel/Client_Report.xlsx`.
- **C. Refresh All verified** — local `tmp/desktop_acceptance/` copies
  (2026-08-26/27). Gitignored when they contain a reader JWT — do not commit
  a BearerToken.

Historically, `build_client_report()` appended unregistered zip parts
`xl/queryMashup/PublishedFacts.m` and `customXml/powerQuery.xml`. Those were
**not** Office DataMashup. Excel Desktop repaired unknown parts. That is why
the old committed file opened as **Repaired**.

V2-X **stopped** writing those fake parts. Tests require them to be **absent**.
The tracked workbook **does** include `xl/connections.xml`, `xl/queryTables/`,
and `http://schemas.microsoft.com/DataMashup`. The Python builder still cannot
manufacture that package; do not run it over the native file.

P7/P8 tests require an empty Settings BearerToken, no secrets, the
published-facts URL in `excel/PublishedFacts.m`, native connection artifacts,
and no fake `queryMashup` parts. Do not embed JWTs.

## Excel Desktop construction that actually refreshed

Excel 16.0 COM created a **local** workbook
`tmp/desktop_acceptance/Client_Report_desktop.xlsx` (gitignored; contains a
reader JWT — do not commit):

1. Settings table with headers **Parameter** / **Value** (`xlYes`).
2. `Queries.Add("PublishedFacts", excel/PublishedFacts.m)` unchanged.
3. Load to a Facts sheet via `Microsoft.Mashup.OleDb.1` /
   `Location=PublishedFacts`.
4. Query Options equivalent: mashup `FirewallEnabled=false` (Ignore Privacy
   Levels). Combining `Excel.CurrentWorkbook()` with `Web.Contents` requires
   that, or Excel raises the privacy-level dialog. Excel Web credentials are
   per Windows profile, not in the workbook. One time per PC: Data Source
   Settings → Global permissions → the API host → Anonymous, so M can send
   `Authorization: Bearer <JWT>`.
5. Data → Refresh All (`Workbook.RefreshAll` +
   `CalculateUntilAsyncQueriesDone`).

The live API logged Power Query traffic (not the Python urllib client):

```text
GET /api/v1/publications/current/facts?limit=200&client_id=a0000000-0000-4000-8000-000000000001&offset=0
```

That is `PublishedFacts.m` (`PageLimit = 200`, Settings `ClientId`, `offset`).

### Observed Refresh All rows (Client A v2, clicks 20)

| Day | Campaign ID | Unique Clicks | Delivered | client_id |
|---|---|---|---|---|
| 2025-08-01 | camp-1 | 20 | 45 | Client A |
| 2025-08-02 | camp-1 | 0 | 60 | Client A |

No `camp-b`.

### Pointer test (Excel Desktop)

| State | PostgreSQL published slice | Excel after Refresh All |
|---|---|---|
| Published clicks=20 | day-1 clicks 20 | clicks **20**, **0** |
| Upload restatement clicks=30, **not** published | `rpt_*` vacated (empty) | **0 rows** (not 30) |
| Publish run `5d822a70-a867-4e35-a7cb-eedcefc9f4c4` **201** | day-1 clicks 30 | clicks **30**, **0** |
| Close Excel, reopen, Refresh All | same | clicks **30**, **0**; query name `PublishedFacts` |

Unpublished restatement **vacates** restated grains from the published slice.
Excel went empty; it did not keep 20 and it did not show 30 until publish.

## Power BI Desktop

Launched (`Untitled - Power BI Desktop`, 2.155.756.0). Blank report. Data pane
empty. Get Data dropdown was opened; unattended UI did **not** complete
PostgreSQL DirectQuery, table load, relationships, DAX paste, theme, five
pages, or card reads. No `DFIP.pbix` was saved.

`dfip_api` is **NOLOGIN**. Desktop cannot connect as that role name. Local
LOGIN roles `dfip_desktop_a` / `dfip_desktop_b` were created on the test
database only (not a migration): `GRANT dfip_api`, `ALTER ROLE SET ROLE
dfip_api`, plus `dfip.role` / `dfip.client_ids` / `dfip.platform_admin`.
Connecting as those logins (psycopg, not Power BI):

- A: `camp-1` clicks 30 and 0; working-set `fact_campaign_day` count **0**;
  CTR **30/105 ≈ 0.285714**
- B: `camp-b` clicks 7 only

That is the reporting LOGIN the README allows (“login that already encodes one
client”). It is **not** a Power BI canvas PASS.

Host for Desktop: `127.0.0.1`, port **5433**, database `dfip_test`. Credentials
live in gitignored `tmp/desktop_acceptance/pbi_desktop_logins.json`.

### Exact remaining Power BI actions

P9 canvas is **not** verified. Power BI Desktop is installed
(`PBIDesktop.exe` present). `dfip_api` is **NOLOGIN**. The application
`DATABASE_URL` user cannot `CREATE ROLE`; a privileged database owner must
apply `powerbi/sql/reporting_login.example.sql` once (operator SQL, not a
migration). Store passwords only under gitignored
`tmp/desktop_acceptance/pbi_desktop_logins.json`.

Live Client A published slice (same pointer Excel proved, HTTP
`GET /api/v1/publications/current/facts` 2026-08-27):

- publication `a0c13f32-910f-4e12-bb1a-a9907c038337`
- run `db7d748f-d59a-4b4e-af73-e4d927e722b0`
- `p7-be947c5bc055-camp-a` on `2025-08-01`
- Clicks **40**, Delivered **100**, Sent **100**, Failed **0**, Impressions
  **100**, Total Cost **15**, CTR **40/100 = 0.400000** (not an average of
  daily CTR)

1. Home → **Get data** → **More…** → **PostgreSQL**. DirectQuery. Encrypt
   the connection. `PgServer` = pooler `host:5432`, database `postgres`,
   user `dfip_desktop_a`.
2. Load **only** `rpt_published_fact`, `rpt_dim_date`, `rpt_dim_client`,
   `rpt_dim_campaign`, `rpt_dim_variation`.
3. Follow `powerbi/README.md` steps 4–10 (keys, date table, relationships,
   hide lineage, paste `dax/measures.dax`, theme, five pages). Do not invent
   KPI formulas.
4. Compare executive cards to the live baseline above /
   `powerbi/sql/validation_kpis.sql` as `dfip_desktop_a`.
5. Pointer: unpublished restatement → Refresh (empty facts, not the new
   clicks) → publish → Refresh (new clicks).
6. Isolation: reconnect as `dfip_desktop_b`; no `p7-be947c5bc055-camp-a`.
7. Save **local** `DFIP.pbix`, close, reopen. Do not commit.

This Desktop build was **not** executed. P9 remains the next checkpoint.

## SPA acceptance — observed 2026-08-27

Browser at `http://127.0.0.1:3000` as `desktop-publisher-a` / `publisher`:

1. Admin upload `.xlsx` → `POST /api/v1/uploads` **201**. Banner: processing
   run `3f4c72aa-a3ef-4037-8380-a55186d2382a`, transformed 1, **published
   false**. Working set had `spa-camp-1`; published slice did not.
2. Explicit Publish → `POST /api/v1/publications` **201**. Publication
   `d7d35786-3776-4401-ad6f-e49bac0354a8`. Published `spa-camp-1`
   unique_clicks **7**.
3. `/client/facts` Download CSV and Download XLSX created blobs. Live
   `GET .../facts.csv` and `.xlsx` **200** with Client A `client_id`.
4. Client B reader CSV **200** without `spa-camp-1`; requesting Client A
   `client_id` **403**; working-set `GET /facts` **403**.
5. Live CORS `Access-Control-Expose-Headers: Content-Disposition` after API
   restart.

Do not commit JWTs or `tmp/desktop_acceptance/` artifacts.

## P9 August-scale PublishedFacts (2026-08-29)

Read-only paging of `GET /api/v1/publications/current/facts` using the same
`limit=200` / `offset` plan as `excel/PublishedFacts.m`:

| Field | Observed |
|---|---|
| Current publication | `5c8e3746-4005-48b3-9298-a4734472d165` |
| Processing run | `4ed7cb35-25ac-4b5d-a73c-27938c8d283a` |
| Period | 2025-08-01 .. 2025-08-31 |
| `pagination.total` | **50286** |
| Pages | 252 (offsets 0 .. 50200) |
| First page | 200 rows |
| Final page | 86 rows |
| Combined / unique grains | 50286 / 50286 |
| Duplicates / missing | 0 / 0 |
| Working-set `/facts` | not called |
| Elapsed | ~370 s |

One unattended Excel COM `RefreshAll` on a gitignored
`tmp/desktop_acceptance/Client_Report_p9_accept.xlsx` copy (token only in that
file) was started once. After ~6 minutes it had produced **no** PublishedFacts
HTTP traffic (privacy-level / unattended COM class of failure). The process
was stopped. **Not retried.** Desktop Refresh All of 50k rows remains a
manual Excel UI operation on a local token-bearing copy.

P9 row-calculation (same day): XML-stored measure formulas lacked
`t="array" ref="{cell}"`, so SUMIFS calculated only the first UNIQUE row.
Adding that flag (Excel Formula2 save) makes each hierarchy row receive KPIs.
Verified in Desktop by seeding PublishedFacts (no Refresh All) with 3 dates /
31 dates / multi-grain groups. Unattended Refresh All was not retried.

Do not commit JWTs or `tmp/desktop_acceptance/` artifacts.

## Automated regression

`python -m pytest -m postgres` was **not** re-run while the live API used this
database (that fixture drops `public`). Acceptance dump:
`tmp/desktop_acceptance/acceptance.dump` (gitignored).

This session: `python -m pytest -m "not postgres"` → **342 passed**.
`python -m ruff check packages tests` passed.
`python -m ruff format --check packages tests` passed.
Prior full suite (before this live API): **384** pytest / **42** postgres /
ruff green.

2026-08-27 after SPA wiring: `python -m pytest -m "not postgres"` → **344
passed**. Postgres marker **not** re-run while the live API used this
database.
