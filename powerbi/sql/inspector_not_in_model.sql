-- INSPECTOR ONLY. Do not add these objects to the Power BI model.
-- Power BI Phase 2B binds exclusively to rpt_* published objects.
-- Failed runs, rejected rows, reconcile failures, and qa_finding severity
-- remain on the inspector API / database role (admin/publisher).

-- Findings by severity for a processing run (inspector GUC required):
-- SELECT severity, rule_id, COUNT(*) FROM qa_finding GROUP BY 1, 2;

-- Failed runs:
-- SELECT id, client_id, status FROM processing_run WHERE status = 'failed';

-- Rejected rows:
-- SELECT reason_code, COUNT(*) FROM stg_rejected_row GROUP BY 1;
