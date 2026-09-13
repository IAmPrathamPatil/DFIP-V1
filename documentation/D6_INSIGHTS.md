# D6 — Deterministic Insights

D6 adds a deterministic insight layer on `/client/overview`. It identifies material period changes, signed improvement/deterioration, dominant additive drivers, and a small set of mix/efficiency relationships, then turns those calculations into concise explanations with structured evidence.

D6 is **not** an AI feature. It does not call an LLM and does not generate SQL. D8 Ask can explain a D6 insight using this evidence; it does not change D6 calculations. Anomaly detection belongs to D7.

D1 KPIs, D2 URL filters, D3 trends, D4 drilldown, and D5 explorer remain the shared analytical state. Changing month, channel, campaign, comparison, or Clear All updates KPIs, the trend, an open drill, the explorer, **and insights** together.

## Files

### New
- `packages/analytics/dfip_analytics/insights.py` — categories, thresholds, driver contribution, ranking, explanation templates
- `tests/test_d6_insights.py`
- `documentation/D6_INSIGHTS.md` (this file)
- `tmp/d6_live_insights.py`, `tmp/d6_insights_explain.py` (operator live/EXPLAIN helpers; gitignored)

### Updated
- `packages/api/dfip_api/analytics_service.py`, `analytics_routes.py`, `schemas.py`
- `packages/analytics/dfip_analytics/__init__.py`
- `packages/web/dfip_web/api_client.py`, `apps/web/static/js/api-client.js`
- `apps/web/static/js/analytics-state.js` — `insightsParamsFromQuery` reuses D2 filters only
- `apps/web/static/js/app.js`, `views.js`, `css/app.css`
- `tests/test_d4_drilldown.py`, `tests/test_d5_performance_explorer.py`
- `documentation/D5_PERFORMANCE_EXPLORER.md` — D6 pointer

Unchanged by design: `/admin` Dashboard, `/client` Reports, D1 eight-KPI semantics, D2 allowlisted filters, D3/D4/D5 contracts, Excel mashup.

## Query contract

`GET /api/v1/analytics/insights`

Same JWT + D2 filter query parameters as Overview/trends/drilldown/explorer, plus:

| Param | Default | Notes |
| --- | --- | --- |
| `metric` | all eight D1/D3 hero keys | Optional allowlisted metric. Unknown keys are 422. |
| `dimension` | campaign + channel | Optional driver dimension: `campaign_id`, `channel`, `filter_logic_1`, `filter_logic_1_group`. `day` and unknown keys are 422. |
| `limit` | 8 | Integer 1–12 |

Company scope is derived from authentication. Query `client_id` cannot widen a bound token (403). Arbitrary field names are not concatenated into SQL. Insights never return fact rows.

The handler:

1. Resolves the same D2 period/comparison/dimension scope as Overview (`AnalyticsService._resolve_scope`).
2. Validates optional metric, driver dimension, and limit against `insights.py`.
3. Loads **one SUM** for the current window and **one SUM** for the comparison window.
4. Loads grouped aggregates for the selected driver dimension(s) current + comparison (default: campaign and channel → four group queries).
5. Computes all requested metrics in Python with `compute_kpis(namespace="client")` (SUM then ratio).
6. Applies materiality, driver, relationship, and ranking rules. Returns structured insights, not narrative-only text.

Unsupported combinations are **422** `VALIDATION_ERROR`. Persistence failures are **503**. Missing auth is **401**. Bound-token widen / unbound publisher is **403**.

### Response shape

- D2 `applied`, `dropped_filters`, `period`, `comparison`
- `empty`, `empty_reason`, `result_count`
- `insights[]` with `insight_id`, `category`, `headline`, `explanation`, metric values, delta, driver fields, `threshold`, `rank`, `score`, `drivers[]`, `evidence`
- `thresholds` echo of the documented constants and ranking formula
- catalog echo: `metrics`, `dimensions`, `categories`

Money, ROAS, and rates are JSON strings. Counts are integers. Divide-by-zero and NULL operands are JSON `null`, never `0`. Missing comparison is not fabricated.

## Insight categories

Only categories justified by D1–D5 capabilities:

