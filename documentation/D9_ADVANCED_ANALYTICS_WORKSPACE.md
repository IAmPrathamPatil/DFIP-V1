# D9 — Advanced Analytics Workspace

**Status:** complete. Final official dashboard phase. **Do not invent D10.**

D9 integrates frozen D1–D8 capabilities into one analytical workspace on `/client/overview`. It does not replace those contracts. The operational Dashboard (`/admin`) and Reports (`/client`) keep their existing purposes.

Primary user: company/client. Secondary: publisher/admin with a bound company. Tenant scope is the authenticated JWT. Frontend filters never define security. Data is published history only; newest successful publication wins for a corrected month.

## Final workspace architecture

```
/admin          operational Dashboard (unchanged purpose)
/client         Reports (controlled published reporting)
/client/overview  Analytics workspace (D1–D8 + D9)
```

One reusable application serves all companies.

```
URL query  = canonical analytical workspace state
Saved JSON = the same allowlisted state, no fact rows
CSV export = current D1–D7 results for that state
Ask        = D8 only (no extra agents)
```

Overview layout (progressive, collapsible `<details>` after KPIs):

1. Sticky workspace chrome (company, period, comparison, filters, metric, plus save/export)
2. D2 global filters
3. KPI Overview (always visible)
4. Dynamic Trends
5. Performance Explorer
6. Insights (empty reason stays visible)
7. Anomalies (insufficient-history reason stays visible)
8. Contextual Ask
9. Drill panel when a drill is open (`role="dialog"`; Escape / Back returns to the prior workspace URL)

One component failure does not unload the others (`Promise.allSettled`). Saved-analysis 403 (unbound/inactive) is shown in the toolbar, not as a full-page error.

## State model

Ownership: **`apps/web/static/js/analytics-state.js`** (URL) and **`packages/analytics/dfip_analytics/workspace.py`** (server allowlist). There is no second store.

Allowlisted single keys: period, month/range bounds, compare bounds, trend_*, drill_*, ex_*, kpi, insight, anomaly, ex_row, trend_point.

Allowlisted multi keys: campaign_id, channel, filter_logic_1, filter_logic_1_group, drill_parent.

Rejected: `client_id`, tokens, SQL, share tokens, unknown fields.

`saved=<id>` is a UI pointer to a loaded record. It is **not** stored inside the JSON payload.

Panel collapse is chrome (`<details>`), not analytical state.

## Cross-component navigation

| From | To | How | Preserved |
|---|---|---|---|
| KPI | Drill | Whole card click (`withDrill` + `withFocus`, origin `kpi`) | D2 + trend/explorer/focus + selected metric |
| KPI | Trend | Trends panel (card stores `data-overview-trend-kpi`; primary click is drill) | D2 + kpi/trend_metric |
| Trend | Drill | Existing point/bar drill | D2 + trend + slice |
| Trend | Explorer | Open in explorer (`withExplorerFromTrend`) | D2 + metric/breakdown |
| Explorer | Drill | Row link | D2 + explorer + parents |
| Insight | Drill | Inspect in drilldown | D2 + insight + kpi |
| Anomaly | Drill | Inspect in drilldown | D2 + anomaly + kpi |
| Any | Ask | Ask about this / seeded hidden fields | D2 + D8 focus |
| Drill | Previous | Back / Escape / crumbs | D2 + remaining drill path |

Filter form submits copy trend, drill, explorer, and focus keys. They drop `saved` so a later edit is a working copy, not a silent mutation of the record.

## Saved-analysis model

Table `analytics_saved_analysis`: id, owner_subject, optional user_id, client_id, title (1–80), state JSONB, timestamps.

API (bound JWT company required):

| Method | Path | Auth |
|---|---|---|
| GET | `/api/v1/analytics/saved` | subject + bound client |
| POST | `/api/v1/analytics/saved` | create; max 50 per subject/company |
| GET | `/api/v1/analytics/saved/{id}` | owner + same company; else 404 |
| POST | `/api/v1/analytics/saved/{id}` | rename and/or replace state |
| DELETE | `/api/v1/analytics/saved/{id}` | owner + same company; 204 |

Authorization:

- Unbound publisher/admin: 403 (“Select a company before using saved analysis.”)
- Client/reader: own subject + JWT company only
- Publisher/admin with a bound company: same owner+company rule (not a company-wide bulletin board)
- Inactive company: 403
- Deleted company: rows deleted; GET 404; no leftover handle

Saved state is configuration. Opening a save after a corrected-month publish re-queries **current** published history.

## Sharing decision

**Deferred.** No share tokens, no public/anonymous analytics URLs.

Reasons:

- Overview URL already encodes workspace state for the **same authorized principal**.
- A token that survives logout or company delete would be a security bypass.
- Encoded tenant data in a public URL is unnecessary when JWT remains the boundary.

Same-user bookmark of `/client/overview?...` is the supported continuation. It still requires a valid session for that company.

## Export decision

**Implemented:** synchronous CSV of the current workspace (`GET /api/v1/analytics/export.csv`).

Includes context, D2 filters, KPIs, trend points, explorer rows, insight/anomaly headlines, and drill rows when a drill is open. Generated from existing D1–D7 services. Failures in optional sections are omitted rather than failing the whole file.

**Deferred:**

- **Excel** — Reports already ships Client Report / refreshable PublishedFacts workbooks. Duplicating that pipeline in Overview adds complexity without new analytical truth.
- **PDF / image** — layout engine, fonts, and chart rasterization are disproportionate for this phase.
- **PowerPoint-style** — same, plus no current operator workflow that needs a deck from Overview.

Unsupported `format=` values return 422. There is no `/analytics/export.pptx`.

CSV stays synchronous: D1–D7 payloads are already capped aggregates, not fact dumps.

## Security model

- JWT `client_id` is authoritative. Query `client_id` cannot widen a bound token (existing analytics rule).
- Saved JSON cannot contain `client_id`. Cross-tenant GET is 404, not 403 with existence leak where another company id is used.
- Exports use the same `_resolve_client_id` + `require_company_active` path as Overview.
- Ask remains D8: no new model calls, no unpublished data, no other-company questions.
- Deactivate: live analytics and saved CRUD are blocked.
- Delete: `dfip_delete_company` and `execute_sql_purge` remove `analytics_saved_analysis` before `client`. In-memory `purge_client` does the same.

## Company lifecycle

| State | Overview | Saved | Export | Ask |
|---|---|---|---|---|
| Active | normal | owner CRUD | CSV | D8 |
| Deactivated | blocked for live ops | 403 | 403 | 403 |
| Deleted | company gone | rows gone / 404 | cannot target it | cannot target it |

Saved analyses are not rebuilt when published data changes. They are not snapshots.

## Performance findings

In-memory TestClient budget (D9 suite): Overview, trends, explorer, insights, anomalies, filtered Overview, and Ask each complete in well under 2 seconds on the fixture store.

D1–D8 already aggregate in PostgreSQL / newest-wins history grain. Workspace queries are those same endpoints. Combined filter changes reuse one D2 resolution.

**Caching decision:** no extra application cache, no warehouse, no new materialized view for D9. Invalidation would have to be publication-driven and tenant-keyed; the existing grain table already is the publication-fresh aggregate. A `(owner_subject, client_id, updated_at)` index on saved analyses is the only new index, and it is for CRUD list, not KPI math.

## D9 UI Refinement — Same-route Overview partial refresh

**Root cause:** Apply Filters / Clear All / autosubmit / KPI drill / trend-explorer URL changes / Back-Forward never did a document reload. They called `navigate()` → `history.pushState()` → `dfip:navigate` → `renderRoute()` → `render(loadingState())` → `#app.innerHTML = layout(...)` → Overview loading → analytics GETs → `#app.innerHTML` again. The SPA tore down sidebar, topbar, filters, and every section for a same-route query change.

**Fix:** Once `/client/overview` is mounted (`[data-overview-shell]`) and the JWT session is already bound, same-route query changes call `refreshOverviewInPlace(query)` instead of `layout()` / `render(loadingState())`.

```
navigate() / popstate
→ history.pushState() remains canonical
→ same route client-overview + shell mounted
→ refreshOverviewInPlace(query)
→ fetch only affected analytics
→ replace section hosts
```

**Mounted shell (not replaced):** `#app`, sidebar, topbar, company context, Overview heading, filter form (patched in place when it still has focus), workspace chrome, section containers, saved/export controls, scroll position.

