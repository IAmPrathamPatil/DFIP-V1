# D1 — KPI Overview

D1 is the first dashboard phase after the accepted D0 audit. It adds a JWT-scoped published-history KPI layer and a new Overview page. It does not add charts, global filters, drilldowns, explorer, insights, anomaly detection, AI, or exports.

## Files changed

### New
- `packages/analytics/dfip_analytics/overview.py` — eight hero KPI specs and latest/prior published-month selection
- `packages/api/dfip_api/analytics_service.py` — aggregate + `compute_kpis(namespace="client")`
- `packages/api/dfip_api/analytics_routes.py` — `GET /api/v1/analytics/overview`
- `tests/test_d1_kpi_overview.py`
- `documentation/D1_KPI_OVERVIEW.md` (this file)

### Updated
- `packages/analytics/dfip_analytics/__init__.py`
- `packages/api/dfip_api/ports.py` — `list_published_month_starts`, `sum_published_history_month`
- `packages/api/dfip_api/publication_store.py` — in-memory newest-wins month SUM
- `packages/db/dfip_db/publication_store.py` — PostgreSQL month DISTINCT + SUM on `publication_history_grain`
- `packages/api/dfip_api/schemas.py` — `OverviewKpiResponse` and nested models
- `packages/api/dfip_api/app.py` — analytics router
- `packages/web/dfip_web/api_client.py`
- `apps/web/static/js/api-client.js`
- `apps/web/static/js/app.js` — `/client/overview`
- `apps/web/static/js/components.js` — nav labels, Overview item, `overviewKpiCard`
- `apps/web/static/js/views.js` — Overview page; existing `/client` titled Reports
- `apps/web/static/css/app.css`
- `tests/test_p6_web.py`, `tests/test_v2_web_ui.py`

Unchanged by design: operational `/admin` Dashboard, Excel mashup, publication HTTP contracts, working-set `/facts`.

## Endpoint / query contract

`GET /api/v1/analytics/overview`

Optional query: `client_id` (UUID). Same scoping rules as publications:

- JWT `client_id` always wins.
- A bound token cannot widen scope with a different `client_id` (403).
- Unbound publisher/admin with no selected company is 403, not an empty KPI payload.
- Inactive company is refused by `require_company_active`.

The handler does not return fact rows. It:

1. Resolves company from the principal.
2. Lists distinct published `month_start` values from newest-wins history.
3. Selects latest month and the immediately preceding published month (gaps allowed).
4. SUMs additive measures for those months in PostgreSQL (or in-memory newest-wins for tests).
5. Applies `dfip_analytics.kpis.compute_kpis(..., namespace="client")`.
6. Returns the eight D1 cards in one response.

### Response shape

- `client_id`, `company_name`
- `has_published_history`
- `period`: `month_start`, `month_label` (`MMM-YY` from `Day`), `day_min` / `day_max`, `grain_row_count`
- `comparison`: `available`, `reason` (`no_published_history` | `no_prior_published_month`), prior period fields when available
- `kpis[]`: `id`, `label`, `kind`, `definition`, `value`, `prior_value`, `delta`, `delta_pct`, `numerator`, `denominator`

Money, ROAS, and rates are JSON strings (same `decimal_to_api` contract as facts). Counts are integers. Ratio divide-by-zero and NULL operands are JSON `null`, never `0`.

`delta_pct` is `(current − prior) / prior` at rate scale, not a 0–100 integer. The UI multiplies by 100 for display.

## KPI definitions and sources

All eight cards reuse existing client-namespace / Excel `MEASURE_OPS` semantics. Formulas are not re-derived in the Overview UI.

| Card | Source | Semantics |
| --- | --- | --- |
| Total Cost | `SUM(total_cost)` | Additive. Unmatched cost is 0.0000 in transform, not here. |
| Revenue | `SUM(revenue_inr)` | Additive. |
| Overall ROAS | `revenue_inr / total_cost` after SUM | `overall_roas`. Zero cost → NULL. |
| Delivered | `SUM(delivered)` | Additive. |
| Unique Clicks | `SUM(unique_clicks)` | Additive SUM of the unique-named column. Not DISTINCT. |
| Unique Conversions | `SUM(unique_conversions)` | Additive. |
| Delivery Rate | `delivered / sent` after SUM | `delivery_rate`. Zero sent → NULL. |
| CTR (Delivered → Clicks) | `unique_clicks / delivered` after SUM | `ctr_del_to_clicks`. Not clicks/impressions. Zero delivered → NULL. |

