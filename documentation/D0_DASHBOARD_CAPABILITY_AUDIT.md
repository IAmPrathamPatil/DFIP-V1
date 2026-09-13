# D0 — DFIP Dashboard Capability Audit

**Status:** complete. Read-only decision gate. **D1 must not start without explicit approval.**

**Date:** 2026-09-07  
**Scope:** inspect the current DFIP repository and live local PostgreSQL. No dashboard UI, KPI cards, charts, analytics endpoints, schema changes, Excel changes, or publication/lifecycle changes were made.

Evidence sources: `supabase/migrations`, `packages/db`, `packages/core/dfip_core/transform`, `packages/analytics/dfip_analytics`, `packages/api/dfip_api`, `apps/web/static`, `packages/web/dfip_web`, `excel/`, `powerbi/`, `documentation/ANALYTICS.md`, `V1_DATA_DICTIONARY.md`, `V1_EXCEL_REPORTING.md`, `V1_POWER_BI.md`, `PUBLICATION.md`, `V1_SECURITY.md`, `WEBSITE.md`. Live read-only counts and `EXPLAIN ANALYZE` against `publication_history_grain`.

---

## 1. Executive Summary

DFIP already has a published-truth stack that a dashboard can consume: `publication_fact` snapshots, a `publication_current` pointer, and `publication_history_grain` (newest-wins per `client_id + campaign_id + variation_id_key + day`). Excel Refresh All and Power BI already use that same truth. Metric formulas already live in `packages/analytics/dfip_analytics/kpis.py` and match Excel’s nine-report calculated fields.

What is **missing** is an HTTP analytics query layer. Existing publication APIs return **row dumps** (`FactResponse` pages / CSV), not SUM-then-divide KPI aggregates. Building Overview KPI cards on those dumps would pull tens to hundreds of thousands of rows into the browser. Live scale: **460,866** history-grain rows; largest tenant **179,596** rows / **3,509** campaigns / **4** months.

**D1 recommendation:** KEEP a client-facing **Overview** KPI page that:

1. Reads **published history grain**, not the working set and not uploaded Excel.
2. Reuses **client-namespace** KPI specs (`kpis.py` / Excel `MEASURE_OPS`).
3. Ships a **scoped aggregate query endpoint** as part of D1 (dependency, not a later surprise).
4. Defaults to the **latest month** with data; MoM when a prior month exists.
5. Does **not** rename `/admin` Dashboard.
6. Does **not** implement charts, drilldown, AI, or exports.

**Do not invent** Orders, CPC, CPM, CPA, CPO, Video Views, Engagements, Visits, Units, brand, or platform. Those fields are not in PublishedFacts.

**Stop condition:** this document is the D0 gate. D1 starts only after explicit approval of the matrices, architecture, and D1 scope below.

---

## 2. Actual DFIP Analytics Capability

| Capability | Exists today | Dashboard implication |
|---|---|---|
| Published day-grain facts | Yes — 45 typed columns | Source of truth |
| Newest-wins cumulative history | Yes — `publication_history_grain` | Default dashboard table |
| Current publication pointer | Yes — `publication_current` | Excel “current snapshot”; **not** the cumulative dashboard default |
| KPI formula registry | Yes — `kpis.py` + `kpi_definition` | Reuse; do not fork |
| SUM-then-KPI engine | Yes — `aggregate.py` / `divide.py` | Server-side only |
| Reporting grains | Yes — `grains.py` | Date/campaign/variation; no product grain |
| QA deterministic rules | Yes — inspector-only | Not client Overview |
| HTTP KPI/query API | **No** (explicitly deferred in `ANALYTICS.md`) | **Required for D1** |
| Website analytical Overview | **No** | `/admin` Dashboard is operational; `/client` “Overview” is a published-report portal |
| Insights / anomaly / ask | **No** | D6–D8 |
| Warehouse / BigQuery | **No** | Not justified at current scale |

