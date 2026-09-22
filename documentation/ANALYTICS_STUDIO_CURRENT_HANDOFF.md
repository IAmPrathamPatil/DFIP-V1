# DFIP Analytics Studio — current handoff

**Audience:** a new Cursor agent with no prior chat history.
**Scope:** the Power BI-like Analytics Studio **inside the DFIP website** (`/client/studio`).
**Not in scope:** Microsoft Power BI Desktop/Service, `.pbix`, gateways, or mobile Power BI.
**Inspection date:** 2026-09-23.
**Rule:** repository implementation overrides older D-series planning docs.

This file is a snapshot of **what is in the working tree now**. Most Studio work is **uncommitted** on `master`.

---

## 1. ANALYTICS STUDIO PURPOSE

Analytics Studio is a full-width published-history canvas for a JWT-bound company. It sits beside Overview, not in place of it.

| Surface | Route | Role |
|---|---|---|
| Overview | `/client/overview` | D1–D9 analytical workspace: KPIs, interactive trends, Performance Explorer, insights, anomalies, Ask, drill, saved analyses, CSV |
| **Analytics Studio** | `/client/studio` | Display-only visual canvas using the **same D2 filters and the same analytics contracts** |
| Reports | `/client` | Publication downloads / Excel |
| Admin | `/admin` | Operational dashboard |

Problem it solves: Overview is a collapsible workspace with drill/Ask/explorer controls. Studio is a denser visual layout (stacked mix, share mix, trend, distribution, combo, detail table) without those interactive workflows.

Constraints already encoded in the implementation:

- JWT `client_id` is the tenant boundary. Query `client_id` cannot widen a bound token.
- Data is **published history only** (`publication_history_grain` / newest-wins), never the working set.
- KPI/ratio math stays in Python (`dfip_analytics.kpis.compute_kpis`). The browser formats and draws; it does not invent business metrics (share display uses existing API ratio fields).
- Studio does **not** depend on a new analytics engine.

Nav: Reporting → **Analytics Studio** in `apps/web/static/js/components.js`.

---

## 2. CURRENT UI

Studio layout in `clientStudioView` (`apps/web/static/js/views.js`):

1. Header (eyebrow / title / company · period · comparison)
2. Left **filter rail** + right **canvas**
3. Canvas order:
   - KPI band
   - Stacked mix
   - Share mix
   - 2-column grid: Trend → Distribution → Cost and revenue → Detail → Narrative (reserved)

### 2.1 Global / shared filters

| | |
|---|---|
| **Status** | Complete for Studio (reuse of Overview D2 bar) |
| **Frontend** | `overviewFilterBar()` in `apps/web/static/js/views.js`; Studio passes `{ clearHref: studioHref() }` |
| **Backend** | Same query params on every Studio request (see §5). Options come from `GET /api/v1/analytics/overview` `options` |
| **Payload** | Overview `applied`, `options`, `dropped_filters` |
| **Interactive** | Yes: Apply, Clear all, autosubmit on period/month/compare, multi-select pickers, chip remove |
| **Filters affect it** | It **is** the filter control |
| **Comparison affects it** | Comparison selectors are part of the bar |
| **Limitations** | If the KPI request fails, `data` is null and month/dimension option lists are empty. No Studio-only filter keys. |
| **Complete?** | Complete for D2 reuse |

### 2.2 KPI band

| | |
|---|---|
| **Status** | Complete, display-only |
| **Frontend** | `clientStudioView` → `overviewKpiCardsHtml({ data, query, interactive: false })` in `apps/web/static/js/views.js`; card chrome `overviewKpiCard` in `apps/web/static/js/components.js` (`data-studio-kpi`) |
| **Backend** | `GET /api/v1/analytics/overview` via `api.getOverviewKpis(params)` |
| **Payload** | `OverviewKpiResponse.kpis[]` (eight hero KPIs) + `period` + `comparison` |
| **Interactive** | **No.** `interactive: false` disables drill/focus/trend hrefs and sparklines |
| **Filters affect it** | Yes |
| **Comparison affects it** | Yes (`delta`, `delta_pct`, `prior_value`, vs text). Hidden when comparison unavailable / `compare=none` |
| **Limitations** | No sparklines. KPI failure does not unload other zones (`kpiError`). |
| **Complete?** | Complete |

### 2.3 Stacked mix (absolute)

| | |
|---|---|
| **Status** | Complete, display-only |
| **Frontend** | `studioStackedBarZone` / `studioStackedBarChart` in `apps/web/static/js/views.js` |
| **Backend** | `GET /api/v1/analytics/trends?breakdown=channel` (same D2 filters; default metric `total_cost`, grain `day`) |
| **Payload** | `TrendResponse.series[].points[].value` |
| **Interactive** | No (SVG titles only) |
| **Filters affect it** | Yes |
| **Comparison affects it** | **No overlay.** Trends omit comparison when `breakdown` is set (`comparison_shown=false`, reason `comparison_with_breakdown`) |
| **Limitations** | Hardcoded breakdown `channel`. Non-positive values omitted from stacks. Truncation uses server `truncated` / Other series. |
| **Complete?** | Complete for the accepted P3-C1 scope |

### 2.4 100% stacked / share mix

| | |
|---|---|
| **Status** | Complete, display-only |
| **Frontend** | `studioShareStackedZone` / `studioShareStackedBarChart` in `apps/web/static/js/views.js` |
| **Backend** | **Same stack request as §2.3** (no extra HTTP call). Uses `points[].bucket_share` filled server-side |
| **Payload** | `TrendPoint.bucket_share` (ratio string, e.g. `"0.500000"`). Filled only for additive primary + breakdown (`_fill_trend_bucket_shares`) |
| **Interactive** | No |
| **Filters affect it** | Yes |
| **Comparison affects it** | No overlay (same breakdown rule) |
| **Limitations** | Y-axis labels are `0`–`1`, not percents. Empty if no positive `bucket_share`. |
| **Complete?** | Complete for P3-C2 |

### 2.5 Trend

