# DFIP V1 — Data dictionary

Sources of truth: `FactResponse` (`packages/api/dfip_api/schemas.py`), `FACT_HEADERS` / `FACT_VALUE_FIELDS` (`client_workbook.py`, `client_report_download.py`), `we_source_column` seed / [SCHEMA.md](SCHEMA.md), `kpi_definition` via `dfip_analytics.kpis`.

The **67-column A:BO mapping below is copied from SCHEMA.md** (2026-08-30). Definitions were **not invented or altered**. If SCHEMA.md later changes, this copy can drift — prefer SCHEMA.md + `we_source_column` SQL seed.

Status labels apply per field group.

---

## Layers

| Layer | Store | Client-visible? |
|---|---|---|
| RAW | `stg_source_row.raw` keyed by exact WE headers | **No** (inspector API only) |
| REJECTED | `stg_rejected_row` | **No** |
| FACT / WORKING | `fact_campaign_day` | Inspector `GET /facts` only |
| HISTORY | `fact_campaign_day_history` | Inspector `GET /facts/history` |
| PUBLICATION SNAPSHOT | `publication_fact` when `snapshot_status=complete` | Via published endpoints |
| POINTER | `publication_current` | Metadata via `/publications/current` |
| API JSON | `FactResponse` | Client if published |
| EXCEL | `FACT_HEADERS` row 1 of PublishedFacts | Yes in client report |
| PIVOT METRICS | `MEASURE_HEADERS` (sum + calculated) | Yes |
| POWER BI | `rpt_published_fact` | If Desktop model built; **canvas NOT VERIFIED** |

---

## Grain

**Fact grain (VERIFIED `FactKey`):** `(client_id, campaign_id, variation_id_key, day)`.

`variation_id_key`: empty string when variation is missing (`derive.variation_id_key`).

---

## Client-facing reporting fields (PublishedFacts / FactResponse)

45 columns (`len(FACT_HEADERS) == 45`). Order is API→Excel map in `FACT_VALUE_FIELDS`.

