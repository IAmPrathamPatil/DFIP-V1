# DFIP V1 database schema

The HTTP API reads P3/P4 stores. Empty `DATABASE_URL` keeps the V1 in-memory
adapters. When `DATABASE_URL` is set, PostgreSQL adapters implement the same
store protocols.

Migrations: `supabase/migrations/2026082300000{1,2,3,4}_p1_*.sql`, P2
`2026082300000{5,6,7,8,9}_p2_*.sql`, and P3
`20260823000010_p3_batch_ingest_metadata.sql`. **P4 adds no migration**; the
transformation engine writes only into columns P1 already defined.

PostgreSQL 15+ / Supabase compatible. P1 does **not** require a live database to
develop or to run tests. Tests inspect the SQL. Apply the migrations in a real
Postgres/Supabase project when one is available.

No RLS and no authentication objects are created in P1. Phase 1 appends
identity, membership, and RLS as defense in depth; see below.

## Table map

| Table | Purpose |
|---|---|
| `client` | Single-tenant placeholder |
| `we_source_column` | Excel letter/header ↔ database column (67 rows A:BO) |
| `source_file` | Uploaded artifact metadata + SHA-256 |
| `batch` | One ingestion attempt of one file (P3 adds worksheet/region metadata) |
| `processing_run` | A transform attempt bound to four config versions |
| `stg_source_row` | Arriving 57-column payload as JSONB (exact headers as keys) |
| `stg_rejected_row` | Rows that fail later validation |
| `campaign_label_version` / `campaign_label_row` | Versioned New Logic A:M |
| `template_label_version` / `template_label_row` | Versioned New Logic Q:R |
| `rate_card_version` / `rate_card_rule` | Versioned Total Cost rules |
| `label_group_version` / `label_group_member` | Filter Logic 1_2 grouping |
| `fact_campaign_day` | Grain `(client_id, campaign_id, variation_id_key, day)` |
| `fact_campaign_day_history` | Superseded fact rows |
| `publication` / `publication_current` | What the client may later see |
| `publication_fact` | Immutable snapshot of one publication's facts |
| `kpi_definition` | 16 Q&A KPIs (P1) plus 15 client-report KPIs (P2) |
| `audit_log` | Append-only metadata log |

V2-C publisher Logic/Labels uploads write the existing `campaign_label_*` and
`label_group_*` tables (`draft` → explicit `active` / `superseded`). Migration
`20260827000015_v2c_catalog_write_grants.sql` grants INSERT/UPDATE to
`dfip_api` / `dfip_worker`. Locked files `20260823000001`–`14` are unchanged.

## Grain and keys

Locked business key: `(campaign_id TEXT, variation_id TEXT, day DATE)`.

PostgreSQL primary keys cannot contain NULL. Eleven production rows have a blank
Variation ID. `variation_id` stores the original value (nullable).
`variation_id_key` is NOT NULL so the PK can exist.

### OPEN-A6 — resolved in P4

`variation_id_key = COALESCE(variation_id, '')`.

A blank Variation ID becomes the **empty string**. Reasoning:

- Excel cannot distinguish an empty cell from an empty string, so `NULL` and
  `''` are the same business value ("no variation") and must share one key.
- The empty string cannot collide with any non-blank Variation ID, so no
  legitimate row is merged into the blank bucket.
- No sentinel literal (`'(blank)'`, `'-'`, a UUID) is invented, so nothing has
  to be decoded on the way out and nothing can collide with a real ID that
  happens to look like the sentinel.

`variation_id` keeps the original NULL, so the distinction is never lost.
Regression tests: `tests/test_p4_transform.py::test_blank_variation_id_uses_the_empty_string_key`
and `::test_blank_variation_key_cannot_collide_with_a_real_variation`.

## Label versions

Campaign labels, template labels, and rate cards are **three independent
version axes**. Group mappings are a fourth. A `processing_run` points at one
of each. Retrospective restatement (OPEN-A1) is supported later by writing a
new run rather than mutating facts in place.

P2 loads snapshots onto these axes. `label_group_member.group_name` and
`filter_logic_1_value` are nullable because the recovered Filter Logic 1_2
membership includes a blank/blank row. That is a P2 source-driven ALTER of the
P1 table, not a redesign.

`campaign_label_row` **allows duplicate `campaign_name`**. The unique key is
`(version_id, row_order)`. `campaign_name_key` is `lower(campaign_name)` with
**no trim**, matching recovered VLOOKUP semantics. First-match-wins is
`ORDER BY row_order` on that key. The same pattern applies to template names.

