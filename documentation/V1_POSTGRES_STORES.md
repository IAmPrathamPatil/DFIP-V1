# DFIP V1 — Postgres store method catalog

**Scope:** `PostgresIngestStore`, `PostgresFactStore`, `PostgresPublicationStore`, `PostgresCatalogStore`, `PostgresReadRepository`, `PostgresAnalyticsRepository`. There is **no** class named `PostgresIdentityStore`; identity is module functions in `dfip_db/identity.py`.

**Common transaction behavior:** `_tx()` → `transaction(pool, current_rls())` → one `conn.transaction()` (BEGIN/COMMIT or ROLLBACK). RLS GUCs applied when `current_rls()` is not None. Errors: `psycopg` integrity errors propagate unless caught; unreachable DB → `DatabaseUnavailableError`.

**In-memory counterparts:** `InMemoryIngestStore`, `InMemoryFactStore`, in-API publication/catalog stores — used when `DATABASE_URL` is empty. Not duplicated here.

**Hosted row contents:** **UNKNOWN** (empty DSN this audit).

---

## PostgresIngestStore (`ingest_store.py`)

Tables: `source_file`, `batch`, `stg_source_row`, `stg_rejected_row`, `processing_run`. Migrations: 000002 + 000010 + 000012 + 000016.

Callers: `ingest_workbook` / inspect pipeline; `UploadService`; reprocess creates runs.

| Method | Signature (essentials) | SQL | Params | Returns | Tx / errors |
|---|---|---|---|---|---|
| `get_source_file_by_sha256` | `(sha256, client_id=None)` | `SELECT * FROM source_file WHERE rtrim(sha256)=%s ORDER BY uploaded_at, id LIMIT 1` **or** `client_id=%s AND rtrim(sha256)=%s` | sha, optional client | `SourceFileRecord` or None | one tx |
| `get_source_file` | `(source_file_id)` | `SELECT * FROM source_file WHERE id=%s` | id | record or None | |
| `register_source_file` | `client_id, sha256, original_filename, byte_size, source_kind` | `INSERT ... ON CONFLICT (client_id, sha256) DO NOTHING RETURNING *`; if none, `SELECT * WHERE client_id AND rtrim(sha256)` | new uuid, UTC `uploaded_at` | record | `RuntimeError` if still None |
| `set_storage_uri` | `(source_file_id, storage_uri)` | `UPDATE source_file SET storage_uri=%s WHERE id=%s RETURNING *` | | record | `KeyError` if no row |
| `successful_batch_for_file` | `(source_file_id)` | `SELECT * FROM batch WHERE source_file_id=%s AND status=ANY(%s) ORDER BY created_at, id LIMIT 1` | `SUCCESS_BATCH_STATUSES` | batch or None | |
| `create_batch` | `source_file_id, client_id, worksheet_name=None, header_row=None, source_start_column=None` | `INSERT INTO batch (... status received, counts 0 ...)` RETURNING | new uuid | `BatchRecord` | |
| `get_batch` | `(batch_id)` | `SELECT * FROM batch WHERE id=%s` | | record or None | |
| `save_batch` | `(BatchRecord)` | `UPDATE batch SET` all mutable columns `WHERE id=%s` | record fields | None | no row-count check |
| `add_staged_row` | `(StagedRowRecord)` | `SELECT client_id FROM batch`; `INSERT INTO stg_source_row (id, batch_id, client_id, source_row_number, raw, campaign_id, variation_id, day)` | `Jsonb(raw)` | None | `KeyError` unknown batch |
| `add_rejected_row` | `batch_id, source_row_number, raw, reason_code, reason_detail` | SELECT client; `INSERT stg_rejected_row` RETURNING | Jsonb raw | `RejectedRowRecord` | `KeyError` unknown batch |
| `staged_for_batch` | `(batch_id)` | materializes `iter_staged_for_batch` | | list | many short txs |
| `iter_staged_for_batch` | `(batch_id, page_size=None)` | keyset pages 1000; `SET LOCAL statement_timeout='60s'` | `_STAGED_FIRST_PAGE_SQL` / `_NEXT` | iterator | **each page own tx** |
| `_fetch_staged_page` | internal | same | | rows | |
| `rejected_for_batch` | `(batch_id)` | `SELECT * FROM stg_rejected_row WHERE batch_id=%s ORDER BY source_row_number, id` | | list | |
| `processing_run_for_batch` | `(batch_id)` | `SELECT * FROM processing_run WHERE batch_id=%s ORDER BY started_at DESC, id DESC LIMIT 1` | | run or None | **latest any status** |
| `get_processing_run` | `(id)` | `SELECT * WHERE id=%s` | | or None | |
| `active_processing_run_for_batch` | `(batch_id)` | `status IN ('pending','running') ORDER BY started_at DESC LIMIT 1` | | or None | |
| `save_processing_run` | `(ProcessingRunRecord)` | `UPDATE processing_run SET` versions, engine, times, status, qa_verdict, error_summary, progress_at `WHERE id=%s` | | None | |
| `create_processing_run` | versions + `engine_version` | if `processing_run_for_batch` exists, **return it**; else `add_processing_run` | | record | idempotent per batch latest |
| `add_processing_run` | same | `INSERT INTO processing_run ... SELECT %s, b.id, b.client_id, ... FROM batch b WHERE b.id=%s RETURNING *` status `'pending'` | | record | `KeyError` unknown batch |

