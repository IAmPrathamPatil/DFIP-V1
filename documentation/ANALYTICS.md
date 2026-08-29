# DFIP V2 analytics and Power BI reporting contract

Phase 2A adds a post-aggregation KPI calculator, an inspector-only QA layer,
and published-only reporting views. Phase 2B adds the version-controlled
Power BI package under `powerbi/`. Neither phase adds a committed `.pbix`,
`/api/v2`, or Amazon ads metrics. Authenticated workbook ingest lives on
`POST /api/v1/uploads` (see `documentation/HTTP_WORKFLOW.md`).

V1 `/api/v1` contracts, `FactResponse`, `MAX_PAGE_LIMIT = 200`, and P4
calculation modules are unchanged. KPI HTTP routes (`GET /api/v1/analytics/kpis`)
remain deferred. Inspector QA findings are listed at
`GET /api/v1/processing-runs/{processing_run_id}/qa-findings` (admin/publisher,
client-scoped). Power BI connects to PostgreSQL reporting objects, not to the
working-set tables or `qa_finding`.

## Architecture

```
Web Engage workbook
    → POST /api/v1/uploads (admin/publisher)
    → P3 ingest (stg_source_row / stg_rejected_row)
    → P4 transform (fact_campaign_day working set)
    → evaluate_qa + persist qa_finding (inspector-only; does not publish)
    → POST /api/v1/publications (explicit)
    → publication_current
    → published_fact_campaign_day
    → dfip_analytics KPI calculator (library)
    → rpt_* reporting views
    → Power BI (Phase 2B dashboards; MANUAL Desktop)
    → GET /api/v1/publications/current/facts.csv|.xlsx
```

Authoritative KPI formulas live in `packages/analytics/dfip_analytics/kpis.py`
and must match `kpi_definition` rows from migrations `20260823000004` (qa) and
`20260823000009` (client).

## KPI dictionary

Calculations always **aggregate first, then apply the operation**. Ratio KPIs
are never averaged across days.

Shared rules:

- NULL operand after SUM → NULL
- Divide by zero → NULL (never 0, never an invented rate)
- Rate KPIs quantize to 6 decimal places (`0.000001`), `ROUND_HALF_UP`
- Money ratios quantize to 4 decimal places (`0.0001`), matching `numeric(18, 4)`
- Linear add/subtract of counts are exact decimals
- Native Web Engage rate columns are evidence-only and are rejected as inputs
- `is_linear = false` KPIs are **not** safe for Power BI SUM

### Additive measures (Power BI SUM)

| Field | Alias (docs only) | Unit |
|---|---|---|
| `sent`, `failed`, `delivered` | | count |
| `unique_impressions` | Impressions | count |
| `unique_clicks` | Clicks | count |
| `unique_conversions` | | count |
| `unique_impression_through_conversions` | | count |
| `unique_click_through_conversions` | | count |
| `revenue_inr` | Sales | INR |
| `impression_through_revenue_inr` | | INR |
| `click_through_revenue_inr` | | INR |
| `total_cost` | Spend | INR (Delivered × rate card) |

### Q&A namespace (`qa`)

| Slug | Formula after SUM | Additive | Power BI |
|---|---|---|---|
| `ctr` | `unique_clicks / delivered` | no | DAX DIVIDE |
| `conversion_rate` | `unique_conversions / delivered` | no | DAX DIVIDE |
| `roas` | `revenue_inr / total_cost` | no | DAX DIVIDE |
| `delivered_rate_star` | `delivered / sent` | no | DAX DIVIDE |
| `cost_conv` | `total_cost / unique_click_through_conversions` | no | DAX DIVIDE |
| `click_thou_conv_rate` | `unique_click_through_conversions / delivered` | no | DAX DIVIDE |
| `click_through_cost_conv` | same as `cost_conv` (duplicate retained) | no | DAX DIVIDE |
| `cost_view_through_conv` | `total_cost / unique_impression_through_conversions` | no | DAX DIVIDE |
| `view_through_conv_rate` | `unique_impression_through_conversions / delivered` | no | DAX DIVIDE |
| `click_plus_view_conv` | UIT conv + UCT conv | yes | SUM parts, then add |
| `click_plus_view_revenue` | IT revenue + CT revenue | yes | SUM parts, then add |
| `cost_click_plus_view_conv` | `total_cost / click_plus_view_conv` | no | after the add |
| `all_click_plus_view_conv_rate` | `click_plus_view_conv / delivered` | no | after the add |
| `delivered_to_imp_rate` | `unique_impressions / delivered` | no | DAX DIVIDE |
| `ctr_del_to_click` | same as `ctr` (duplicate retained) | no | DAX DIVIDE |
| `ctr_impr_to_click` | `unique_clicks / unique_impressions` | no | DAX DIVIDE |