**Section hosts:** `[data-overview-context-host]`, `[data-overview-kpis-host]`, `[data-overview-trends-host]`, `[data-overview-explorer-host]`, `[data-overview-insights-host]`, `[data-overview-anomalies-host]`, `[data-overview-ask-host]` (patched, textarea kept), `[data-overview-drill-host]`. Inner panels keep their own attributes (`data-overview-explorer`, `data-overview-ask`, `data-overview-drill`, …).

## D9 UI Refinement — Full-width sectioned workspace

Overview content uses the available main-column width (`main.content:has([data-overview-shell])`), not the 1180px form cap used by other pages. Sidebar and topbar stay mounted.

**Hierarchy**

1. Overview header + context bar
2. Filters & analytical context (full-width D2 panel)
3. Key Performance Indicators (existing eight D1 cards)
4. Trends | Performance Explorer
5. Insights | Anomalies | Analysis Tools (Ask, save, export CSV)
6. D4 drilldown host (unchanged overlay when open)

**Desktop grid:** KPI cards `4×2`; mid row two equal columns; lower row three columns where width allows. Filter row 1 is the 6-field period/comparison strip; row 2 is four compact dimension pickers; row 3 is active-filter chips.

**Responsive:** desktop keeps Trends|Explorer as two columns and Insights|Anomalies|Tools as three columns through standard desktop widths (including ~1280). Around 1100px KPIs go to three columns, the lower row becomes two columns with Analysis Tools full-width as three cards, and filters wrap. Around 900px mid/lower rows stack and KPIs go to two columns; below 720px KPIs and filter fields stack and the D4 drawer is a full-width sheet. Tables and charts scroll inside their containers. No page-level horizontal overflow.

**Accessibility:** KPI cards remain a single focusable link (Enter/Space, visible `:focus-visible`, comparison included in the accessible name). Compact filter triggers are labelled buttons (`aria-expanded`, `aria-controls`); popovers are dialogs with checkbox options, labelled Select all / Clear, Escape close, and focus return to the trigger. Campaign search stays inside the campaign popover. Active chips expose labelled remove buttons. The D4 drawer is `role="dialog"` `aria-modal="true"`: focus moves in on open, Tab is trapped, Escape/X/backdrop close, `inert` de-emphasizes background chrome, and focus returns to the originating KPI/explorer/insight/anomaly control. Filter popovers close on Escape before D4. `prefers-reduced-motion` disables Overview/drawer transitions and busy shimmer without changing interaction.

Partial-refresh hosts are unchanged. Same-route Apply still calls `refreshOverviewInPlace()` without replacing `#app`.

**KPI cards (interactive):** the eight D1 KPIs stay in a 4×2 grid. Each card is the D4 click target (`<a class="metric-card overview-kpi">` with `data-overview-drill-kpi`). Hover uses a restrained accent border; `:focus-visible` is a keyboard outline; `:active` is a quiet press. Accessible name is `{KPI} {value} … View details`. After drill closes, focus returns to `[data-overview-kpi="{id}"]`. Formulas, kinds, and `formatKpiValue` are unchanged. Comparison=None shows **No comparison** with no delta / prior / vs line. Auto-without-prior still shows **No prior-period comparison**. Mini-trends are decorative SVGs (`aria-hidden`) from one batched D3 `include_metric` GET (company total, no breakdown, request-local `compare=none`, day grain except all-history → month). Card values still come from D1. Sparkline failure omits the chart only.

**Requests:** ordinary filter changes do not `loadSession()` and do not refetch the saved-analysis list. Trend-only / explorer-only / drill-only URL changes fetch only those regions. KPI sparklines refetch with D2 filter changes, not Trends-panel keys. Saved list refetches after save / rename / delete.

