# DFIP Dashboard — D0–D9 complete

**Status:** frozen. D0–D9 are the official dashboard. Do not invent D10.

This is the final dashboard completion record. Analytical contracts live under `/client/overview`. Operational work stays on `/admin`. Controlled reporting stays on `/client`.

| Phase | Name | Outcome |
|---|---|---|
| D0 | Capability audit | Published history is the dashboard source of truth. No invented metrics. |
| D1 | KPI Overview | Eight hero KPIs, latest published month, SUM-then-divide. |
| D2 | Global filters | Allowlisted period, comparison, campaign/channel/logic filters. JWT scope. |
| D3 | Dynamic trends | Allowlisted metric, grain, breakdown. Same D2 window. |
| D4 | Drilldown | Depth ≤ 3, allowlisted dimensions, back/crumbs. |
| D5 | Performance Explorer | Ranking / top / bottom / movers. Row drill reuses D4. |
| D6 | Deterministic insights | No LLM. Materiality thresholds. Empty = no material insight. |
| D7 | Anomaly / diagnostic | Median of prior published months. Insufficient history is explicit. |
| D8 | Contextual Ask | Allowlisted operations + grounding. Only AI layer. Provider default `none`. |
| D9 | Advanced workspace | One state model, navigation continuity, saved analysis, CSV export. |

## Product split (locked)

- **Dashboard** = `/admin` operational/system surfaces.
- **Reports** = `/client` published-reporting downloads and fact lists.
- **Overview** = `/client/overview` analytics workspace.

## D9 additions

- Canonical URL workspace state (no competing stores).
- Saved analysis: configuration JSON, owner+company, wiped on company delete.
- Sharing: **deferred** (no public tokens).
- Export: **CSV only**. Excel/PDF/PPT deferred.
- Performance: measured against existing aggregates; no warehouse.
- AI: still D8 only. M4 rate limiting remains a production prerequisite for external providers.
- **D9 UI Refinement — Same-route Overview partial refresh:** `/client/overview` query changes no longer replace `#app` / `layout()`. See `documentation/D9_ADVANCED_ANALYTICS_WORKSPACE.md`.
- **D9 UI Refinement — Full-width sectioned workspace:** Overview uses the available content width with Filters → KPIs (4×2) → Trends|Explorer → Insights|Anomalies|Tools. Host contracts and analytics semantics are unchanged.
- **D9 UI Refinement — Filter UX + Comparison None:** Compare Month includes explicit None. Canonical no-comparison state is `compare=none` with no `compare_month_start`.
- **D9 UI Refinement — KPI cards:** eight D1 cards are interactive D4 entry points (hover/focus/keyboard). Comparison=None hides delta/prior/vs.
- **D9 UI Refinement — D4 drawer:** right-side analytical drawer over the mounted Overview with Breakdown / Trend / Details modes. Same D4 contract, contextual D3 trend for the selected KPI, same-route refresh, localized 403/loading, focus restore.
- **D9 UI Refinement — Compact analytical filters:** Channel / Logic / Group / Campaign use closed summary + popover checkboxes. Period and comparison stay visible. Apply still stages via the existing form and same-route refresh. No D10.
- **D9 UI Refinement — KPI sparklines:** eight D1 cards show current-period mini-trends from one batched D3 `include_metric` request. Card scalars stay D1. Sparkline failure omits charts only. No D10.

## Remaining limitations

Documented in `documentation/D9_ADVANCED_ANALYTICS_WORKSPACE.md`. They are freeze conditions, not a backlog that implies D10.

## Tests

`tests/test_d9_analytics_workspace.py` plus D1–D8 suites and existing Dashboard/Reports web contracts.
