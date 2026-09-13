# DFIP V1 — Python source inventory

Grep of `packages/` found **~650** `class` / `def` / `async def` symbols including private helpers. This file documents **behavior and signatures of V1-relevant public surfaces**, not every `_helper`.

Call sites are **typical**, not exhaustive.

---

## Pipeline map (implementation functions)

```
.xlsx bytes
  UploadService.accept_parts / read_upload / _assert_xlsx_payload
  → ingest_workbook (dfip_core.ingest.pipeline)
      inspect_workbook, iter_source_rows, sha256_file
  → run_transformation (dfip_core.transform.engine)
      transform_row → extract_source_fields → bind_configuration / ConfigBinder.for_day
                   → calculate_total_cost → FactRecord persist (chunk 500)
  → qa_workflow (evaluate_qa + optional reconcile_run) → qa_verdict
  → PublicationService.create → PublicationStore.create (publication_fact + publication_current)
  → GET list_published_facts / downloads
  → render_client_report_xlsx → bind_pivot_cache_for_snapshot
```

There is **no** separate “normalization” module. Typing/clean happens in `extract_source_fields` (integers/decimals/dates; rejects via `RowValidationError`).

`ingest_workbook(..., engine_version=ENGINE_VERSION)` default matches transform
`ENGINE_VERSION` (`0.4.0`). HTTP upload passes the same constant.

---

## `dfip_api`

| Symbol | Signature / role | Side effects | Authz |
|---|---|---|---|
| `create_app` | `(settings=None, stores…=None) -> FastAPI` | Pool if DSN; abandoned-run sweep; thread pool upload; CORS | startup `validate_auth_settings` + production storage gate |
| `health` | `() -> HealthResponse` | none | public |
| `authenticate_bearer` | `(Settings, header) -> Principal` | none | 401 |
| `issue_access_token` | `(Settings, Principal) -> (token, ttl)` | none | needs secret |
| `login` | `LoginRequest -> LoginResponse` | rate-limit + best-effort `audit_log` | public; multi-inspector publisher may omit `client_id` |
| `select_client` | `SelectClientRequest -> LoginResponse` | re-issues bound JWT | inspector membership only; clients 403 |
| `list_clients` | `-> ClientPage` | reads `client` | inspector; membership-scoped |
| `create_client` | `POST /clients` | insert `client` + caller membership | inspector; 201; clients 403 |
| `create_client_user` | `POST /clients/{id}/users` | insert `app_user` hash + client membership | inspector on that tenant; confirm_password; never returns password |
| `setup_status` | `GET /auth/setup-status` | true when no publisher/admin exists | public; no secrets |
| `setup_publisher` | `POST /auth/setup-publisher` | one-time operator publisher | public until a publisher exists; 409 after; hash only |
| `rename_client` | `POST /clients/{id}/rename` | updates `client.name` only | inspector on that tenant; 403 otherwise |
| `logout` | `-> 204` | `increment_token_version` | Bearer |
| `refresh` | `-> LoginResponse` | none | JWT only |
| `get_session` | `-> SessionResponse` | none | Bearer; `clients` lists memberships |
| `list_source_files` etc. | query `client_id`, pagination | reads | inspector |
| `fail_abandoned_processing_runs` | leftover `pending`/`running` → `failed` | startup with `DATABASE_URL` | n/a |
| `evaluate_processing_run_qa` | POST `.../qa` | replaces `qa_finding`, `qa_verdict` | inspector; no publish |
| `list_facts` / `list_fact_history` | filters below | reads working set | inspector |
| `upload_workbook` | multipart `file`/`files`, form `client_id`, **`force: bool=False`** | ingest+transform+QA; 202/200 | inspector |
| `create_publication` | body `PublicationCreateRequest` | insert publication, snapshot, **replace pointer** | `can_publish` |
| downloads | query `client_id` | none on DB except reads | Bearer scoped |
| catalog routes | path `kind`, `version_id` | insert/activate catalogs | inspector |

**Inspector fact/history query params (VERIFIED `routes.py`):** `client_id`, `batch_id`, `processing_run_id`, `campaign_id` (text), `variation_id` (text), `day`, `limit`, `offset`. Exact match. Extra SQL-style params ignored (`test_p5_api`).

**Staged-rows filters:** `source_row_number` (≥1), `campaign_id`, `variation_id`, `day`.

**Batches list:** `source_file_id`, `client_id`, `status`.

**Runs list:** `batch_id`, `status`.

**SourceFileResponse omits `storage_uri`** (security).

**Error codes:** `VALIDATION_ERROR`, `AUTHENTICATION_FAILED`, `AUTHORIZATION_FAILED`, `NOT_FOUND`, `INVALID_PAGINATION`, `PAYLOAD_TOO_LARGE`, `PERSISTENCE_UNAVAILABLE`, `INTERNAL_ERROR`, `METHOD_NOT_ALLOWED`. Body `{error:{code,message,details?}}`. Production handlers strip traces (`errors.py`).

**Classes:** `Principal`, `ApiError` hierarchy, `PublicationService`, `UploadService`, `CatalogService`, `ReadService`, `InMemory*` stores, Pydantic models in `schemas.py` (39 class defs including pages).

**Upload `force`:** re-ingest same SHA instead of replay (**VERIFIED** form field). Previously undocumented in API table.

---

## `dfip_core.ingest`

