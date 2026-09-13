# D7 — Anomaly / Diagnostic

D7 adds a deterministic anomaly layer on `/client/overview`. It asks whether the **selected published month** is unusual relative to a **multi-month median baseline** under the same D2 filters. If it is, the API returns severity, the affected slice when contribution is valid, and structured evidence.

D7 is **not** an AI feature and **not** D6. A large change versus one comparison month remains an Insight. An anomaly requires a baseline of prior published months and extra false-positive controls.

D8 Ask / LLM wording is implemented separately (`documentation/D8_CONTEXTUAL_ASK.md`). The model does not replace D7 calculations.

## History coverage (why these rules)

Local `publication_history_grain` coverage (2026-09-07):

| Companies | Distinct published months |
| --- | --- |
| 8 of 9 | 2 |
| 1 of 9 (`e7450265-3799-4569-b488-405319a4d3c6`) | 4 (2025-06 through 2025-10) |

Implications:

- A 12-month seasonal model, parametric z-score on long windows, or day-of-week intra-month model is **not supportable** for typical tenants.
- Anomalies require **3 prior published months** after filters (4 months total). Tenants with only two months receive `insufficient_history` rather than a guessed baseline.
- Rolling robust-z is used only when MAD is large enough to be meaningful. With short, flat baselines MAD is often ~0, so spike/drop uses the period-vs-median + new-extreme rule instead.

Evaluated and **not** implemented:

| Candidate | Decision |
| --- | --- |
| Spike / drop vs multi-month median | Implemented (period-vs-baseline) |
| Rolling deviation (Iglewicz–Hoaglin robust z) | Implemented when MAD is usable |
| Period-vs-baseline (median of prior months) | Implemented as the shared calculation |
| Metric-specific floors (money/count vs rate pp vs ROAS) | Implemented |
| Intra-month day spikes | Rejected: incomplete months and weekday seasonality would need a model we do not have |
| ARIMA / STL / ML scores | Rejected: history too short; not deterministic in the D7 sense |

## Files

### New
- `packages/analytics/dfip_analytics/anomalies.py`
- `tests/test_d7_anomaly_diagnostic.py`
- `documentation/D7_ANOMALY_DIAGNOSTIC.md` (this file)
- `tmp/d7_live_anomalies.py`, `tmp/d7_anomalies_explain.py`, `tmp/d7_history_coverage.py`

### Updated
- `analytics_service.py`, `analytics_routes.py`, `schemas.py`
- `dfip_analytics/__init__.py`
- web/API clients, `app.js`, `views.js`, `analytics-state.js`, `app.css`
- D4/D5/D6 SPA assertions
- `documentation/D6_INSIGHTS.md`

Unchanged: `/admin` Dashboard, `/client` Reports, D1–D6 contracts, Excel.

## Query contract

`GET /api/v1/analytics/anomalies`

Same JWT + D2 filter parameters as Overview. Optional `metric`, `dimension` (driver allowlist; `day` rejected), `limit` 1–12.

Company scope is from authentication. Query `client_id` cannot widen a bound token.

Anomalies are defined on **`period=month`** (default latest published month). Date range and all-history return `period_not_month`. D2 comparison is echoed for shared state but **is not the baseline**.

Handler:

1. Resolve D2 scope.
2. Select up to 12 published months strictly before the selected month.
3. If fewer than 3 → `insufficient_history`.
4. One monthly series for totals over `[first baseline, end of current month)`.
5. Optional campaign and channel monthly series (`limit_series=50`) for additive drivers.
6. Per metric: SUM then `compute_kpis(namespace="client")` on each month. Never average subgroup ratios.
7. Classify spike / drop / rolling deviation. Rank by documented severity.

422 unsupported input. 503 store failure. 401 unauthenticated. 403 widen / unbound publisher.

## Baseline

- Grain: published month
- Method: **median** of prior months that have a valid value after filters and denominator checks
- Window: last 12 prior published months, minimum 3
- Current month is the observation, not a baseline member
- Newest-wins month grain still applies (same history table as D1–D6)

## Rules

### Shared period-vs-baseline

`delta = observed − median(baseline)`  
`delta_pct = delta / median` (NULL if median is 0)

### Spike / drop (when MAD is not usable)

Used when `MAD < max(abs_floor, 5% × |median|)`.

All of:

1. `|delta|` ≥ kind floor (`5.0000` money, `5` count, `0.10` rate as 10pp, `0.50` ROAS)
2. New extreme: observed `< min(baseline)` or `> max(baseline)`
3. Money/count/ROAS: `|delta_pct| ≥ 50%`. Rates: the 10pp floor already applies (relative % is not used, so 1% → 2% cannot fire)

Direction is `spike` if observed > median, else `drop`.

Why: with 3–4 months, MAD is often zero. Requiring a new extreme **and** a 50% (or 10pp) move versus the median is stricter than D6’s 10–25% versus one comparison month.

### Rolling deviation (when MAD is usable)

Iglewicz–Hoaglin robust z: `z = 0.6745 × (observed − median) / MAD`.

Fire `rolling_deviation` when `|z| ≥ 3.5` and `|delta|` ≥ the smaller abs floor (`5.0000` / `5` / `0.05` rate / `0.25` ROAS).

Why: only when the baseline actually varies. A tight series should not turn a 0.01 wiggle into an “anomaly” (MAD floor). A 20% move can still be unusual if the baseline is stable and z clears 3.5 with the abs floor.

### Metric-specific notes

