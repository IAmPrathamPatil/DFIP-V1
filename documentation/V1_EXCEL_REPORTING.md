# DFIP V1 — Excel reporting architecture

Evidence: `excel/Client_Report.xlsx` (tracked native package), `excel/PublishedFacts.m`, `packages/web/dfip_web/{client_workbook,client_report_download,daily_report,pivot_report}.py`, `documentation/DAILY_REPORT.md`, `documentation/DESKTOP_ACCEPTANCE.md`, tests `test_p8_client_report.py`, `test_p11_pivot_report.py`, `test_daily_report.py`.

**Tracked git template ZIP was re-listed 2026-08-30** (113 parts, cache `PublishedFacts!A1:AS2`). Historical Refresh All of the **tracked template** with empty `queryTableFields` produced Field2…Field45. RUN 005C verified a different path: generated **refreshable** workbooks author the 45 queryTableFields, keep the Excel-authored DataMashup package, and emit published JSON keys in `FACT_VALUE_FIELDS` order. Same-file Refresh All then keeps named PivotCache fields (no FieldN). The tracked git template is still not a client deliverable.

---

## Two workbooks (do not confuse)

### A. LIVE AUTHORING TEMPLATE — `excel/Client_Report.xlsx` (git)

| | |
|---|---|
| Purpose | Operator/authoring file: Power Query pages **current** publication JSON into PublishedFacts; nine native PivotTables share one cache |
| Sheets | `PublishedFacts`, `Facts`, plus nine report tabs (names include **trailing spaces** on Overall and D2C) |
| Query | Native DataMashup + `xl/connections.xml` + `xl/queryTables/` named **`ExternalData_1`** |
| Settings | Parameter/Value: `ApiBaseUrl`, `BearerToken`, `ClientId`. **Git BearerToken must stay empty** |
| Cache at rest | Worksheet source typically `PublishedFacts!A1:AS2` (headers + one empty data row) until a successful mashup that Excel binds correctly |
| Who should Refresh All | **Nobody delivering a client file.** Operators who understand the PivotCache failure may refresh **query only** in a throwaway copy |
| Safe use | Keep as template; generate client files from the API |

`build_client_report()` in `client_workbook.py` writes **structure-only** (Settings + Facts). It **cannot** author DataMashup. **Do not run it over the tracked native file.** Fake zip parts `xl/queryMashup/` and `customXml/powerQuery.xml` are **forbidden** (Excel “Repaired”).

### B. STATIC CLIENT REPORT — `GET .../client-report.xlsx`

| | |
|---|---|
| Purpose | Recovery deliverable: same nine pivots, **static** PublishedFacts cells for **one** publication |
| Generator | `render_client_report_xlsx(..., artifact="static")` |
| Query tables | **Removed** |
| Mashup | keepAlive / background / refreshOnLoad **disabled** |
| Cache | `bind_pivot_cache_for_snapshot` → `PublishedFacts!A1:AS{n}` with named fields matching `FACT_HEADERS` |
| Settings | ApiBaseUrl / BearerToken / ClientId **cleared** |
| Refresh All | **Do not.** Open file; if needed, refresh **PivotTables only** against the worksheet (not Web.Contents) |

Filename:
- Current static (`/publications/current/client-report.xlsx`): `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report.xlsx`
- Historical static (`/publications/{publication_id}/client-report.xlsx`): `DFIP_<client_code>_<YYYY-MM-DD>_<publication_short_id>_Client_Report.xlsx`

Same-day republishes keep distinct historical files via the short id. The
current filename continues to represent the current snapshot. Re-download the
current route after publish if the recovery workbook is lost.

### C. REFRESHABLE CLIENT REPORT — `GET .../refreshable-client-report.xlsx`

| | |
|---|---|
| Purpose | Same physical file: Refresh All loads the company's **current** publication |
| Generator | `render_client_report_xlsx(..., artifact="refreshable")` |
| Query tables | **Kept**; 45 `queryTableFields` = `FACT_HEADERS` |
| Mashup | Native Excel-authored DataMashup package is **copied, not rewritten**. Python rewrite of `Section1.m` makes Excel skip Refresh All |
| Settings | ApiBaseUrl plus the download session JWT in BearerToken. ClientId **empty** |
| Credentials | Short-lived access JWT from the authenticated download. Not a permanent secret. History endpoint stays authenticated. Excel web connection is Anonymous. Token expiry → re-download |
| Authorization | JWT `client_id` is authoritative. Settings ClientId cannot widen access (403) |
| Current pointer | `GET /publications/current/facts` — processing-run snapshot of `publication_current`. `fact_scope` unchanged |
| Row cap | JSON paging is 200/page with **no** 75k cap. Static download still 422 over `DFIP_DOWNLOAD_MAX_ROWS` |
| Historical | `/{id}/refreshable-client-report.xlsx` is **not** a route |