| Symbol | Role |
|---|---|
| `ingest_workbook(path, store=None, *, client_id=DEFAULT_CLIENT_ID, force=False, engine_version=ENGINE_VERSION, catalog=None, reserved_batch=None) -> IngestResult` | SHA, inspect, stage, create pending run; replay on SHA unless force |
| `discover_source_workbooks(directory)` | filesystem discovery (library; not HTTP) |
| `bind_versions_for_day` | P2 version windows; persistable IDs owned by `client_id` or NULL |
| `expected_source_headers` / `match_header_rows` | locked 57-header contract; `HeaderContractError` |
| `classify_trailing_headers` | RUN 009: approved extras after the 57-window, or `UNAPPROVED_SOURCE_COLUMN` / `EMPTY_COLUMN_NAME` / `DUPLICATE_HEADERS` |
| `inspect_workbook` / `iter_source_rows` | openpyxl read; no write; extras only in `raw` |
| `sha256_file` | hashing |
| `InMemoryIngestStore` | records: SourceFile, Batch, Staged, Rejected, ProcessingRun |

External: local path or temp file from upload. Object store via `SourceObjectStore.put` in upload service.

---

## `dfip_core.transform`

| Symbol | Role |
|---|---|
| `extract_source_fields(raw) -> SourceFields` | parse named 57-header subset; extra `raw` keys ignored; `RowValidationError` |
| `bind_configuration` / `bind_configuration_for_run` / `ConfigBinder.for_day` | P2 content + persistable version IDs; `ConfigurationBindingError` |
| `calculate_total_cost(rate_card_version_label, template_status, channel, delivered) -> CostResolution` | unmatched cost `0.0000` |
| `transform_row(staged, binder, *, client_id, batch_id, processing_run_id, now=None) -> RowOutcome` | one row |
| `transform_rows` / `run_transformation` | batch; persist chunks; run status |
| `reconcile_fact` / `reconcile_run` | P8 independent math |
| `month_label`, `month_start`, `hhh`, `variation_id_key` | derive |
| `FactRecord` / `FactKey` | grain |
| `InMemoryFactStore` | upsert + history |

---

## `dfip_config`

`Settings` / `load_settings()` (`.env`). `resolve.py` ~20 resolvers (campaign, template, rate, FL1 group, version-for-day). `catalog_identity.persistable_catalog_version_id` (stored IDs must match run `client_id`). `InMemoryCatalogStore`. Packaged JSON under `dfip_config/data/`. `rate_cards.py`, `workbook.py` Logic/Labels xlsx parse.

---

## `dfip_db`

`create_pool` / `close_pool` / `DatabaseUnavailableError`. Postgres* stores. `apply_migrations`. `bind_rls`. `sql_inspect.parse_tables` for tests. `mapping.py` Excel letter maps. `identity.py` user SQL (`insert_client_password_user` / `_from_pool` store a hash only). `catalog.py` `SOURCE_COLUMNS` 67-tuple (A:BO metadata; not migrated for extras) plus `APPROVED_EXTRA_SOURCE_COLUMNS` (RUN 009 header registry, not `we_source_column`). `ingest_store.add_processing_run` / `save_processing_run` persist version UUIDs only when `version.client_id` matches the batch/run client. `persistable_rate_card_rule_id` (transform) stores packaged `rate_card_rule` UUIDs only for the default catalog client.

---

## `dfip_analytics`

`evaluate_qa(QaContext) -> list[QaFinding]`. `verdict_from_findings`. `compute_kpis`. `safe_divide`/`safe_add`/`safe_subtract`. `aggregate_facts`. `Grain` enum + `grain_key`. `powerbi_contract.py` validates package files.

**QA-RATIO-GT-ONE pairs (VERIFIED):** clicks>impressions, conversions>delivered, uctc>clicks, impression-through-conv>impressions, impressions>delivered.

---

## `dfip_web`

| Symbol | Role |
|---|---|
| `create_web_app` | static SPA; `/config.json`; no DB |
| `render_client_report_xlsx(rows, *, published_at, template_path=None) -> bytes` | ZIP clone |
| `bind_pivot_cache_for_snapshot(cache_xml, row_count) -> str` | A1:AS{n} |
| `apply_native_pivots(raw) -> bytes` | inject P11 parts into template |
| `assert_native_pivot_package` | tests/generator guard |
| `calculated_cache_fields() -> tuple[tuple[str,str], ...]` | Excel calculated field formulas from `MEASURE_OPS` |
| `report_formula(contract)` | P10 reconstruction; **not** on P11 sheets |
| `DfipApiClient` (`api_client.py`) | test/operator HTTP |
| `published_facts_page_offsets` | M paging mirror |

---

## `dfip_shared`

`__version__ = "0.0.0"` only. **No business types.**

---

## Frontend (not Python)

`apps/web/static/index.html`, `css/app.css`, JS: `app.js` (routes), `api-client.js`, `auth.js` (token storage), `components.js`, `dialogs.js`, `format.js`, `roles.js`, `router.js`, `views.js`.

---

## Scripts / bats (ops, not imported by API)

`START_DFIP_DEMO.bat`, `STOP_DFIP_DEMO.bat`, `CHECK_DFIP_DEMO.bat`, `scripts/dfip_demo_env_presence.ps1`, `scripts/dfip_demo_port.ps1`, `scripts/dfip_demo_prepare.py`. Start bat prepares local identities via existing `local_demo_seed` (idempotent; local DSN only). Optional `--company-2` remains a separate manual command.

Root `tmp_*.py` diagnostic scripts may exist locally; **not** part of packaged V1; **NOT VERIFIED** as committed product.
