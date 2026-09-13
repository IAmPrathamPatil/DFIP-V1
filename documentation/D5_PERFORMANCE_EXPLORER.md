# D5 — Performance Explorer

D5 adds a reusable Performance Explorer on `/client/overview`. It ranks allowlisted dimensions for a selected D1/D3 metric, with top/bottom limits, movers, and share-of-total contribution where the metric is additive. It does not add anomaly detection, AI, exports, or a saved workspace. Insights cards are D6.

D1 KPIs, D2 URL filters, D3 trends, and D4 drilldown remain the shared analytical state. Changing month, channel, campaign, comparison, or Clear All updates KPIs, the trend, an open drill, and the explorer together.

## Files

### New
- `packages/analytics/dfip_analytics/explorer.py` — ranking modes, limits, contribution validity, D4 drill-target tokens
- `tests/test_d5_performance_explorer.py`
- `documentation/D5_PERFORMANCE_EXPLORER.md` (this file)
- `tmp/d5_live_explorer.py`, `tmp/d5_explorer_explain.py` (operator live/EXPLAIN helpers; gitignored)

### Updated
- publication stores (in-memory + Postgres) — `list_published_history_pairs` (D4 `list_published_history_groups` unchanged)
- `packages/api/dfip_api/ports.py`, `analytics_service.py`, `analytics_routes.py`, `schemas.py`
- `packages/analytics/dfip_analytics/__init__.py`
- `packages/web/dfip_web/api_client.py`, `apps/web/static/js/api-client.js`
- `apps/web/static/js/analytics-state.js` — `ex_*` URL keys; filter/trend submit preserves them
- `apps/web/static/js/app.js`, `views.js`, `css/app.css`
- `tests/test_d3_dynamic_trends.py`, `tests/test_d4_drilldown.py`, `tests/test_p6_web.py`

Unchanged by design: `/admin` Dashboard, `/client` Reports, D1 eight-KPI semantics, D2 allowlisted filters, D3 metric/dimension registries, D4 drill contract (including Top 50 + Other).

## Query contract

`GET /api/v1/analytics/explorer`

Same JWT + D2 filter query parameters as Overview/trends/drilldown, plus:

| Param | Default | Notes |
| --- | --- | --- |
| `metric` | `total_cost` | D1/D3 registry key only |
| `dimension` | `campaign_id` | Allowlisted explorer dimension, including terminal `day` |
| `secondary` | omitted | Optional second allowlisted dimension. Must differ from primary. Depth 2 only. Day cannot have a secondary. |
| `mode` | `ranking` | `ranking` \| `top` \| `bottom` \| `movers` |
| `direction` | `desc` (`asc` for bottom / movers-down) | `asc` \| `desc` |
| `sort` | `value` (`delta` for movers) | `value` \| `delta` \| `delta_pct` \| `contribution` |
| `limit` | 25 ranking / 10 top, bottom, movers | Integer 1–50 |
| `mover` | `up` when `mode=movers` | `up` \| `down` |
| `contribution` | `auto` | `auto` \| `share` \| `none`. `share` is rejected for ratios. |
| `min_value` | omitted | Optional minimum current metric value |
| `min_contribution` | omitted | Optional minimum share-of-total. Additive metrics only. |

Company scope is derived from authentication. Query `client_id` cannot widen a bound token (403). Arbitrary field names are not concatenated into SQL. Dimension keys are allowlisted (`campaign_id`, `channel`, `filter_logic_1`, `filter_logic_1_group`, `day`).

The handler:

1. Resolves the same D2 period/comparison/dimension scope as Overview (`AnalyticsService._resolve_scope`).
2. Validates metric, dimensions, mode, sort, direction, limit, contribution, and thresholds against `explorer.py`.
3. Aggregates additive measures in PostgreSQL (or in-memory newest-wins) with `GROUP BY` primary [+ secondary].
4. Applies `compute_kpis(namespace="client")` **per group** (SUM then ratio).
5. Ranks in the API after aggregation. Returns ranked groups, not fact rows. There is **no Other remainder** (that remains D4).

Unsupported combinations are **422** `VALIDATION_ERROR`. Persistence failures are **503**. Missing auth is **401**. Bound-token widen / unbound publisher is **403**.

### Response shape