Filename: `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report_Refreshable.xlsm`. Do not silently use re-download as a substitute for this path.

Runtime never mutates tracked `excel/Client_Report.xlsx`. `excel/PublishedFacts.m` (CanonicalKeys + 30s Timeout) is the operator source to paste when rebuilding that template in Desktop Excel.

Inert provenance (not Settings, not Power Query):
- Facts sheet row 5: `Published`, `Company` (display name), `Code` (`client_code`)
- `docProps/custom.xml`: `client_id`, `publication_id`, `published_at`

Static downloads clear Settings B2–B4. Refreshable website/API downloads write ApiBaseUrl and the caller's short-lived access JWT into BearerToken. ClientId stays empty. Current download with no `publication_current` returns 404 rather than an empty workbook. The row cap is unchanged (`DFIP_DOWNLOAD_MAX_ROWS`, default 75,000) and returns 422.

---

## Workbook inventory

| File | Purpose | In git? |
|---|---|---|
| `excel/Client_Report.xlsx` | Native V2-X live template | Yes (binary) |
| `excel/PublishedFacts.m` | M source of truth | Yes |
| API-generated static Client_Report.xlsx | Static recovery snapshot | No (download / tmp) |
| API-generated `_Refreshable.xlsx` | Same-file Refresh All deliverable | No (download / tmp) |
| `tmp/Client_Report.xlsx` / broken Refresh All copies | Local debris | gitignored |
| FY-2026 `Web Engage - Daily Report - FY-2026.xlsx` | **Reference specification** for sheet names/pivots; not the DFIP refresh source | Not in this repo as tracked product |
| Python scaffold from `build_client_report()` | Tests / structure | Must not replace native file |

Acceptance/desktop copies under `tmp/desktop_acceptance/` are **gitignored** (may contain JWTs). **Do not commit.**

---

## Sheets (tracked / generated)

Technical:

1. **PublishedFacts** — 45 headers `FACT_HEADERS`; live: query table destination; static: cell grid `A1:AS{n}`.
2. **Facts** — Settings table (`ApiBaseUrl`, `BearerToken`, `ClientId`) plus a Facts header row used as the **parameter sheet**, not as the pivot source.

Report tabs (`REPORT_SHEET_NAMES` — exact strings):

| Constant | Sheet name (exact) | Pivot name | Grain (row fields) | Default page/group |
|---|---|---|---|---|
| OVERALL | `Overall Daywise Report ` (trailing space) | PivotOverall | Month → Day | none |
| VERTICAL | `Vertical Level` | PivotVertical | Month → Filter Logic 1_2 | none; FL4/FL5 slicers |
| CHANNEL | `Channel Wise` | PivotChannel | Month → FL1_2 → Filter Logic 1 | Channel is **filter**, not row |
| SUB_SPLIT | `Sub-Split` | PivotSubSplit | … → Campaign Name | |
| AMC_DAYWISE | `AMC DayWise` | PivotAmcDaywise | Month → Day | FL1_2 = Group7 |
| AMC_SPLIT | `AMC Split` | PivotAmcSplit | FL1 → FL2 → Month | Group7 |
| AMC_VERTICAL | `AMC Vertical Monthly Split` | PivotAmcVertical | FL1 → Month → FL2 | Group7 |
| D2C | `D2C Vertical ` (trailing space) | PivotD2c | FL1 → Month → FL2 | Group5 |
| SERVICE | `Service Campaigns` | PivotService | FL1_2 → Month → Day | FL1 = `Service \| FMS & LMS \| Campaigns`; Group2 |

**Nine PivotTables. VERIFIED** `PIVOT_TABLE_NAMES` and `REPORT_CONTRACTS`.

Layout: compact+outline (`outline="1"`). Shared `cacheId` **1** (`PIVOT_CACHE_ID`). Slicer tabular id `110000011` (distinct). ~35 slicer caches, independent per sheet (Channel on Overall does not filter Sub-Split).

