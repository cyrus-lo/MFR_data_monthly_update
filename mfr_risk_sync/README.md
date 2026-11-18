# Step 5 — Risk DB Sync (template)

This is a placeholder for the future manual Risk DB sync. It is deliberately **not configured** in the dashboard until the dev environment is available.

The future sequence is:

1. SSH to the approved server.
2. Change to `backend`.
3. Run `curl -sS -X POST "http://localhost:8000/api/update-db" | jq`.
4. Copy the generated Risk workbook from Docker into [mfr_risk_check/MFR_vs_Risk_calculation/Risk_calculation_files](../mfr_risk_check/MFR_vs_Risk_calculation/Risk_calculation_files).
5. Confirm the agreed sync status, then run Step 6.

[sync_risk_db_template.ps1](sync_risk_db_template.ps1) contains placeholders for the SSH, Docker, exported-file, and status details. It exits without running anything until all placeholders are replaced with approved values.
