# Step 3 — Snowflake upload

`upload_mfr_files_to_snowflake.py` is executed only by the shared worker after Steps 1 and 2 succeed. It receives the reporting month, run ID, the active Step 3 run artifact directory, and Step 2's `file_manifest.json`.

The script uploads only manifest-approved `eMFR_*.xlsx` and `Sub_eMFR_*.xlsx` files whose SHA-256 checksums still match. It never scans a user Downloads folder. `eMFR_*.xlsx` files go only to `SF_EMFR_STAGE_NAME`; `Sub_eMFR_*.xlsx` files go only to `SF_SUB_EMFR_STAGE_NAME`. Each upload uses `AUTO_COMPRESS=FALSE OVERWRITE=TRUE`; `UPLOADED` and the stage's explicit `SKIPPED` response are accepted outcomes.

When an operator clicks **Run Step 3**, both dashboards first show the latest same-month Step 2 `mfr_preparation_log.csv`, including the HKT timestamp, filename, and status. If every file has `SUCCESS_PREPARED` or `SUCCESS_UNCHANGED`, the dialog reports that all files are successful and offers the normal manifest-backed upload.

Copy `.env.example` to `.env` and provide the approved Snowflake values. `SF_EMFR_STAGE_NAME` and `SF_SUB_EMFR_STAGE_NAME` must both be unquoted `@stage` identifiers, optionally qualified or with a stage subdirectory. They are mandatory and must be different. Secrets remain in the ignored `.env` file and are not printed by the script.

For a standalone Step 3 run, the worker resolves the latest successful Step 2 run for the selected reporting month. The script reads workbook paths relative to that Step 2 manifest's own artifact directory, so it uploads the prepared files from that successful Step 2 run rather than looking for copies in the new Step 3 run folder. On success, `upload_receipt.json` is written in the current Step 3 run folder under `run_logs/<month>/<step-3-run-id>/artifacts/`; each file record includes its destination stage. A missing/mismatched manifest file, missing/invalid/equal stage settings, connection failure, unexpected PUT status, or incomplete result fails Step 3.

If the Step 2 log is missing or contains a warning/failure, the dialog offers an exception option to select a folder on the computer hosting Streamlit. The native Windows folder picker has a manual path fallback. Only non-empty `eMFR_*.xlsx` and `Sub_eMFR_*.xlsx` files are included recursively; the selected folder, checksums, files, and optional reason are recorded in the Step 3 run configuration and generated manifest. Step 1 for the selected reporting month is still required.