AZ:BA caption lookup for group display names — not a classification engine.

P10 `report_formula()` UNIQUE/FILTER/SUMIFS is the **Python reconstruction contract**. It is **not** stored on delivered P11 pivot sheets.

---

## Facts sheet vs PublishedFacts

| | Facts | PublishedFacts |
|---|---|---|
| Why it exists | Settings + header documentation; historical “Facts table” layout from V2-X | Actual reporting data + pivot source |
| Pivot source | **No** (VERIFIED: cache binds PublishedFacts) | **Yes** |
| Power Query destination | Settings via `Excel.CurrentWorkbook(){[Name="Settings"]}` | Query table on PublishedFacts (live only) |
| Template leftover | Header row may look like a data table | Empty A2 until refresh or static fill |
| Active for client totals | Settings credentials on **live** file only | Data |

Do not assume the sheet named Facts feeds PivotTables because of the name.

---

## PublishedFacts.m (conceptual)

Location: `excel/PublishedFacts.m` (also embedded in DataMashup of the live xlsx).

1. Read Settings table (`ApiBaseUrl`, `BearerToken`, `ClientId`).
2. `PageLimit = 200` (API max page).
3. `Web.Contents(ApiBaseUrl, RelativePath="/api/v1/publications/current/facts", Query limit/offset/client_id, Header Authorization Bearer)`.
4. Page until `pagination.total` consumed (`List.Combine`).
5. `HeaderMap` snake_case → display headers (must match `FACT_HEADERS` / `FACT_VALUE_FIELDS`).
6. Decimal fields parsed from JSON strings.

**Does not** call `GET /facts`. JWT `client_id` scopes API; Settings `ClientId` only needed for unscoped **dev_token**.

Privacy: combining CurrentWorkbook + Web.Contents requires Ignore Privacy Levels (documented in DESKTOP_ACCEPTANCE). Excel Data Source for `http://127.0.0.1:8000` should be **Anonymous**; token is in the M header, not Excel’s stored web password.

---

## Client report generation (implementation)

**Entry:** `PublicationService` download helpers → `render_client_report_xlsx(rows, published_at=…)`.

| Step | Behavior |
|---|---|
| Clone | ZIP clone of `XLSX_PATH` (`excel/Client_Report.xlsx`) |
| Populate | `_published_facts_sheet_xml` writes header row + one XML row per fact using `FACT_VALUE_FIELDS` |
| Stamp | Settings/Facts published timestamp via `_facts_settings_xml` |
| Strip query tables | Remove query table parts / rels |
| Disable connections | `_disable_connections_xml` |
| Bind cache | `bind_pivot_cache_for_snapshot` to `A1:AS{n}` (`n = 1 + rowcount`) |
| Page filters | `apply_page_filter_defaults_xml` |
| Credentials | Blank ApiBaseUrl, BearerToken, ClientId |
| Guard | `_require_complete_workbook`; `assert_native_pivot_package` |
| Cap | Same `DFIP_DOWNLOAD_MAX_ROWS` as other downloads |

Empty publication: nine-sheet workbook, **no fact rows**, empty-state text `No published data available.` where applicable.

---

## PivotCache / query-table failure (KNOWN LIMITATION — CRITICAL)

### What happened

On the **tracked live template** with empty `queryTableFields`, **Refresh All** historically:

1. Mashup can load tens of thousands of rows into the query table.
2. Excel rebuilds the PivotCache from `ExternalData_1` without named fields.
3. Cache fields become `Field2` … `Field45`.
4. Pivot layouts lose row/value field bindings.
5. Calculated fields can rename (e.g. `Actual Sent` → `Actual Sent2`) and show `#NAME?`.

RUN 005C refreshable downloads author the 45 queryTableFields and keep JSON keys in `FACT_VALUE_FIELDS` order. Desktop Refresh All on that artifact kept named `FACT_HEADERS` (no FieldN), nine PivotTables, and 35 slicer caches. After Excel **Save**, PivotCache XML may grow extra calculated-field copies with a `2` suffix (observed 57 → 101). Pivots and slicers remained usable. Do not rewrite DataMashup in Python; Excel skips that query.

### Why static download avoids live query