- D2 `applied`, `dropped_filters`, `period`, `comparison`
- `selection`: metric, dimension, secondary, mode, direction, sort, limit, mover, contribution mode, `contribution_supported`, thresholds, `max_depth=2`, `truncated`
- `metric`: registry label, kind, additive flag, definition
- `truncated` / `truncated_message` when more groups exist than `limit`
- `empty`, `result_count` (groups after thresholds/mode filters, before limit)
- `rows[]`: `rank`, `key`, `label`, optional `parent_key`/`parent_label`, `value`, `prior_value`, `delta`, `delta_pct`, `contribution_pct` / `contribution_amount` when valid, numerator/denominator, `grain_row_count`, `drillable`, `drill_dimension`, `drill_parents`
- catalog echo: `metrics`, `dimensions`, `modes`

Money, ROAS, and rates are JSON strings. Counts are integers. Divide-by-zero and NULL operands are JSON `null`, never `0`. Missing comparison values are `null`, never fabricated zeros.

`delta_pct` is `(current − prior) / prior` at rate scale, identical to D1–D4.

## Metric support

The eight D1/D3 hero keys. Formulas stay in `kpis.py`. D5 does not invent CPA/CPC/CPM/CPO or other unsupported metrics.

| Key | Aggregation | Additive | Contribution share | Efficiency ranking |
| --- | --- | --- | --- | --- |
| `total_cost` | SUM | yes | yes | — |
| `revenue_inr` | SUM | yes | yes | — |
| `overall_roas` | revenue/cost after SUM | no | no | yes |
| `delivered` | SUM | yes | yes | — |
| `unique_clicks` | SUM | yes | yes | — |
| `unique_conversions` | SUM | yes | yes | — |
| `delivery_rate` | delivered/sent after SUM | no | no | yes |
| `ctr_del_to_clicks` | clicks/delivered after SUM | no | no | yes |

Zero/NULL: same as D1. Ratios aggregate numerator and denominator first, then divide. Row-level ratios are never averaged.

## Dimensions

Same allowlist as D4: Campaign, Channel, Filter Logic 1, Filter Logic 1 group, and Day. Product, Brand, Platform, Variation, Template, and other reference fields are rejected.

## Ranking modes

### Ranking

All groups in the filtered window, after optional `min_value` / `min_contribution`, sorted by `sort` + `direction`, then truncated to `limit`. Default sort is current metric value descending.

### Top / bottom

Always rank by **current metric value**. Top is descending; bottom is ascending with nulls last. An explicit `limit` is required by the contract (default 10, max 50). Unbounded result sets are not returned.

Clicking a table column header switches the URL to ranking mode so the requested sort is honored.

### Movers

A row is a mover only when:

1. D2 comparison is available (`comparison.available=true`)
2. Both current and prior metric values are non-null
3. Absolute delta is strictly positive (`mover=up`) or strictly negative (`mover=down`)

`mode=movers` without a valid comparison period is **422** (`Movers require a valid comparison period.`). Rows with no prior value are omitted, not labeled as movers with a zero delta.

Default sort is absolute `delta`. `delta_pct` is allowed. Up defaults to descending (largest increase first). Down defaults to ascending (most negative first).

### Contribution

For additive metrics:

- `contribution_amount` = the group's metric value
- `contribution_pct` = group metric / **canonical window total** (the same D1 `_sum` + `compute_kpis` total as the Overview KPI for that filter/period)

For ratios/ROAS: `contribution_supported=false`. Share-of-total is not calculated. `contribution=share` is **422**. A contribution threshold on a ratio is **422**.

`contribution=none` hides share fields even for additive metrics.

### Efficiency

ROAS, CTR, and Delivery Rate are ranked with the same contract as other registry metrics. They are not a separate endpoint. Unsupported efficiency metrics are not exposed.

## Secondary dimension

Controlled depth **2**: primary → one optional secondary (example: Campaign → Channel). This is not a pivot cube. Deeper multidimensional exploration stays out of D5 (D4 nested drill remains the path for a third grouping).

Day is terminal: it cannot take a secondary breakdown.

## Comparison

Inherited D2 comparison only. D5 does not invent a comparison period. When comparison is off or unavailable, ranking still works and prior/delta fields are `null`.

## Thresholds

Deterministic user-facing filters only:

- `min_value` — drop rows whose current metric is null or below the threshold
- `min_contribution` — drop rows whose share is null or below the threshold (additive only)
- `limit` — top N / bottom N / ranking / mover cap
- mode top vs bottom

Anomaly thresholds, baselines, and severity belong to D7.

## D4 integration

Explorer rows that have a D4-compatible next dimension include `drillable=true`, `drill_dimension`, and `drill_parents` (`dimension:value` tokens). The Overview table links reuse `withDrill` (origin `kpi`). Metric, period, comparison, D2 filters, company scope, and the selected dimension context are preserved. Closing the drill returns to Overview with explorer URL keys intact.

Explorer ranking does **not** use D4's Top 50 + Other remainder.

## Performance

