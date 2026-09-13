# D2 — Global Filters

D2 adds one shared analytical filter/state layer on the D1 Overview contract. It does not add charts, trend builders, drilldown, explorer, insights, anomaly detection, AI, or exports.

## Approved D2 filters

These are the filters D0 marked KEEP for global use **and** that are useful on an executive Overview without overloading the page.

| Filter | Source field | Type | UI | Empty means | Period-scoped options | Global |
| --- | --- | --- | --- | --- | --- | --- |
| Period grain | derived from `day` | `month` / `range` / `all_history` | single | default `month` | n/a | Yes |
| Month | `month_start` from `Day` | date (1st of month) | single, published months only | latest published month | Company-wide month list | Yes |
| Date range | `day` | inclusive `day_from`–`day_to` | range, clipped to published min/max | n/a | Published day bounds | Yes |
| Comparison | prior published month or explicit month/range | `compare=auto\|none` plus optional `compare_month_start` / `compare_from`+`compare_to` | auto for single month; none otherwise | auto | Published months | Yes |
| Channel | `channel` | text, NULL/blank = `(blank)` | multi | all | Current period | Yes |
| Campaign | `campaign_id` + `campaign_name` | text | multi + search | all | Current period (~2.5k on largest tenant month) | Yes |
| Filter Logic 1 | `filter_logic_1` | text | multi | all | Current period | Yes |
| Filter Logic 1 group | `filter_logic_1_group` | text | multi | all | Current period | Yes |

Company is **auth context**, never a user security filter.

## Rejected / deferred

| Candidate | Decision | Why |
| --- | --- | --- |
| Day grain toggle / week | **Defer D3** | D0: week is derived later; day series is a trend concern |
| YoY | **Defer** | Live history is 2–4 months |
| Custom vs custom comparison builder | **Defer D3+** | D0 listed it after D2; D2 supports explicit compare month/range only |
| Filter Logic 2 | **Defer D5** | Available, but not in the primary Excel slicer set (FL1, Channel, FL1_2, Month) |
| AMC Device / Product Cat | **Defer D5** | Optional Excel slicers; product cat is not a product grain |
| Variation / template / type of campaign / manual-automated | **Defer D4/D5** | D0 defer for global filters |
| HHH / Start Date | **Rejected** | Campaign attributes, not a period |
| Brand / platform / business / ASIN | **Rejected** | Not PublishedFacts columns |
| `client_id` as a filter | **Rejected** | JWT/company selection only |
| Saved analyses | **Defer D9** | Out of D2 scope |

## State model

Shared object (API `applied` + URL query):

- `period`: `month` | `range` | `all_history`
- `month_start`, `day_from`, `day_to`
- `compare`: `auto` | `none`
- `compare_month_start` / `compare_from` / `compare_to`
- `campaign_id[]`, `channel[]`, `filter_logic_1[]`, `filter_logic_1_group[]`
- `is_default`: true only for latest published month + auto comparison + no dimensions

Frontend module: `apps/web/static/js/analytics-state.js`. One parser/serializer for Overview; later D phases should reuse it.

**Persistence:** URL query on `/client/overview`. No localStorage, no saved-analysis. Clear all = `/client/overview`.

## API contract changes

Still one route: `GET /api/v1/analytics/overview`

New optional query parameters (allowlisted; unknown dimension names are not accepted):

- `period`, `month_start`, `day_from`, `day_to`
- `compare`, `compare_month_start`, `compare_from`, `compare_to`
- repeated `campaign_id`, `channel`, `filter_logic_1`, `filter_logic_1_group`
- existing `client_id` (cannot widen a bound JWT)

Default with no params remains **D1**: latest published month, auto prior published month, no dimension filters.

Response additions:

- `applied` — resolved state after drops
- `options` — month list, published day bounds, period-scoped dimension values
- `dropped_filters` — selections not valid in the current period (cleared, not queried)

`period.grain` may be `month`, `range`, or `all_history`. KPI formulas are unchanged.

## Validation rules

- `month_start` / `compare_month_start` must be the first day of a **published** month for that company (422 otherwise).
- `period` must be `month`, `range`, or `all_history` (`all` accepted as alias).
- Range requires both `day_from` and `day_to`; bounds clip to published min/max.
- At most 500 values per multi-select (422).
- Stale dimension values are **dropped** (200 + `dropped_filters`), not used in SQL.
- Comparison window must differ from the current window.
- Auto comparison only for **month grain**. Range / all-history → `comparison_not_applicable` unless an explicit compare window is sent.
- `compare=none` → `comparison_disabled`. A stale `compare_month_start` does not override None.

SQL predicates use a server-side column allowlist (`campaign_id`, `channel`, `filter_logic_1`, `filter_logic_1_group`). Clients cannot pass a field name into SQL.

## Security / scoping

Unchanged from D1: `_resolve_client_id` + `require_company_active`. Filters only **narrow**. Query `client_id` cannot read another tenant. Dimension filters are not a security boundary.

## Performance (largest local tenant, Oct 2025, 29,129 month rows)

| Query | Time | Plan | Result size |
| --- | --- | --- | --- |
| DISTINCT campaigns | **18.9 ms** | Bitmap Index Scan `publication_history_grain_page_idx` | 2,495 |
| DISTINCT channels | **12.8 ms** | same index | 6 |
| Channel-filtered SUM | **14.3 ms** | same index + `channel = ANY(...)` filter | 1 aggregate (12,025 grains) |

No new index, cache, materialized view, or warehouse. Channel is not in the history-grain index; the day-range bitmap is sufficient at this scale.

One Overview load also SUMs current + comparison windows and lists FL1 / FL1 group the same way.

## Tests

`tests/test_d2_global_filters.py` plus D1 regression:

1. Default latest month + auto comparison  
2. Other published month changes KPIs  
3. Date range / all history  
4. Auto vs none vs explicit compare month  
5. Campaign + channel together  
6. Filter Logic 1  
7. Unknown campaign dropped  
8. Invalid month 422  
9. Tenant isolation with filters  
10. D1 eight-KPI regression  
11. SPA shared state / no charts  

Live `demo-client`: default Oct-25 `is_default=true`; Aug-25 changes row count; invalid month 422; channel filter narrows rows; omitting params restores default.

## Files

New: `packages/analytics/dfip_analytics/filters.py`, `apps/web/static/js/analytics-state.js`, `tests/test_d2_global_filters.py`, this document.

Updated: publication stores (in-memory + Postgres), analytics service/routes/schemas, Overview UI/CSS, API clients.

## D3 status

Implemented in `documentation/D3_DYNAMIC_TRENDS.md`. Overview reuses this D2 `applied` + URL state for `GET /api/v1/analytics/trends`. Week grain and daily points live there. Auto comparison for multi-month ranges is still not invented.

## D4 status

Implemented in `documentation/D4_DRILLDOWN.md`. KPI cards and supported trend points reuse this same D2 URL state for `GET /api/v1/analytics/drilldown`. Filter Logic 2 / AMC slicers remain deferred to D5.