Generator writes **cells**, deletes query tables, rebinds cache to **worksheet range with explicit field names**. Refresh All cannot replace the snapshot via Web.Contents.

### Residual risk on static file

DataMashup XML may still **embed** `PublishedFacts.m` text inside the package. Settings credentials are cleared, so the query has no URL/token **unless** a user pastes them later. See [V1_SECURITY.md](V1_SECURITY.md).

### Safe workflow

| Workbook | Action |
|---|---|
| Tracked live `excel/Client_Report.xlsx` | Not a client deliverable. Do not commit tokens. |
| Static API download | Open; use pivots; **do not Refresh All**. Re-download if lost. |
| Refreshable API download | Open; Refresh All. Session JWT is already in Settings. Re-download after expiry. |

---

## Metrics on the nine reports

See `MEASURE_HEADERS` in `daily_report.py` and [V1_DATA_DICTIONARY.md](V1_DATA_DICTIONARY.md). Aggregation: **SUM additives first**, then divide/subtract (`kpis.py` `client` namespace). Never average daily percentages.

Zero vs blank: 0 means summed zero; blank means N/A or zero denominator. Empty publication: message, not fake zeros.

---

## New month / pointer change

See [V1_OPERATIONS_RUNBOOK.md](V1_OPERATIONS_RUNBOOK.md). Summary:

- **Refreshable client file** Refresh All follows `publication_current` for the JWT's company.
- **Static client file** does **not** follow a new publication. Re-download for recovery.
- **Tracked template** Refresh All is operator-only and historically FieldN-unsafe when `queryTableFields` are empty.
- Slicers/filters on a static file are local Excel state; a new download resets to template defaults (Group7/Group5/Service FL1, etc.).

---

## Connections and hidden content

- Live: HTTP connection to `DFIP_API_BASE_URL` (often loopback).
- Named range/table: Settings.
- Calculated fields: Excel pivot calculated fields from `calculated_cache_fields()` / `MEASURE_OPS` in `daily_report.py` (ratio = `left/right`, diff = `left-right`, Excel-quoted names with spaces). Examples: `Failed Rate SM` = `'Failed'/'Sent'`; `Actual Sent` = `Sent-Failed`. Sum measures are cache fields of PublishedFacts columns, not calculated fields.

**Slicers (VERIFIED contracts):** per-sheet tuples on `ReportSheetContract.slicers` — typically Filter Logic 1, Channel, Filter Logic 1_2; Sub-Split/AMC/D2C/Service also Month; Vertical also FL4 and FL5. Independent slicer caches (Channel on Overall does not filter Sub-Split).

**P11 injection vs download:** `apply_native_pivots` builds pivot XML into a workbook ZIP. Client download **clones the already-native tracked template** and rebinds cache; it does not need to re-run full pivot generation if the template already contains P11 parts.

---

## Tracked `excel/Client_Report.xlsx` ZIP (re-inspected 2026-08-30)

**VERIFIED** by opening the git binary as a ZIP (no Excel COM). Size **93847** bytes. **113** parts. No `xl/queryMashup/` fake parts.

### Package parts (complete list)

`[Content_Types].xml`, `_rels/.rels`, `customXml/{item1.xml,itemProps1.xml,_rels/item1.xml.rels}`, `docProps/{app.xml,core.xml}`, `xl/_rels/workbook.xml.rels`, `xl/connections.xml`, `xl/drawings/drawing1.xml`–`drawing9.xml`, `xl/metadata.xml`, `xl/pivotCache/{pivotCacheDefinition1.xml,pivotCacheRecords1.xml,_rels/pivotCacheDefinition1.xml.rels}`, `xl/pivotTables/pivotTable1.xml`–`pivotTable9.xml` plus each `_rels/pivotTableN.xml.rels`, `xl/queryTables/queryTable1.xml`, `xl/sharedStrings.xml`, `xl/slicerCaches/slicerCache1.xml`–`slicerCache35.xml`, `xl/slicers/slicer1.xml`–`slicer9.xml`, `xl/styles.xml`, `xl/tables/{table1.xml,table2.xml}`, `xl/theme/theme1.xml`, `xl/workbook.xml`, `xl/worksheets/sheet1.xml`–`sheet11.xml` plus `sheet1`–`sheet11` `_rels`.

### Workbook.xml sheet names (exact)