| | |
|---|---|
| **Status** | Complete, display-only |
| **Frontend** | `studioTrendZone` → `renderTrendChart(primaryOnly, { interactive: false, tooltip: true })` in `apps/web/static/js/trend-chart.js`. Secondary is stripped before draw so this zone is primary-only |
| **Backend** | `GET /api/v1/analytics/trends?secondary=revenue_inr` (D2 filters; default metric `total_cost`, grain `day`) |
| **Payload** | `TrendResponse.series[0].points[].value` and `comparison_value` |
| **Interactive** | Tooltips yes; no point-click drill, no metric/grain form |
| **Filters affect it** | Yes |
| **Comparison affects it** | Yes, period-offset overlay when comparison window exists (no breakdown on this call) |
| **Limitations** | No Studio grain/metric picker. URL `trend_*` keys are stripped on Apply. Grain is API default **day**. |
| **Complete?** | Complete for P3-B1 |

### 2.6 Distribution (bars + donut)

| | |
|---|---|
| **Status** | Complete, display-only |
| **Frontend** | `studioDistributionZone`, `studioDonutChart`, `studioDonutSlices` in `apps/web/static/js/views.js` |
| **Backend** | `GET /api/v1/analytics/explorer` with **D2 filters only** (`studioParamsFromQuery` — **not** `explorerParamsFromQuery`) |
| **Payload** | `ExplorerResponse.rows[]` (`value`, `contribution_pct`, `rank`, `label`, `key`) |
| **Interactive** | No. Bars use `is-static`; no explorer form, no drill |
| **Filters affect it** | Yes |
| **Comparison affects it** | Ranking still uses current-window value (default mode `ranking`, sort `value`). Comparison fields exist on rows but this chart does not plot them |
| **Limitations** | Defaults: metric `total_cost`, dimension `campaign_id`, mode `ranking`, limit **25**. Bars/donut use `drillChartRows` (**max 10** + Other). Share display = `Number(contribution_pct) * 100` in `contributionSharePct` (format only). |
| **Complete?** | Complete for P3-B2/B3 |

### 2.7 Cost & revenue combo

| | |
|---|---|
| **Status** | Complete, display-only |
| **Frontend** | `studioComboZone` / `studioComboChart` in `apps/web/static/js/views.js` |
| **Backend** | **Same trend request as §2.5** (`secondary=revenue_inr`). No extra call |
| **Payload** | `points[].value` (bars = Total Cost) and `points[].secondary_value` (line = Revenue) |
| **Interactive** | SVG titles only |
| **Filters affect it** | Yes |
| **Comparison affects it** | Comparison series exist on the payload but **combo chart does not draw** `comparison_value` / `comparison_secondary_value` |
| **Limitations** | Dual axes; not a ratio. Empty unless both bars and line have at least one numeric point. Secondary cannot be combined with breakdown (that is why stack is a second trends call). |
| **Complete?** | Complete for P3-C3 |

### 2.8 Detail table (not a matrix)

| | |
|---|---|
| **Status** | Complete as **display-only ranked table**. Matrix **not implemented** |
| **Frontend** | `studioDetailZone` in `apps/web/static/js/views.js` |
| **Backend** | **Same explorer request as §2.6**. No extra call. No drilldown. No explorer CSV |
| **Payload** | Full `mix.rows` (not the 10-bar chart slice): `rank`, `label`, `value`, `prior_value`, `delta`, `contribution_pct` |
| **Interactive** | **No** sort, drill, row click, CSV, or secondary/matrix |
| **Filters affect it** | Yes |
| **Comparison affects it** | Yes for Comparison / Delta columns when the explorer comparison window is available |
| **Limitations** | Single dimension only (`campaign_id` default). `parent_key` / `parent_label` are unused because Studio does not send `ex_secondary`. P3-C4B matrix was **stopped** for that reason. Server order only. |
| **Complete?** | Table: complete (P3-C4A). Matrix: **not done** |

### 2.9 Narrative

| | |
|---|---|
| **Status** | **Reserved empty shell** |
| **Frontend** | `studioReservedZone({ zone: "narrative", title: "Narrative", description: "Reserved for insights and anomaly copy." })` |
| **Backend** | **Not called from Studio** |
| **Payload** | None in Studio |
| **Interactive** | n/a |
| **Filters / comparison** | n/a in this zone |
| **Limitations** | Copy placeholder only (`aria-hidden` body mark) |
| **Complete?** | **Not done** (shell only) |

### 2.10 Insights

| | |
|---|---|
| **Status** | Backend + Overview UI exist. **Studio does not fetch or render** |
| **Frontend (Overview only)** | `overviewInsightsSection` in `apps/web/static/js/views.js` |
| **Backend** | `GET /api/v1/analytics/insights` — `AnalyticsService.insights` → `dfip_analytics.insights.build_insights` |
| **Studio** | Explicitly absent: `getOverviewInsights` is not in the Studio `viewFor` branch |
| **Complete?** | **Not done in Studio.** Implemented on Overview |

### 2.11 Anomalies

| | |
|---|---|
| **Status** | Backend + Overview UI exist. **Studio does not fetch or render** |
| **Frontend (Overview only)** | `overviewAnomaliesSection` in `apps/web/static/js/views.js` |
| **Backend** | `GET /api/v1/analytics/anomalies` — `AnalyticsService.anomalies` → `dfip_analytics.anomalies.build_anomalies` |
| **Studio** | `getOverviewAnomalies` is not in the Studio branch |
| **Complete?** | **Not done in Studio.** Implemented on Overview |

### 2.12 Other Studio sections

None. Studio does **not** include: Ask, saved analyses, workspace CSV, KPI sparklines, drill dialog, explorer controls, trend generate-form, finding-grain selector.

Studio HTTP budget (current): **4 requests**, `Promise.allSettled`:

1. `getOverviewKpis(params)`
2. `getOverviewTrends({ ...params, secondary: "revenue_inr" })`
3. `getOverviewExplorer(params)`
4. `getOverviewTrends({ ...params, breakdown: "channel" })`

Source: `apps/web/static/js/app.js` `if (name === "client-studio")`.

---

## 3. CURRENT SCREEN / UX STATE

**Page layout**

- Shell: `[data-studio-shell]` `.studio-workspace` (`overflow-x: hidden`).
- Header: eyebrow `Analytics Studio`, title `Performance canvas`, lede that KPIs share the Overview contract.
- Context line: company, period label, comparison label (or “Loading published-history analysis.”).
- Body: sticky left rail (~16.5–19rem) + canvas.

