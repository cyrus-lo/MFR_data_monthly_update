# MFR Submission Check — Business User Run Guide

## Purpose

This small read-only utility connects to the configured MySQL database and runs a SQL query. It is intended to support the monthly MFR submission check.

The current `sample.sql` is a dummy query only. It returns `SELECT 1` and does not check real MFR projects. Replace it only after the approved production SQL and schema have been provided.

## Run from the internal dashboard

For normal monthly operations, start the Streamlit dashboard and use its **Run step** or **Run All** controls. The shared worker is a separate process. When **Check submission** or **Run All** is queued, the dashboard attempts to start the worker if it is stopped. It first invokes `MFR-Worker.ps1` normally and retries with `-ExecutionPolicy Bypass` only if PowerShell explicitly reports an execution-policy block. The bypass applies only to that PowerShell process and does not alter persistent policy settings. If automatic startup fails, start the worker manually with `MFR-Worker.ps1 Start`.

The dashboard selects the reporting **year and month** only. Each dashboard-triggered Step 1 execution appends one audit row to `run_logs\sql_execution_log.csv`, including `report_month` and `run_id`. There is one latest completion signal at `run_logs\mfr_completion_signal.csv`. When the completion result is `All completed`, Step 1 also writes `artifacts\project_manifest.json` under its run-specific `run_logs\<reporting-month>\<run-id>\` folder. Steps 2 and 3 consume this run-scoped artifact contract; they do not read the shared signal as a project list. The worker keeps `submission_check.log` in the same run folder. The worker stops when the Streamlit server is stopped or restarted.

## Before running

1. Open PowerShell in the workspace folder:

   `C:\Users\cyrus.lo\Documents\vibe_code_test\MFR_data_monthly_update`

2. Activate the workspace Python environment:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

3. Create the local configuration file by copying `mfr_submission_check\.env.example` to `mfr_submission_check\.env`.

4. Replace the placeholder values in `.env` with the approved MySQL connection details. Never commit or email this file.

Required settings:

- `DB_HOST`: MySQL server name or IP address
- `DB_PORT`: MySQL port, normally `3306`
- `DB_USER`: approved database user
- `DB_PASSWORD`: database password
- `DB_NAME`: target database name

## Run the MFR completion check

From the workspace root, run:

```powershell
python .\mfr_submission_check\check_mfr_submission.py `
   --sql-file .\mfr_submission_check\check_MFR_submission.sql `
   --param 2026-06-30
```

Replace `2026-06-30` with the required reporting month in `YYYY-MM-DD` format. The MFR query returns all 21 target projects, including projects with no MFR record for that month. A missing MFR is returned as `missing` and is not completed.

The check expects 20 projects by default. Only if the approved target-project list changes, provide its matching count with `--expected-project-count`.

The SQL file is loaded, comments are ignored, and the query is checked to ensure it is one read-only `SELECT` or `WITH` statement. A leading `USE database_name;` is allowed only when it matches `DB_NAME`; the script does not switch away from the configured database. The script then connects to MySQL and executes the query.

After each run, the query result is appended to `run_logs\sql_execution_log.csv`. The log uses a stable set of columns so different SQL files can be recorded in the same file. Dashboard runs also record `report_month` and `run_id`; the returned rows are stored as JSON in the `result_json` column.

The log contains:

- Hong Kong timestamp (HKT, UTC+08:00)
- SQL file name
- Execution status
- Completion check result: `All completed` or `Not all completed`
- Number of returned rows
- Human-readable summary
- JSON-encoded returned rows
- Redacted error message, when applicable

Use `--log-file` to choose another CSV path:

```powershell
python .\mfr_submission_check\check_mfr_submission.py `
   --sql-file .\mfr_submission_check\sample.sql `
   --log-file .\run_logs\sql_execution_log.csv
```

## Pipeline completion signal

Every run overwrites the one shared `run_logs\mfr_completion_signal.csv`. This is the control file for the next pipeline step. It contains one row with the exact `check_result` value:

- `All completed` — every expected project is present once and has `status = completed`.
- `Not all completed` — one or more projects are missing, have a different status, are duplicated, the returned project count is wrong, or the query failed.

The next pipeline step must trigger only when `check_result` is exactly `All completed`. The signal also contains counts, non-completed project codes, and diagnostic reasons for review. Running a dummy or unrelated SQL file will safely overwrite the signal with `Not all completed`.

The current sample query has no `%s` parameters. For a future parameterized SQL file, pass parameters in order using repeated `--param` options:

```powershell
python .\mfr_submission_check\check_mfr_submission.py `
  --sql-file .\mfr_submission_check\sample.sql `
  --param 2026-07 `
  --param 1
```

## How output is shown

Output is printed directly in the PowerShell terminal.

### Successful MFR completion check

When every target project is completed, a successful MFR run looks like:

```text
SUCCESS: MySQL connection and read-only query completed.
RESULT: read-only query returned 21 record(s); MFR completion status was not evaluated.
CHECK RESULT: All completed
LOG: appended execution result to run_logs\sql_execution_log.csv
SIGNAL: wrote latest result to run_logs\mfr_completion_signal.csv
```

If a project is missing or has a different status, the output contains `CHECK RESULT: Not all completed` and lists the affected project codes.

### No matching records

For a real query that returns no rows:

```text
SUCCESS: MySQL connection and read-only query completed.
RESULT: no matching records returned.
```

### Records requiring review

For a real query that returns a `status` value such as `not_submitted`, `pending`, or `incomplete`:

```text
SUCCESS: MySQL connection and read-only query completed.
RESULT: 3 record(s) require review.
```

### Errors

- Missing configuration: `CONFIGURATION ERROR: ...` (exit code `2`)
- Invalid or unsafe SQL file: `QUERY CONFIGURATION ERROR: ...` (exit code `2`)
- Cannot connect to MySQL: `DATABASE CONNECTION ERROR: ...` (exit code `1`)
- MySQL rejects the query: `QUERY ERROR: ...` (exit code `1`)

Passwords are not printed in normal output or error messages.
The CSV execution log and pipeline signal are ignored by Git because they may contain database results.

## Important limitations

- The utility performs read-only query execution only; it does not insert, update, or delete data.
- A real SQL file must use MySQL Connector/Python `%s` placeholders for parameters.
- The MFR query expects one MFR row per project for the requested month. A duplicate fails the completion check closed as `Not all completed`.