| Excel header | API / DB field | Type (API) | Meaning | Source | Transform | Nullable | Client-visible | Used in 9 pivots as row/filter | Used in MEASURE_HEADERS |
|---|---|---|---|---|---|---|---|---|---|
| Filter Logic 1 | `filter_logic_1` | str | Campaign vertical/label from New Logic | P2 campaign snapshot | lookup | yes | yes | slicer / rows | no |
| Filter Logic 2 | `filter_logic_2` | str | Secondary campaign label | P2 | lookup | yes | yes | some sheets | no |
| Template Status | `template_status` | str | Q:R template status (Utility vs other) | P2 templates | lookup | yes | yes | lineage | cost input |
| AMC Status - Filter Logic 3 | `amc_status_filter_logic_3` | str | AMC status label | P2 | lookup | yes | yes | lineage | no |
| AMC Device Category -  Filter Logic 4 | `amc_device_category_filter_logic_4` | str | Device category | P2 | lookup | yes | yes | Vertical slicer | no |
| AMC Product Cat -  Filter Logic 5 | `amc_product_cat_filter_logic_5` | str | Product category | P2 | lookup | yes | yes | Vertical slicer | no |
| Manual Or Automated | `manual_or_automated` | str | Campaign mode | P2 | lookup | yes | yes | lineage | no |
| Total Cost | `total_cost` | decimal string | Delivered × rate card | calculated | `calculate_total_cost` | stored 0 not null | yes | value | yes |
| HHH | `hhh` | str | Hour bucket from start_date | derived | `derive.hhh` | yes | yes | lineage | no |
| Month | `month_label` | str | e.g. Oct-25 | derived | `month_label(day)` | yes | yes | rows | no |
| Day | `day` | date | Activity day | WE source | extract | **required grain** | yes | rows | no |
| Campaign Name | `campaign_name` | str | | WE | extract | yes | yes | Sub-Split | no |
| Campaign ID | `campaign_id` | str | | WE | extract | required grain | yes | lineage | no |
| Variation Name | `variation_name` | str | | WE | extract | yes | yes | lineage | no |
| Variation ID | `variation_id` | str | | WE | extract | yes | yes | lineage | no |
| Channel | `channel` | str | SMS/Email/RCS/WhatsApp | WE | extract | yes | yes | filter/slicer; cost | no |
| Type of Campaign | `type_of_campaign` | str | | WE | extract | yes | yes | lineage | no |
| Start Date | `start_date` | datetime | | WE | extract | yes | yes | HHH input | no |
| Sent | `sent` | int | | WE col AF (see SCHEMA) | 1:1 extract | yes | yes | value | yes |
| Failed | `failed` | int | | WE | 1:1 | yes | yes | value | yes |
| Delivered | `delivered` | int | | WE | 1:1; cost units | yes | yes | value | yes |
| Unique Impressions | `unique_impressions` | int | | WE | 1:1 | yes | yes | value | yes |
| Unique Clicks | `unique_clicks` | int | | WE | 1:1 | yes | yes | value | yes |
| Unique Conversions | `unique_conversions` | int | **Overall** conversions | WE | 1:1 **not calculated** | yes | yes | value | yes |
| Unique Impression-Through Conversions | `unique_impression_through_conversions` | int | View-through | WE | 1:1 | yes | yes | **not** in MEASURE_HEADERS | no (nine reports) |
| Unique Click-Through Conversions | `unique_click_through_conversions` | int | Click-through | WE | 1:1 **not calculated** | yes | yes | value | yes |
| Revenue (INR) | `revenue_inr` | decimal string | Overall revenue | WE | 1:1 | yes | yes | value | yes |
| Impression-Through Revenue (INR) | `impression_through_revenue_inr` | decimal string | | WE | 1:1 | yes | yes | not MEASURE_HEADERS | no |
| Click-Through Revenue (INR) | `click_through_revenue_inr` | decimal string | | WE | 1:1 | yes | yes | value | yes |
| Template Name (WhatsApp) | `template_name_whatsapp` | str | | WE | extract | yes | yes | lineage | no |
| client_id | `client_id` | uuid str | Tenant | ingest | bound | no | yes (in sheet) | no | no |
| variation_id_key | `variation_id_key` | str | Grain key | derived | | no | yes | no | no |
| month_start | `month_start` | date | Month floor | derived | | yes | yes | sort identity | no |
| filter_logic_1_group | `filter_logic_1_group` | str | Pivot field **Filter Logic 1_2** | P2 labels | group membership | yes | yes | rows/filters | no |
| label_match_status | `label_match_status` | str | Lineage | transform | | yes | yes | no | no |
| template_match_status | `template_match_status` | str | Lineage | transform | | yes | yes | no | no |
| rate_card_rule_id | `rate_card_rule_id` | uuid str | Lineage | cost | | yes | yes | no | no |
| processing_run_id | `processing_run_id` | uuid str | Lineage | run | | yes | yes | hide in PBI spec | no |
| batch_id | `batch_id` | uuid str | Lineage | batch | | yes | yes | hide in PBI | no |
| campaign_label_version_id | `campaign_label_version_id` | uuid str | Config pin | P2 | | yes | yes | no | no |
| template_label_version_id | `template_label_version_id` | uuid str | | P2 | | yes | yes | no | no |
| rate_card_version_id | `rate_card_version_id` | uuid str | | P2 | | yes | yes | no | no |
| label_group_version_id | `label_group_version_id` | uuid str | | P2 | | yes | yes | no | no |
| first_seen_at | `first_seen_at` | datetime | First grain write | store | | no | yes | hide in PBI | no |
| last_seen_at | `last_seen_at` | datetime | Last restatement | store | | no | yes | hide in PBI | no |

**Example (safe):** `month_label` = `Oct-25`; `sent` = `0`; `total_cost` = `"0.0000"`.

---

## Additive vs calculated (reports)

**Additive (sum in Pivot / `ADDITIVE_HEADERS`):** Total Cost, Sent, Failed, Delivered, Unique Impressions, Unique Clicks, Unique Click-Through Conversions, Click-Through Revenue (INR), Unique Conversions, Revenue (INR).

