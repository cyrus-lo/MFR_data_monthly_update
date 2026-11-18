# Accrual worksheet-name error investigation

**Status:** Open investigation  
**First documented:** 2026-08-01  
**Related Snowflake objects:**

- Procedure: `CHUNWO_DB.PUBLIC.PROC_MFR_EXCEL_TO_TABLE_MFR_ACCRUAL()`
- UDTF: `CHUNWO_DB.PUBLIC.EXCEL_READ_UDTF_ACCRUAL(VARCHAR)`
- Stage: `@CHUNWO_DB.PUBLIC.MFR_XLSX`
- Target table: `CHUNWO_DB.PUBLIC.MFR_1_BRONZE_ACCRUAL`

## Issue

The Accrual load has produced the error:

```text
Read Failed: Worksheet named 'Accrual' not found
```

At the same time, valid Accrual rows exist in the Bronze table. The current question is how the valid rows and the worksheet-name error can coexist, and which staged workbook caused the error.

## Current observed impact

The read-only Step 4 QA check for reporting month `2026-06` observed the following project-level result for the affected logical ingestion:

- Expected projects: `20`
- Parallel table: `91/91` selected rows
- Accrual table: `71/91` selected rows
- Cash Flow table: `71/91` selected rows
- Committed Cost table: `71/91` selected rows
- Projection table: `71/91` selected rows
- Affected project identified by the QA result: `969`
- Rows outside the temporary 120-second ingestion cluster had `DW_CREATED_TIME` equal to `1969-12-31 00:00:00.000 -2400`.

The malformed timestamp issue is related context but is separate from the worksheet-name error. The timestamp was observed in UDTF output before the procedure inserted it into the Bronze table.

## Relevant processing path

The procedure supplied for investigation is append-only and has these characteristics:

1. It scans `DIRECTORY(@CHUNWO_DB.PUBLIC.MFR_XLSX)`.
2. It passes each directory entry to `EXCEL_READ_UDTF_ACCRUAL(...)`.
3. It does not currently apply an Accrual-specific file filter.
4. It uses a positional `INSERT INTO ... SELECT ...` without an explicit target-column list.
5. It inserts the UDTF-provided `t.DW_CREATED_TIME` directly into the Bronze table.

The UDTF supplied in the conversation uses an exact worksheet lookup for `Accrual`. Worksheet names that differ by spelling, spacing, punctuation, or capitalization may therefore fail, depending on the lookup behavior in the deployed code.

## How valid rows and the error can coexist

The following explanation is currently the strongest hypothesis, but it is not yet proven for the incident:

1. The stage contains more than one workbook or directory entry.
2. A workbook with an `Accrual` worksheet is processed successfully and produces valid rows.
3. A different workbook, or a different staged file, does not contain the exact `Accrual` worksheet.
4. The same broad `DIRECTORY(...)` scan sends that file to the Accrual UDTF, which raises the worksheet error.
5. Because the Bronze table is append-only, rows from an earlier successful invocation remain even if a later invocation fails.

Other possible explanations that must remain open:

- The valid rows and the error came from different procedure invocations or retries.
- A previous successful load left rows in the table and a later load failed before adding new rows.
- The stage contained temporary, unrelated, corrupt, or differently named workbooks that were included by the unfiltered directory scan.
- The deployed UDTF contains an error-handling or multi-file path that differs from the source pasted in the conversation.
- The error was reported by a separate execution from the execution that produced the rows counted by QA.

Do **not** conclude that one workbook both inserted all valid rows and then failed, or that project `1010` caused the error, until Snowflake query history and workbook metadata confirm it.

## What is known and unknown

### Known

- The UDTF expects a worksheet named `Accrual`.
- The procedure passes stage directory entries to the UDTF without a demonstrated file filter.
- The target table is append-only.
- The local repository does not contain the deployed UDTF source or its SQL DDL.
- The complete UDTF definition currently available to the investigation is the code pasted by the user in the conversation.
- Two local prepared workbooks were found under the project `1010` artifact folder:

  ```text
  run_logs/2026-06/ecc7a927-7405-4db9-956e-5de407a9fa42/artifacts/prepared/1010/eMFR_1010_2026-06.xlsx
    run_logs/2026-06/ecc7a927-7405-4db9-956e-5de407a9fa42/artifacts/prepared/1010/Sub_eMFR_1010-2026-06.xlsx
  ```

    The worksheet names of these files have not yet been verified.

### Unknown

- Which exact stage file caused the worksheet error.
- Whether the error and the valid rows came from the same procedure invocation.
- Whether the deployed UDTF exactly matches the code pasted in the conversation.
- Whether Snowflake query execution history contains a file-level error or query ID that links the error to a particular stage path.
- Whether worksheet matching is case-sensitive in the deployed UDTF implementation.
- Whether the stage contained both the main `eMFR` workbook and `Sub_eMFR` workbook during the failing invocation.

