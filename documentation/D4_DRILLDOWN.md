# D4 — KPI / Chart Drilldown

D4 turns D1 KPI cards and supported D3 primary trend points into context-preserving analytical entry points. It does not add a Performance Explorer, insights, anomalies, AI, exports, or a standalone report page.

D1 KPIs, D2 URL filters, and D3 trends remain the shared analytical state. Changing month, channel, campaign, or Clear All updates KPIs, the trend, and any open drill.

## Files

### New
- `packages/analytics/dfip_analytics/drill.py` — origin/dimension/parent/depth rules, trend-slice windows
- `tests/test_d4_drilldown.py`
- `documentation/D4_DRILLDOWN.md` (this file)

### Updated
- publication stores (in-memory + Postgres) — `list_published_history_groups`
- `packages/api/dfip_api/ports.py`, `analytics_service.py`, `analytics_routes.py`, `schemas.py`
- `packages/analytics/dfip_analytics/__init__.py`
- `packages/web/dfip_web/api_client.py`, `apps/web/static/js/api-client.js`
- `apps/web/static/js/analytics-state.js` — drill URL keys; filter/trend submit preserves them
- `apps/web/static/js/app.js`, `views.js`, `components.js`, `trend-chart.js`, `css/app.css`
- `tests/test_d3_dynamic_trends.py`, `tests/test_p6_web.py`

Unchanged by design: `/admin` Dashboard, `/client` Reports, D1 eight-KPI semantics, D2 allowlisted filters, D3 metric/dimension registries.

## Query contract

`GET /api/v1/analytics/drilldown`

Same JWT + D2 filter query parameters as Overview/trends, plus:

| Param | Default | Notes |
| --- | --- | --- |
| `origin` | `kpi` | `kpi` or `trend` |
| `metric` | `total_cost` | D1/D3 registry key only |
| `dimension` | `campaign_id` | Allowlisted drill dimension, including terminal `day` |
| `parent` | omitted | Repeated `dimension:value`. Max 2 parents (depth 3). |
| `slice_grain` | `day` when `origin=trend` | Required with `slice_bucket` for trend origin |
| `slice_bucket` | omitted | Bucket start date of the selected trend point |

Company scope is derived from authentication. Query `client_id` cannot widen a bound token (403). Arbitrary field names are not concatenated into SQL. Parent values are validated against published history under the inherited D2 filters.

The handler:

1. Resolves the same D2 period/comparison/dimension scope as Overview (`AnalyticsService._resolve_scope`).
2. Validates origin, metric, dimension, parents, and depth against `drill.py`.
3. For `origin=trend`, intersects the D2 window with the selected ISO bucket. Comparison uses the **same bucket index** in the D2 comparison window (D3 period-relative offset). If that index does not exist, comparison is omitted (`comparison_not_available_for_slice`).
4. Applies parent values as additional equals-filters (must already be in any inherited D2 multi-select).
5. Aggregates additive measures in PostgreSQL (or in-memory newest-wins) with `GROUP BY` dimension only.
6. Applies `compute_kpis(namespace="client")` **per group** (SUM then ratio).
7. Returns ranked groups, not fact rows.

Unsupported combinations are **422** `VALIDATION_ERROR`. Persistence failures are **503**. Missing auth is **401**. Bound-token widen / unbound publisher is **403**.

### Response shape

- D2 `applied`, `dropped_filters` (original Overview period/filters, including when the result is a trend slice)
- `period` — query window actually aggregated (slice window when origin is trend)
- `comparison` — D2/D3 comparison window after slice intersection; `available=false` with an explicit reason when none
- `selection`: origin, metric, dimension, depth, `max_depth`, slice, parents, `next_dimensions`, `can_go_deeper`
- `metric`: registry label, kind, additive flag, definition
- `truncated` / `truncated_message` when more than 50 groups exist
- `empty`, `result_count`
- `rows[]`: `rank`, `key`, `label`, `value`, `prior_value`, `delta`, `delta_pct`, `contribution_pct`, numerator/denominator, `grain_row_count`, `drillable`
- catalog echo: `dimensions`