**Filter rail**

- Heading “Filters” / “Same D2 period, comparison, and dimension keys as Overview.”
- Reused Overview form: Period, Month, From, To, Comparison, Compare month, Channel, Filter Logic 1, Filter Logic 1 group, Campaign.
- Footer: chips, **Clear all** → `/client/studio`, **Apply filters**.
- Autosubmit on period/month/range/compare change (`data-overview-autosubmit`).

**KPI cards**

- Eight equal slots on wide screens.
- Label, value, optional delta + vs comparison, definition.
- Class `metric-card overview-kpi studio-kpi`. Not links.

**Chart ordering**

Stacked mix (full width) → Share mix (full width) → 2-col grid of Trend, Distribution, Combo, Detail, Narrative.

**Table behavior**

- Sticky rank + name columns (shared explorer table CSS).
- Horizontal/vertical scroll in `.studio-detail-wrap` (`max-height: min(28rem, 55vh)`).
- Server rank order; no client sort.

**Scrolling**

- Page scrolls vertically.
- Filter rail `position: sticky; top: var(--sticky-offset)` on desktop.
- Mix bars `max-height: 18rem; overflow: auto`.
- Workspace forbids horizontal overflow.

**Responsive** (`apps/web/static/css/app.css`)

| Breakpoint | Behavior |
|---|---|
| default | 8 KPI columns; rail + canvas; 2-col visual grid |
| `max-width: 1180px` | 4 KPI columns |
| `max-width: 980px` | Rail stacks above canvas (static); visuals 1 column; mix donut/bars stack |
| `max-width: 640px` | 1 KPI column; tighter content padding |

**Dark / light**

- No Studio-specific theme engine. Uses global tokens (`--panel`, `--text`, `--chart-1`…`--chart-9`, `--bg-2` donut hole). Theme toggle is the existing app switcher (`a755f14` / `46529c8` lineage).

**Loading**

- On Studio navigation, `render(clientStudioView({ loading: true }))` **before** fetches (`apps/web/static/js/app.js` ~1728).
- Every zone shows skeleton via `studioZoneLoading`. `aria-busy` on shell. Live region: “Loading Analytics Studio.”
- Full remount after fetch (not Overview’s `refreshOverviewInPlace`). P3-D2 accepted this.

**Empty**

- Per-zone empty copy + optional Clear filters when dimension filters are in the URL (`queryHasDimensionFilters`).
- KPI empty: “No published history is available for this company.”

**Error**

- Per-zone `errorBanner` for non-422 failures; 422 as warn banner (“not supported”).
- Auth errors still throw and bounce to credential/unauthorized.
- KPI error does not drop mix/trend/stack.

**Labels**

Zone titles/descriptions are static explanatory sentences (published-history, server values, not 100% vs absolute, etc.). Zones use `aria-labelledby` / `aria-describedby`. Detail table has `sr-only` caption and `th scope="row"`.

---

## 4. SHARED FILTER STATE

Canonical client state: **URL query**. Owner: `apps/web/static/js/analytics-state.js`. Server allowlist: `packages/analytics/dfip_analytics/filters.py`.

Studio **reads only D2 keys**. It does not persist Overview `trend_*`, `ex_*`, `drill_*`, `kpi`/`insight`/`anomaly`, or `finding_grain`.

### Keys Studio sends

| URL / form | API param | Default if omitted |
|---|---|---|
| `period` | `period` | `month` (latest published month) |
| `month_start` | `month_start` | latest published month (omitted from URL when it matches latest) |
| `day_from` / `day_to` | same | only when `period=range` |
| `compare` | `compare` | `auto` |
| `compare_month_start` | `compare_month_start` | auto prior published month |
| `compare_from` / `compare_to` | same | unused unless explicit range compare |
| `channel` (multi) | `channel` | all |
| `filter_logic_1` (multi) | `filter_logic_1` | all |
| `filter_logic_1_group` (multi) | `filter_logic_1_group` | all |
| `campaign_id` (multi) | `campaign_id` | all |

`studioParamsFromQuery` = `filterParamsFromQuery` only (`ANALYTICS_SINGLE` + `ANALYTICS_MULTI`). If `compare=none`, compare date keys are deleted.

### Filter logic (server)

`packages/db/dfip_db/publication_store.py` `_history_where` / `_text_predicate`:

- Dimensions are **AND**ed together.
- Multiple values **within** one dimension are `column = ANY(...)` (**OR**).
- Empty selection = no predicate (all values).
- Blank/`""` includes `IS NULL OR column = ''`.
- Unknown values outside the current period are **dropped** (`dropped_filters`), not 422.

Company is JWT context, never a Studio filter.

### How filters reach every zone

`viewFor("client-studio")` builds one `params` object and spreads it into all four calls. Stack/trend only **add** `breakdown` / `secondary`.

### Apply / Clear / chips

| Action | Code | Result |
|---|---|---|
| Apply / autosubmit | `queryFromStudioForm` → `studioHref` | Rebuilds D2 query; **deletes** trend/explorer/drill/focus/finding keys |
| Clear all | `studioHref()` | `/client/studio` with empty search |
| Chip remove | click handler; if Studio, also strips explorer/trend/drill keys | `sharedFilterHref(query)` |

### Back / Forward

`apps/web/static/js/router.js` `navigate()` uses `history.pushState` + `dfip:navigate`. `popstate` re-dispatches `dfip:navigate` → `renderRoute()`. Query state **is** preserved by the browser history stack.

Studio does **not** use Overview in-place patch refresh, so Back remounts the full Studio shell and refetches.

---

## 5. ANALYTICS API CONTRACTS USED BY STUDIO

Prefix: `/api/v1`. JWT required. Router: `packages/api/dfip_api/analytics_routes.py`. Service: `packages/api/dfip_api/analytics_service.py`. Schemas: `packages/api/dfip_api/schemas.py`. Client: `apps/web/static/js/api-client.js`.

Shared D2 query parameters on all of these: `client_id` (ignored for widening), `period`, `month_start`, `day_from`, `day_to`, `compare`, `compare_month_start`, `compare_from`, `compare_to`, `campaign_id`, `channel`, `filter_logic_1`, `filter_logic_1_group`.