| Category | Trigger |
| --- | --- |
| `material_change` | Period movement with `\|delta_pct\| >= 25%` and the kind-specific absolute floor |
| `positive_signal` | Meaningful improvement at `\|delta_pct\| >= 10%` and below the 25% material-change cut |
| `negative_signal` | Meaningful deterioration at the same 10% band |
| `dominant_driver` | Additive metric with a material period change and a group that accounts for `>= 40%` of the total delta (or two groups at `>= 25%` each that together account for `>= 60%`) |
| `relationship` | Two independently material metrics with opposite-signed deltas: Revenue vs Overall ROAS, or Delivered vs Delivery Rate |

Higher is better for every metric except **Total Cost**, where an increase is deterioration and a decrease is improvement. Language is "increased" / "declined" for magnitude and "improved" / "deteriorated" for signed signals. Insights never say "anomaly" or "statistically significant".

## Materiality

Do not report ordinary fluctuation. A period-change insight is emitted only when **all** of the following hold:

1. A valid comparison window exists (D2 semantics). `compare=none` does not invent a baseline.
2. Current and comparison values are non-NULL.
3. Ratio metrics have a sufficient denominator in **both** windows (`NULL` or `0` → suppress; count rates also require denominator `>= 1`).
4. `\|delta_pct\| >= 0.10` (`MATERIAL_PCT`).
5. `\|delta\|` meets the kind floor:

| Kind | Absolute floor |
| --- | --- |
| money | `1.0000` |
| count | `1` |
| rate | `0.010000` (1 percentage point) |
| ROAS | `0.1000` |

`delta_pct` is `(current − prior) / prior` at rate scale, identical to D1–D5. If prior is `0`, `delta_pct` is NULL and the insight is suppressed (no infinite percent).

## Driver logic

When an **additive** metric is material:

1. Total period delta is taken from the window SUM (not from averaging subgroup ratios).
2. The change is broken down by an allowlisted D5 dimension (default campaign and channel).
3. Groups present in only one window are treated as `0` on the missing side for additive contribution math only.
4. Contribution is `group_delta / total_delta` (share of **delta**, not share of the current total).
5. Same-sign groups are ranked by `\|contribution\|`.
6. A dominant-driver insight is emitted only when there are at least **2** groups and:
   - the top group accounts for `>= 40%` of the total delta and meets the absolute floor, or
   - the top two groups each account for `>= 25%` and together `>= 60%`.

Ratio metrics never receive contribution percentages. Invalid share language is not generated.

Driver phrasing uses "accounted for" / "contributed". It does not claim causality.

Day is not a driver dimension (too granular; D4 already owns day drill).

## Ranking formula

Transparent, not ML:

```
score = min(|delta_pct|, 10) * 10000
      + min(|delta| / max(|prior|, 1), 10) * 100
      + (driver_share or 0) * 1000
      + category_weight
```

Category weights:

- `dominant_driver` = 400
- `material_change` = 300
- `negative_signal` = 250
- `positive_signal` = 200
- `relationship` = 150

Sort: score desc, then `|delta_pct|` desc, then metric key, category, `insight_id`. Rank is 1..n after that sort. Default limit 8, max 12.

## Evidence structure

Each insight includes:

- current / comparison / absolute delta / delta %
- dimension, driver key/label, driver contribution when relevant
- `threshold` string naming the rule that fired
- `evidence`: grain counts, group counts, materiality constants, `higher_is_better`, `additive`, `contribution_valid`, related-metric values for mix changes
- `drivers[]` with per-group current/prior/delta/contribution and D4 drill tokens (`drill_dimension`, `drill_parents`)

The frontend shows the explanation **and** this evidence. Explanation is never the only output.

## Supported metrics

The eight D1/D3 hero keys. Formulas stay in `kpis.py`. D6 does not invent CPA/CPC/CPM/CPO.

| Key | Aggregation | Additive | Contribution |
| --- | --- | --- | --- |
| `total_cost` | SUM | yes | yes |
| `revenue_inr` | SUM | yes | yes |
| `overall_roas` | revenue/cost after SUM | no | no |
| `delivered` | SUM | yes | yes |
| `unique_clicks` | SUM | yes | yes |
| `unique_conversions` | SUM | yes | yes |
| `delivery_rate` | delivered/sent after SUM | no | no |
| `ctr_del_to_clicks` | clicks/delivered after SUM | no | no |

## Supported dimensions

Driver dimensions: Campaign, Channel, Filter Logic 1, Filter Logic 1 group.

