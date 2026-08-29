-- DFIP V2 Phase 1 — tenant denormalization and per-client source SHA uniqueness.
-- V1 global source_file.sha256 uniqueness is replaced by UNIQUE(client_id, sha256).
-- SHA calculation is unchanged.

ALTER TABLE processing_run
    ADD COLUMN client_id uuid REFERENCES client (id);

UPDATE processing_run AS processing_run
SET client_id = batch.client_id
FROM batch
WHERE processing_run.batch_id = batch.id;

ALTER TABLE processing_run
    ALTER COLUMN client_id SET NOT NULL;

ALTER TABLE stg_source_row
    ADD COLUMN client_id uuid REFERENCES client (id);

UPDATE stg_source_row AS stg_source_row
SET client_id = batch.client_id
FROM batch
WHERE stg_source_row.batch_id = batch.id;

ALTER TABLE stg_source_row
    ALTER COLUMN client_id SET NOT NULL;

ALTER TABLE stg_rejected_row
    ADD COLUMN client_id uuid REFERENCES client (id);

UPDATE stg_rejected_row AS stg_rejected_row
SET client_id = batch.client_id
FROM batch
WHERE stg_rejected_row.batch_id = batch.id;

ALTER TABLE stg_rejected_row
    ALTER COLUMN client_id SET NOT NULL;

ALTER TABLE audit_log
    ADD COLUMN client_id uuid REFERENCES client (id);

ALTER TABLE source_file
    DROP CONSTRAINT source_file_sha256_key;

ALTER TABLE source_file
    ADD CONSTRAINT source_file_client_sha256_key UNIQUE (client_id, sha256);

CREATE INDEX processing_run_client_idx
    ON processing_run (client_id);

CREATE INDEX stg_source_row_client_idx
    ON stg_source_row (client_id);

CREATE INDEX stg_rejected_row_client_idx
    ON stg_rejected_row (client_id);

CREATE INDEX source_file_client_idx
    ON source_file (client_id);

CREATE INDEX fact_campaign_day_published_slice_idx
    ON fact_campaign_day (client_id, processing_run_id, day);

CREATE INDEX audit_log_client_idx
    ON audit_log (client_id);

COMMENT ON COLUMN processing_run.client_id IS
    'Denormalized from batch.client_id for RLS and publication integrity.';
COMMENT ON COLUMN stg_source_row.client_id IS
    'Denormalized from batch.client_id for RLS.';
COMMENT ON COLUMN stg_rejected_row.client_id IS
    'Denormalized from batch.client_id for RLS.';
COMMENT ON COLUMN audit_log.client_id IS
    'Nullable so platform-level events are representable.';
COMMENT ON CONSTRAINT source_file_client_sha256_key ON source_file IS
    'Per-client SHA uniqueness. V1 idempotency is preserved within a client.';