**Calculated (`MEASURE_HEADERS` extras, sum-first then operate — `kpis.py` client namespace):**

| Display name | Formula (after SUM) | Units | Zero/null |
|---|---|---|---|
| Failed Rate SM | Failed/Sent | ratio | blank if den 0 |
| Actual Sent | Sent−Failed | count | 0 is real zero |
| Delivery Rate | Delivered/Sent | ratio | blank if den 0 |
| Delivered to Imp. rate | Unique Impressions/Delivered | ratio | blank if den 0 |
| CTR (Del to Clicks) | Unique Clicks/Delivered | ratio | blank if den 0 |
| CTR ( Impr. to Click ) | Unique Clicks/Unique Impressions | ratio | blank if den 0 |
| Cost/ UCT conversion | Total Cost / Unique Click-Through Conversions | money | blank if den 0 |
| UCT conversion rate | Unique Click-Through Conversions / Unique Clicks | ratio | blank if den 0 |
| Unique Click Through Conv ROAS | Click-Through Revenue / Total Cost | ROAS | blank if den 0 |
| Cost/Unique Conversion | Total Cost / Unique Conversions | money | blank if den 0 |
| Delivered Thru Conv. rate | Unique Conversions / Delivered | ratio | blank if den 0 |
| Overall ROAS | Revenue / Total Cost | ROAS | blank if den 0 |

**Defined in client KPI namespace but unused by the nine pivots (`used_by_reports=False` in kpis.py):** Click Through Cost/Conv, UC to UCTC Conversions, UC to UCTC Revenue.

**Not in source / UNAVAILABLE_METRICS:** orders, amazon_cpc, acos, product_asin.

**Native WE rate columns** (`failed_rate`, `delivered_rate_native`, …): staged if present in raw JSON; **never** KPI inputs.

### Staged-only Web Engage columns (not on `fact_campaign_day` / PublishedFacts)