**Constraint:** uniqueness `(client_id, sha256)` after 000012 (not global sha256).

---

## PostgresFactStore (`fact_store.py`)

Tables: `fact_campaign_day`, `fact_campaign_day_history`. View: `published_fact_campaign_day`. Columns via `FACT_SELECT` / `FACT_COLUMNS` (`mapping.py`).

Callers: transform persist; QA `for_run`; publication snapshot copy; inspector `ReadService` via this store for published slice in some paths.

| Method | SQL / behavior | Returns | Errors / tx |
|---|---|---|---|
| `get(FactKey)` | `SELECT FACT_SELECT FROM fact_campaign_day WHERE client_id, campaign_id, variation_id_key, day` | fact or None | one tx |
| `upsert` | delegates `upsert_many([one])[0]` | action string | |
| `upsert_many` | one tx: `SET LOCAL statement_timeout='120s'`; `_load_for_update` `FOR UPDATE`; insert new; history insert; UPDATE restates (all cols except `first_seen_at`); UPDATE `last_seen_at` for unchanged | `list[str]` actions `inserted`/`restated`/`unchanged` | `UniqueViolation` → **retry entire `_upsert_many_once` once** |
| `for_batch` | `SELECT ... WHERE batch_id=%s` | list | unbounded fetch |
| `count_for_run` | `SELECT COUNT(*) WHERE processing_run_id=%s` | int | |
| `iter_for_run` | keyset pages 1000, timeout 60s; `ORDER BY client_id, campaign_id, variation_id_key, day` | iterator | **each page own tx** |
| `for_run` | `count` then `list(iter)`; mismatch → `RuntimeError` incomplete load | list | |
| `list_current` | `SELECT FACT_SELECT FROM fact_campaign_day` **no WHERE** | all visible under RLS | inspector dump |
| `list_history` | `SELECT FACT_SELECT, superseded_at, superseded_by_run_id FROM fact_campaign_day_history` | list | |
| `list_published_slice` | COUNT + SELECT page `ORDER BY day, campaign_id, variation_id_key, client_id LIMIT OFFSET`. Filters: `client_id`, `day IS NOT NULL`, optional `processing_run_id` unless `fact_scope=="client_current"`, optional day range. **Table:** `fact_campaign_day` if `working_set or current_rls() is None` else `published_fact_campaign_day` | `(facts, total)` | |

Helpers `_load_for_update`, `_insert_many` (multi-VALUES).

---

## PostgresPublicationStore (`publication_store.py`)

Tables: `publication`, `publication_current`, `publication_fact`. Callers: `PublicationService`.

| Method | SQL / behavior | Returns | Errors |
|---|---|---|---|
| `create` | **Single tx:** INSERT `publication` with `snapshot_status=complete`, `snapshot_row_count=expected`; copy live facts `INSERT publication_fact SELECT ... FROM fact_campaign_day WHERE` client/day/run/scope **or** chunk INSERT from `snapshot_facts`; COUNT must equal expected **and** written; UPSERT `publication_current` ON CONFLICT `client_id` | `(PublicationRecord, PublicationCurrentRecord)` | `PersistenceUnavailableError` incomplete snapshot or duplicate grains (`UniqueViolation` on insert chunks) |
| `get` | `SELECT * FROM publication WHERE id=%s` | or None | |
| `get_current` | `SELECT * publication_current WHERE client_id`; then `publication` by pointer id | `(pub or None, pointer or None)`; if pointer without pub: `(None, pointer)` | |
| `list_for_client` | `SELECT * FROM publication WHERE client_id ORDER BY published_at, id` | list | |
| `list_snapshot` | COUNT + SELECT `FACT_SELECT FROM publication_fact WHERE publication_id AND client_id ORDER BY day,... LIMIT OFFSET` | `(facts, total)` | |