Conceptual target (confirmed as the right consumer architecture):

```
publication_history_grain  (and optional named publication_fact snapshot)
        → Analytics Query Service (missing)
        → Metric Registry (kpis.py) + Dimension Registry (fact columns)
        → Query / aggregation (SQL SUM, then kpis.compute_kpis)
        → Dashboard state (SPA)
        → Presentation (Overview first; explorer later)
        → Optional insight / AI last (approved operations only)
```

Excel remains the controlled report artifact. Dashboard must not become a second calculation engine.

---

## 3. PublishedFacts / History Schema Summary

### Grain

One fact row = `(client_id, campaign_id, variation_id_key, day)`.

Variation is required (`variation_id_key` is `""` when Variation ID is blank). Not keyed by month or `publication_id` on the serving table.

### Tables

| Object | Role |
|---|---|
| `fact_campaign_day` | Working set (unpublished). **Do not** use for client Overview. |
| `publication` | One publish event; `fact_scope`, `snapshot_status`, optional `period_start`/`period_end` |
| `publication_current` | One current pointer per company |
| `publication_fact` | Immutable snapshot rows for one publication |
| `publication_history_grain` | Newest complete snapshot wins per grain. Excel history CSV and dashboard history must use this. |
| `published_fact_campaign_day` / `rpt_*` | SQL views for Power BI / current pointer |

Corrected month: republish overlapping grains; history serving keeps **newest `published_at`**. Duplicate months do not appear in history grain. Older snapshots remain addressable as `GET /publications/{id}/facts`.

`/publications/current/facts` is the **pointed snapshot**, not a union of all months. Product rule “old + new published history” maps to **history grain**, not current-only.

### Client-facing 45 PublishedFacts columns

Excel `FACT_HEADERS` / API `FactResponse` (not the 67-column Web Engage staging map):

Filter Logic 1–2, Template Status, AMC Status / Device / Product Cat, Manual Or Automated, Total Cost, HHH, Month, Day, Campaign Name/ID, Variation Name/ID, Channel, Type of Campaign, Start Date, Sent, Failed, Delivered, Unique Impressions/Clicks/Conversions, Unique Impression-Through Conversions, Unique Click-Through Conversions, Revenue (INR), Impression-Through Revenue (INR), Click-Through Revenue (INR), Template Name (WhatsApp), plus lineage (`client_id`, `variation_id_key`, `month_start`, `filter_logic_1_group`, match statuses, version ids, `first_seen_at`, `last_seen_at`).

Pivot **calculated measures** (Failed Rate SM, Overall ROAS, CTRs, …) are **not** database columns.

### Live local database (read-only, 2026-09-07)

| Measure | Value |
|---|---|
| `publication` rows | 20 |
| `publication_current` pointers | 9 |
| `publication_fact` / history grain rows | 460,866 |
| Working-set rows | 1,009,970 |
| History min/max `day` | 2025-06-01 … 2025-10-16 |
| Distinct `month_start` in history | 5 globally; **2–4 per company** |
| Largest tenant | 179,596 grains, 3,509 campaigns, 107 days, 4 months |

Schema **can** retain 12+ months. Live data does **not** yet demonstrate a year of history. YoY is therefore a later-phase capability, not D1.

---

## 4. Metric Semantics Matrix

Shared rules (authoritative: `kpis.py`, `divide.py`, `ANALYTICS.md`, Excel `MEASURE_OPS`):

- Aggregate **SUM first**, then divide/add/subtract.
- Never average daily ratios.
- NULL operand after SUM → NULL.
- Divide by zero → NULL (never 0).
- Rates: 6 decimal places. Money ratios: 4 decimal places (`numeric(18,4)`).
- Source names “Unique *” are **SUMMED**, not DISTINCT across rows.
- Native Web Engage rate columns are evidence-only and forbidden as KPI inputs.

### Additive measures