`we_source_column` includes 67 letters A:BO. P4 **extract** maps a subset onto the fact grain. The following remain in `stg_source_row.raw` only (VERIFIED catalog vs `FactRecord` / `FactResponse`): e.g. Status, Segment Name/ID, Journey Name/ID, Campaign Tags, Created By, Conversion Event/Deadline, Control Group fields, native rate columns, queued counts, and other K:BO fields not listed in [client-facing table](#client-facing-reporting-fields-publishedfacts--factresponse). **Client reports never include them.** Inspector staged-rows API returns `raw` JSON. Full letter map: [SCHEMA.md](SCHEMA.md) / `dfip_db.catalog.SOURCE_COLUMNS`.

### RUN 009 approved extras (not `we_source_column`, not facts, not Excel)

Identity is the **exact Excel header**, not column letter. These headers are optional, must appear **immediately after** the locked 57-window (no empty gap), and are stored only on `stg_source_row.raw`. They are not `we_source_column` rows (that table stays A:BO 1–67). They are not `fact_campaign_day` columns, not `FactResponse` fields, not `FACT_HEADERS`, and not Power BI `rpt_*` columns. Excel/Power BI/dashboard exposure requires an explicit later contract update.

| Source header | Internal name | Type | Required | Excel | Power BI | Notes |
|---|---|---|---|---|---|---|
| Campaign Objective | `campaign_objective` | text | no | none | none | Synthetic approved extra for RUN 009. Pass-through staging only. |

Unknown trailing headers fail ingest: `UNAPPROVED_SOURCE_COLUMN: Unapproved source column: {name}`. Missing this optional extra is valid (existing 57-column files). No database migration: existing `stg_source_row.raw` JSON already holds extra keys.

---

## Click-through vs overall conversions

**VERIFIED root cause of “two conversion families”:** they are **two different Web Engage source measures**, copied 1:1 in extract, stored as `unique_conversions` vs `unique_click_through_conversions` (and matching revenue columns). They are **not** derived from each other in P4.

| | Overall | Click-through |
|---|---|---|
| Fact fields | `unique_conversions`, `revenue_inr` | `unique_click_through_conversions`, `click_through_revenue_inr` |
| In PublishedFacts | yes | yes |
| In nine Pivot MEASURE_HEADERS | Unique Conversions, Revenue, Cost/Unique Conversion, Delivered Thru Conv. rate, Overall ROAS | Unique Click-Through Conversions, Click-Through Revenue, Cost/UCT, UCT rate, UCT ROAS |
| Impression-through | in facts sheet | **omitted** from MEASURE_HEADERS |

Discrepancy between the two sections is **expected** when WE reports view-through vs click-through separately. Do not “fix” by equating them.

QA `QA-RATIO-GT-ONE` can fire when clicks > impressions etc. That is a **warning**, not a missing column.

---

## Traceability (major metrics)

| Source (WE / P2) | Transform | DB/API | Excel | Pivot metric |
|---|---|---|---|---|
| Sent | 1:1 | `sent` | Sent | Sent; Actual Sent; Failed Rate SM den; Delivery Rate den |
| Failed | 1:1 | `failed` | Failed | Failed; Actual Sent; Failed Rate SM num |
| Delivered | 1:1; × rate | `delivered`, `total_cost` | Delivered, Total Cost | Delivery Rate, CTRs, cost-per dens |
| Unique Impressions | 1:1 | `unique_impressions` | Unique Impressions | Delivered to Imp. rate; CTR impr |
| Unique Clicks | 1:1 | `unique_clicks` | Unique Clicks | CTR del; UCT rate den |
| Unique Conversions | 1:1 | `unique_conversions` | Unique Conversions | overall conv KPIs |
| Unique Click-Through Conversions | 1:1 | `unique_click_through_conversions` | same | UCT KPIs |
| Revenue | 1:1 | `revenue_inr` | Revenue (INR) | Overall ROAS |
| Click-through revenue | 1:1 | `click_through_revenue_inr` | Click-Through Revenue (INR) | UCT ROAS |
| Channel + Template Status + Delivered | rate card | `total_cost` | Total Cost | all cost KPIs |
| Campaign labels | P2 | `filter_logic_*` | Filter Logic * | rows/slicers |
| FL1 membership | Labels catalog | `filter_logic_1_group` | cache caption Filter Logic 1_2 | groups |

---

## Publication metadata

| Field | Purpose |
|---|---|
| `publication.id` | Immutable publication id |
| `publication_current.publication_id` | What `/current/` means |
| `snapshot_status` | `none` (legacy live join) \| `complete` (immutable `publication_fact`) |
| `snapshot_row_count` | Rows written at publish when complete |
| `fact_scope` | `processing_run` \| `client_current` |
| `period_start` / `period_end` | Optional slice window on create |

---

## Staging / raw (not client)

See P1 tables in `supabase/migrations/20260823000002_p1_tables.sql`: `client`, `we_source_column`, `source_file`, `batch`, `stg_source_row`, `stg_rejected_row`, `processing_run`, config version tables, `fact_campaign_day`, `kpi_definition`, later identity/RLS/`rpt_*`/`publication_fact`.

Full SQL inventory: [V1_DATABASE.md](V1_DATABASE.md), [V1_POSTGRES_STORES.md](V1_POSTGRES_STORES.md), [V1_RLS.md](V1_RLS.md).

---

## Complete 67-column Excel ↔ database mapping (from SCHEMA.md)

Exact headers, including the two double-space names on E and F. Copied from SCHEMA.md “Column mapping (Excel ↔ database)”. Role column is SCHEMA.md’s terminology (`derived` / `source` / `source / key` / `native rate` / etc.).

The 57 Web Engage source columns **K:BO** are retained in `stg_source_row.raw` under the **exact Excel header**. P3 does not copy A:J derived columns into `raw`. Typed key copies (`campaign_id`, `variation_id`, `day`) on `stg_source_row` are extracted from the payload for indexing only.

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

**Client PublishedFacts / `FACT_HEADERS` is 45 columns**, not 67. Native rates AO:AV and most JSONB-only staging columns are **not** on the pivot sheet. RUN 009 approved extras are also **not** appended to this 45-column contract.