Money, ROAS, and rates are JSON strings. Counts are integers. Divide-by-zero and NULL operands are JSON `null`, never `0`.

`delta_pct` is `(current − prior) / prior` at rate scale. `contribution_pct` is the group's **rank-measure volume** over all groups before the 50-row cap, so `Other` is honest.

## Drillable metrics

The eight D1/D3 hero keys. Formulas stay in `kpis.py`. D4 does not invent metrics.

| Key | Aggregation |
| --- | --- |
| `total_cost` | SUM |
| `revenue_inr` | SUM |
| `overall_roas` | revenue/cost after SUM |
| `delivered` | SUM |
| `unique_clicks` | SUM |
| `unique_conversions` | SUM |
| `delivery_rate` | delivered/sent after SUM |
| `ctr_del_to_clicks` | clicks/delivered after SUM |

## Drillable dimensions

Allowlist from the D3 registry plus terminal `day`:

| Key | Label | Parent filter | Notes |
| --- | --- | --- | --- |
| `campaign_id` | Campaign | yes | Default first breakdown |
| `channel` | Channel | yes | |
| `filter_logic_1` | Filter Logic 1 | yes | |
| `filter_logic_1_group` | Filter Logic 1 group | yes | |
| `day` | Day | **no** | Terminal grouping. Cannot be a parent. |

Suggested path: KPI → Campaign → Channel → Day. The user may start with another allowlisted dimension. Product / Brand / Platform / Variation / Template are rejected.

## Allowed drill depth

`MAX_DRILL_DEPTH = 3` (current dimension + at most two parents). Unlimited recursion is not implemented. `Other` (`__other__`) is not drillable. When `next_dimensions` is empty, the UI shows that deeper grouping is unavailable.

`MAX_DRILL_ROWS = 50` plus an `Other` remainder. This is driver discovery, not a ranking explorer.

## Context model

URL query is the analytical state. Drill keys:

| URL key | API |
| --- | --- |
| `drill` | `origin` (`kpi` \| `trend`) |
| `drill_metric` | `metric` (omitted when `total_cost`) |
| `drill_dimension` | `dimension` (omitted when `campaign_id`) |
| `drill_parent` | repeated `parent` |
| `drill_slice` | `slice_bucket` |
| `drill_slice_grain` | `slice_grain` (omitted when `day`) |

D2 keys and D3 `trend_*` keys stay on the same URL. Filter/trend form submit copies drill keys, so an open drill inherits month/channel/campaign changes. Clear All (`/client/overview`) drops every query key, including drill.

Breadcrumbs, Back, and Return to Overview are derived from that URL. Back pops one parent (or closes the panel at depth 1). Escape and the backdrop also close the panel without resetting D2 filters.

## Comparison handling

Same D2/D3 definitions. Groups are matched by **dimension value**, not by rank. A current group with no comparison match shows `prior_value` / `delta` as null (UI: n/a). `compare=none` is an explicit no-comparison state.

Trend slices compare the same bucket index in the prior window. If the prior window is shorter, comparison is unavailable for that slice.

## Ranking / contribution

Rows are ordered by rank-measure volume (additive metric value, or denominator volume for ratios), then label. Rank 1 is the largest. Contribution is that volume / total volume of **all** groups before the cap.

## Security

- JWT company scope is authoritative. Frontend company selection is not the boundary.
- Bound `client_id` cannot be widened by query `client_id`.
- Unknown dimension/metric keys are 422, not SQL.
- Parent values must exist in the current scoped published history.
- Publisher/admin must select a company; unbound is 403.
- Inactive companies are refused by `require_company_active`.
- Historical newest-wins isolation is unchanged (`publication_history_grain`).

## UI

Overview modal/panel (`overviewDrillPanel`): metric, period, comparison, inherited filters, selected breakdown, ranked table. KPI cards expose **View details**. Primary trend points/bars (not `Other`, not comparison dashes, not secondary-only series) set `origin=trend` plus slice/parent.