| Metric | Source | Type | Aggregation | Ratio Logic | Time Grain | Comparison | Excel Alignment | KPI | Trend | Ranking | Drilldown | Decision |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Sent | `sent` | additive count | SUM skip NULL | — | day / month / range | MoM/range OK | Nine-report SUM | Yes | Yes | Yes | Yes | **KEEP** |
| Failed | `failed` | additive count | SUM | — | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** (secondary) |
| Delivered | `delivered` | additive count | SUM; cost units | — | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** |
| Unique Impressions | `unique_impressions` | additive count | SUM of “unique” | alias Impressions | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** |
| Unique Clicks | `unique_clicks` | additive count | SUM | alias Clicks | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** |
| Unique Conversions | `unique_conversions` | additive count | SUM | — | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** |
| Unique CTC | `unique_click_through_conversions` | additive count | SUM | — | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** (secondary) |
| Unique ITC | `unique_impression_through_conversions` | additive count | SUM | — | same | OK | On sheet; **not** nine-report `MEASURE_HEADERS` | PBI/QA | Yes | Yes | Yes | **DEFER** homepage |
| Revenue (INR) | `revenue_inr` | additive money | SUM | alias Sales | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** |
| CT Revenue | `click_through_revenue_inr` | additive money | SUM | — | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** (secondary) |
| IT Revenue | `impression_through_revenue_inr` | additive money | SUM | — | same | OK | Not nine-report dataField | PBI/QA | Yes | Yes | Yes | **DEFER** homepage |
| Total Cost | `total_cost` | additive money | SUM; unmatched rate → **0.0000** | Delivered × rate card | same | OK | SUM | Yes | Yes | Yes | Yes | **KEEP** |

### Client-namespace ratios (Excel nine reports)

| Metric | Source | Type | Aggregation | Ratio Logic | Time Grain | Comparison | Excel Alignment | KPI | Trend | Ranking | Drilldown | Decision |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Failed Rate SM | failed/sent | ratio | SUM then divide | NULL if den 0 | recompute per bucket | OK if recomputed | calculated field | Yes | Yes | Yes* | Yes* | **KEEP** secondary |
| Actual Sent | sent−failed | linear | SUM parts | NULL if either NULL | same | OK | calculated | Yes | Yes | Yes | Yes | **KEEP** secondary |
| Delivery Rate | delivered/sent | ratio | SUM then divide | NULL if den 0 | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** D1 |
| Delivered to Imp. rate | impressions/delivered | ratio | SUM then divide | NULL if den 0 | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **MODIFY** (not D1 hero) |
| CTR (Del to Clicks) | clicks/delivered | ratio | SUM then divide | **this is DFIP “CTR”** | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** D1 |
| CTR (Impr. to Click) | clicks/impressions | ratio | SUM then divide | not Q&A CTR | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** D1 secondary |
| Cost/ UCT conversion | cost/UCT | money ratio | SUM then divide | CPA-like; **not named CPA** | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** secondary |
| UCT conversion rate | UCT/clicks | ratio | SUM then divide | | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **DEFER** D1 |
| Unique Click Through Conv ROAS | CT rev/cost | ROAS | SUM then divide | | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** secondary |
| Cost/Unique Conversion | cost/conversions | money ratio | SUM then divide | | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** D1 |
| Delivered Thru Conv. rate | conversions/delivered | ratio | SUM then divide | | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **DEFER** D1 |
| Overall ROAS | revenue/cost | ROAS | SUM then divide | same as QA `roas` | recompute | OK | calculated | Yes | Yes | Yes* | Yes* | **KEEP** D1 |

\*Ratio ranking/drilldown only if **recomputed at that grain**. Never SUM stored ratios.

### Requested names that DFIP does not support

