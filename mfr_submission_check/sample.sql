-- Dummy example only.
-- Replace this file with the real Phase 3 SQL after the schema is provided.
-- This query is read-only and assumes the following placeholder schema:
--   mfr_projects(project_id, project_name, reporting_month,
--               mfr_submitted_at, is_active)
--
-- The two %s placeholders must be supplied by Python as parameters:
--   1. reporting month, for example '2026-07'
--   2. active-project flag, for example 1

/*
SELECT
    p.project_id,
    p.project_name,
    p.reporting_month,
    p.mfr_submitted_at,
    CASE
        WHEN p.mfr_submitted_at IS NULL THEN 'not_submitted'
        ELSE 'submitted'
    END AS status
FROM mfr_projects AS p
WHERE p.reporting_month = %s
  AND p.is_active = %s
ORDER BY p.project_id;
*/

SELECT 1, 2;