## Investigation steps

### 1. Retrieve the deployed UDTF definition

Run this in Snowflake with a role that can view the function definition:

```sql
SELECT GET_DDL(
    'FUNCTION',
    'CHUNWO_DB.PUBLIC.EXCEL_READ_UDTF_ACCRUAL(VARCHAR)'
);
```

If the signature is not accepted, first confirm the registered signature:

```sql
SHOW USER FUNCTIONS IN SCHEMA CHUNWO_DB.PUBLIC;
```

Then inspect the exact function signature:

```sql
DESCRIBE FUNCTION
    CHUNWO_DB.PUBLIC.EXCEL_READ_UDTF_ACCRUAL(VARCHAR);
```

Save the returned DDL as evidence. Do not replace it with an unverified copy from the conversation.

### 2. Identify the failing procedure query

Use Snowflake query history around the reported failure time and locate executions of:

```sql
CHUNWO_DB.PUBLIC.PROC_MFR_EXCEL_TO_TABLE_MFR_ACCRUAL()
```

Capture, where available:

- Query ID
- Start and end time
- User, role, warehouse, and session
- Procedure call text
- Error code and full error message
- Whether the execution was a retry
- Any query ID or child-query information exposed for the UDTF call

Do not include passwords, tokens, connection strings, or other secrets in this document.

### 3. Inspect every file currently on the stage

Use Snowflake metadata to list the stage entries and paths visible to the executing role. Record sanitized relative paths and timestamps, not credentials or scoped URLs.

The important question is whether the unfiltered directory contains files that are not valid Accrual workbooks.

### 4. Verify local workbook worksheet names correctly

Do not inspect `.xlsx` files as text. Use `openpyxl` to read workbook metadata. For example, from the workspace root in PowerShell:

```powershell
& ".\.venv\Scripts\python.exe" -c "
from openpyxl import load_workbook
from pathlib import Path

root = Path(r'run_logs/2026-06/ecc7a927-7405-4db9-956e-5de407a9fa42/artifacts/prepared/1010')
for path in sorted(root.glob('*.xlsx')):
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        print(path.name, workbook.sheetnames)
        workbook.close()
    except Exception as exc:
        print(path.name, f'ERROR: {exc}')
"
```

Repeat this for the prepared workbooks of all expected projects if the stage contents cannot be correlated directly with the local run artifacts.

### 5. Correlate rows with load/invocation evidence

For the affected project, compare:

- `PROJECT_CODE`
- `REPORT_DATE`
- `DW_CREATED_TIME`
- Any available file name, source path, query ID, or run identifier
- The Step 3 upload receipt and its staged file list
- Procedure/query-history timestamps

The current Bronze schema does not appear to preserve a source file name, so correlation may require query history, stage metadata, or a controlled reproduction.

## Recommended hardening after evidence is collected

These changes should be reviewed before production deployment:

1. Filter `DIRECTORY(...)` to only the intended Accrual workbook paths.
2. Use an explicit target-column list in the `INSERT` statement.
3. Create one procedure-level `load_ts` with `CURRENT_TIMESTAMP()::TIMESTAMP_TZ(9)` and insert that value for every row in the invocation instead of relying on per-row UDTF timestamp conversion.
4. Add an exception block that uses `RAISE;` when the Snowflake Task must be marked failed. Returning a text value such as `FAILED: ...` normally completes the procedure and may allow the Task to appear successful.
5. If multi-file processing is intentional, record file-level success and failure separately and make the Task success criteria explicit.
6. Consider rejecting a workbook before UDTF invocation when its worksheet set is incompatible with the requested loader.

## Evidence and related artifacts

- QA output directory: `run_logs/qa/`
- Latest known QA artifact: `run_logs/qa/step4_postcheck_2026-06_20260730T084341Z.json`
- Project `1010` prepared artifact directory: `run_logs/2026-06/ecc7a927-7405-4db9-956e-5de407a9fa42/artifacts/prepared/1010/`
- Step 4 post-check implementation: `mfr_snowflake_task/trigger_task.py`
- Step 4 post-check configuration: `mfr_snowflake_task/task_postchecks.json`
- Read-only QA utility: `tools/qa_step4_postcheck.py`

## Working conclusion

The worksheet error is generated by the Accrual UDTF's exact worksheet lookup. The procedure's unfiltered stage-directory scan makes it possible for a valid workbook and a workbook without the required `Accrual` worksheet to be considered in the same broad load operation. However, the exact failing file and the relationship between that error and the rows counted by QA remain unproven. The next decisive evidence is the deployed UDTF DDL, Snowflake query history, the stage file listing, and verified worksheet names for the relevant workbooks.