| Metric | Decision |
|---|---|
| Orders | **UNSUPPORTED** — use conversion counts |
| CPC / CPM / CPA / CPO | **UNSUPPORTED** as names; Cost/Conv is the recovered cost-per |
| Video Views, Engagements, Visits, Units | **UNSUPPORTED** — not in FactRecord |
| ACOS | **UNSUPPORTED** — do not invert ROAS |
| Native Failed Rate / Delivered Rate columns | **REMOVE** from dashboard inputs |

QA-namespace duplicates (CTR vs CTR Del to Click, Cost/Conv vs Click Through Cost/Conv) stay in the registry for Power BI/QA. Dashboard should use **client** slugs that match Excel captions.

---

## 5. Dimension Capability Matrix

| Dimension | Available | Global Filter | Trend | Ranking | Drilldown | Comparison | Decision |
|---|---|---|---|---|---|---|---|
| Day | `day` date | Yes (range) | Yes | Weak | Yes | Yes | **KEEP** |
| Month | `month_start` / `month_label` | Yes | Yes | Yes | Yes | MoM yes; YoY later | **KEEP** |
| Campaign | `campaign_id` + `campaign_name` | Multi later | Breakdown | Yes (3.5k values) | Yes | Yes | **KEEP** |
| Channel | `channel` | Yes | Yes | Yes | Yes | Yes | **KEEP** |
| Filter Logic 1 | campaign label | Yes (Excel slicer) | Yes | Yes | Yes | Yes | **KEEP** |
| Filter Logic 1_2 / Group | `filter_logic_1_group` | Yes (Excel “Filter Logic 1_2”) | Yes | Yes | Yes | Yes | **KEEP** |
| Filter Logic 2 | label | Yes | Yes | Yes | Yes | Yes | **KEEP** D2+ |
| AMC Device (FL4) | label | Yes on some Excel sheets | Optional | Optional | Optional | Optional | **KEEP** D2, not D1 |
| AMC Product Cat (FL5) | label slicer **not** product grain | Yes | Optional | Optional | Slicer only | Optional | **KEEP** as slicer; **UNSUPPORTED** as product dimension |
| Variation | `variation_id_key` | Later | Optional | Optional | Yes (native grain) | Optional | **DEFER** D1–D2; **KEEP** D4 |
| Type of Campaign | text | Optional | Optional | Optional | Optional | Optional | **DEFER** |
| Template Status / Name | text | Optional | Weak | Weak | Weak | Weak | **DEFER** |
| Manual Or Automated | text | Optional | Optional | Optional | Optional | Optional | **DEFER** |
| HHH / Start Date | campaign attrs | No as period | No | No | No | No | **REMOVE** from global filters |
| Brand / Platform / Business | **not columns** | — | — | — | — | — | **UNSUPPORTED** |
| Product / ASIN | **not a grain** | — | — | — | — | — | **UNSUPPORTED** |
| Segment / Journey / Tags | staging JSONB only | — | — | — | — | — | **UNSUPPORTED** for PublishedFacts dashboard |
| `client_id` | tenant key | Backend only | N/A | N/A | N/A | N/A | **KEEP** as auth scope, never a user security filter |

Excel slicers already prove FL1, Channel, FL1_2, Month as the primary interactive set.

---

## 6. Date / History Capability

| Need | Support | Safety |
|---|---|---|
| Day | Native grain | **KEEP** |
| Week | No week column | **MODIFY** — derive ISO week in query layer if needed (D3+). Not D1. |
| Month | `month_start` derived from `day` | **KEEP**; default Overview period |
| Custom range | Filter `day >= / <` | **KEEP** D2; D1 can omit the control |
| All available history | History grain | **KEEP** D2; current live max ~4 months |
| Latest published month | `MAX(month_start)` per tenant on history grain | **KEEP** D1 default |
| MoM | Prior `month_start` | **KEEP** when that month exists; else hide comparison |
| YoY | Needs same month prior year | **DEFER** — live data does not yet span 12 months |
| Custom vs custom | Two range aggregates | **KEEP** D3+ |
| Corrected month | Newest-wins grain | **KEEP**; no extra UI if serving table is used |
| Duplicate months | Prevented on history grain | **KEEP** |
| Unpublished / failed runs | Not in history grain | **KEEP** |
| Current pointer vs history | Different semantics | Dashboard default = **history**. Offer “this publication only” later if needed. |