**Section visual polish:** Trends, Explorer, Insights, Anomalies, and Analysis Tools share the workspace kicker/lede header (title, short description, optional right-side meta). Trends and Explorer remain equal mid-row columns: compact grouped D3/D5 controls, framed chart/table, legend inside the chart frame, and localized empty/retry. Insight cards lead with direction + headline + **Primary driver**; evidence and Keep-in-context are secondary; View details remains the D4 action. Anomaly cards lead with status (Anomaly found / No anomaly / Insufficient history / Unavailable), then what happened, then context, then action. Analysis Tools is three deliberate cards: Ask a question, Save this analysis, Export CSV (icon, title, short explanation, one CTA). Empty states stay compact and offer Clear filters when a dimension filter is active. Loading uses per-host `aria-busy` + shimmer overlay without replacing `#app` or collapsing layout. Host contracts, D4 drawer, and same-route refresh are unchanged.

**D4 drilldown drawer:** KPI click still uses `withDrill` → `pushState` → `refreshOverviewInPlace()`. The surface is a right-side drawer (`[data-drill-panel]`) over the mounted Overview (backdrop de-emphasizes the workspace; `#app` is not replaced). Header shows the selected metric, Overview KPI value when origin is `kpi`, comparison delta/vs, or **No comparison**. A compact context block lists period, comparison, active filters, and the existing breakdown dimension control. Modes are **Breakdown**, **Trend**, and **Details** (local tab state, not a D9 URL key). Breakdown keeps existing D4 `rows` (ranked bars, values, contribution, drillable rows). Details is the same D4 row table with compact aligned columns. Trend is a company-total D3 request for the selected KPI (`drillTrendParamsFromQuery`: current D2 filters/period/comparison, no breakdown, grain `day` or `month` for all-history). It does **not** reuse Overview Trends URL keys (`trend_metric` / `trend_grain` / `trend_breakdown`) when those represent a different metric. Comparison=None shows the current series only; an active comparison uses the existing D3 overlay when the contract supplies it. Identical sparkline or Trends-panel payloads are reused instead of a duplicate GET. Deeper drill, Other-not-drillable, depth 3, day terminal, and top 50 + Other are unchanged (`withDrill` → in-place refresh). Close: X, Escape, backdrop, Back. Opening focuses the drawer; closing restores the originating control. Loading keeps header/tabs and skeletons the content area (`data-drill-loading`); close stays usable. 403 stays `data-drill-unauthorized`. 500 retries inside the drawer (`data-overview-retry="drill"`). Export CSV and Ask about this reuse existing actions with current drill context. Desktop = right drawer; ≤1100px wider drawer; ≤720px bottom sheet. D4 analytics semantics are unchanged.

**Not in this step:** new drill backend or D10.

**Filter workspace:** period/comparison stay visible native controls (Period, Month, From, To, Comparison, Compare Month). Channel, Filter Logic 1, Filter Logic 1 group, and Campaign are compact closed summary buttons (`[data-filter-picker]`) that open a checkbox popover (search when useful, Select all, Clear, Escape / outside click / focus-leave close, arrow-key option movement). Closed labels summarize: no restriction → `All selected (n)`; 1–2 values by name; many → `Email, SMS +2`; campaigns → `n selected`. Checkbox edits stay in the existing filter form until **Apply filters** (`queryFromOverviewForm` → `pushState` → `refreshOverviewInPlace()`). Active-filter chips below the controls reflect **canonical URL / applied** state, not uncommitted popover edits; empty dimension+compare state shows **All values**. Chip × still uses `data-filter-chip-clear`. Clear all remains left; Apply remains right. Compare Month includes explicit **None**.

**Comparison = None:** canonical URL is `compare=none` with `compare_month_start` omitted. Auto is the default (those keys absent). An explicit month is `compare_month_start` only. None does not fall back to auto or a leftover month. KPI deltas, context “vs” labels, and comparison chips are hidden. D6 uses `insufficient_comparison`. Ask comparison-dependent operations stay `missing_comparison`. Saved/export persist `compare=none` without a compare month.

**Requests:** ordinary filter changes do not `loadSession()` and do not refetch the saved-analysis list. Trend-only / explorer-only / drill-only URL changes fetch only those regions. KPI sparklines refetch with D2 filter changes, not Trends-panel keys. Saved list refetches after save / rename / delete.

**Races:** incrementing `overviewRefreshSeq`. A slower older response cannot overwrite a newer in-place paint. The winning refresh calls `clearAllOverviewBusy()` so a discarded request cannot leave `aria-busy="true"`.

**Loading:** section `aria-busy` / local skeleton for an empty drill host. No full-page Overview skeleton on same-route updates. Section failures stay in that host (`data-overview-retry`).

