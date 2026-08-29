-- Persist processing_run failure reason and chunk heartbeat.
-- Does not alter fact grain, publication_current, or catalog membership.

ALTER TABLE processing_run
    ADD COLUMN IF NOT EXISTS error_summary text,
    ADD COLUMN IF NOT EXISTS progress_at timestamptz;

COMMENT ON COLUMN processing_run.error_summary IS
    'Terminal failure reason for failed runs. Null when the run did not fail.';
COMMENT ON COLUMN processing_run.progress_at IS
    'Last committed transform chunk. Null until the first fact chunk commits.';
