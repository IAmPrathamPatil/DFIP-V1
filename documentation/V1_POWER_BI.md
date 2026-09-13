# DFIP V1 — Power BI

**Completion status: PARTIAL (source package only). Desktop report canvas: NOT VERIFIED. Binary `.pbix`: NOT IN REPOSITORY.**

Evidence: `powerbi/` tree, `powerbi/README.md`, `documentation/DESKTOP_ACCEPTANCE.md` (table row “Power BI Desktop … **P9 / NOT VERIFIED**”, “Local `DFIP.pbix` **NOT VERIFIED (not saved)**”), `packages/analytics/dfip_analytics/powerbi_contract.py`, tests `test_v2_phase2b_model.py`, `test_v2_phase2b_postgres.py`.

This is **not** Excel Power Query. Power BI is specified to read PostgreSQL **`rpt_*`** views, never working-set tables.

---

**Can you open/run a report from git?** **No.** There is no PBIX. Desktop must be built by an operator from `powerbi/README.md`. M files are PostgreSQL DirectQuery specs, not a runnable workbook.

`dfip_analytics.powerbi_contract` (`ALLOWED_TABLES`, `FORBIDDEN_TABLES`, relationship dataclasses) is the **testable Python contract** for those files (`test_v2_phase2b_model.py`).

## What exists (VERIFIED files)

| Path | Role |
|---|---|
| `powerbi/README.md` | Operator build instructions |
| `powerbi/queries/rpt_published_fact.m` | Fact query |
| `powerbi/queries/rpt_dim_date.m` | Date dimension |
| `powerbi/queries/rpt_dim_client.m` | Client |
| `powerbi/queries/rpt_dim_campaign.m` | Campaign |
| `powerbi/queries/rpt_dim_variation.m` | Variation (empty `variation_id_key` as `""`) |
| `powerbi/dax/measures.dax` | Canonical measures (paste into Desktop) |
| `powerbi/model/semantic-model.json` | Relationships spec |
| `powerbi/model/pages.json` | Five pages, slicers, visuals **as a spec** — not a built report |
| `powerbi/model/kpi-mapping.md` | KPI mapping notes |
| `powerbi/theme/dfip-theme.json` | Theme JSON |
| `powerbi/sql/reporting_login.example.sql` | Example LOGIN (passwords not in git) |
| `powerbi/sql/validation_kpis.sql` | SQL KPI check vs Desktop cards |
| `powerbi/sql/inspector_not_in_model.sql` | Forbidden tables check |
| `powerbi/VALIDATION.md` | Validation notes |

SQL views themselves: `supabase/migrations/20260823000014_v2_analytics_reporting.sql` (**requires PostgreSQL**).

---

## What does not exist

- Committed `*.pbix` / `*.pbit` — glob found **zero**.
- Automated compile of M+DAX → pbix (README: no CLI; do not unzip-fabricate).
- Verified five-page canvas, visuals, slicers in Desktop.
- Azure AD → `dfip.client_ids` session mapping — **BLOCKED** / parked (`DESKTOP_ACCEPTANCE.md`).
- On-premises gateway / workspace deployment config in repo.

Reporting logins `dfip_desktop_a` / `_b` were **MANUALLY VERIFIED against `rpt_*` SQL**, not against a Power BI canvas.

P13F company deactivation is enforced by the DFIP API. `rpt_*` views and RLS
are unchanged, so a DirectQuery model can still see an inactive tenant if the
SQL login is allowed. P14 / Power BI work must account for this. Dashboard
D0–D9 are not started.

---

## Intended semantic model (spec)

**Allowed sources:** `rpt_published_fact`, `rpt_dim_date`, `rpt_dim_client`, `rpt_dim_campaign`, `rpt_dim_variation`.

**Forbidden:** `fact_campaign_day`, `stg_*`, `processing_run`, `qa_finding`, unpublished working set.

**Relationships (from README / semantic-model.json):** four single-direction `1:*` — date, client, `campaign_key`, `variation_key`.

**DirectQuery default.** Import must fully replace after every publish. Incremental refresh on `day` can serve a **stale pointer** — not recommended.

**Credentials:** Desktop user is **not** `dfip_api` (NOLOGIN). Example SQL creates logins that `SET ROLE dfip_api` with encoded client GUCs. Unset GUCs → no tenant fact rows; date dim still readable.

Parameters: `PgServer` (`host` or `host:port`), `PgDatabase`. Encrypt hosted Postgres (`sslmode=require`).

---

## Specified report pages (`pages.json`)

1. Executive Overview  
2. Campaign Performance  
3. Client Performance  
4. Campaign Label / Product-Category Analysis  
5. Published Data Health  

Canvas 1280×720. Navigation buttons specified. Ratio cards `%`; money `₹ #,0.00`; ROAS as number.

**Not built in this repo.** Treat as PLANNED/SPEC unless an operator’s local `DFIP.pbix` exists outside git.

---

## DAX (package)

Rules: `DIVIDE(SUM(num), SUM(den))`. Never `AVERAGE([Daily CTR])` or `SUM([CTR])`. BLANK on zero/missing denominator.

Additive examples from `measures.dax`: Sent, Failed, Delivered, Impressions (`unique_impressions`), Clicks, Conversions (`unique_conversions`), Impression-Through / Click-Through conversions and revenue, Total Cost.

Recovered KPIs include CTR (`unique_clicks/delivered`), Conversion Rate, ROAS, Delivered Rate*, Cost/Conv (click-through den), etc. Full file is the authority — do not paste secrets; there are none in DAX.

Hide lineage columns on fact: `processing_run_id`, `batch_id`, `first_seen_at`, `last_seen_at`.

---

## Refresh / credentials / deployment

| Topic | Status |
|---|---|
| Refresh | Operator Desktop; follows `publication_current` if DirectQuery |
| Credentials | Local Desktop; **never** service-role in pbix |
| Workspace | UNKNOWN / not in repo |
| Gateway | UNKNOWN / not in repo. V1 production PostgreSQL is colocated and not public; a future Desktop/gateway path would need a private operator route, not a public 5432. P13G does not deploy Power BI. |

---

## Relation to Excel V1

Excel client reports **do not** use this Power BI package. They use the HTTP published-facts API. Completing Power BI is **not** required for Excel V1 client delivery.

RUN 009 approved extras (for example `Campaign Objective`) do **not** automatically become `rpt_*` columns, DAX measures, or semantic-model dimensions. Power BI support for a new field requires an explicit later package/view update. Do not invent dynamic DAX.

---

## Tests

`test_v2_phase2b_model.py` — JSON/M/DAX contract tests (no Desktop).  
`test_v2_phase2b_postgres.py` — marked postgres; needs `DFIP_TEST_DATABASE_URL`.