Validation: unknown metric/dimension/grain → 422 `ValidationFailed`. Extra schema fields forbidden (`extra="forbid"`). No fact rows.

### 5.1 `GET /analytics/overview` — **used by Studio**

| | |
|---|---|
| Handler | `get_overview_kpis` → `AnalyticsService.overview` |
| Defaults | Latest published month; `compare=auto` prior published month |
| Response | `OverviewKpiResponse`: `has_published_history`, `period`, `comparison`, `kpis[]`, `applied`, `options`, `dropped_filters` |
| Studio consumes | `kpis`, `period`, `comparison`, `has_published_history`, `options`/`applied`/`dropped_filters` for the filter bar, `company_name` |
| Rules | Eight `HERO_KPIS` only. SUM additives then `compute_kpis(..., namespace="client")`. Divide-by-zero → null, never 0 |

### 5.2 `GET /analytics/trends` — **used twice by Studio**

| | |
|---|---|
| Handler | `get_overview_trends` → `AnalyticsService.trends` |
| Extra params | `metric` (default `total_cost`), `secondary`, `grain` (default `day`), `breakdown`, `include_metric` |
| Studio call A | `secondary=revenue_inr` → Trend + Combo |
| Studio call B | `breakdown=channel` → Stacked + Share |
| Response | `TrendResponse`: `selection`, `metric`, `secondary`, `series[].points[]`, `comparison_shown`, `empty`, `truncated` |
| Point fields Studio uses | `bucket`/`bucket_label`, `value`, `secondary_value`, `comparison_value`, `bucket_share` |
| Rules | Secondary **cannot** combine with breakdown (`validate_trend_selection`). `bucket_share` only when breakdown + additive primary. Missing buckets stay in the series as null values. Max series rows validated. Breakdown cap → Other |

### 5.3 `GET /analytics/explorer` — **used by Studio**

| | |
|---|---|
| Handler | `get_overview_explorer` → `AnalyticsService.explorer` |
| Extra params | `metric`, `dimension`, `secondary`, `mode`, `direction`, `sort`, `limit`, `mover`, `min_value`, `min_contribution`, `contribution` |
| Studio sends | **none of the extra params** → defaults: metric `total_cost`, dimension `campaign_id`, mode `ranking`, sort `value`, direction `desc`, limit **25**, no secondary |
| Response | `ExplorerResponse.rows[]` (`ExplorerRow`) |
| Studio consumes | `rows`, `empty`, `selection`, `metric`, `truncated`, `truncated_message` |
| Rules | Contribution share only for additive metrics. Secondary is depth-2 pairs, not a cube. Studio never requests it |

### 5.4 `GET /analytics/insights` — **not used by Studio**

Exists and is used by Overview. Params: D2 + optional `metric`, `dimension`, `limit`, `grain` (default `month`). Response: `InsightResponse` (`insights[]`, `empty_reason`, `thresholds`).

Studio wiring: **missing**. Backend work: **not required** for a first Narrative fill.

### 5.5 `GET /analytics/anomalies` — **not used by Studio**

Exists and is used by Overview. Params: D2 + optional `metric`, `dimension`, `limit`, `grain` (default `month`). Response: `AnomalyResponse`. Month grain expects a month period (`REASON_PERIOD_NOT_MONTH` otherwise). Needs ≥3 prior published months for a baseline.

Studio wiring: **missing**. Backend work: **not required** for a first Narrative fill.

### 5.6 `GET /analytics/drilldown` — **not used by Studio**

Overview/drill only. Studio KPI cards and charts are non-interactive.

---

## 6. DATA FLOW

```
Published campaign-day grain
  publication_history_grain  (serving)
  else newest-wins snapshot subquery
        │  JWT client_id + day window + D2 predicates
        ▼
AnalyticsService._sum / _series / _groups / _pairs
  packages/api/dfip_api/analytics_service.py
  → packages/db/dfip_db/publication_store.py
        │  ADDITIVE_MEASURES summed in SQL
        ▼
compute_kpis(measures, namespace="client")
  packages/analytics/dfip_analytics/kpis.py
        │  ratios AFTER sum; never average daily rates
        ▼
HTTP schemas (Overview / Trend / Explorer)
        │
        ▼
apps/web/static/js/app.js  (Studio four-request allSettled)
        ▼
apps/web/static/js/views.js  (format + SVG/HTML)
```

**Where calculation happens**

| Kind | Where | Not where |
|---|---|---|
| Additive totals (cost, revenue, delivered, clicks, …) | SQL `SUM` on published grain | Browser |
| Ratios (ROAS, CTR, delivery rate, …) | `compute_kpis` after SUM | Browser, native WebEngage rate columns |
| Trend bucket share | `_fill_trend_bucket_shares`: `safe_divide(point.value, bucket_total)` | Browser (Studio only draws `bucket_share`) |
| Explorer rank / contribution | `AnalyticsService.explorer` + `dfip_analytics.explorer` | Browser |
| Insights / anomalies | `build_insights` / `build_anomalies` | Not called by Studio today |
| Display percent for donut/table | `contributionSharePct` = API `contribution_pct` × 100, clamped 0–100 | Must not be used to invent KPIs |

Canonical formula source: `packages/analytics/dfip_analytics/kpis.py` (`KPI_SPECS`, `compute_kpis`). Hero card list: `packages/analytics/dfip_analytics/overview.py` `HERO_KPIS`.

Studio does **not** read reporting `rpt_*` views. Those are a separate reporting package.

---

## 7. KPI / METRIC DEFINITIONS (Studio-displayed)

Authoritative card list: `HERO_KPIS` in `packages/analytics/dfip_analytics/overview.py`.
Runtime: `AnalyticsService.overview` → `_card(spec, …)` after `compute_kpis`.
Engine: `packages/analytics/dfip_analytics/kpis.py`.

