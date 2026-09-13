# D3 — Dynamic Trends

D3 adds a reusable trend builder on `/client/overview`. Rankings explorer, insights, anomalies, AI, and exports remain out of scope. KPI/chart drilldown is D4.

D1 KPI cards and D2 filters remain the shared analytical state. Changing month, channel, campaign, or Clear All updates both KPIs and the trend.

## Files

### New
- `packages/analytics/dfip_analytics/trends.py` — metric/dimension registry, ISO week helpers, combination rules
- `apps/web/static/js/trend-chart.js` — small SVG line/bar renderer (no Chart.js/D3)
- `tests/test_d3_dynamic_trends.py`
- `documentation/D3_DYNAMIC_TRENDS.md` (this file)

### Updated
- publication stores (in-memory + Postgres) — `list_published_history_series`
- `packages/api/dfip_api/ports.py`, `analytics_service.py`, `analytics_routes.py`, `schemas.py`
- `packages/analytics/dfip_analytics/__init__.py`
- `packages/web/dfip_web/api_client.py`, `apps/web/static/js/api-client.js`
- `apps/web/static/js/analytics-state.js` — trend URL keys; filter submit preserves trend keys
- `apps/web/static/js/app.js`, `views.js`, `css/app.css`
- `tests/test_d2_global_filters.py`, `tests/test_p6_web.py`

Unchanged by design: `/admin` Dashboard, `/client` Reports, D1 eight-KPI semantics, D2 allowlisted filters.

## Trend query contract

`GET /api/v1/analytics/trends`

Same JWT + D2 filter query parameters as Overview, plus:

| Param | Default | Notes |
| --- | --- | --- |
| `metric` | `total_cost` | Must be a registry primary key |
| `secondary` | omitted | Optional; must differ from primary; cannot combine with `breakdown` or `include_metric` |
| `grain` | `day` | `day` \| `week` \| `month` |
| `breakdown` | omitted | Allowlisted dimension only |
| `include_metric` | omitted | Optional repeated allowlisted keys (max 8 unique). Company-total extra `points[].values`. Cannot combine with `breakdown` or `secondary`. |

Company scope is derived from authentication. Query `client_id` cannot widen a bound token (403). Arbitrary field names are not concatenated into SQL.

The handler:

1. Resolves the same D2 period/comparison/dimension scope as Overview (`AnalyticsService._resolve_scope`).
2. Validates metric, secondary, grain, and breakdown against the registry.
3. Aggregates additive measures in PostgreSQL (or in-memory newest-wins) with `GROUP BY` bucket [+ dimension].
4. Applies `compute_kpis(namespace="client")` **per bucket** (SUM then ratio).
5. Returns series points, not fact rows.

### Response shape

- D2 `applied`, `dropped_filters`, `period`, `comparison`
- `selection`: metric, secondary, grain, breakdown, `dual_axis`, `chart` (`line` or `bar`)
- `metric` / `secondary`: registry labels, kind, additive flag, definition
- `comparison_shown` and `comparison_omitted_reason`
- `truncated` / `truncated_message` when a breakdown is capped
- `empty`
- `series[]`: `key`, `label`, `points[]` with `bucket`, `bucket_label`, `value`, optional secondary/comparison values, numerator/denominator, `grain_row_count`, and optional `values` when `include_metric` was sent
- catalog echo: `metrics`, `dimensions`, `grains` (`metrics` remains the registry catalog, not the sparkline map)

### Optional `include_metric`

When omitted, the response is unchanged: `points[].value` is the primary metric and `values` is absent.

When present (repeated query keys, max 8 unique, `TREND_METRICS` only):

- Unknown key → 422
- Duplicates are dropped, first-seen order kept
- `breakdown` or `secondary` → 422
- Same SQL `GROUP BY` bucket and one `compute_kpis(..., namespace="client")` per bucket
- `points[].values` maps requested keys to the same serialized numbers eight sequential single-metric GETs would return
- Missing buckets and divide-by-zero stay JSON `null` (never 0)
- `TrendResponse.metrics` is still the catalog list

Overview KPI sparklines use this mode as a **second** D3 GET (not the Trends panel request): D2 filters, no breakdown, no secondary, request-local `compare=none` (not written to the Overview URL), `grain=day` for month/range and `grain=month` for `all_history`. Failure omits sparklines; D1 cards still render.

Unsupported combinations are **422** `VALIDATION_ERROR` (the Overview KPI request is unaffected). Persistence failures are **503**.

## Metric registry