`publication.period_start/end` clip a snapshot; they are not a dashboard calendar.

---

## 7. API / Query Capability Audit

### Reusable

| Endpoint | Use for dashboard? |
|---|---|
| `GET /publications/history/facts` (+ `.csv`) | Truth dump for Excel; **not** KPI cards |
| `GET /publications/current/facts` | Current snapshot only |
| `GET /publications` | Metadata / empty-state “nothing published” |
| JWT `_resolve_client_id` + `require_company_active` | **Must** wrap any new analytics route |
| `dfip_analytics.aggregate_facts` / `compute_kpis` | In-process engine after SQL SUM |
| `Grain` enum | Server grouping vocabulary |

### Missing (document; D1 must add the minimum)

- No `GET /api/v1/analytics/*`
- No group-by, metric filter, or KPI aggregate
- No period comparison payload
- No dimension-value enumeration endpoint (needed for D2 filter pickers)
- Pagination is row-oriented (200 current / 250000 history), not aggregate-oriented
- Inspector `GET /facts` is working-set and **403 for clients**

### Required D1 query contract (do not implement in D0)

`GET /api/v1/analytics/overview` (name can be bikeshed in D1) scoped by JWT:

Request: `period=latest_month|month_start=YYYY-MM-DD|all` plus optional `compare=prior_month`.

Response: additive SUMs + client-namespace KPIs for current period and optional comparison, plus `period_start`, `period_end`, `as_of_month`, `row_count`.

Authorization: same as history facts (`PrincipalDep`, JWT client, `require_company_active`). Query `client_id` must not widen a bound token.

Implementation: `SELECT SUM(...) FROM publication_history_grain WHERE client_id = $jwt AND day in period` then `compute_kpis(..., namespace="client")`. Do not SUM ratios in SQL.

---

## 8. PostgreSQL Performance Findings

**Do not introduce BigQuery/warehouse.** Current history is sub-million rows. Measured on local Postgres (largest tenant `e7450265-…`, cold-ish cache):

| Shape | Filter | Plan (simplified) | Execution |
|---|---|---|---|
| KPI aggregate | Oct 2025 month | Bitmap Index Scan `publication_history_grain_page_idx` → 29,129 rows | **117 ms** |
| Top 25 campaigns | same month | HashAggregate 2,495 groups | **30 ms** (warm) |
| Daily series | Jun–Oct 2025 | **Parallel sequential scan** (179k tenant rows) | **118 ms** |

### Good indexes

- PK `(client_id, campaign_id, variation_id_key, day)`
- `publication_history_grain_page_idx (client_id, day, campaign_id, variation_id_key)` — used for month KPI

### Gaps / risks (document, do not migrate in D0)

- No dedicated `(client_id, month_start)` index
- No `(client_id, channel)` / label indexes on **history grain** (channel index exists on working set only)
- All-history series seq-scanned this tenant; still ~0.12s now, will grow with 12+ months
- Ranking 3,500 campaigns is fine; ranking + multi-dimension group-bys need limits (D5 pagination)
- Dashboard must **never** scan `fact_campaign_day` (1.0M working-set rows)
- Caching: HTTP ETag / short TTL per `(client_id, month)` is optional later (D9)

Materialized KPI cubes are **not** required for D1.

---

## 9. Security / Tenant Isolation Findings

| Rule | Evidence | Dashboard inheritance |
|---|---|---|
| JWT `client_id` is authoritative | `publication_service._resolve_client_id` | Required |
| Query `client_id` cannot widen | mismatch → 403 | Required |
| Client/reader blocked from working set | `InspectorDep` | Overview must use **published** routes |
| History facts require active company | `require_company_active` | Same for analytics |
| Inactive companies: data retained, live HTTP blocked | `lifecycle.py` | Correct for Overview |
| RLS `dfip_member_client` | defense in depth | Keep; do not treat as the product gate |
| Frontend filters | not a security boundary | Dimension filters are **narrowing only** |