| Studio label | id / slug | Kind | Formula | Source |
|---|---|---|---|---|
| Total Cost | `total_cost` | money | `SUM(total_cost)` | Additive measure |
| Revenue | `revenue_inr` | money | `SUM(revenue_inr)` | Additive measure |
| Overall ROAS | `overall_roas` | roas | `revenue_inr / total_cost` after SUM | `KPI_SPECS` client slug `overall_roas` |
| Delivered | `delivered` | count | `SUM(delivered)` | Additive measure |
| Unique Clicks | `unique_clicks` | count | `SUM(unique_clicks)` | Additive measure |
| Unique Conversions | `unique_conversions` | count | `SUM(unique_conversions)` | Additive measure |
| Delivery Rate | `delivery_rate` | rate | `delivered / sent` after SUM | `KPI_SPECS` client slug `delivery_rate` |
| CTR (Delivered → Clicks) | `ctr_del_to_clicks` | rate | `unique_clicks / delivered` after SUM | `KPI_SPECS` / HERO definition. **Not** clicks/impressions |

Null / zero-denominator: `safe_divide` → null (`n/a` in UI), never 0.

**Also displayed (not extra KPI math):**

- Explorer/distribution/detail: same default metric **Total Cost** plus contribution share from explorer.
- Combo line: **Revenue** (`revenue_inr`) as trends `secondary_value`.
- Comparison columns: server `prior_value` / `delta`.

Trend metric registry is exactly the eight hero KPIs (`TREND_METRICS` built from `HERO_KPIS` in `packages/analytics/dfip_analytics/trends.py`). Studio currently **fixes** primary to default `total_cost` except combo secondary `revenue_inr`.

---

## 8. INSIGHTS (actual implementation; not on Studio canvas)

| | |
|---|---|
| File | `packages/analytics/dfip_analytics/insights.py` |
| Entry | `build_insights(...)` |
| HTTP | `AnalyticsService.insights` / `_granular_insights` in `packages/api/dfip_api/analytics_service.py` |
| AI | **None.** Module docstring: “D6 deterministic Insights. Not AI.” |

**Rule logic**

1. Snapshot each requested metric (default all `TREND_METRICS`) current vs comparison window via `compute_kpis`.
2. Material if `abs(delta_pct) >= 0.10` **and** `abs(delta) >=` kind floor (`money 1`, `count 1`, `rate 0.01`, `roas 0.10`).
3. High material if also `abs(delta_pct) >= 0.25` → category `material_change`; else signed `positive_signal` / `negative_signal`. Higher-is-better is false only for `total_cost`.
4. For additive metrics, driver breakdowns on `campaign_id` and `channel` (or requested dimension). Contribution = group delta / total delta. Dominant driver if share ≥ 0.40, or two groups each ≥ 0.25 and together ≥ 0.60. Min 2 groups.
5. Relationship pair if both sides material and **opposite sign**: `(revenue_inr, overall_roas)`, `(delivered, delivery_rate)`.

**Ranking:** `insight_score` = capped `|delta_pct|*10000` + capped abs ratio `*100` + driver share `*1000` + category weight (dominant 400, material 300, negative 250, positive 200, relationship 150). Sort score desc, then `|delta_pct|`, metric, category, id. Limit default 8, max 12.

**Explanations:** template strings in `movement_explanation` / `driver_explanation` / `relationship_explanation`. Explicitly “not a causal claim.”

**Empty reasons:** `no_published_history`, `insufficient_comparison`, `insufficient_history`, `empty_period`, `no_material_insights`.

**UI:** Overview only (`overviewInsightsSection`). Studio Narrative is reserved.

**Studio limitation:** payloads exist; frontend wiring on `/client/studio` does not.

---

## 9. ANOMALIES (actual implementation; not on Studio canvas)

| | |
|---|---|
| File | `packages/analytics/dfip_analytics/anomalies.py` |
| Entry | `build_anomalies` / `classify_anomaly` |
| HTTP | `AnalyticsService.anomalies` / `_granular_anomalies` |
| AI | **None.** “deterministic baseline diagnostic, not a causal claim and not an LLM explanation.” |

**Baseline (this is what the repo does — not IQR, not ML):**

- Method string: **`median`**.
- Prior published months: last **3–12** months strictly before current (`MIN_BASELINE_MONTHS=3`, `MAX_BASELINE_MONTHS=12`).
- Same D2 filters on each month observation.

**Normal-variation / threshold**

1. Discard if `|current - median|` < abs floor (`money 5`, `count 5`, `rate 0.05`, `roas 0.25`).
2. MAD of baseline vs median. MAD is “ok” if `mad >= max(floor, |median|*0.05)`.
3. Robust z = `0.6745 * (current - median) / mad` when MAD ok.
4. If `|z| >= 3.5` → kind **`rolling_deviation`**, direction spike if current > median else drop.
5. Else fallback **period-vs-median new extreme**: current outside min/max of baseline **and** relative move ≥ 50% vs median (rates use abs pp floor instead) **and** abs ≥ `NON_Z_ABS`. Kind is `spike` or `drop`.

**Severity:** score from z, pct vs 50%, abs vs median, +15 if new extreme. Labels: high ≥75, medium ≥50, else low.

**Drivers:** additive metrics only; group vs **group median**; contribution to observed-versus-median delta; require top share ≥ 0.40.

**Time grain:** default **month**. Day/week go through `_granular_anomalies`. Month grain on a non-month period → `period_not_month`.

**Insufficient history:** fewer than 3 usable prior observations → empty `insufficient_history` (or no drafts → `no_anomalies`).

**UI:** Overview only. Studio does not render.

---

## 10. TREND (Studio)

| Topic | Current Studio behavior |
|---|---|
| Primary metric | Default **Total Cost** (`total_cost`). No Studio picker |
| Secondary metric | Requested **`revenue_inr`** on the trend HTTP call. Line trend **strips** it; combo uses it |
| Time grain | API default **`day`**. Day / week / month are supported by the contract (`TREND_GRAINS`) but Studio does not send `grain` or show a control |
| Comparison | Overlay on the line chart when a comparison window exists. Notes state period-offset alignment |
| Aggregation | Per-bucket SQL SUM then `compute_kpis` |
| Chart | Existing `renderTrendChart`, non-interactive, tooltips on |
| API | `GET /analytics/trends?secondary=revenue_inr` + D2 |
| Limitations | No grain/metric UI; comparison not drawn on combo; second trends call is channel breakdown (not this chart) |