D3 does **not** create a second formula system. `TREND_METRICS` is a projection of `HERO_KPIS` + `kpis.py`.

| Key | Label | Kind | Aggregation | Additive | Primary | Secondary |
| --- | --- | --- | --- | --- | --- | --- |
| `total_cost` | Total Cost | money | SUM | yes | yes | yes |
| `revenue_inr` | Revenue | money | SUM | yes | yes | yes |
| `overall_roas` | Overall ROAS | roas | revenue/cost after SUM | no | yes | yes |
| `delivered` | Delivered | count | SUM | yes | yes | yes |
| `unique_clicks` | Unique Clicks | count | SUM | yes | yes | yes |
| `unique_conversions` | Unique Conversions | count | SUM | yes | yes | yes |
| `delivery_rate` | Delivery Rate | rate | delivered/sent after SUM | no | yes | yes |
| `ctr_del_to_clicks` | CTR (Delivered → Clicks) | rate | clicks/delivered after SUM | no | yes | yes |

All eight support Day/Week/Month. All eight support the D3 dimension allowlist.

Zero/NULL: same as D1. Divide-by-zero and NULL operands → JSON `null`, never `0`. Missing days in a filled axis are `null`, not zero.

Formatting: money/ROAS/rates are decimal strings; counts are integers.

Comparison: period-relative overlay of the D2 comparison window (see below). Breakdown ranking for the series cap uses additive volume, or the **denominator** for ratio metrics.

Not in the registry (D0 unsupported or later phases): Orders, CPC, ACOS, brand, ASIN, variation, Filter Logic 2, AMC slicers.

## Dimension registry

Allowlisted only. Clients cannot pass a SQL field name.

| Key | Label | Source | Grouping | Filtered by D2 |
| --- | --- | --- | --- | --- |
| `campaign_id` | Campaign | `campaign_id` (`campaign_name` for label) | yes | yes |
| `channel` | Channel | `channel` | yes | yes |
| `filter_logic_1` | Filter Logic 1 | `filter_logic_1` | yes | yes |
| `filter_logic_1_group` | Filter Logic 1 group | `filter_logic_1_group` | yes | yes |

Blank/NULL dimension values display as `(blank)`.

When a breakdown would explode (campaign × day), the query keeps the **8** largest series by rank measure and folds the remainder into `Other` (`truncated=true`). This is visualization safety, not a D5 ranking explorer.

## Time grains

| Grain | Semantics |
| --- | --- |
| Day | Native `day` |
| Week | ISO-8601 week starting Monday. Postgres: `date_trunc('week', day::timestamp)::date`. Python: `day - timedelta(days=day.weekday())`. |
| Month | `month_start` from `Day` (`date_trunc('month', day)`) |

Partial weeks at a period edge are included and labelled with that week's Monday. Week numbers are not invented beyond ISO-8601.

## Supported and unsupported combinations

Supported:

- Primary only, any grain
- Primary + secondary when keys differ
- Dual axis only when the two metrics have **different kinds** (`money` vs `count`/`rate`/`roas`). Same kind shares one axis even if magnitudes differ.
- Breakdown without secondary
- Bar chart when a breakdown is selected and the period has a single time bucket; otherwise line

Unsupported (422, not a silent wrong chart):

- Unknown metric (`orders`, Amazon names, …)
- Unknown dimension (`variation_id`, arbitrary fields)
- Primary == secondary
- Secondary **and** breakdown together
- Grain other than day/week/month
- Metric not in the D1 hero set

Comparison with breakdown is **not** 422: the comparison window is still returned, but `comparison_shown=false` and `comparison_omitted_reason=comparison_not_shown_with_breakdown`.

If D2 comparison is off or not applicable, the trend shows an explicit no-comparison state. Overlay uses **period-relative bucket index** (day 0 of current ↔ day 0 of comparison). No year-over-year. Unequal window lengths leave extra current buckets without a comparison point.

## Chart / UX

Section **Dynamic Trends** on `/client/overview` (same page). Controls: primary, optional secondary, Day/Week/Month, optional breakdown. URL keys: `trend_metric`, `trend_secondary`, `trend_grain`, `trend_breakdown`. Defaults are omitted from the URL.

States: loading (page load), empty, error (non-422), unsupported (422, KPI cards still render), truncated breakdown note, comparison shown/hidden copy.

Chart points and bars for the **primary** series (except `Other`) open a D4 drill for that bucket. Comparison dashed lines and secondary-axis series do not. See `documentation/D4_DRILLDOWN.md`.