Recovered Q&A **CTR is clicks / delivered**, not clicks / impressions.

### Client namespace (`client`)

Includes `actual_sent = sent - failed` (additive) and `overall_roas =
revenue_inr / total_cost`. Unused-by-reports flags from P2 are preserved.
Slugs can collide with Q&A slugs; they are distinct by namespace.

### Unavailable (do not invent)

| Requested name | Status |
|---|---|
| Orders | No order count. Use conversion counts. |
| Amazon CPC | Not a recovered KPI. Closest: Cost/Conv. |
| ACOS | Amazon ads metric. Do not invert ROAS. |
| Product / ASIN grain | Not in the fact model. |
| Native Failed Rate / Delivered Rate columns | Evidence only. |

`amc_product_cat_filter_logic_5` is a **campaign attribute / slicer**, not a
product dimension key.

## Reporting grains

Supported: Client, Date, Campaign, Variation, Client+Date,
Client+Campaign+Date, Client+Campaign+Variation+Date (native fact grain).

Drill path: **Client → Date → Campaign → Variation → Day**.

Not supported: Product/ASIN, Client+Product+Date.

## QA engine

Library: `packages/analytics/dfip_analytics/qa.py`.
Table: `qa_finding` (migration 14). Inspector-only FORCE RLS. Not a Power BI
source. HTTP ingest calls `evaluate_qa` after transform and persists findings
for that `processing_run_id`. `processing_run.qa_verdict` is written from those
findings:

| Verdict | Meaning | Publish |
|---|---|---|
| `pass` | No `error` or `warning` findings (info-only is still pass) | Allowed, explicit Publish only |
| `warn` | One or more `warning` findings, no `error` | Allowed, explicit Publish only |
| `fail` | One or more `error` findings | Blocked |
| `unavailable` | QA did not complete | Blocked |
| `null` | QA has not been recorded | Blocked |

Zero findings after a successful evaluation is `pass`, not `null`. A QA
engine/load/persist failure is `unavailable` and must not be stored as an empty
finding set that looks like pass. Re-evaluate with
`POST /api/v1/processing-runs/{id}/qa`. Findings do not auto-publish a run.

| Rule ID | Severity | Evidence |
|---|---|---|
| QA-MISSING-KEY | error | `MISSING_CAMPAIGN_ID` / blank campaign_id |
| QA-INVALID-NUMERIC | error | `INVALID_NUMERIC` |
| QA-DUPLICATE-GRAIN | error | `DUPLICATE_GRAIN_KEY` |
| QA-NEGATIVE-COUNT | error | Negative count on a fact |
| QA-NEGATIVE-MONEY | error | Negative money on a fact |
| QA-IMPOSSIBLE-DELIVERED | warning | delivered > sent |
| QA-IMPOSSIBLE-FAILED | warning | failed > sent |
| QA-RATIO-GT-ONE | warning | inverted count relationships |
| QA-ZERO-DENOMINATOR | info | ratio denom SUM = 0 |
| QA-MISSING-DATE | error | `MISSING_DAY` |
| QA-MALFORMED-ID | error | `INVALID_DAY` / `IMPOSSIBLE_ROW` / non-UUID ids |
| QA-CLIENT-MISMATCH | error | fact.client_id ≠ run client |
| QA-REJECTED-ROWS | warning | `stg_rejected_row` count |
| QA-RUN-FAILED | error | `processing_run.status = failed` |
| QA-RECONCILE-FAIL | error | P8 `reconcile_run` result (not a forked formula) |
| QA-UNPUBLISHED-ONLY | info | facts exist but run is not current publication |

## Reporting views

Approved Power BI objects:

| Object | Kind | Grain / role |
|---|---|---|
| `rpt_published_fact` | view | Published fact grain. Additive measures only. |
| `rpt_dim_client` | view | Client slicer (`dfip_member_client`) |
| `rpt_dim_campaign` | view | Distinct published campaigns (latest `last_seen_at`) |
| `rpt_dim_variation` | view | Distinct published variations, including empty key |
| `rpt_dim_date` | table | Calendar 2020-01-01 .. 2035-12-31 |