Gaps to carry into D1 (do not “fix” silently in D0):

- `GET /publications/{id}/facts` has no `require_company_active` (metadata/snapshot path). Analytics must not use this as the default Overview source.
- `rpt_*` views can still be read via SQL tools; out of SPA scope.

Every D1–D9 operation must bind `client_id` from the principal, not from a trusted UI control.

---

## 10. Website Architecture Findings

Vanilla ES-module SPA (`apps/web/static`). No React. `sessionStorage` JWT. Static origin does not proxy the API.

| Route | Title / nav | Role |
|---|---|---|
| `/admin` | **Dashboard** | Operational control center (health, latest run, publish flag). **Do not rename or replace.** |
| `/client` | Nav label **Overview**; page title **Published reporting** | Publication metadata + downloads. **Not** KPI analytics. |
| `/client/facts` | Published data | Row table of **current** facts |

Reusable: `layout`, `pageHeader`, `metricCard`, `dataTable`, `errorBanner`, `emptyState`, `loadingState`, `DfipApiClient`, dark CSS tokens (`--accent`, `.metric-card`).

**Naming collision:** product wants analytical **Overview**, but the sidebar already uses “Overview” for `/client`. D1 must pick one:

1. Relabel `/client` nav to “Published reporting” / “Reports” and add `/client/overview` for analytics, or  
2. Replace `/client` body with analytics and move downloads under `/client/facts` / `/admin/downloads`.

**D0 recommendation:** option 1 — new `/client/overview`, keep `/admin` Dashboard, relabel existing `/client` nav. That is a D1 routing change, not D0.

Publisher should open the same Overview when a company is selected (`session.client_id`). Client-role users are the primary audience.

---

## 11. Reference Capability Mapping

Reference dashboards are interaction evidence only.

| Reference interaction | DFIP support | Decision |
|---|---|---|
| Hero KPI row + comparison delta | Yes with client KPIs + MoM | **KEEP** D1 |
| Date picker / month / all history | Yes on `day`/`month_start` | **KEEP** D2 (D1 = latest month only) |
| Channel / campaign / business filters | Channel + FL1/FL1_2 (not “brand”) | **MODIFY** D2 to DFIP labels |
| Dual-metric trend | Yes after query service | **KEEP** D3 |
| Day/week/month grain toggle | Day/month yes; week derived | **MODIFY** D3 |
| Ranked table / top N | Yes; cap N | **KEEP** D5 |
| KPI → dimension drill | Yes if filters inherit + ratios recomputed | **KEEP** D4 |
| Product/ASIN explorer | No grain | **UNSUPPORTED** |
| CPC/ROAS Amazon pack | No CPC; ROAS yes | **MODIFY** to DFIP names |
| Natural-language ask | Needs operation layer first | **DEFER** D8 last |
| PDF/PPT export | Possible later | **DEFER** until D5+ |
| Sparkline on every card | Decorative if no query grain | **DEFER** D3 |

---

## 12. D1–D9 Decision Matrix

