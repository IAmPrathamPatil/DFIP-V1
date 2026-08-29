-- DFIP V1 P3 — batch worksheet / source-region metadata.
-- Does not add RLS, authentication, or transformation tables.
-- stg_source_row.raw remains the 57-column payload keyed by exact Excel headers.

ALTER TABLE batch
    ADD COLUMN worksheet_name text,
    ADD COLUMN header_row integer,
    ADD COLUMN source_start_column text,
    ADD COLUMN empty_row_count integer;

COMMENT ON COLUMN batch.worksheet_name IS
    'Worksheet ingested. Supplied artifacts use Web-Engage Raw.';
COMMENT ON COLUMN batch.header_row IS
    '1-based Excel row that holds the 57 source headers.';
COMMENT ON COLUMN batch.source_start_column IS
    'Excel letter of the first of the 57 source columns (K on legacy workbooks).';
COMMENT ON COLUMN batch.empty_row_count IS
    'Fully empty K:BO rows skipped. Not rejected and not staged.';