Largest local tenant (`e7450265-3799-4569-b488-405319a4d3c6`), Oct 2025 unless noted. Group queries use the same `publication_history_grain` allowlisted `GROUP BY` as D4. Secondary breakdown adds one extra grouping column. Plans did not sequential-scan the table. No warehouse, materialized view, cache, or new index was added.

Practical result cap: default 25 (ranking) / 10 (top, bottom, movers); maximum 50. Ranking ~2.5k campaign groups in Python after SQL is acceptable at this scale (same order as D4). Do not add indexes from this evidence.

| Workload | SQL `EXPLAIN ANALYZE` | HTTP `GET /analytics/explorer` (includes D2 comparison query + per-group KPIs) |
| --- | --- | --- |
| Campaign ranking (Oct, limit 25 of 2,495) | 203 ms | 587 ms |
| Channel ranking | 28 ms | 316 ms |
| Top 10 campaigns | (same campaign GROUP BY) | 607 ms |
| Bottom 10 campaigns | (same campaign GROUP BY) | 747 ms |
| Movers up | (current + June comparison GROUP BY) | 529 ms |
| Movers down | (current + June comparison GROUP BY) | 579 ms |
| Contribution (additive cost) | (same campaign GROUP BY) | 570 ms |
| Efficiency ROAS | (same campaign GROUP BY) | 653 ms |
| Efficiency CTR by channel | (same channel GROUP BY) | 327 ms |
| SMS filter → campaign | 20 ms | 389 ms |
| Campaign → Channel pairs | 62 ms | 719 ms |
| Comparison June campaign | 130 ms | included in ranking HTTP above |
| Channel + Filter Logic 1 group | 18 ms | — |

See `tmp/d5_explorer_explain.json` after `python tmp/d5_explorer_explain.py` and HTTP timings from `python tmp/d5_live_explorer.py`.

## Security

Tenant isolation is mandatory and server-enforced:

- Client A cannot see client B ranking rows
- Query `client_id` cannot widen a bound token (403)
- Dimension values from another tenant cannot leak
- Publisher selected-company scope remains enforced
- Historical newest-wins data remains isolated
- Comparison queries use the same JWT company scope

## Tests

`tests/test_d5_performance_explorer.py` plus D1–D4 regression:

1. Campaign ranking
2. Channel ranking
3. Top N
4. Bottom N
5. Positive movers
6. Negative movers
7. Additive contribution
8. Valid efficiency metric (ROAS / CTR / delivery rate)
9. Comparison values
10. Absolute delta
11. Delta %
12. Sorting (ranking ascending)
13. Limit validation (0 and >50 → 422; truncated top N)
14. Secondary Campaign → Channel
15. Multiple D2 filters
16. Invalid metric
17. Invalid dimension
18. Invalid metric/dimension combination (duplicate primary/secondary)
19. Invalid ranking mode
20. Unsupported contribution on a ratio
21. No comparison (ranking still works; movers 422)
22. Empty result
23. API/query error (store 503)
24. Tenant isolation
25. Corrected-month newest-wins
26. D1 KPI regression
27. D2 filter regression
28. D3 trend regression
29. D4 drill regression

SPA: Performance Explorer on Overview; Dashboard and Reports nav labels unchanged; no Insights / Anomaly copy.

## Live path

`tmp/d5_live_explorer.py` via `create_app()` + TestClient (same method as D1–D4; does not print passwords or metric values).

CLIENT `demo-client`: Overview 8 KPIs; explorer campaign/channel ranking; top/bottom; movers; contribution; efficiency; secondary breakdown; invalid dimension 422; other `client_id` 403.

PUBLISHER: select company → same explorer contract scoped to that company; `/api/v1/ops/ready` and current publication still 200.

## Known limitations

- Secondary depth is 2. A third grouping is D4 nested drill, not explorer pivoting.
- Explorer does not emit an Other remainder. Groups beyond `limit` are omitted (`truncated=true`).
- Movers omit rows without a comparable prior value.
- Top/bottom always sort by current metric value; other sorts require ranking mode.
- No chart: D3 owns general trend creation. The explorer is a ranked table.
- No anomaly detection, AI, exports, or saved analysis. Insights cards are D6.

## D6+

D6 Deterministic Insights is implemented separately (`documentation/D6_INSIGHTS.md`).

D8 Contextual Ask is implemented separately (`documentation/D8_CONTEXTUAL_ASK.md`).

Still out of scope of D5:

- Anomaly detection / baselines / severity (D7)
- Saved workspace / advanced integration (D9)
- Advanced export
- Filter Logic 2 / AMC / product-cat slicers
- Deeper than 2-dimension explorer pivoting