New Logic column I (`Template Status` on the sheet) is stored as
`unused_sheet_template_status`. Recovered lookups do not use it. Template
Status used by cost is New Logic column R (header `Rate`) →
`template_label_row.template_status`.

## Rate cards

Rules are rows, not CHECK constraints. Priority 1 is always
`template_status = Utility` (case-insensitive match mode `equals_ci`). Then
Channel SMS / Email / RCS / WhatsApp. No match → later engine yields 0.

Seeded:

- `rate-v1` Apr–Jul 2025: Utility 0.12, WhatsApp 0.83
- `rate-v2` Aug 2025 onward: Utility 0.115, WhatsApp 0.785 (the card listed in the P1 prompt)

SMS 0.15, Email 0.01, RCS 0.25 are unchanged in both.

## KPI registry

Sixteen Q&A KPIs are seeded in namespace `qa`, solve_order 1–16. They are
metadata. There are **no** generated KPI columns on `fact_campaign_day`.
KPI 12 and 13 depend on KPI 10 (`Click + View Conv `).

## P2 configuration (loaded)

JSON snapshots in `packages/config/dfip_config/data/` are the runtime source
for resolvers. Matching SQL inserts live in the P2 migrations.

| Axis | Versions | Notes |
|---|---|---|
| Campaign labels | `campaign-v1` (3905 rows, Apr–Jul), `campaign-v2` (4093 rows, Aug–Oct) | Duplicate `campaign_name` retained. `row_order` is first-match-wins. Names are not trimmed. |
| Templates | `template-v1`…`v4` (41 / 41 / 42 / 49) | Lookup key is Template Name (WhatsApp). Result is New Logic column R (`Rate`). Column I is stored on campaign rows only and is unused. |
| Rate cards | `rate-v1`, `rate-v2` | Unchanged from P1. Workbook H2 confirms both vintages. |
| Filter Logic 1_2 | `fl1-group-v1` (43 members) | Recovered from the client Daily Report pivot cache `fieldGroup`. Unknown Filter Logic 1 values pass through. Display captions for Group1–Group7 live in the same versioned JSON (`captions`); membership rows are unchanged. |

Missing campaign match → NULL outputs (`blank_key` / `unmatched`).
Missing template match → `template_status = ""`.

Q&A workbooks use sheet `New Logic V1`. That sheet was inspected and is **not**
a production configuration version.

Client Daily Report KPIs (15 formulas, namespace `client`) are seeded in P2.
The 16 `qa` KPI rows from P1 are unchanged.

## Column mapping (Excel ↔ database)

Exact headers, including the two double-space names on E and F:

| Col | Excel header | `db_column` | Role |
|---|---|---|---|
| A | Filter Logic 1 | `filter_logic_1` | derived |
| B | Filter Logic 2 | `filter_logic_2` | derived |
| C | Template Status | `template_status` | derived |
| D | AMC Status - Filter Logic 3 | `amc_status_filter_logic_3` | derived |
| E | AMC Device Category -  Filter Logic 4 | `amc_device_category_filter_logic_4` | derived |
| F | AMC Product Cat -  Filter Logic 5 | `amc_product_cat_filter_logic_5` | derived |
| G | Manual Or Automated | `manual_or_automated` | derived |
| H | Total Cost | `total_cost` | derived |
| I | HHH | `hhh` | derived |
| J | Month | `month_label` | derived (`month_start` is extra) |
| K | Day | `day` | source / key |
| L | Campaign Name | `campaign_name` | source |
| M | Campaign ID | `campaign_id` | source / key |
| N | Variation Name | `variation_name` | source |
| O | Variation ID | `variation_id` | source / key |
| P | Channel | `channel` | source |
| Q | Type of Campaign | `type_of_campaign` | source |
| R | Status | `status` | source (staging JSONB) |
| S | Segment Name | `segment_name` | source (staging JSONB) |
| T | Segment ID | `segment_id` | source (staging JSONB) |
| U | Journey Name | `journey_name` | source (staging JSONB) |
| V | Journey ID | `journey_id` | source (staging JSONB) |
| W | Campaign Tags | `campaign_tags` | source (staging JSONB) |
| X | Start Date | `start_date` | source |
| Y | Created By | `created_by` | source PII (staging JSONB) |
| Z | Conversion Event | `conversion_event` | source (staging JSONB) |
| AA | Conversion Deadline | `conversion_deadline` | source (staging JSONB) |
| AB | Control Group | `control_group` | source (staging JSONB) |
| AC | Total in Control Group | `total_in_control_group` | source (staging JSONB) |
| AD | Unique Control Group Conversions | `unique_control_group_conversions` | source (staging JSONB) |
| AE | Unique Control Group Conversion Rate | `unique_control_group_conversion_rate` | native rate |
| AF | Sent | `sent` | source measure |
| AG | Failed | `failed` | source measure |
| AH | Queued | `queued` | source |
| AI | Delivered | `delivered` | source measure |
| AJ | Unique Impressions | `unique_impressions` | source measure |
| AK | Unique Clicks | `unique_clicks` | source measure |
| AL | Unique Conversions | `unique_conversions` | source measure |
| AM | Unique Impression-Through Conversions | `unique_impression_through_conversions` | source measure |
| AN | Unique Click-Through Conversions | `unique_click_through_conversions` | source measure |
| AO | Failed Rate | `failed_rate` | native rate |
| AP | Queued Rate | `queued_rate` | native rate |
| AQ | Delivered Rate | `delivered_rate_native` | native rate (not the KPI) |
| AR | Unique Impression Rate | `unique_impression_rate` | native rate |
| AS | Unique Click Rate | `unique_click_rate` | native rate |
| AT | Unique Conversion Rate | `unique_conversion_rate` | native rate |
| AU | Unique Impression-Through Conversion Rate | `unique_impression_through_conversion_rate` | native rate |
| AV | Unique Click-Through Conversion Rate | `unique_click_through_conversion_rate` | native rate |
| AW | Revenue (INR) | `revenue_inr` | source measure |
| AX | Impression-Through Revenue (INR) | `impression_through_revenue_inr` | source measure |
| AY | Click-Through Revenue (INR) | `click_through_revenue_inr` | source measure |
| AZ | Failed (DND Queue Drop) | `failed_dnd_queue_drop` | source (staging JSONB) |
| BA | Failed (Frequency Capping Queue Drop) | `failed_frequency_capping_queue_drop` | source (staging JSONB) |
| BB | Failed (Personalization Error) | `failed_personalization_error` | source (staging JSONB) |
| BC | Failed (Channel Not Available) | `failed_channel_not_available` | source (staging JSONB) |
| BD | Layout Name | `layout_name` | source (staging JSONB) |
| BE | ESP/SSP/WSP name | `esp_ssp_wsp_name` | source (staging JSONB) |
| BF | Title/Subject Line | `title_subject_line` | source (staging JSONB) |
| BG | Message | `message` | source (staging JSONB) |
| BH | Default CTA Label | `default_cta_label` | source (staging JSONB) |
| BI | Default CTA Link | `default_cta_link` | source (staging JSONB) |
| BJ | Image | `image` | source (staging JSONB) |
| BK | Key-value Pairs | `key_value_pairs` | source (staging JSONB) |
| BL | Sender ID (SMS) | `sender_id_sms` | source (staging JSONB) |
| BM | From Name (Email) | `from_name_email` | source (staging JSONB) |
| BN | From Email (Email) | `from_email_email` | source (staging JSONB) |
| BO | Template Name (WhatsApp) | `template_name_whatsapp` | source / template lookup key |

The 57 Web Engage source columns are retained in `stg_source_row.raw` under the
**exact Excel header**. P3 does not copy A:J derived columns into `raw`. Typed
key copies (`campaign_id`, `variation_id`, `day`) on `stg_source_row` are
extracted from the payload for indexing only.

## P3 ingestion semantics

Inspected supplied production and Q&A workbooks all use worksheet
`Web-Engage Raw`, header row 1, source region **K:BO**. The client Daily Report
workbook is not a source artifact.

- SHA-256 of file bytes is the `source_file` identity (`UNIQUE (sha256)`).
- A successful `batch` (`staged` / later `validated` / `processed`) is reused on
  repeat ingest of the same bytes (idempotent). A failed batch does not block retry.
- `source_row_number` is the Excel row number. Fully empty K:BO rows are counted
  in `batch.empty_row_count` and are neither staged nor rejected.
- Strings are not trimmed. JSON `null` is an empty Excel cell. Excel does not
  distinguish an empty string from a blank cell, so `""` vs `null` is preserved
  only when the value exists before workbook round-trip. Datetimes become
  ISO-8601 in JSONB because JSON has no datetime type.
- `processing_run` is inserted as `pending` with P2 version FKs selected from the
  batch's `observed_day_min`. Labels are not applied to rows.

Native exports (57 headers starting at column A, any sheet name) are accepted
when `Web-Engage Raw` is absent and exactly one sheet matches the header sequence.

## P4 transformation semantics

P4 turns `stg_source_row` into `fact_campaign_day`. It adds **no tables and no
columns**; every value it writes already had a column in P1.

The derived A:J columns are reproduced from the recovered `Web-Engage Raw`
formulas, which are byte-identical across all supplied production workbooks
apart from the two rate literals:

| Col | Recovered formula | Fact column |
|---|---|---|
| A | `VLOOKUP([Campaign Name],'New Logic'!A:M,7,0)` | `filter_logic_1` |
| B | `VLOOKUP(...,8,0)` | `filter_logic_2` |
| C | `IFERROR(VLOOKUP([Template Name (WhatsApp)],'New Logic'!Q:R,2,0),"")` | `template_status` |
| D | `VLOOKUP(...,10,0)` | `amc_status_filter_logic_3` |
| E | `VLOOKUP(...,11,0)` | `amc_device_category_filter_logic_4` |
| F | `VLOOKUP(...,12,0)` | `amc_product_cat_filter_logic_5` |
| G | `VLOOKUP(...,13,0)` | `manual_or_automated` |
| H | `IF(C="Utility",AI*<utility>,IF(P="SMS",AI*0.15,IF(P="Email",AI*0.01,IF(P="RCS",AI*0.25,IF(P="WhatsApp",AI*<whatsapp>,0)))))` | `total_cost` |
| I | `TEXT([Start Date],"HH")` | `hhh` |
| J | `TEXT([Day],"MMM-YY")` | `month_label` |

`month_start` has no Excel counterpart; it is the first day of `Day`.

Behaviour that follows from the formulas:

- **Total Cost is `Delivered x resolved rate`.** Never Sent, never Clicks, and
  never a native Web Engage rate column (AE, AO–AV are evidence only).
- Template Status `Utility` outranks Channel. An unmatched Channel costs **0**,
  not NULL and not an invented rate; `rate_card_rule_id` stays NULL.
- A blank `Delivered` on a matched branch costs 0 (Excel multiplies a blank
  cell as zero) while `delivered` itself stays NULL.
- Malformed numerics are **rejected**, never coerced. The row lands in
  `stg_rejected_row` with `reason_code = 'INVALID_NUMERIC'`.
- `Campaign ID` and `Day` are PK components, so a blank one rejects the row
  (`MISSING_CAMPAIGN_ID` / `MISSING_DAY` / `INVALID_DAY`).

Configuration versions are bound **per row day**, not per batch, so a batch that
spans 2025-08-01 gets `rate-v1` before the boundary and `rate-v2` from it. Each
fact stores the four version ids that actually produced it.

### Known, intentional divergence from the raw cell values

A `VLOOKUP` that lands on an *empty* New Logic cell renders as numeric **0** in
Excel, and a miss renders as `#N/A`. Both are spreadsheet artifacts, not
business labels, so DFIP stores NULL and records
`label_match_status = 'unmatched'`. `dfip_core.transform.reconcile.excel_blank_equivalent`
normalises this when reconciling against a workbook.

### Rerun behaviour

Re-transforming a batch is idempotent. An unchanged fact is only re-stamped with
`last_seen_at`. A changed fact is copied to `fact_campaign_day_history` with
`superseded_by_run_id` set to the run that previously produced it, and the
current row keeps its original `first_seen_at`. Facts are never overwritten in
place. Two staged rows that collapse onto one grain key in a single run keep the
first and reject the second (`DUPLICATE_GRAIN_KEY`), so nothing is silently lost.

## P5 API (no schema change)

The P5 API exposes existing `source_file`, `batch`, `processing_run`,
`stg_source_row`, `fact_campaign_day`, and `fact_campaign_day_history` fields.
It does not invent a competing data model. `storage_uri` is not returned.
Live database connectivity is **not verified** in this checkout.

## P7 publication (no schema change)

P7 uses the existing `publication` and `publication_current` tables as an
in-memory store. No new migration. `GET /api/v1/facts` remains the working set.
The client workbook reads `GET /api/v1/publications/current/facts`. This is not
RLS.

## Publication fact scope

Additive column `publication.fact_scope` (`20260828000017`):

| Value | Published facts |
|---|---|
| `processing_run` (default) | Current `fact_campaign_day` rows whose `processing_run_id` matches the publication. Existing and August publications stay here. |
| `client_current` | All current `fact_campaign_day` rows for that client, clipped by `period_start` / `period_end` when set. |

`processing_run_id` remains on the publication row for lineage. Existing rows
default to `processing_run`. The published view and `list_published_slice`
branch on this column. Facts are not rewritten.

## Publication snapshots

Additive `publication_fact` plus `publication.snapshot_status` /
`snapshot_row_count` (`20260829000018`). New publishes write the candidate
slice into `publication_fact` before `publication_current` moves. Complete
snapshots are the published set. Legacy `snapshot_status=none` (existing
August until a new publish after the migration is applied) still joins live
`fact_campaign_day` and is not immutable. Do not apply this migration to the
live August database from this workflow.