Forbidden working-set objects (never in a Power BI model):

- `fact_campaign_day`
- `fact_campaign_day_history`
- `stg_source_row`
- `stg_rejected_row`
- `processing_run` (working inspection)
- `qa_finding`
- unpublished rows on a non-current `processing_run_id`

`rpt_published_fact` selects from `published_fact_campaign_day`, which already
clips to `publication_current` and `dfip_member_client`. Unset GUCs return no
tenant rows. `rpt_dim_date` is not tenant-owned and remains readable.

There are **no** row-level CTR / ROAS / conversion-rate columns. Those would
be wrong if summed or averaged in Power BI.

## Power BI consumption

### Connection

- PostgreSQL 16
- Role `dfip_api` (`NOBYPASSRLS`)
- Transaction-local GUCs must be set the same way the API sets them:
  `dfip.role`, `dfip.client_ids`, `dfip.platform_admin`
- Not PostgREST, not OData, not Excel redesign, no service-role credentials
  in the browser or the `.pbix`

DirectQuery is the safer default because publication is a **pointer**. Import
mode must fully replace the published slice after every publish. Incremental
refresh on `day` can serve a stale pointer and is not recommended unless the
partition is rebuilt on publish.

### Relationships

```
rpt_dim_client[client_id]     1 → *  rpt_published_fact[client_id]
rpt_dim_date[day]             1 → *  rpt_published_fact[day]
rpt_dim_campaign[client_id + campaign_id]  1 → *  fact
rpt_dim_variation[client_id + campaign_id + variation_id_key]  1 → *  fact
```

### Measures (Power BI, not SQL)

```dax
CTR = DIVIDE(SUM(rpt_published_fact[unique_clicks]), SUM(rpt_published_fact[delivered]))
ROAS = DIVIDE(SUM(rpt_published_fact[revenue_inr]), SUM(rpt_published_fact[total_cost]))
```

Never `AVERAGE([Daily CTR])` and never `SUM([CTR])`.

Dependent KPIs: compute `Click + View Conv` from summed parts, then DIVIDE
cost by that result.

### Slicers

Client, date / month (`rpt_dim_date`), campaign, channel, type of campaign,
variation (including blank), `amc_product_cat_filter_logic_5` and other label
attributes.

### Drill-down

Client → Date → Campaign → Variation → Day.

### Phase 2B dashboards

Version-controlled package in `powerbi/`. Build the Desktop report from
`powerbi/README.md`. Bind **only** `rpt_*`. Do not fabricate a committed
`.pbix`. Validation: `powerbi/VALIDATION.md` plus
`tests/test_v2_phase2b_model.py` / `tests/test_v2_phase2b_postgres.py`.

1. Executive Overview
2. Campaign Performance
3. Client Performance
4. Campaign Label / Product-Category Analysis (not ASIN)
5. Published Data Health (published-slice flags only; `qa_finding` stays inspector-only)


## API

Inspector Review can list persisted findings:

`GET /api/v1/processing-runs/{processing_run_id}/qa-findings`

Admin/publisher only. JWT or bound development `client_id` is authoritative.
Another client's run is 404. Findings are informational: they do not auto-publish
and they do not block `POST /api/v1/publications` unless the run already failed
P4 (`status` is not `succeeded`).

Phase 2A still does **not** add `GET /api/v1/analytics/kpis` or
`GET /api/v1/analytics/qa-findings`. KPIs remain a library (`dfip_analytics`)
plus SQL views.

## Security

- FORCE RLS on `qa_finding` (inspector roles only)
- Reporting facts/dims inherit published-slice tenant filters
- Client A cannot see Client B
- Readers cannot SELECT working-set facts or QA findings
- Platform admin still uses `dfip_platform_admin`
- No SECURITY DEFINER shortcut around membership

## Known limitations

- Unique counts are still SUM'd at report grain (source naming is "unique")
- Campaign dimension attributes are taken from the latest published fact for
  that campaign, not a slowly-changing dimension
- Date dimension is a fixed calendar, not distinct fact days
- QA does not auto-publish. `qa_verdict` is written after evaluation.
- Power BI identity mapping (Azure AD → `dfip.client_ids`) is **BLOCKED**
  (Entra ID tenant, app registration, Azure AD authentication to PostgreSQL).
  It is not required for the JWT API or Excel refresh flow.
