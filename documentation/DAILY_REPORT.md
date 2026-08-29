# Nine-sheet Web Engage Daily Report

The client-facing Daily Report is the FY-2026 workbook's **business report
specification**, rebuilt so Excel consumes DFIP published facts instead of a
local Combine-Files folder and a 336k-row pivot cache.

Source inspected (read-only): `Web Engage - Daily Report - FY-2026.xlsx`
(nine visible sheets, nine PivotTables, one shared cache of 336,434 records /
86 fields, 35 slicers). Production raw workbooks remain
`Web-Engage Raw` + `New Logic`. They are not this report's refresh source.

## Architecture

```
Publisher processes + publishes
        → publication_current
        → GET /api/v1/publications/current/facts
        → excel/PublishedFacts.m (unchanged, page size 200)
        → PublishedFacts sheet (native V2-X query)
        → one shared Pivot cache (worksheet source = PublishedFacts sheet)
        → nine native PivotTables + per-sheet slicers
```

P8 historical / current downloads clone that template, write the selected
publication as static PublishedFacts cells, empty the live query connection,
and **rebind the shared cache** to `PublishedFacts!A1:AS{n}` so the PivotTables
cannot refresh into another publication.

P10 UNIQUE/FILTER/SUMIFS remains the Python reconstruction contract
(`report_formula()`, `aggregate_published_rows`). It is not stored on the
delivered report sheets.

Excel is the reporting + refresh layer. Label resolution, Filter Logic 1_2,
rate card, Total Cost, QA, and publication stay on the backend.

`PublishedFacts.m` is not modified. Fake `xl/queryMashup/` parts stay forbidden.
The tracked BearerToken stays empty. Isolation is the API / JWT `client_id`
(application-level, not PostgreSQL RLS).

## Sheets

Technical (unchanged V2-X):

1. `PublishedFacts` — query table / Refresh All destination
2. `Facts` — Settings (`ApiBaseUrl`, `BearerToken`, `ClientId`) + Facts headers

Client-facing (source tab names, including trailing spaces):

| Sheet | Grain | Default filter |
|---|---|---|
| Overall Daywise Report | Month → Day | none (all published rows) |
| Vertical Level | Month → Filter Logic 1_2 | none. Slicers include AMC Device (FL4) / AMC Product (FL5) |
| Channel Wise | Month → Filter Logic 1_2 → Filter Logic 1 | Channel is a **filter**, not a row field |
| Sub-Split | Month → Filter Logic 1_2 → Filter Logic 1 → Campaign Name | none |
| AMC DayWise | Month → Day | Filter Logic 1_2 = Group7, displayed as **D2C AMC Vertical** |
| AMC Split | Filter Logic 1 → Filter Logic 2 → Month | Group7 / D2C AMC Vertical |
| AMC Vertical Monthly Split | Filter Logic 1 → Month → Filter Logic 2 | Group7 / D2C AMC Vertical |
| D2C Vertical | Filter Logic 1 → Month → Filter Logic 2 | Group5, displayed as **D2C Product Vertical** |
| Service Campaigns | Filter Logic 1_2 → Month → Day | Filter Logic 1 = `Service \| FMS & LMS \| Campaigns`; Group2 displayed as **Overall Service Campaigns** |

The source file's last saved view had extra slicer selections (often WhatsApp
and a FY-2025 month window). Those are snapshot state, not the report
definition. Overall is **not** permanently filtered to Group7 even though the
source file's saved Overall pivot matched AMC DayWise numerically.

Native PivotTables use compact+outline form (`outline="1"`) so Month groups
expand/collapse to days (and nested row fields where present). This is a real
PivotTable hierarchy, not worksheet outline grouped rows. PivotTable parts are
re-serialized to Excel's native child order (`rowItems` immediately after
`rowFields`) so desktop Excel opens the workbook as PivotTable objects.

Slicers are independent per report sheet (35 slicer caches, 9 slicer parts),
matching the reference. A Channel slicer on Overall does not filter Sub-Split.

Page fields:

- Service Campaigns: Filter Logic 1 (default `Service | FMS & LMS | Campaigns`)
  and Filter Logic 2 (all)
- AMC DayWise / AMC Split / AMC Vertical: Filter Logic 1_2 = Group7
- D2C Vertical: Filter Logic 1_2 = Group5

Filter Logic 1_2 membership is published `filter_logic_1_group` (backend
Labels). The pivot field caption is `Filter Logic 1_2`. Excel displays group
keys unless the user maps captions; the AZ:BA caption table remains on each
sheet for reference. Do not VLOOKUP membership in the workbook. Do not invent
leftover captions; ungrouped Filter Logic 1 values pass through.

Vertical Level exposes AMC Device Category -  Filter Logic 4 and AMC Product
Cat -  Filter Logic 5 as slicers, not worksheet F4/G4 cells.

## Measures

Additive columns are summed from PublishedFacts. Rate / cost-per / ROAS columns
use the client KPI namespace (`dfip_analytics.kpis`): **SUM first, then
operate**. Native Web Engage rate columns are not inputs.

Failed Rate SM / Actual Sent / Delivery Rate / Delivered to Imp. rate /
CTR (Del to Clicks) / CTR ( Impr. to Click ) / Cost per conversion / UCT rate /
UCT ROAS / Overall ROAS match the recovered calculated fields, not averaged
daily percentages.

Click Through Cost/Conv, UC to UCTC Conversions, and UC to UCTC Revenue exist
in the source cache but are unused by the nine pivots and are omitted.

Empty publication: `No published data available.` Null denominators stay blank
(not zero).

## Zero vs blank

The report does not fill blanks with zero to make the sheet look full.

| Cell shows | Meaning |
|---|---|
| `0` | 0 is a real total of zero — the additive metric summed to a mathematical zero (for example Sent = 0). |
| blank | The metric is not applicable or the denominator is 0 / non-numeric (rates, ROAS, cost-per). Empty publication. |
| text | A business label or hierarchy value (Month, Filter Logic, Campaign Name). |

Filter cells that are blank mean **all published values**. They are not missing data.

Columns after the last KPI through AY are hidden. AZ:BA remain the caption lookup
and stay hidden. That unused gap was presentation-only; it was not missing facts.

Campaign-grain sheets (especially Sub-Split) legitimately show many blank rate
cells when a campaign has 0 delivered, 0 impressions, 0 clicks, 0 conversions,
or 0 cost. Those rows are not suppressed: the campaign exists in PublishedFacts.

## Refresh

1. Open `excel/Client_Report.xlsx` (or a local copy with a Bearer token in Settings).
2. Data → Refresh All.
3. PublishedFacts pages the current publication.
4. The nine report sheets recalculate from that table.

Unpublished processing runs do not appear. A pointer change is visible after
the next Refresh All.

The installed Excel Desktop is **16.0 build 20326** (Windows 64-bit). It is
not a full Microsoft 365 GROUPBY/LAMBDA surface. Probe results:

| Supported | `#NAME?` / absent |
|---|---|
| LET, INDEX, MATCH, UNIQUE, FILTER, SORT, SORTBY, SEQUENCE, XLOOKUP, TEXTJOIN, SUMIFS, CHOOSE-as-HSTACK | LAMBDA, GROUPBY, PIVOTBY, DROP, TAKE, HSTACK, VSTACK, CHOOSECOLS, BYROW, MAP, TEXTSPLIT |

The first `#NAME?` token on the previous nine-sheet formulas was **LAMBDA**
(GROUPBY / DROP / HSTACK / CHOOSECOLS would also fail). Replacement formulas
use UNIQUE/FILTER/CHOOSE/SORTBY + SUMIFS only.

LET binding names must not look like A1 cells (`f1` = F1, `flt1` = FLT1) or
Excel rejects the formula and may refuse to open the workbook.