## P9 authorization (no schema change)

Application-level role gates on the existing FastAPI boundary. Working-set
routes are admin/publisher only. That is not PostgreSQL RLS.

## Applying migrations (V2 runtime, not V1)

When a Postgres or Supabase database exists:

```text
psql "$DATABASE_URL" -f supabase/migrations/20260823000001_p1_extensions.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000002_p1_tables.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000003_p1_indexes.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000004_p1_seed.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000005_p2_config_versions.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000006_p2_campaign_v1.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000007_p2_campaign_v2.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000008_p2_templates.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000009_p2_client_kpis.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000010_p3_batch_ingest_metadata.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000011_v2_identity.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000012_v2_tenant_denorm.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000013_v2_rls_roles.sql
psql "$DATABASE_URL" -f supabase/migrations/20260823000014_v2_analytics_reporting.sql
psql "$DATABASE_URL" -f supabase/migrations/20260827000015_v2c_catalog_write_grants.sql
psql "$DATABASE_URL" -f supabase/migrations/20260828000016_processing_run_failure_progress.sql
psql "$DATABASE_URL" -f supabase/migrations/20260828000017_publication_fact_scope.sql
psql "$DATABASE_URL" -f supabase/migrations/20260829000018_publication_fact_snapshot.sql
```

Do not apply `20260829000018_publication_fact_snapshot.sql` to the live August
database from this workflow. The file is additive; applying it does not move
`publication_current`, but it must not be run against that hosted database here.

Or `python -m dfip_db` / `supabase db push` / `supabase migration up` from this folder.

`python -m dfip_db` records applied filenames in `dfip_schema_migration` (created
by the migrator, not a domain table) and skips them on re-run. On a database
that already has `rpt_published_fact` but no ledger, files through
`20260823000014` are recorded as applied and not re-executed. Hosted Supabase
uses the same SQL files; set `DATABASE_URL` to the Direct or Session-pooler URI.
Do not commit that URI. Do not use Transaction pooler port 6543 with psycopg.

## V2 Phase 1 additions

Locked V1 files `20260823000001`–`20260823000010` are unchanged. Phase 1 appends:

| Object | Purpose |
|---|---|
| `app_user` | JWT subject identity. No FK to `auth.users`. |
| `client_membership` | Per-client role (`admin` / `publisher` / `reader` / `client`) |
| `processing_run.client_id` | Denormalized from `batch` (NOT NULL) |
| `stg_source_row.client_id` / `stg_rejected_row.client_id` | Denormalized from `batch` |
| `audit_log.client_id` | Nullable for platform-level events |
| `source_file` unique `(client_id, sha256)` | Replaces global SHA uniqueness |
| `published_fact_campaign_day` | Published slice view (snapshot when complete; not PostgREST) |
| `publication_fact` | Immutable published fact rows keyed by publication_id |
| roles `dfip_migrator` / `dfip_api` / `dfip_worker` | DDL / API DML / future worker |

Row-level security is defense in depth. HTTP authorization remains the API's
responsibility. `dfip_api` does not have `BYPASSRLS`. Request identity uses
transaction-local GUCs (`dfip.role`, `dfip.client_ids`, `dfip.platform_admin`).

## V2 Phase 2A additions

Locked files `20260823000001`–`20260823000013` are unchanged. Phase 2A appends
`20260823000014_v2_analytics_reporting.sql`.

| Object | Kind | Purpose |
|---|---|---|
| `qa_finding` | table | Inspector-only QA results per processing run (`GET /api/v1/processing-runs/{id}/qa-findings`) |
| `rpt_dim_date` | table | Static calendar 2020–2035 (`month_label` matches P4 MMM-YY) |
| `rpt_published_fact` | view | Published additive facts for BI. No row-level ratio columns |
| `rpt_dim_client` | view | Client slicer (`dfip_member_client`) |
| `rpt_dim_campaign` | view | Distinct published campaigns plus label attributes |
| `rpt_dim_variation` | view | Distinct published variations, including empty `variation_id_key` |

Power BI must query `rpt_*` only. Working-set `fact_campaign_day` remains
inspector-only. KPI formulas are not generated columns on facts. See
`documentation/ANALYTICS.md`.

## V2 Phase 2B

No new database objects. Power BI consumes the Phase 2A `rpt_*` layer. Artifacts:
`powerbi/` (DAX, model, pages, theme, M queries, validation SQL). Do not grant
Power BI access to `fact_campaign_day` or `qa_finding`. There is no committed
`.pbix`.