---

## 11. EXPLORER / DETAIL (Studio)

Studio is **not** Performance Explorer. It reuses the explorer **payload** with defaults.

| Topic | Actual |
|---|---|
| Primary dimension | `campaign_id` (API default) |
| Secondary dimension | **None** (not requested) |
| Ranking mode | `ranking` (not top/bottom/movers) |
| Movers / top / bottom | Not used |
| Contribution / share | Server `contribution_pct` for additive total_cost |
| Table | Display-only HTML table, server order |
| Sorting | None in UI |
| Drill | None (`data-explorer-drill` absent) |
| CSV / export | **Not** called (`downloadExplorerExport` / `explorer.csv` unused by Studio) |
| Matrix | **Not implemented.** Would need `secondary` / `parent_key`. P3-C4B stopped |

Default limit 25 (`DEFAULT_EXPLORER_LIMIT` in `packages/analytics/dfip_analytics/explorer.py`). Distribution chart visually caps at 10 bars via `drillChartRows`; the detail table shows all returned rows.

---

## 12. NARRATIVE / INSIGHT AREA

**What currently happens:** `studioReservedZone` renders title **Narrative** and description **“Reserved for insights and anomaly copy.”** The body is an empty mark (`aria-hidden="true"`). No fetch, no list, no empty-reason.

| Question | Answer |
|---|---|
| Currently populated? | **No** |
| Reserved/empty? | **Yes** |
| Insight/anomaly payloads already exist? | **Yes**, on Overview and the HTTP APIs |
| Frontend wiring missing? | **Yes** |
| Backend work required? | **No** for a display-only Narrative that calls the existing endpoints |

Do not rebuild insight/anomaly engines. Reuse `getOverviewInsights` / `getOverviewAnomalies` and D2 `params`. Prefer Studio-local rendering (display-only). Do not copy Overview drill/Ask links unless a later bounded task says so.

---

## 13. TESTS

Studio tests are **static source assertions** (read JS/CSS) except `test_p3_c2a_trend_bucket_share.py` (HTTP against in-memory publication store).

### Backend / API

| File | What |
|---|---|
| `tests/test_p3_c2a_trend_bucket_share.py` | `bucket_share` = value / bucket total; omitted without breakdown; zeros stay null |
| Existing D1/D3/D5/D6/D7 suites | Overview/trends/explorer/insights/anomalies contracts (not Studio-specific) |

### Frontend / Studio

| File | Batch |
|---|---|
| `tests/test_p3_a_analytics_studio.py` | Route, nav, D2 reuse, shell, KPI band, non-interactive cards |
| `tests/test_p3_b_studio_trend.py` | Trend zone |
| `tests/test_p3_b_studio_distribution.py` | Distribution bars |
| `tests/test_p3_b_studio_donut.py` | Donut from explorer shares |
| `tests/test_p3_c_studio_stacked.py` | Absolute stacked |
| `tests/test_p3_c2_studio_share_stack.py` | 100% stack from `bucket_share` |
| `tests/test_p3_c3a_studio_secondary.py` | `secondary=revenue_inr` on existing trends call |
| `tests/test_p3_c3_studio_combo.py` | Combo chart |
| `tests/test_p3_c4a_studio_detail.py` | Detail table, still 4 requests |
| `tests/test_p3_d1_studio_states.py` | Loading/empty/error + a11y; **asserts insights/anomalies are not fetched** |

Regression touched by Studio route/nav:

- `tests/test_p6_web.py` — `/client/studio` in nav current-path map
- `tests/test_d1_kpi_overview.py` — nav item presence
- `tests/test_d9_analytics_workspace.py` — Overview still owns `/client/overview` (do not replace)

### Latest known result (this inspection)

```
python -m pytest tests/test_p3_a_analytics_studio.py tests/test_p3_b_studio_trend.py
  tests/test_p3_b_studio_distribution.py tests/test_p3_b_studio_donut.py
  tests/test_p3_c_studio_stacked.py tests/test_p3_c2_studio_share_stack.py
  tests/test_p3_c2a_trend_bucket_share.py tests/test_p3_c3_studio_combo.py
  tests/test_p3_c3a_studio_secondary.py tests/test_p3_c4a_studio_detail.py
  tests/test_p3_d1_studio_states.py tests/test_p6_web.py
  tests/test_d1_kpi_overview.py tests/test_d9_analytics_workspace.py -q --tb=no
```

**114 passed** (2026-09-23). One FastAPI/Starlette `httpx` deprecation warning. Not a live browser test.

### Not verified

- Authenticated browser pass of Studio (login automation was blocked in prior work; unauthenticated module probes only).
- Visual QA of dark/light and 640px with real published data.
- Insight/anomaly rendering on Studio (does not exist).
- Matrix.
- Performance beyond the P3-D2 inspection (4 analytics calls + `loadSession`; full `innerHTML` remount accepted).

---

## 14. GIT STATE

Recorded 2026-09-23 from the live repo.

| | |
|---|---|
| Branch | `master` |
| Latest commit | `22d95630a90dbe39b4da4f68b33cd84ba8edc409` — `fix: restore feature2 grain synchronization in production frontend` (2026-09-22) |
| Staged | **None** |
| Analytics Studio commits on this branch | **None.** Studio exists only in the working tree |

### Uncommitted (Studio-related)

```
 M apps/web/static/css/app.css
 M apps/web/static/js/analytics-state.js
 M apps/web/static/js/app.js
 M apps/web/static/js/components.js
 M apps/web/static/js/views.js
 M packages/api/dfip_api/analytics_service.py
 M packages/api/dfip_api/schemas.py
 M tests/test_d1_kpi_overview.py
 M tests/test_d9_analytics_workspace.py
 M tests/test_p6_web.py
?? tests/test_p3_a_analytics_studio.py
?? tests/test_p3_b_studio_distribution.py
?? tests/test_p3_b_studio_donut.py
?? tests/test_p3_b_studio_trend.py
?? tests/test_p3_c2_studio_share_stack.py
?? tests/test_p3_c2a_trend_bucket_share.py
?? tests/test_p3_c3_studio_combo.py
?? tests/test_p3_c3a_studio_secondary.py
?? tests/test_p3_c4a_studio_detail.py
?? tests/test_p3_c_studio_stacked.py
?? tests/test_p3_d1_studio_states.py
```

