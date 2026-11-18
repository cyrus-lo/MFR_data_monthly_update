# Step 2 — Excel download and preparation

`download_CIS_mfr_files.py` is executed only by the shared worker after Step 1 succeeds. It receives the reporting month, run ID, an artifact directory, and the successful Step 1 project manifest. It does not use a hard-coded project list or a user Downloads folder.

The step writes all temporary and final files below `run_logs/<month>/<run-id>/artifacts/`:

- `downloads/`: CIS response ZIP files
- `extracted/`: project-isolated archive extraction
- `prepared/`: Excel workbooks approved for upload
- `mfr_preparation_log.csv`: per-workbook preparation audit log
- `file_manifest.json`: checksum-protected Step 3 input

Copy `.env.example` to `.env` to override the CIS endpoint or timeout. The current endpoint does not require credentials. `CIS_MFR_REPORT_URL_TEMPLATE` must contain `{project_id}` and `{reporting_month}`.

## Windows worker requirement

Both `eMFR_*.xlsx` and `Sub_eMFR_*.xlsx` workbooks are extracted and copied to `prepared/`. Only `eMFR_*.xlsx` workbooks are opened by Excel: their active-sheet C7 and C8 formulas are calculated and saved as values. A blank or empty calculated result is valid; it is written back to the same cell as a blank value. `Sub_eMFR_*.xlsx` workbooks are copied without opening or modifying them. Consequently, this step requires an interactive-capable Windows worker with desktop Microsoft Excel and `pywin32` installed. It fails closed only if Excel or workbook processing has an operational error.

The run-scoped `mfr_preparation_log.csv` records `reporting_month`, `run_id`, `timestamp_hkt`, `filename`, `status`, and `error_message`. Statuses are `SUCCESS_PREPARED` for modified eMFR workbooks, `SUCCESS_UNCHANGED` for copied Sub_eMFR workbooks, and `FAILED` for processing errors. The log is written as files are handled, so earlier results remain available when a later file fails.

The step returns zero only after every Step 1 project has a valid workbook and the file manifest has been written. It rejects unsafe archive paths and encrypted archive authentication failures.

## Selecting project files

For a standalone Step 2 run, the dashboard offers **All projects confirmed ready** (the normal choice) or **Choose specific projects**. Operators may select verified project codes, paste comma- or line-separated codes, or use both. Every code is checked against the successful Step 1 project list before the run is queued. The worker writes the approved selection to the Step 2 run's own project manifest; the Python script continues to read only that manifest.