Engine: `packages/analytics/dfip_analytics/kpis.py` + `divide.safe_divide`.

## Period / default / comparison

- Default current period = latest distinct published `month_start` for that company.
- Comparison = previous item in that sorted published-month list, not calendar-minus-one-month when the prior published month is older (Oct vs Jun is valid).
- Month identity is `COALESCE(month_start, date_trunc('month', day)::date)`, matching `dfip_core.transform.derive.month_start` from `Day`.
- Not derived from filename, batch name, notes, processing labels, or system date.
- No published months → `has_published_history=false`, empty cards, `reason=no_published_history`.
- One published month → latest selected, `reason=no_prior_published_month`.
- Corrected months: newest successful snapshot wins per grain (`publication_history_grain` / in-memory merge). Older versions are not added.

## Authorization / scoping

Tenant isolation is application-level JWT scope (same as published history), plus PostgreSQL RLS when the live store uses `current_rls`. Clients cannot read another company by changing the URL or `client_id` query. Publishers see Overview for the currently bound company only.

One UI (`/client/overview`) and one query contract serve both roles.

## Website integration

| Role | Nav | Route |
| --- | --- | --- |
| Publisher | Dashboard | `/admin` (unchanged operational dashboard) |
| Publisher | Companies | `/admin/companies` |
| Publisher | Overview | `/client/overview` (selected company) |
| Client | Overview | `/client/overview` (authenticated company) |
| Both | Reports | `/client` (existing published-reporting page, relabeled) |
| Both | Published data | `/client/facts` (unchanged) |

Client sign-in lands on Overview. Publisher sign-in still lands on Dashboard.

## Performance

D1 queries `publication_history_grain` with `client_id` and a calendar-month `day` range (`day >= month_start AND day < month_start + 1 month`). That range is the same month identity as `derive.month_start` from `Day`. It does not page fact rows to the browser.

Measured on local Postgres against the largest tenant (179,596 history-grain rows, 4 published months, latest `2025-10-01`):

| Query | Execution time | Plan | Rows returned |
| --- | --- | --- | --- |
| Month SUM (8 additive SUMs + COUNT/MIN/MAX) | **30.3 ms** | Bitmap Index Scan on `publication_history_grain_page_idx`, then aggregate. 29,129 grain rows scanned for October. | 1 aggregate row |
| Distinct published months | **66.5 ms** | Parallel seq scan + hash unique (4 months). | 4 rows |

A first draft that filtered `COALESCE(month_start, date_trunc('month', day)) = $month` could not use the day index (parallel seq scan, ~73 ms SUM). The day-range predicate is the only D1 SQL adjustment. No new index, cache, materialized view, or warehouse.

The month-list seq scan is acceptable at current scale (once per Overview load). Do not add an extra `(client_id, month_start)` index unless later phases query months much more often.


## Tests

`tests/test_d1_kpi_overview.py`:

1. One published month → latest + no-comparison
2. Two published months (Jun then Oct) → Oct vs Jun
3. Same grain republished → newest cost, not double count
4. Company A vs B; client JWT cannot pass the other `client_id`
5. No history → empty
6. Unauthenticated → 401; unbound publisher → 403
7. ROAS / delivery rate / CTR NULL on zero denominators
8. SPA route/nav labels (Overview vs Reports; Dashboard intact)

Live local database (TestClient + `create_app()`, 2026-09-07):

- `demo-client` Overview 200: latest `2025-10-01` (Oct-25), comparison `2025-08-01`, eight KPI ids, widen-`client_id` 403
- Unbound `demo-publisher` Overview 403; after `POST /auth/select-client`, Overview 200 with eight KPIs; `GET /ops/ready` and `GET /publications/current` still 200
- SPA: `/client/overview` is a real route and shows sign-in when unauthenticated. Interactive browser password entry was not used; KPI/auth behavior was verified through the live API contract above.

## Known limitations (D1)

- Period defaults to latest + prior published month. D2 adds month/range/all-history and dimension filters on the same endpoint.
- No charts.
- `company_name` is filled when the client directory has the company; the UI also uses session membership labels.
- In-memory tests scan snapshot facts in process; production uses SQL aggregates.

## D2

Implemented in `documentation/D2_GLOBAL_FILTERS.md`. D1 KPI semantics are unchanged. Default Overview with no query parameters is still latest published month + auto prior-month comparison.