Backend delta is small and Studio-enabling: `TrendPoint.bucket_share` + `_fill_trend_bucket_shares` (~22 lines service, ~3 lines schema). Everything else is frontend/tests.

### Unrelated untracked (do not treat as Studio)

- `New folder/`
- `tmp_excel_12m/`
- `tmp_excel_cumulative/`

Do not commit those unless a human explicitly asks.

HEAD history immediately before Studio WIP: grain sync, insight/anomaly grain on **Overview**, explorer CSV, theme toggle — **Overview/Feature-2**, not Studio.

---

## 15. KNOWN ISSUES

| Issue | File | Impact | Reproduction | Recommended action |
|---|---|---|---|---|
| Narrative reserved empty | `views.js` `studioReservedZone` | Insights/anomalies invisible on Studio | Open `/client/studio` | Next task: display-only Narrative from existing APIs |
| Matrix not implemented | Studio explorer call omits `secondary` | No pivot | Inspect `mix.rows` — no `parent_key` | Do not start until a task explicitly requests explorer secondary |
| No grain/metric/dimension Studio controls | `studioParamsFromQuery` | Always cost/day/campaign/channel | Apply filters; charts stay on defaults | Later; do not invent chrome in Narrative task |
| Combo ignores comparison series | `studioComboChart` | Cost/revenue vs prior not drawn | Enable comparison; combo has no overlay | Optional later |
| Share-mix Y labels are 0–1 | `studioShareStackedBarChart` | Less readable than % | View Share mix | Optional CSS/label tweak |
| Distribution caps 10 bars; detail shows 25 | `drillChartRows` vs `mix.rows` | Chart vs table mismatch | >10 campaigns | Documented limitation; do not “fix” unless asked |
| Stacked charts drop ≤0 values | `studioStackedBarChart` | Negatives/zeros omitted | Negative cost rows | Keep unless asked |
| KPI failure empties filter option lists | `overviewFilterBar(data=null)` | Apply still works from URL but pickers empty | Fail overview request | Later: use any successful payload’s `options` |
| Full-page remount on every filter | `renderRoute` + Studio loading shell | Brief skeleton flash; 4 refetches | Change month | Accepted in P3-D2 |
| `test_p3_d1` forbids insights/anomalies fetches | `tests/test_p3_d1_studio_states.py` | Will fail if Narrative is wired without updating tests | Add `getOverviewInsights` | Update those assertions in the Narrative task |
| Authenticated browser Studio not verified | n/a | Visual/a11y gaps possible | Login + `/client/studio` | Required in next implementation task |
| Uncommitted WIP on master | working tree | Easy to lose / mix with other work | `git status` | Human decides commit; do not auto-commit |
| `contributionSharePct` multiplies by 100 | `views.js` | Display only; tests forbid `* 100` **inside** new helpers | Copy-paste into a new helper | Reuse the existing function; do not reimplement |

---

## 16. COMPLETION STATUS

| Feature | Complete | Partial | Not done | Evidence |
|---|---|---|---|---|
| Route `/client/studio` + client access | ✓ | | | `app.js` route `client-studio` |
| Nav item + page title | ✓ | | | `components.js` |
| D2 shared filters + URL state | ✓ | | | `studioHref`, `studioParamsFromQuery`, `queryFromStudioForm` |
| KPI band (8 hero KPIs, display-only) | ✓ | | | `overviewKpiCardsHtml({ interactive: false })` |
| Absolute stacked mix (channel) | ✓ | | | `studioStackedBarZone` + trends `breakdown=channel` |
| 100% share mix | ✓ | | | `bucket_share` + `studioShareStackedZone` |
| Trend (cost by day) | ✓ | | | `studioTrendZone` + `renderTrendChart` |
| Distribution bars + donut | ✓ | | | `studioDistributionZone` |
| Cost & revenue combo | ✓ | | | `secondary=revenue_inr` + `studioComboZone` |
| Detail ranked table | ✓ | | | `studioDetailZone` from explorer rows |
| Per-zone loading/empty/error + a11y labels | ✓ | | | `test_p3_d1_studio_states.py` |
| Dark/light + responsive CSS | ✓ | | | `app.css` Studio tokens/breakpoints |
| Bounded 4-request fetch | ✓ | | | `Promise.allSettled` in `app.js` |
| Narrative | | shell only | | `studioReservedZone` narrative |
| Insights on Studio | | | ✓ | No `getOverviewInsights` in Studio branch |
| Anomalies on Studio | | | ✓ | No `getOverviewAnomalies` in Studio branch |
| Matrix / secondary explorer | | | ✓ | No `ex_secondary`; P3-C4B stopped |
| Studio grain/metric pickers | | | ✓ | Hardcoded defaults |
| Drill / Ask / saved / CSV on Studio | | | ✓ | Intentionally omitted |
| Authenticated browser sign-off | | | ✓ | Not verified this inspection |

No percentages. “Complete” means the accepted P3 batch is in the working tree and covered by the listed tests.

---

## 17. REMAINING WORK

Based only on this repo. Do not invent product.

### NOW

Populate the **Narrative** zone from **existing** `GET /analytics/insights` and `GET /analytics/anomalies`. Display-only. Same D2 `params`. Per-zone loading/empty/error. No new formulas. See §19.

### NEXT

If Narrative lands: tighten empty reasons (`no_material_insights`, `insufficient_history`) and keep Overview insight/anomaly panels unchanged. Only then consider whether Studio should show finding-grain (`day|week|month`) — Overview already has `finding_grain`; Studio currently strips it.

### LATER

- Explorer secondary / matrix (requires a new request or extra explorer params; do not fold into Narrative).
- Studio metric/grain/breakdown controls (would expand URL state; today Apply strips `trend_*` / `ex_*` on purpose).
- Combo comparison overlay.
- Filter options when KPI request fails.

### OPTIONAL

- Studio CSV (Overview/explorer export already exist — do not duplicate unless asked).
- Click-through from Studio to Overview drill (explicitly disabled today).
- In-place Studio refresh (Overview has `refreshOverviewInPlace`; Studio remounts).