Clear All (`/client/overview`) restores D1 default filters **and** default trend (Total Cost, day, no secondary, no breakdown). Explorer `ex_*` keys are also cleared.

## D5 Performance Explorer

Implemented in `documentation/D5_PERFORMANCE_EXPLORER.md`. Ranking, top/bottom, movers, and additive contribution reuse this D2/D3/D4 state plus allowlisted `GET /api/v1/analytics/explorer`.

## Performance

Largest local tenant, Oct 2025, 29,129 history-grain rows. Plans use `Bitmap Index Scan` on `publication_history_grain_page_idx`. No sequential scan of the table. No new index, cache, materialized view, or warehouse.

| Workload | SQL `EXPLAIN ANALYZE` | HTTP `GET /analytics/trends` (includes D2 comparison query + per-bucket KPIs) |
| --- | --- | --- |
| Day, no breakdown | 47 ms | 307 ms |
| Week, no breakdown | 44 ms | 298 ms |
| Month, no breakdown | 44 ms | 334 ms |
| Channel breakdown (month) | 122 ms | 370 ms |
| Campaign breakdown (day + top 8 + Other) | 291 ms | — |
| Campaign breakdown (month, truncated 8+Other) | — | 336 ms |
| Channel filter SMS + month | 19 ms | 397 ms |

Campaign×day without a series cap would be ~2.5k campaigns × ~16 days. The cap is required. The top-N join is a nested loop over 8 keys; acceptable at this scale. Do not add indexes from this evidence.

## Tests

`tests/test_d3_dynamic_trends.py` plus D1/D2 regression:

1. Primary metric only (default Total Cost / day)
2. Primary + secondary (dual axis)
3. Day grain
4. Week grain (ISO Monday, `2025-W41`)
5. Month grain
6. Campaign breakdown
7. Channel breakdown
8. D2 filter state applied (KPI and trend match)
9. Channel + campaign + Filter Logic 1 together
10. Invalid metric 422
11. Invalid dimension 422
12. Secondary + breakdown 422
13. Identical metric pair 422
14. `compare=none`
15. Auto comparison overlay
16. Empty range (Jul inside published bounds, no facts)
17. Store error 503
18. Tenant isolation + `client_id` cannot widen + publisher selected company
19. Corrected-month newest-wins
20. Ratio after SUM (delivery rate ≠ average of daily rates)
21. Percentage / ROAS after SUM
22. D1 eight-KPI default regression
23. D2 default + channel filter regression

SPA: trend controls on Overview; Dashboard and Reports nav labels unchanged; no ranking/anomaly copy in the trend section.

## Live path

`tmp/d3_live_trends.py` via `create_app()` + TestClient (same method as D1/D2; does not print passwords or metric values).

CLIENT `demo-client`: Overview 8 KPIs `is_default=true`; default day trend 31 points; week 5 / month 1; campaign month truncated 9 series; channel 6 series; invalid metric 422; other `client_id` 403.

PUBLISHER: select company → same trend contract scoped to that company; `/api/v1/ops/ready` and current publication still 200.

## D4 drilldown

Implemented in `documentation/D4_DRILLDOWN.md`. KPI cards and supported primary trend points reuse this D2/D3 state plus allowlisted `GET /api/v1/analytics/drilldown`.

## Limitations

- Week is ISO Monday, including partial weeks at range edges.
- Comparison overlay is index-aligned, not calendar-day-aligned across unequal months (31 vs 30).
- Dual-axis is kind-based only; two money metrics with different magnitudes share one axis on purpose.
- Secondary and breakdown cannot be combined in D3.
- Breakdown comparison overlay is omitted.
- Campaign breakdown shows top 8 + Other; visible series do not each equal the KPI total unless `truncated` is false.
- Frontend chart is a small SVG helper, not a chart library.

## Generate Trend (Ask)

Natural-language chart requests such as “Show revenue and ROAS by day” are Ask intent `generate_trend`. Gemini (when configured) returns only a bounded selection object (`metric`, optional `secondary`, `grain`, optional `breakdown`, `compare` `omit`|`none`). DFIP validates that object with `parse_generated_trend_selection` → existing `parse_trend_metric` / `parse_trend_grain` / `parse_breakdown` / `validate_trend_selection`, then calls the existing `GET /analytics/trends` path. The model does not calculate series. Provider-off uses the same allowlisted text parser and never calls Gemini. Date filters stay on the current D2 Ask/Overview state.