Not introduced: Product, Brand, Platform, Day-as-driver, or other unsupported reference dimensions.

## Suppression conditions

| `empty_reason` | When |
| --- | --- |
| `no_published_history` | Company has no published months |
| `insufficient_comparison` | Comparison disabled (`compare=none`) |
| `insufficient_history` | No valid prior window (including a first published month under auto-compare) |
| `empty_period` | Current window has zero grain rows |
| `no_material_insights` | Comparison exists but nothing passes materiality/driver/relationship rules |

Per-metric suppression (metric omitted, not a 4xx): NULL values, zero/near-zero denominator, `|delta_pct|` or `|delta|` below floor, fewer than two groups for drivers, ratio contribution requests.

## Comparison rules

Insights use D2 comparison semantics. If no comparison exists, period-change insights are not generated. Range / all-history without an explicit compare window follow D2 (`comparison.available=false`) and return `insufficient_history` or `insufficient_comparison` rather than inventing a baseline.

## Security

- JWT company scope is the authorization boundary.
- Query `client_id` cannot widen a bound token.
- Publisher selected-company scope is required (unbound publisher → 403).
- Client A aggregates cannot include Client B dimension values or amounts.
- Newest successful publication wins for a corrected month (`publication_history_grain`).

## Performance

Insights reuse D1 SUM and D4/D5 `GROUP BY` shapes. Default path is **6 aggregated queries** (current SUM, comparison SUM, campaign current/prior, channel current/prior), then Python ranking. There is no per-metric SQL and no N+1. No warehouse, cache, materialized view, or extra index was added.

Measured on the local demo (`publication_history_grain`, large tenant EXPLAIN `e7450265-3799-4569-b488-405319a4d3c6`):

| Shape | SQL execution | Seq scan |
| --- | --- | --- |
| SUM current month | 878 ms | no |
| SUM comparison month | 665 ms | no |
| GROUP BY campaign | 40 ms | no |
| GROUP BY channel | 28 ms | no |
| SMS-filtered campaign groups | 21 ms | no |

HTTP (`tmp/d6_live_insights.py` via TestClient):

| Request | HTTP |
| --- | --- |
| default insights (6 queries, cold-ish) | 1528 ms |
| channel driver only | 342 ms |
| SMS filter | 653 ms |
| `compare=none` (no driver queries) | 283 ms |
| invalid dimension/metric | 422 in ~195 ms |
| publisher selected-company insights | 117 ms |

The SUM window is the same D1 shape already accepted; D6 does not add indexes without a new bottleneck. Group queries are fast relative to the window SUM.

## Tests

`tests/test_d6_insights.py` covers:

1. Material positive change
2. Material negative change
3. Below-threshold suppression
4. Dominant driver
5. Multiple drivers
6. Contribution calculation
7. Ratio metric handling
8. Zero denominator
9. NULL / new-group handling
10. No comparison
11. Insufficient history
12. Insufficient denominator
13. Ranking
14. D2 filters
15. Corrected-month newest-wins
16. Tenant isolation
17. Invalid analytical input
18. Empty / no-insight state
19. Query error (503)
20–24. D1–D5 regression plus SPA (Insights present; anomaly/Ask/export/chat absent; Dashboard/Reports nav unchanged)

## UI

Insights is a section on `/client/overview` (not a new nav item). Cards show category, headline, explanation, current vs comparison, driver/contribution, period, and threshold. Driver links reuse D4 `withDrill` (period, comparison, D2 filters, and company scope preserved). States: page loading, section error, 422 unsupported, empty/no-material, insufficient comparison/history.

No AI chat and no export. Anomaly diagnostics are D7. D8 adds an Overview Ask control that can attach a D6 insight as context.

## D7+

D8 Contextual Ask / LLM explanations is implemented separately (`documentation/D8_CONTEXTUAL_ASK.md`).

Still out of scope:

- Saved analyses / advanced workspace (D9)
- Insight export

## Known limitations

- Default driver analysis is campaign and channel. Filter Logic 1 / group require `dimension=`.
- At most 12 insights are returned; lower-ranked material changes may be truncated.
- Relationship coverage is two explicit pairs only.
- Additive missing-side groups are treated as `0` for contribution math; ratio missing-side groups stay NULL.
- Day is not used as a driver dimension.
- No statistical significance test is implemented; none is claimed.