`PublishedFacts`, `Facts`, `Overall Daywise Report ` (trailing space), `Vertical Level`, `Channel Wise`, `Sub-Split`, `AMC DayWise`, `AMC Split`, `AMC Vertical Monthly Split`, `D2C Vertical ` (trailing space), `Service Campaigns`.

### Tables / query tables / connections

| Object | Evidence |
|---|---|
| Excel tables | `xl/tables/table1.xml`, `table2.xml` (Settings + Facts layout; names not re-parsed this pass beyond part presence) |
| Query table | `xl/queryTables/queryTable1.xml` name **`ExternalData_1`**. Field tags count **1** (nearly empty `queryTableFields` — matches known Refresh All field-loss mode) |
| Connection | `xl/connections.xml` name **`Query - PublishedFacts`**. `keepAlive` present/true on live template. `refreshOnLoad` **not** set true in this inspect (`CONN_REFRESH` false). No Bearer token substring and no `127` substring in `connections.xml` (VERIFIED this inspect) |
| DataMashup | `customXml/itemProps1.xml` contains DataMashup schema marker. `customXml/item1.xml` length **20226**; **no** plaintext `PublishedFacts` / `Web.Contents` in that part. Bytecode mashup is **not** line-auditable here. Source of truth for M: **`excel/PublishedFacts.m`** |
| PivotCache | `cacheId` **1**. Source XML: `<cacheSource type="worksheet"><worksheetSource ref="A1:AS2" sheet="PublishedFacts"/></cacheSource>` — **empty template range** (header + one row). `cacheField` count **57**. `recordCount` on definition not re-printed; Python generator uses `recordCount="0"` on authored cache |
| Calculated cache fields (formulas in definition) | `Failed/Sent`, `Sent-Failed`, `Delivered/Sent`, `'Unique Impressions'/Delivered`, `'Unique Clicks'/Delivered`, `'Unique Clicks'/'Unique Impressions'`, `'Total Cost'/'Unique Click-Through Conversions'`, `'Unique Click-Through Conversions'/'Unique Clicks'`, `'Click-Through Revenue (INR)'/'Total Cost'`, `'Total Cost'/'Unique Conversions'`, `'Unique Conversions'/Delivered`, `'Revenue (INR)'/'Total Cost'` |
| PivotTables | All `cacheId` 1: PivotOverall, PivotVertical, PivotChannel, PivotSubSplit, PivotAmcDaywise, PivotAmcSplit, PivotAmcVertical, PivotD2c, PivotService |
| Slicers | 35 slicer cache parts + 9 slicer parts. Independent per-sheet caches (contract) |
| OOXML rels | Workbook rels → sheets, pivotCache, slicerCaches, connections. Each pivotTable rels → cache. Each report sheet rels → pivot + drawing + slicers. `pivotCacheDefinition1.xml.rels` → `pivotCacheRecords1.xml` |

### Report contract (runtime, not a ZIP part)

`REPORT_CONTRACTS` / `ReportSheetContract` in `daily_report.py`: row fields, default page filters (Group7/Group5/Service FL1), slicer tuples, `MEASURE_OPS`. P10 `report_formula()` is **not** stored on P11 sheets.

### Known Refresh All failure (live template)

Refreshing the mashup can load `/publications/current/facts` JSON into a **query table** (`ExternalData_1`) whose fields become `Field2`… instead of `FACT_HEADERS`. PivotCache then loses named fields → `#NAME?` / repaired workbook. **Client deliverable must be static `GET .../client-report.xlsx`**, not a Refresh All of this git file.

### Static client architecture

`render_client_report_xlsx`: clone ZIP; rewrite PublishedFacts cells; blank Settings/`sharedStrings` demo client id; `_disable_connections_xml` (keepAlive 0, background 0, refreshOnLoad 0); **drop** `xl/queryTables/` parts and Content_Types overrides; `bind_pivot_cache_for_snapshot` sets `PublishedFacts!A1:AS{n}` with `refreshOnLoad="0"` and `recordCount="0"`; `_require_complete_workbook`. Mashup customXml **may still be present** (residual live query if user pastes credentials — see security doc). After page-filter pinning, `apply_slicer_defaults_xml` (RUN 007) seeds the existing 35 slicer caches, then `apply_reference_formatting` (RUN 006) patches styles. `apply_hidden_technical_sheets` (RUN 008) then sets `PublishedFacts` and `Facts` to OOXML `state="hidden"` and `activeTab`/`firstSheet` to the Overall report. Neither rewrites PivotCache field names/order. Sheets are not deleted.