| Phase | Decision | User Value | Data Fit | Correctness | Security | Performance | Complexity | Prerequisites | Risk | Recommended Effort |
|---|---|---|---|---|---|---|---|---|---|---|
| D1 KPI Overview | **KEEP** | High | High on Excel KPIs | High if reuse `kpis.py` | High if JWT scope | High at measured ~0.12s | Medium (needs query API + route) | History grain + KPI specs | Homepage KPI sprawl; `/client` name clash | M |
| D2 Global Filters | **KEEP** | High | High for FL1/Channel/Month | High | High if filters only narrow | Medium (value lists) | Medium | D1 query service | 3.5k campaigns as multi-select | M |
| D3 Dynamic Trends | **KEEP** | High | High day/month | High if recompute ratios | High | Medium; all-history seq-scan today | Medium | D1–D2 | Invalid metric pairs; week grain | M |
| D4 Drilldown | **KEEP** | High | High along Excel drill | High if context preserved | High | Medium | Medium–High (SPA state) | D2–D3 | Lost filters; ratio misuse | M |
| D5 Performance Explorer | **KEEP** | High | High | High | High | Need LIMIT/pagination | Medium | D2–D3 | Unbounded GROUP BY | M |
| D6 Insights | **DEFER** light D6 after D5 | Medium | Medium (short history) | Only if evidence-backed | High | Low | Medium | D3/D5 results | Hallucinated copy | S–M later |
| D7 Anomaly | **DEFER** | Medium | **Weak** (2–4 months) | Baselines unreliable | High | Unknown | High | ≥6–12 months retained | False positives | L later |
| D8 Contextual Ask | **DEFER last** | High later | Needs D1–D5 ops | Must refuse unknown | Critical: no model SQL | Depends | High | Approved operation catalog | SQL injection via LLM | L last |
| D9 Workspace | **DEFER** | High when D1–D5 exist | Yes | Yes | Yes | Caching/export | High | D1–D8 subset | Premature platform | L |

No D1A/D1B splits.

---

## 13. Recommended Analytics Architecture

```
[JWT company scope] → Analytics Query Service
                         ├─ read publication_history_grain only
                         ├─ SQL SUM of ADDITIVE_MEASURES
                         ├─ dfip_analytics.compute_kpis(namespace="client")
                         ├─ optional compare period (second SUM)
                         └─ never execute generated SQL
        Metric Registry = kpis.py (client) + Excel MEASURE_OPS captions
        Dimension Registry = fact columns listed in §5
        Dashboard State = SPA session + Overview URL/query (period)
        Presentation = /client/overview (D1 cards) → D3 charts → D5 explorer
        Excel / Power BI remain parallel consumers of the same grain + formulas
```

**Non-goals:** second metric engine, reading `stg_source_row`, reading working-set facts, trusting UI `client_id`.

---

## 14. Exact D1 Scope

Implement **only** after approval.

### In

1. **Analytics overview query** (new authenticated GET). JWT-scoped. `publication_history_grain`. SUM additives then client KPIs. Optional prior-month comparison. No group-by beyond the selected period. No working-set reads.
2. **Route** `/client/overview` (or approved alternative). Do **not** change `/admin` Dashboard.
3. **Nav:** add Overview under Reporting; relabel existing `/client` away from “Overview” if that name is taken for analytics.
4. **Default period:** latest `month_start` with history rows for the JWT company.
5. **KPI cards (hero):** Total Cost, Revenue (INR), Overall ROAS, Delivered, Unique Clicks, Unique Conversions, Delivery Rate, CTR (Del to Clicks).
6. **Secondary row (optional, still D1):** Unique Impressions, Cost/Unique Conversion, Sent. No sparkline requirement.
7. **Comparison:** MoM absolute + % when prior month exists; hide when it does not. No YoY.
8. **Empty states:** no publications / no history grain / inactive company (existing 403 copy).
9. **Units:** INR for money; counts as integers; rates as percent using existing Excel display intent (do not change stored precision).
10. **Tests:** authz (JWT wins), tenant isolation, SUM-then-divide vs a fixture, empty history, MoM absent.

### Out of D1

Charts, global dimension filters, week grain, YoY, drilldown, ranking explorer, insights, anomaly, AI, CSV/PDF/PPT export, metric-registry UI, schema migrations, Excel/Power BI edits, renaming Dashboard, reading current-only snapshot as the default (unless product later asks for a toggle).

### D1 KPI zero/NULL