States: page loading, drill loading, empty, error, unsupported (422), unauthorized (403 on the drill request). Dashboard and Reports routes are unchanged.

## Performance

Largest local tenant (`e7450265-3799-4569-b488-405319a4d3c6`), Oct 2025 (29,129 history-grain rows) unless noted. Plans use `Bitmap Index Scan` / `Index Scan` on `publication_history_grain_page_idx`. No sequential scan of the table. No new index, cache, materialized view, or warehouse. D4 is `GROUP BY` dimension only (lighter than D3 campaign×day). June comparison is slower because that month has more rows (48,890) and some heap reads, not because of a missing index.

| Workload | SQL `EXPLAIN ANALYZE` | HTTP `GET /analytics/drilldown` (includes D2 comparison query + per-group KPIs) |
| --- | --- | --- |
| KPI → campaign (Oct) | 85 ms | 426 ms (2,495 groups, truncated to 50+Other) |
| KPI → channel | 32 ms | 346 ms (6 groups) |
| Trend slice → campaign (2025-10-08) | 10 ms | 311 ms |
| Nested campaign → channel | 2 ms (fixture campaign) / 334 ms live first campaign | 1 channel group |
| SMS filter → campaign | 29 ms | 318 ms |
| Comparison June campaign | 361 ms | included in KPI campaign HTTP above |

Python ranking of ~2.5k campaign groups after SQL is acceptable at this scale. Do not add indexes from this evidence.

See `tmp/d4_drill_explain.json` after `python tmp/d4_drill_explain.py` and HTTP timings from `python tmp/d4_live_drill.py`.

## Tests

`tests/test_d4_drilldown.py` plus D1/D2/D3 regression:

1. KPI opens campaign drill
2. Valid channel dimension
3. Invalid dimension (`brand`) 422
4. Invalid metric (`orders`) and repeated parent/dimension 422
5. Invalid parent value 422
6. Inherited D2 month
7. Inherited D2 channel filter
8. Comparison context + unmatched prior is null
9. Nested campaign → channel
10. Depth 4 rejected
11. SPA Back / Clear All / filter-submit preserves or clears drill keys
12. Ratio-after-SUM delivery rate
13. Absolute delta
14. Contribution 50/50 on equal cost
15. Empty July range
16. Store error 503
17. Missing JWT 401
18. Tenant isolation + widen 403 + publisher selected company
19. Corrected-month newest-wins
20. D1 eight-KPI regression
21. D2 default + SMS filter regression
22. D3 month trend regression

SPA: View details, drill panel, trend points; Dashboard and Reports nav labels unchanged; no Insights / Anomaly copy. Performance Explorer is D5 (`documentation/D5_PERFORMANCE_EXPLORER.md`).

## Live path

`tmp/d4_live_drill.py` via `create_app()` + TestClient (same method as D1–D3; does not print passwords or metric values).

CLIENT `demo-client`: Overview 8 KPIs; KPI campaign drill; channel drill; nested campaign→channel; trend-slice campaign; invalid dimension 422; other `client_id` 403.

PUBLISHER: select company → same drill contract scoped to that company; `/api/v1/ops/ready` and current publication still 200.

## Known limitations

- Maximum depth is 3. There is no recursive explorer.
- Top 50 + Other is a cap, not a top/bottom mover workspace (D5).
- Trend drill is primary-series points/bars only.
- Comparison for slices is index-aligned, matching D3.
- Changing the breakdown dropdown at a nested level drops later parents that would repeat the new dimension.
- The drill surface is a modal on Overview, not a saved analysis.

## D5 Performance Explorer

Implemented. See `documentation/D5_PERFORMANCE_EXPLORER.md`.

## D6 prerequisites (not implemented)

- Deterministic Insights, anomaly detection, Contextual Ask / AI
- Exports and saved analyses
- Filter Logic 2 / AMC / product-cat slicers
- Deeper than 2-dimension explorer pivoting
