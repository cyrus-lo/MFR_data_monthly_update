USE businessplan;

-- MFR completion check for the selected reporting month.
-- Supply the reporting month through --param, for example: 2026-06-30.
-- The project table drives the result so missing MFRs are returned as `missing`.
SELECT
	pj.code AS project_code,
	COALESCE(mfr.status, 'missing') AS status,
	mfr.report_month,
	mfr.id AS mfr_id
FROM projects AS pj
LEFT JOIN monthly_financial_reports AS mfr
	ON mfr.project_id = pj.id
	AND mfr.report_month = %s
WHERE pj.code IN (
	969, 997, 1010, 1014, 1016, 1027, 1036, 1069, 1073, 1076,
	1086, 1089, 1090, 1105, 1113, 1120, 1131, 1132, 1148, 1154
)
ORDER BY pj.code;