### RUN 006 formatting (generated workbooks) — FROZEN

Reference: untracked `Web Engage - Daily Report - FY-2026.xlsx`.

| Topic | Decision |
|---|---|
| Fonts / numFmts / col widths 2–24 / header row heights | Applied |
| Col A | FY-2026 gutter `2.88671875` |
| DFIP title `Web Engage Daily Report` + purpose/hint | Kept as product chrome, moved to column B so A can be the gutter |
| Freeze panes | Kept (DFIP scanning; reference has none) |
| Month/Day | Published text (`Aug-25`); not converted to Excel serials (would change cache/grouping semantics) |
| Pivot `<formats>` + 1715 `dxfs` | **Not copied.** Records bind `field="10"` / `field="85"` on the 86-field FY-2026 cache; DFIP has 57 cache fields. Unsafe. Subtotals use native `PivotStyleLight16` |
| Slicers | Unchanged by RUN 006; RUN 007 seeds defaults on generate |

### RUN 007 slicers (generated workbooks)

The tracked template already contains **35** independent slicer caches (same field set as FY-2026). Generation **clones** them; it does not author a second engine.

| Field (`sourceName`) | Count | Scope | Default |
|---|---|---|---|
| Filter Logic 1 | 9 (one per report) | That sheet's PivotTable only | All, except Service → `Service \| FMS & LMS \| Campaigns` when present |
| Channel | 9 | That sheet only | All |
| filter_logic_1_group (caption Filter Logic 1_2) | 9 | That sheet only | All, except AMC → Group7, D2C → Group5, Service → Group2 when that value exists in the snapshot |
| Month | 6 (not Overall / Vertical / Channel) | That sheet only | Latest `month_label` in **this** snapshot (`month_start` order). Missing → All |
| AMC Device Category -  Filter Logic 4 | 1 (Vertical) | Vertical only | All |
| AMC Product Cat -  Filter Logic 5 | 1 (Vertical) | Vertical only | All |

`apply_slicer_defaults_xml` writes tabular `<i x="…" s="1"/>` against cache shared items. Intended single defaults that are absent from the snapshot fall back to All. Company A/B values come only from that download's `rows`.

FY-2026 Channel slicers are often single-select and Month slicers often keep several recent months. DFIP does **not** copy those live filters: Channel/FL4/FL5 default to All, and Month defaults to the latest month **in that snapshot** so a new company is not stuck on a reference WhatsApp/month mix.

Live Refresh All may rebuild slicer items (Excel-native); 005C owns that path. Worksheet `PivotCache.Refresh` is used in RUN 007 desktop tests. After cache refresh, Excel may reset a selection if the item is gone; slicer objects remain attached.

### RUN 008 technical-sheet visibility (generated workbooks)

Deletion of `PublishedFacts` or `Facts` is **unsafe**. Evidence from `excel/Client_Report.xlsx`:

| Sheet | Must remain because |
|---|---|
| PublishedFacts | PivotCache `worksheetSource sheet="PublishedFacts"`; query connection `Location=PublishedFacts`; query table on that sheet; defined name `ExternalData_1`; empty-state `COUNTA(PublishedFacts!A:A)` on report chrome |
| Facts | Excel table `Settings` (`A1:B4`) read by `Excel.CurrentWorkbook(){[Name="Settings"]}` in `PublishedFacts.m`; leftover `Facts` table is not the pivot source |

Generated static and refreshable downloads set `state="hidden"` (normal hide, not veryHidden) on both technical sheets and open on Overall (`activeTab`/`firstSheet` = 2). `tabSelected` is cleared on the hidden Facts sheet so Excel does not open in grouped-sheet edit mode. The tracked git template stays visible for operator authoring. Hiding is presentation only, not tenant security. Support can Unhide.

### RUN 009 schema extensibility vs the frozen Excel contract

`FACT_HEADERS` remains 45 columns in this exact order. Do not append, insert, or rename fields on generated workbooks. PivotCache names/order, queryTableFields, and slicer `sourceName` stay bound to that list.