**Authorization on same-route refresh:** 401 still uses the existing credential path. Inactive-company 403 falls through to `loadSession()` and the existing company-selection/lifecycle path. Saved-analysis 403 and drilldown 403 stay in their hosts (`workspaceSavedPanel` / `data-drill-unauthorized`) and do not call `unauthorizedView()`.

**Back/Forward:** `popstate` still dispatches `dfip:navigate`. Same-route Overview uses in-place refresh; leaving Overview still does a full route render.

Initial load, entering Overview from another route, company switch, and real auth changes still use the existing full `renderRoute()` path.

## Error / recovery

- KPI failure on **initial Overview load** still occupies the page error banner. Same-route refresh keeps the shell and shows a KPI-host retry. Sibling sections are not given cached KPI period/comparison captions; they use their own payload context or a localized unavailable state.
- Same-route Ask answers are cleared when D2/D3/D4/D5 grounding keys change.
- Trend / explorer / insights / anomalies / drill / saved list / KPI sparklines can fail independently. Sparkline failure does not fail the KPI host.
- Saved-analysis 403 and drilldown 403 stay inline on same-route refresh.
- Invalid saved JSON: 422 on write; load of a missing id: 404.
- Expired session: 401 on data routes; SPA existing auth handling.
- Unsupported metric/dimension: existing 422 banners.
- Empty published history: explicit empty state.
- No comparison / no insights / insufficient anomaly history: explicit reasons, not silent hide.

## D1–D8 integration

D9 does not fork KPI formulas, filter resolution, trend grains, drill depth, explorer modes, insight thresholds, anomaly medians, or Ask intents. Workspace transitions only compose URL/state keys those contracts already understand.

Ask remains the only AI layer. `DFIP_ASK_PROVIDER` default stays `none`.

## Production AI prerequisite (M4)

Before enabling an external LLM provider in production:

1. Per-principal rate limiting
2. Completion budget
3. Spend controls

Do not silently set `DFIP_ASK_PROVIDER` to a network provider. Template answers remain available with `none`.

## Final limitations / deferred

- No D10.
- No anonymous or cross-user share tokens.
- No Overview Excel / PDF / PPT export.
- Saved analyses are per JWT subject within a company, not a shared company library.
- No Ask prompt library (Ask questions are not the saved unit).
- No warehouse.
- Collapsed panels are not part of saved state.

## Live verification (this implementation)

Publisher (`publisher@123`, company Pratham Patil) on `/client/overview`:

- Latest published month Oct-25 vs Sep-25
- Channel SMS applied; KPI and trend magnitudes changed vs all-channels
- KPI → Trend (`kpi=revenue_inr&trend_metric=revenue_inr`) preserved SMS
- Trend → Explorer (`ex_metric=revenue_inr`) preserved SMS
- Trend point → Drill (`drill=trend`); Escape returned to the same filters/metrics
- Saved analysis **D9 SMS Revenue Oct** (`POST /analytics/saved` 200); URL `saved=` pointer; list shows the title
- Ask: grounded `explain_metric_change`, Revenue −62.6% Oct vs Sep under SMS, template wording
- CSV export `GET /analytics/export.csv` 200 with the same company, SMS, revenue
- `/admin` Dashboard still operational (working-set facts / processing)
- `/client` Reports still reporting (Client Report / published downloads)

Client (`demo-client`) live API (sign-in form reached in-browser; password was not typed into the browser tool):

- Overview 200 with published history
- Saved list empty (does not see the publisher save)
- Cross-tenant saved GET 404
- CSV export 200
- Share route 404

Pytest: `tests/test_d9_analytics_workspace.py` plus D1–D8 and `tests/test_p6_web.py`.

Same-route partial refresh (this run): `test_same_route_overview_partial_refresh_contract` plus the D1–D8 / P6 frontend contracts. Live origin `GET http://127.0.0.1:3000/js/app.js` includes `refreshOverviewInPlace` and that function does not assign `#app` (`root.innerHTML`), call `loadSession()`, or `render(loadingState())`. Interactive Apply Filters / Back / Forward in the Cursor browser was not completed here because sign-in was not entered.

Dashboard freeze: after acceptance, do not add features merely because they are possible.