- Additive 0 is a real zero.
- Ratio with 0 denominator or NULL operand: show em dash / “n/a”, never 0%.
- Unmatched cost rows already store `total_cost = 0.0000`.

---

## 15. Parked / Deferred Items

- YoY and 12-month default ranges until retention exists
- Week grain
- Variation / template / type-of-campaign as global filters
- Impression-through measures on Overview
- QA-only duplicate KPI names
- Dimension-value APIs (D2)
- Trends/charts (D3)
- Drill breadcrumbs (D4)
- Top/bottom movers, contribution % (D5)
- Deterministic insight sentences (D6)
- Anomaly severity (D7)
- Contextual Ask (D8) — last
- Saved views, export, cache (D9)
- `(client_id, month_start)` index — add when D1 query is implemented if EXPLAIN regresses

---

## 16. Unsupported Items

- Orders, CPC, CPM, CPA, CPO, Video Views, Engagements, Visits, Units
- Brand, platform, business (as distinct fields)
- Product/ASIN grain
- Native Web Engage rate columns as dashboard metrics
- Averaging daily CTR/ROAS
- Frontend company filter as authorization
- Arbitrary LLM SQL
- Dashboard rebuild on each publish (history grain already newest-wins)
- Warehouse/BigQuery at current scale
- Replacing Excel as the controlled artifact

---

## 17. Open Risks / Questions (product)

1. **Overview naming:** new `/client/overview` vs taking over `/client`? D0 recommends new route + relabel.
2. **Hero KPI set:** eight cards above vs Excel’s full 22 measures. D0 recommends eight + optional three.
3. **Publisher Overview:** same page when a company is selected, or clients only?
4. **Default “all history” vs latest month:** product asked latest month; confirm Overview does not start on all-history (that hides MoM meaning).
5. **Current snapshot toggle:** should Overview ever follow `publication_current` instead of cumulative history?
6. **YoY commitment:** wait until 12 months retained, or show “insufficient history”?
7. **Campaign filter cardinality:** 3,509 campaigns — search-select vs top-N only in D2?
8. **Inactive company:** Overview 403 vs last-known published (HTTP currently 403 for live published reads).

No D1 work should guess these silently; 1–4 should be confirmed at approval.

---

## 18. D0 Completion Checklist

| Check | Result |
|---|---|
| Repository inspected | Yes |
| Schema inspected | Yes — migrations + stores |
| PublishedFacts inspected | Yes — 45 `FACT_HEADERS` / `FactResponse` |
| Metric definitions inspected | Yes — `kpis.py`, Excel `MEASURE_OPS` |
| Dimensions inspected | Yes — fact columns + Excel slicers |
| API/query layer inspected | Yes — row dumps only; KPI HTTP missing |
| PostgreSQL performance inspected | Yes — live counts + three `EXPLAIN ANALYZE` |
| Security inspected | Yes — JWT scope, active-company, RLS defense |
| Website architecture inspected | Yes — Dashboard vs `/client` Overview collision |
| Excel/Power BI semantic alignment inspected | Yes — client namespace / `rpt_*` |
| D1–D9 challenged | Yes — §12 |
| Reference features challenged | Yes — §11 |
| No dashboard UI implemented | Yes |
| No production behavior changed | Yes — documentation + this audit only |

---

## Appendix A — Checks performed

- Read-only SQL totals and per-client grain counts (`tmp/d0_scale_probe.py`, gitignored).
- `EXPLAIN ANALYZE` KPI month SUM, campaign top-25, daily series (`tmp/d0_explain.py`, gitignored).
- No writes, no new API routes, no SPA routes, no migrations.

## Appendix B — D1 recommendation (one paragraph)

Approve D1 as a JWT-scoped Overview KPI page plus a published-history aggregate endpoint that reuses `dfip_analytics` client KPIs, defaults to the latest month, compares MoM when possible, and leaves `/admin` Dashboard untouched. Do not start D2–D9 or AI until that query contract and KPI set are accepted.
