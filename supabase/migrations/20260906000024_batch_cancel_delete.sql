-- Publisher cancel/delete for non-authoritative uploads.
-- Cancelled is a terminal batch/run status. cancel_requested is the
-- cooperative stop flag the worker checks between persist chunks.
-- Published publications are not deleted here.

ALTER TABLE batch DROP CONSTRAINT IF EXISTS batch_status_chk;
ALTER TABLE batch ADD CONSTRAINT batch_status_chk
    CHECK (status IN (
        'received',
        'staged',
        'validated',
        'failed',
        'processed',
        'cancelled'
    ));

ALTER TABLE batch
    ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false;

ALTER TABLE batch DROP CONSTRAINT IF EXISTS batch_progress_stage_chk;
ALTER TABLE batch ADD CONSTRAINT batch_progress_stage_chk
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
            'failed',
            'cancelling',
            'cancelled'
        )
    );

ALTER TABLE processing_run DROP CONSTRAINT IF EXISTS processing_run_status_chk;
ALTER TABLE processing_run ADD CONSTRAINT processing_run_status_chk
    CHECK (status IN (
        'pending',
        'running',
        'succeeded',
        'failed',
        'cancelled'
    ));
