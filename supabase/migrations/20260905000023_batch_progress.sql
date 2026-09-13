-- Durable live processing progress for HTTP upload.
-- Status remains received/staged/processed/failed. These columns record the
-- current worker stage and measurable counters so GET /batches/{id} can
-- reconstruct progress after refresh. They are not a second batch status.

ALTER TABLE batch
    ADD COLUMN progress_stage text,
    ADD COLUMN progress_current integer,
    ADD COLUMN progress_total integer,
    ADD COLUMN progress_message text,
    ADD COLUMN progress_at timestamptz;

ALTER TABLE batch
    ADD CONSTRAINT batch_progress_stage_chk
    CHECK (
        progress_stage IS NULL
        OR progress_stage IN (
            'received',
            'ingesting',
            'staging',
            'pending',
            'processing',
            'validating',
            'succeeded',
            'failed'
        )
    );

ALTER TABLE batch
    ADD CONSTRAINT batch_progress_counts_chk
    CHECK (
        (progress_current IS NULL OR progress_current >= 0)
        AND (progress_total IS NULL OR progress_total >= 0)
    );
