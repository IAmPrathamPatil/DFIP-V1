# DFIP Power BI — Phase 2B setup

This folder is the version-controlled Power BI reporting layer. It is **not** a
binary `.pbix`. Build the Desktop report from these artifacts. Do not point
Power BI at working-set tables.

## Allowed sources

| Object | Role |
|---|---|
| `rpt_published_fact` | Additive published facts |
| `rpt_dim_date` | Date table (mark as date table on `day`) |
| `rpt_dim_client` | Client slicer |
| `rpt_dim_campaign` | Campaign + label attributes |
| `rpt_dim_variation` | Variation, including empty `variation_id_key` |

Do **not** load: `fact_campaign_day`, `stg_*`, `processing_run`, `qa_finding`,
or any unpublished working-set table.

## Connection

1. PostgreSQL 16. Encrypt the Desktop connection (`sslmode=require` on hosted
   Postgres). `PgServer` may be `host:port`.
2. Do **not** connect as `dfip_api`. That role is **NOLOGIN**. Apply
   `sql/reporting_login.example.sql` once (operator SQL, not a migration) and
   connect as `dfip_desktop_a` / `dfip_desktop_b`. Those logins `SET ROLE
   dfip_api` and encode one client in role defaults.
3. Unset GUCs return no tenant fact rows. `rpt_dim_date` remains readable.
4. No service-role credentials in the `.pbix` or gateway. Do not commit
   reporting-login passwords.

## DirectQuery vs Import

**DirectQuery is the default.** Publication is a pointer
(`publication_current`). Import must fully replace the published slice after
every publish. Incremental refresh on `day` can serve a stale pointer and is
not recommended.

## Build in Power BI Desktop

Desktop (this environment): `C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe`
(2.155.756.0). There is no CLI that compiles this package into a valid `.pbix`.
Do not unzip or fabricate a binary.

1. **PostgreSQL connection.** Get Data → PostgreSQL. Create parameters
   `PgServer` (host or `host:port`) and `PgDatabase`. User `dfip_desktop_a`
   (see `sql/reporting_login.example.sql`). Do not use `dfip_api` (NOLOGIN),
   a superuser, or a service-role credential. Enable encryption on hosted
   Postgres.
2. **Session identity.** The reporting login already encodes `dfip.role`,
   `dfip.client_ids`, and `dfip.platform_admin`. Unset GUCs return no tenant
   fact rows. `rpt_dim_date` remains readable.
3. **Load the five `rpt_*` objects** using the queries in `queries/`. Do not
   navigate to `fact_campaign_day`, `stg_*`, `processing_run`, or `qa_finding`.
4. **Keys.** On `rpt_published_fact` and `rpt_dim_campaign` add `campaign_key`.
   On fact and `rpt_dim_variation` add `variation_key`. Keep empty
   `variation_id_key` as `""`.
5. **Date table.** Mark `rpt_dim_date[day]` as the date table.
6. **Relationships.** Create the four single-direction `1:*` relationships in
   `model/semantic-model.json` (date, client, campaign_key, variation_key).
7. **Hide lineage columns** `processing_run_id`, `batch_id`, `first_seen_at`,
   `last_seen_at` on the fact table.
8. **DAX measures.** New table `Measures` (optional empty calculated table)
   and paste `dax/measures.dax`.
9. **Theme and pages.** Import `theme/dfip-theme.json`. Build the five pages
   from `model/pages.json`. Add page-navigation buttons. Format ratio cards as
   `%` and money as `₹ #,0.00` (ROAS as a number).
10. **Validate and save.** Run `sql/validation_kpis.sql` as `dfip_desktop_a`.
    Compare Desktop cards to that result. Save as `DFIP.pbix` **locally**. Do
    not commit the binary.

## DAX rules

```dax
CTR = DIVIDE ( SUM(rpt_published_fact[unique_clicks]), SUM(rpt_published_fact[delivered]) )
```

Never `AVERAGE([Daily CTR])`. Never `SUM([CTR])`. DIVIDE returns BLANK on a
zero or missing denominator.

Recovered Q&A **CTR is clicks / delivered**. Impression CTR is the separate
measure `QA CTR ( Impr. to Click )`.

## KPI mapping

See `documentation/ANALYTICS.md` and `packages/analytics/dfip_analytics/kpis.py`.
Every recovered KPI has a DAX measure in `dax/measures.dax`. Executive cards
use the Q&A names plus `Delivery Rate` (same formula as Delivered/Sent).

Unavailable: Orders, Amazon CPC, ACOS, Product/ASIN grain.
`amc_product_cat_filter_logic_5` is a campaign-label slicer.

## QA page

`Published Data Health` uses **published-fact flags only** (impossible
delivered/failed, clicks > impressions, zero delivery, unmatched labels,
zero-denominator cards). Inspector findings live in `qa_finding` and are
**not** in this model. See `sql/inspector_not_in_model.sql`.

## Validation

1. Run `sql/validation_kpis.sql` as `dfip_api` with tenant GUCs set.
2. Compare card values in Desktop to that result (DIVIDE vs
   `SUM / NULLIF(SUM, 0)`).
3. Confirm two days with different CTRs combine as total clicks / total
   delivered, not the average of daily CTR.
4. Confirm unpublished campaigns do not appear.
5. Confirm a reader role cannot see `fact_campaign_day` or `qa_finding`.

Automated checks: `tests/test_v2_phase2b_model.py` and
`tests/test_v2_phase2b_postgres.py`. Recorded results and the Desktop
checklist: `VALIDATION.md`.

## Security

Client isolation is PostgreSQL RLS + GUCs on `rpt_*`, not a DAX RLS
expression. Power BI RLS is optional extra scoping and must not replace
database membership. Azure AD → `dfip.client_ids` mapping is **BLOCKED**
(Entra ID tenant, app registration, Azure AD authentication to PostgreSQL).
It is not required for the JWT API or Excel refresh flow.