| Metric kind | Why this threshold |
| --- | --- |
| money / count | Absolute floors avoid tiny currency/count noise; 50% vs median avoids D6-style ordinary swings |
| rate | Percentage-point floors. `0.80 → 0.81` is not an anomaly |
| ROAS | Absolute 0.50 (non-z) / 0.25 (z) plus ratio 50% on the non-z path |

Contribution / dominant slice is **additive metrics only**, same D6 share-of-delta rule versus `(observed − median)`, not versus D2 comparison. Ratios never get contribution %.

## Severity

```
score = min(100,
  min(|z|/3.5, 3) * 40
  + min(|delta_pct|/0.50, 3) * 30
  + min(|delta| / max(|median|, 1), 3) * 10
  + (15 if new_extreme else 0)
)
```

`high` ≥ 75, `medium` ≥ 50, else `low`. Sort severity desc, then `|z|`, `|delta_pct|`, metric key.

## Suppression

| `empty_reason` | When |
| --- | --- |
| `no_published_history` | No published months |
| `period_not_month` | Range / all-history |
| `insufficient_history` | Fewer than 3 prior published months after filters |
| `empty_period` | Current month has zero grain rows |
| `no_anomalies` | Baseline exists but nothing passes |

Per-metric omit (not 4xx): NULL values, zero/near-zero denominator, below-floor movement, inside the baseline min/max without a usable z-score.

## Evidence

Each anomaly includes: `anomaly_id`, `kind`, `direction`, metric, observed, baseline median, delta, delta %, severity, dimension/affected value, drivers, threshold string, explanation, and evidence (`baseline_months`, observation count, robust z, MAD, `new_extreme`, additive/contribution flags).

Frontend shows explanation **and** evidence. Driver links reuse D4 `withDrill`.

## Security

JWT company scope. `client_id` cannot widen. Publisher must select a company. Newest-wins remains on `publication_history_grain`.

## Performance

Default path: **1 monthly total series + 1 campaign series + 1 channel series** over the lookback window (plus the current-month SUM already used to detect empty). No per-metric SQL. No new warehouse/index.

Measured locally (2026-09-07):

HTTP TestClient (`create_app()`):

| Request | HTTP | Result |
| --- | --- | --- |
| client default (2 published months) | 202 ms | `insufficient_history` (correct) |
| SMS filter | 199 ms | `insufficient_history` |
| `compare=none` | 318 ms | still `insufficient_history` (baseline ≠ D2 compare) |
| date range | 307 ms | `period_not_month` |
| invalid metric/dimension | ~200 ms | 422 |
| widen `client_id` | — | 403 |

Live uvicorn `GET /api/v1/analytics/anomalies` (`demo-client`): **686 ms**, `insufficient_history` (company `0fb2ad45-…`, 2 published months).

SQL EXPLAIN on the 4-month tenant (`e7450265-…`, Jun–Oct window):

| Shape | SQL execution | Seq scan |
| --- | --- | --- |
| month totals | 277 ms | yes |
| month × campaign | 223 ms | yes |
| month × channel | 121 ms | yes |
| SMS-filtered month totals | 99 ms | yes |

No new index: the lookback is a handful of months and these times are in the same band as accepted D5/D6 group queries. A warehouse/MV was not added.

## Tests

`tests/test_d7_anomaly_diagnostic.py`: sufficient history, insufficient history, spike, drop, normal movement suppressed, near-zero denominator, ratio, additive, metric-specific rate floor, filters, newest-wins, tenant isolation, invalid input, empty, 503, D1–D6 regression, SPA (Anomalies present; Ask/chat/export absent).

## UI

Anomalies section on `/client/overview` after Insights. States: loading (page), error, 422, insufficient history, period-not-month, empty/no-anomalies. No chat, no Ask, no export.

## Browser verification (2026-09-07)

Publisher (`demo-publisher`), company **Pratham Patil** (`e7450265-…`, 4 published months Jun/Jul/Sep/Oct):

- `/client/overview` default Oct-25: eight KPIs, Insights (period-vs-Sep), Anomalies with baseline `2025-06, 2025-07, 2025-09` and **no cards** (`no_anomalies`). Oct vs Sep is large in Insights (~45–58%) and is **not** labeled an anomaly versus the three-month median.
- SMS filter: Insights and Anomalies both refresh. Four anomaly cards: rolling-deviation drops on Delivered and Total Cost (high); period-vs-median drops on Revenue (high, campaign driver) and Unique Clicks (medium).
- Anomaly driver **Inspect … in drilldown** opened existing D4 Drilldown (`KPI · Revenue · Channel` under the campaign parent). SMS filter stayed in the query string.
- `/admin` Dashboard heading and nav unchanged. `/client` Reports still offers Client Report / refreshable workbook downloads.

Client (`demo-client`): live API `insufficient_history`. Browser sign-in form was reached on a separate tab; password auto-fill was blocked by the environment, so the client Overview click-path was not completed in-browser. TestClient + live API cover that tenant.

`/admin` Dashboard and `/client` Reports were not redesigned.

## D8+

D8 Ask / LLM wording of D7 evidence is implemented separately (`documentation/D8_CONTEXTUAL_ASK.md`). It does not replace D7 calculations or generate SQL.

Still out of scope:

- Saved analyses / advanced workspace (D9)
- Exports

## Known limitations

- Most local demo companies have only two published months and will show insufficient history. That is correct, not a bug.
- Maximum 12 baseline months; older history is ignored.
- Campaign/channel driver series are capped at 50 groups (same cap as D5).
- Day is not a driver dimension.
- No seasonal adjustment, no weekday model, no statistical significance language.
- D2 comparison is displayed for shared state only; it is not the anomaly baseline.