An approved extra source column (currently `Campaign Objective`) is **not** written to PublishedFacts. Legacy static and refreshable workbooks stay valid: Refresh All still maps the canonical 45 JSON keys via `HeaderMap`; extra JSON keys are dropped. Excel exposure of a newly approved field requires a controlled workbook-contract update (new generator/HeaderMap version), not silent FieldN growth.

Unknown source columns never reach Excel: ingest fails closed before transform/publish.

---

## Excel XML helpers that affect generation or PivotCache binding

Material helpers only (not every `_xml_attr`).

### `pivot_report.py`

| Function | Effect |
|---|---|
| `_published_facts_ref` / `_cache_source_xml` | Worksheet source `PublishedFacts!A1:{last_col}{last_row}` |
| `pivot_cache_definition_xml` / `pivot_cache_records_xml` | Authored cache (tests / `apply_native_pivots`) |
| `bind_pivot_cache_for_snapshot` | **P8:** replace `<cacheSource>`, force `refreshOnLoad="0"`, `recordCount="0"` |
| `_shared_items_xml` / `_replace_cache_shared_items` / `apply_page_filter_defaults_xml` | Shared items + page-field pins so Excel does not drop defaults |
| `pivot_table_xml` / `_pivot_field_xml` / `_page_fields` | Nine pivot definitions vs `FACT_HEADERS` + calculated fields |
| `_excel_safe_pivot_xml` | Strip COM-hostile markup |
| `slicer_cache_xml` / `slicers_xml` / `drawing_xml` / `worksheet_rels_xml` | Slicer binding to cache id `SLICER_PIVOT_CACHE_ID` |
| `build_pivot_parts` / `_patch_workbook_rels` / `_patch_workbook_xml` / `_patch_content_types` | ZIP wiring |
| `apply_native_pivots` | Rewrite report sheets; skip fake mashup; skip `xl/calcChain.xml`; then RUN 006 formatting |
| `assert_native_pivot_package` | Download/template completeness gate |

### `report_format.py` (RUN 006)

| Function | Effect |
|---|---|
| `apply_reference_formatting` | Styles + dataField numFmtId + report col/row layout on generated zips |
| `apply_datafield_formats` | `numFmtId` only; axes/cacheId unchanged |
| `DATAFIELD_NUMFMT` | FY-2026 built-in/custom format map |

### `slicer_defaults.py` (RUN 007)

| Function | Effect |
|---|---|
| `apply_slicer_defaults_xml` | Seed tabular items + defaults from snapshot rows |
| `assert_slicer_package` | 35 unique caches, sourceName in FACT_HEADERS, no FieldN |
| `latest_month_label` | Company-scoped latest month from `month_start` / label |

### `client_report_download.py`

| Function | Effect |
|---|---|
| `render_client_report_xlsx` | Orchestrates static/refreshable package + RUN 007 slicer defaults + RUN 006 formatting + RUN 008 hidden technical sheets |
| `apply_hidden_technical_sheets` | `PublishedFacts`/`Facts` `state="hidden"`; activate Overall |
| `_disable_connections_xml` | Stops silent mashup refresh |
| `_published_facts_sheet_xml` / `_fact_row_xml` / `_value_cell` | Static grid |
| `_facts_settings_xml` / `_blank_template_client_id` | Clear credentials / demo UUID |
| `_require_complete_workbook` | Refuse truncated zip |

### `daily_report.py` (contract + leftover formula engine)

| Function | Effect on OOXML / pivots |
|---|---|
| `REPORT_CONTRACTS`, `MEASURE_OPS`, `MEASURE_HEADERS` | Pivot field lists and calculated-field formulas |
| `report_formula` / `additive_formula` / `derived_formula` | **P10 reconstruction only** — not written onto P11 sheets |
| `attach_daily_report_sheets` | Injects formula sheets if used (legacy path vs P11) |
| `_ensure_report_styles`, workbook/rels/content-type patchers | Shared with pivot attach |
| `aggregate_published_rows` | Python totals, not cache XML |

### `client_workbook.py`

`build_client_report()` / `mashup_text()` — **scaffold**; must not overwrite native git xlsx. `FACT_HEADERS` is the 45-column contract bound into cache field names.

- Hidden columns after last KPI through AY on report sheets (DAILY_REPORT.md).
- No production Supabase public CSV in the DFIP xlsx (Google “published-feeds” listings observed on a PC were **other workbooks**, not DFIP — PARTIALLY VERIFIED from prior investigation).