`_copy_live_facts` / `_count_live_facts` / `_live_fact_filter`: `client_id`, `day IS NOT NULL`; `processing_run_id` unless `fact_scope == FACT_SCOPE_CLIENT_CURRENT`; optional period.

---

## PostgresCatalogStore (`catalog_store.py`)

**Only** HTTP Logic/Labels uploads with `notes = UPLOAD_NOTES`. Does **not** write template/rate JSON P2 tables except via separate resolvers.

| Method | SQL | Returns | Errors |
|---|---|---|---|
| `add_version` | INSERT version then per-row INSERT `campaign_label_row` **or** `label_group_member` | same record | one tx |
| `get` / `get_for_client` | `_load` | version or None | client mismatch → None |
| `list_for_client` | Logic: `SELECT * campaign_label_version WHERE client_id AND notes=UPLOAD_NOTES ORDER created_at DESC`; Labels: versions + member COUNT | tuple without full rows | |
| `save` | UPDATE status/effective/created_by WHERE id, client, notes | record | no rowcount |
| `active_for` | first `status==active` from list then `get_for_client` | or None | |
| `activate` | draft only, nonempty rows; supersede other active; set this active | loaded | `KeyError`, `ValueError` |
| `deactivate` | active → superseded | loaded | `KeyError`, `ValueError` |
| `_load` | SELECT version by id+notes then child rows | | same connection (caller tx) |

Grants: 000015 INSERT/UPDATE catalog tables for `dfip_api`.

---

## PostgresReadRepository (`read_repository.py`)

Inspector pagination. `limit`/`offset` (API max 200 elsewhere). **Application** `client_id` / `client_ids` AND RLS.

| Method | Tables / SQL | Errors |
|---|---|---|
| `list_source_files` | `_page` `source_file` | |
| `get_source_file` | `SELECT * WHERE id` | `NotFoundError` |
| `list_batches` | optional source_file_id, status | |
| `get_batch` | | `NotFoundError` |
| `list_processing_runs` | optional batch_id, status | |
| `get_processing_run` | | `NotFoundError` |
| `list_staged_rows` | `get_batch` first then filter stg | |
| `list_facts` | `fact_campaign_day` FACT_SELECT + `_fact_filters` | |
| `list_fact_history` | history + superseded cols | |
| `_page` | `SELECT COUNT(*)`; `SELECT columns FROM table WHERE ... ORDER LIMIT OFFSET` | one tx both queries |

`_client_clause`: empty `client_ids` tuple → `WHERE FALSE`.

---

## PostgresAnalyticsRepository (`analytics_repository.py`)

| Method | SQL | Tx notes | Errors |
|---|---|---|---|
| `replace_findings` | `replace_for_run(first.processing_run_id, findings)` | empty list no-op | |
| `replace_for_run` | `_delete_for_run` then chunks of 1000 `insert_finding_unnest` | **delete 1 tx; each insert chunk own tx**; timeout 120s | on failure: log, try delete again, **re-raise** |
| `_delete_for_run` | `DELETE FROM qa_finding WHERE processing_run_id=%s` | | |
| `list_findings` | maps `list_for_run` to subset keys | | |
| `list_for_run` | `SELECT` finding fields `FROM qa_finding WHERE processing_run_id ORDER BY rule_id, entity_key` | | |
| `list_published_facts` | `SELECT ... FROM rpt_published_fact ORDER BY ...` | unused by HTTP routes (grep PARTIALLY — analytics repo used by QA persist + tests) | |
| `list_campaigns` | `rpt_dim_campaign` | | |
| `list_variations` | `rpt_dim_variation` | | |

`insert_finding_unnest`: `INSERT INTO qa_finding (...) SELECT ... FROM unnest(%s::uuid[], ...)`.

---

## identity.py (not a Store class)

| Function | SQL | Tx | Notes |
|---|---|---|---|
| `fetch_identity` / `fetch_credential` | `app_user` LEFT JOIN `client_membership` `WHERE u.subject=%s` | caller conn | credential includes `password_hash` |
| `increment_token_version` | `UPDATE app_user SET token_version=token_version+1` RETURNING | | |
| `*_from_pool` | same via `transaction(pool, rls=None)` | **no SET ROLE dfip_api** | login path |

---

## Call path summary

```
HTTP → bind_rls → services
  UploadService → ingest_workbook → PostgresIngestStore → (later) run_transformation → PostgresFactStore.upsert_many
                → QA → PostgresAnalyticsRepository.replace_for_run
  PublicationService → PostgresPublicationStore.create → publication_fact + publication_current
  ReadService → PostgresReadRepository / FactStore.list_published_slice
  CatalogService → PostgresCatalogStore
  auth → identity *_from_pool (rls=None)
```