Campaign Name and Filter Logic 2 grouping matches Excel PivotTables: case
variants (`renewal T-02` / `Renewal T-02`) occupy one report row. UNIQUE uses
`LOWER` on those columns only, then MATCH restores the first-seen source
casing for display. PublishedFacts strings are not rewritten. SUMIFS is
already case-insensitive, so that one row receives the combined total. Native
PivotTables (P11) use the same case-insensitive field identity; this is not a
hidden PublishedFacts column.

Total Cost business values stay as published (4 decimal places). Report cells
use `#,##0.00` (money / cost-per / ROAS) and `0.00%` (rates). Pivot-cache IEEE
floats that snap to the same 4-decimal amount, then the same 2-decimal display,
are FORMAT-ONLY. A 0.01 difference after that snap is a real numeric mismatch.

Hierarchy on the delivered workbook is a native compact PivotTable outline
(expand/collapse), not separate UNIQUE columns. The P10 reconstruction formulas
still UNIQUE/FILTER/CHOOSE/SORTBY + SUMIFS for tests and `aggregate_published_rows`.

Do not use unattended COM Refresh All loops. Refresh All from the Excel UI
on a token-bearing tmp copy. P8 snapshot workbooks refresh the pivot cache
from the static PublishedFacts sheet (`refreshOnLoad=1`) and do not call the
live API. Snapshot `connections.xml` stays well-formed (`keepAlive=0`,
`refreshOnLoad=0`); an empty `<connections/>` part will not open in Excel 16.

## Authenticated Client Report download

Clients recover a published nine-sheet `Client_Report.xlsx` from the API
without reprocessing, republishing, or embedding a Bearer token.

| Route | Meaning |
|---|---|
| `GET /api/v1/publications/current/client-report.xlsx` | Current publication's workbook |
| `GET /api/v1/publications/{publication_id}/client-report.xlsx` | That publication's workbook |

The server clones the tracked native template and writes the authorized
publication snapshot as static `PublishedFacts` cells. Complete P6 snapshots
stay bound to that `publication_id`. CSV/XLSX fact dumps remain available
and are not this report.

Delivered copies clear Settings `BearerToken` / `ClientId` / `ApiBaseUrl` and
neutralize the live query connection so Desktop Refresh All cannot silently
switch a historical download to a later current publication. The tracked
`excel/PublishedFacts.m` is unchanged and still pages
`GET /publications/current/facts` for Desktop authors who supply their own
token locally.

## August-scale PublishedFacts paging

`PublishedFacts.m` uses `PageLimit = 200` against
`GET /api/v1/publications/current/facts` until `pagination.total` is consumed.
Offsets are `0 .. 200 * Number.RoundDown((Total - 1) / 200)`. An August-scale
slice of 50,286 rows is 252 pages (last offset 50,200, last page 86 rows).
Report formulas UNIQUE/FILTER/SUMIFS over `PublishedFacts!$A:$XFD` remain the
P10 reconstruction contract. Delivered sheets are native PivotTables over the
same PublishedFacts table. An August-scale slice of 50,286 rows is 252 pages
(last offset 50,200, last page 86 rows). Additive and derived measures are
Pivot calculated fields (sum first, then operate) using the same P10 KPI
definitions.

Desktop Refresh All of that query belongs on a **local** token-bearing copy
under `tmp/desktop_acceptance/` (gitignored). Do not write a BearerToken into
the tracked `excel/Client_Report.xlsx`.

P8 Client Report downloads inject a snapshot rather than Refresh All. The
download row cap is `DFIP_DOWNLOAD_MAX_ROWS` (default 75,000) so an August
slice is not refused.

File size of the tracked report workbook is the native V2-X mashup plus Pivot
parts (still far smaller than the source FY-2026 file at ~54 MB, which embeds
a 336k-row cache). The legacy cache is not cloned.

## Security

Do not commit tokens, `DATABASE_URL`, or passwords. Do not point Power Query at
Downloads folders or monthly raw files. Do not run `build_client_report()` over
the native workbook; that scaffold would strip DataMashup.

See `excel/README.md` and `documentation/DESKTOP_ACCEPTANCE.md`.