---

## 18. DO NOT TOUCH

Unless a later human prompt gives evidence and a bounded task:

- `packages/analytics/dfip_analytics/kpis.py` and `compute_kpis`
- Existing analytics route shapes for overview / trends / explorer / insights / anomalies / drilldown (additive `bucket_share` already shipped in WIP — do not rename/remove)
- Excel / PublishedFacts / Client Report / refreshable workbooks
- Publication pointer, ingest, QA publish
- AuthN/AuthZ, JWT, company bind, RLS
- `/client/overview` D9 workspace behavior (tests assert it is not replaced)
- Admin routes, Reports (`/client`), facts list
- Unrelated Feature-2 grain work already committed
- `New folder/`, `tmp_excel_12m/`, `tmp_excel_cumulative/`
- Microsoft Power BI packages

Do not add JS business-metric math. Do not add a fifth analytics engine. Do not redesign Overview.

---

## 19. EXACT NEXT TASK

### P3-E1 — Studio Narrative from existing insights + anomalies (display-only)

**Why this is next**

It is the only remaining Studio zone that is a reserved shell. Insight and anomaly **APIs and Overview UI already exist**. Matrix is blocked on explorer secondary. Grain/metric pickers would be new chrome. This is the smallest closed loop.

**Likely files**

- `apps/web/static/js/app.js` — add `getOverviewInsights(insightsParamsFromQuery(query, publishedParams({})))` and `getOverviewAnomalies(anomaliesParamsFromQuery(...))` to the existing `Promise.allSettled` (expect 6 jobs). Pass results into `clientStudioView`.
- `apps/web/static/js/views.js` — replace narrative `studioReservedZone` with a Studio zone that lists headlines/explanations; loading/empty/error like other zones. **Do not** wire drill/Ask.
- `apps/web/static/js/analytics-state.js` — reuse existing helpers; Studio Apply still strips `finding_grain` unless the task explicitly keeps it (default API grain is `month` — acceptable).
- `apps/web/static/css/app.css` — only if the list needs Studio-scoped overflow/spacing.
- `tests/test_p3_e1_studio_narrative.py` (new)
- Update `tests/test_p3_d1_studio_states.py` (and any P3 file that asserts insights/anomalies are absent)

**Backend changes required?** **No**, unless a contract bug is proven. Do not edit `insights.py` / `anomalies.py` / KPI engine.

**Acceptance**

- Narrative is no longer an empty reserved mark.
- Studio still uses D2 filters; insights/anomalies honor comparison / insufficient-history empty reasons from the API.
- Display-only: no drill URLs, no Ask, no JS KPI math.
- Overview `/client/overview` insights/anomalies unchanged.
- Request budget documented (6 allSettled; auth errors still throw).
- Focused tests pass; assertions that forbade these fetches are updated.

**Tests to run**

```
python -m pytest tests/test_p3_a_analytics_studio.py tests/test_p3_b_studio_trend.py
  tests/test_p3_b_studio_distribution.py tests/test_p3_b_studio_donut.py
  tests/test_p3_c_studio_stacked.py tests/test_p3_c2_studio_share_stack.py
  tests/test_p3_c2a_trend_bucket_share.py tests/test_p3_c3_studio_combo.py
  tests/test_p3_c3a_studio_secondary.py tests/test_p3_c4a_studio_detail.py
  tests/test_p3_d1_studio_states.py tests/test_p3_e1_studio_narrative.py
  tests/test_p6_web.py -q
```

Then authenticated browser: `/client/studio` Narrative loading → empty reason or list; Overview insights still work.

**Stop** when Narrative is wired and tested. Do not start matrix or metric pickers in the same change.

---

## 20. NEW AGENT START PROMPT

Copy everything in the block below.

```
START HERE — DFIP ANALYTICS STUDIO

You are continuing DFIP Analytics Studio (the Power BI-like canvas inside the website at /client/studio).
This is NOT Microsoft Power BI, PBIX, Desktop, Service, or gateway work.

1. Read documentation/ANALYTICS_STUDIO_CURRENT_HANDOFF.md first. Treat it as the snapshot of where the previous agent stopped. Then inspect the live repo; repository truth wins if they diverge.
2. Inspect git status, branch, and HEAD. Studio work is expected to be uncommitted on master. Do not commit unless the user asks. Do not touch New folder/, tmp_excel_12m/, or tmp_excel_cumulative/.
3. Before editing, verify current Studio in code (and in the browser if the app is reachable): route /client/studio, four analytics requests, reserved Narrative zone.
4. Preserve existing APIs/contracts: GET /api/v1/analytics/overview, /trends, /explorer, /insights, /anomalies. Reuse payloads. Do not duplicate KPI math in JavaScript. Do not change Excel, publication, auth, Overview redesign, or unrelated routes.
5. Implement ONLY the single next bounded task from the handoff §19:
   P3-E1 — Studio Narrative from existing insights + anomalies, display-only.
6. Run the focused pytest list in the handoff. Update tests that currently assert Studio does not fetch insights/anomalies.
7. Browser-validate Narrative (loading, empty reason, populated list) and confirm Overview insights/anomalies still work.
8. Report: files changed, tests run and results, what you verified in the browser, what you did not verify.
9. Stop after P3-E1. Do not start matrix, grain/metric pickers, or extra features.

Constraints from prior Studio batches (still in force):
- No commit/deploy unless asked
- No new analytics engine
- Shared D2 filters only
- DFIP theme tokens; dark/light; responsive
- No JWT inject/store/print
- Bounded request count; Promise.allSettled; one zone failure must not unload the others
```

---

## Quick reference — Studio request map

| Zone | Request | Extra params | Interactive |
|---|---|---|---|
| Filters + KPI | `GET /analytics/overview` | D2 | Filters yes; KPIs no |
| Trend | `GET /analytics/trends` | `secondary=revenue_inr` | No |
| Combo | same trend response | — | No |
| Stacked mix | `GET /analytics/trends` | `breakdown=channel` | No |
| Share mix | same stack response | uses `bucket_share` | No |
| Distribution | `GET /analytics/explorer` | D2 defaults | No |
| Detail | same explorer response | — | No |
| Narrative | none today | — | — |
