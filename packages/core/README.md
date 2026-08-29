# packages/core

## `dfip_core.ingest` (P3)

- read-only Excel adapter for `Web-Engage Raw` (K:BO = 57 source columns)
- SHA-256 source-file identity
- in-memory `source_file` / `batch` / `stg_source_row` / `processing_run` store
- V2 Phase 1: the same `IngestStore` protocol is implemented by PostgreSQL
  adapters; P3 pipeline semantics are unchanged

The adapter does not apply campaign labels, template status, rate cards, or cost.

## `dfip_core.transform` (P4)

Turns staged rows into `fact_campaign_day` records:

| Module | Responsibility |
|---|---|
| `extract` | Source-field extraction and validation. No trim, no numeric repair. |
| `labels` | Per-day configuration binding. Delegates every rule to `dfip_config.resolve`. |
| `cost` | Rate-rule resolution and `Delivered x rate` in `Decimal`. |
| `derive` | `hhh`, `month_label`, `month_start`, `variation_id_key` (OPEN-A6). |
| `fact` | The `FactRecord` and its grain key. |
| `store` | `fact_campaign_day` / `_history` upsert and supersede semantics. |
| `engine` | Streaming run over a batch, rejections, `processing_run` lifecycle. |
| `reconcile` | Independent recomputation of every derived value. |

Business rules are **not** duplicated here — campaign first-match-wins,
case-insensitive no-trim matching, Q:R template lookup, and Filter Logic 1_2
membership all stay in `dfip_config.resolve`.

P4 does not compute KPIs, evaluate QA rules, or publish. KPI/QA engines are
V2. Publication is P7 in `packages/api`. The P5 HTTP API lives in
`packages/api` and must not be added here.

## Usage

```python
from dfip_core.transform import InMemoryFactStore, reconcile_run, run_transformation

facts = InMemoryFactStore()
result = run_transformation(ingest_store, facts, batch_id)
report = reconcile_run(
    facts.for_run(result.processing_run.id),
    rate_card_version_label="rate-v2",
    processing_run_id=result.processing_run.id,
)
assert report.passed, report.failures
```
