# Step 4 — Snowflake Task Graph

Step 4 manually triggers the root Snowflake Task Graph after an operator confirms that all files uploaded by Step 3 are present in the Snowflake Stage.

## Scope

- Step 4 is standalone-only. Run All and the monthly scheduler stop after Step 3.
- A successful Step 3 run for the selected reporting month is required.
- The dashboard requires a fresh per-run Stage confirmation.
- The reporting month is used for local prerequisite and audit correlation. It is not passed to Snowflake; the Task derives its month internally.
- Existing active graph executions are rejected safely.
- Automatic retries and `RETRY LAST` are not supported.

## Configuration

Copy `.env.example` to `.env` and provide the approved service-account settings. The real `.env` file is ignored by source control.

`SF_PASSWORD` must be the normal Snowflake password for `SF_USER`, passed using
the same password authentication as Step 3. Do not paste an OAuth access token
or JWT into `SF_PASSWORD`; those require a separate, explicitly configured
authentication flow and are not supported by this script. Step 4 intentionally
keeps its own `.env` file, so changing Step 3's credentials does not change Step
4's credentials.

Configure one or more fully qualified root Tasks with `SF_TASK_NAMES`, separated by commas. Each Task may use a different schema because every value is a complete `DATABASE.SCHEMA.TASK` identifier. The existing `SF_TASK_NAME` setting remains supported for backward compatibility. For example:

`SF_TASK_NAMES=CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL,CHUNWO_DB.PUBLIC.TASK_MFR_POST_PROCESS`

Set `SF_TASK_EXECUTION_MODE` to `sequential` or `parallel`. Sequential mode is the default and runs tasks in the configured order, stopping before later tasks after a failure. Parallel mode is intended only for independent tasks: all tasks are triggered after preflight and then monitored together. Tasks already triggered in parallel mode are not cancelled if another task fails.

## Optional transition post-checks

Set `SF_TASK_POSTCHECKS_FILE=task_postchecks.json` to enable a versioned, non-secret JSON rule file. Rules are directed `after_task` → `before_task` gates and must join consecutive Tasks in `SF_TASK_NAMES`; enabled rules require `sequential` mode. The supplied rule runs after `CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL` succeeds and before `CHUNWO_DB.MFR_POC.TASK_TEST_API_TRIGGER` is triggered.

The built-in checks obtain their expected distinct `PROJECT_CODE` set from the latest successful same-month Step 3 `upload_receipt.json`. This expected count is displayed in the Step 4 confirmation dialog and is explicitly **not** a live Snowflake table count. The Bronze transition check compares current and prior `REPORT_DATE` snapshots, unchanged. The terminal Silver check keeps that snapshot comparison for `CHUNWO_DB.MFR.MFR_2_SILVER`; its four `MFR_2_SILVER_MONTHLY_*` tables compare the operating fiscal month against the immediately preceding calendar month within the current report-date snapshot, using `FISCAL_YEAR_MONTH`.

The supplied configuration uses a temporary `ingestion_cluster_interval_seconds` value of `120` for each table. For each table, project, and report date, the query counts every row with `DW_CREATED_TIME` in the trailing 120-second window ending at that partition's newest timestamp. This avoids losing portions of a logical load when parallel writes receive slightly different timestamps. For example, 71 rows at one timestamp and 20 rows 232 milliseconds earlier are counted as 91 rows.

This interval is a heuristic, not a durable Snowflake run identifier: independent loads that write the same table/project/report-date within 120 seconds can be counted together, while one slow logical load whose writes span more than 120 seconds can be split. For monthly Silver tables, the current report-date snapshot is selected first using this window, then both fiscal-month counts come from that selected snapshot. Do not change the interval without reviewing its impact for that table. The post-check receipt records comparison mode, snapshot date, fiscal periods, selection method, interval, and per-project counts. A missing current or prior monthly fiscal period fails the terminal Silver check; otherwise a row-count difference of up to one is allowed. The main Silver and Bronze rules retain their report-date snapshot semantics. A mismatch, query error, or invalid rule fails Step 4. The receipt contains per-table and per-project results and the dashboard shows the concise failure reason as the latest Step 4 result.

The service account needs permission to execute and monitor the Task Graph. In practice, request `OPERATE` or `OWNERSHIP` on the root Task, the required global `EXECUTE TASK` and warehouse privileges for the Task owner, monitoring access, and `USAGE` on the database and schema.

## One-off post-check QA

For investigating post-check SQL or Bronze/Silver data without running either Task, use the standalone QA script from the workspace root:

```powershell
$env:RUN_SNOWFLAKE_QA = "1"
& .\.venv\Scripts\python.exe .\tools\qa_step4_postcheck.py --reporting-month 2026-06
```

The `--step3-upload-receipt` argument is optional. If omitted, the script reads the latest successful same-month Step 3 run from `data/pipeline.db` and uses its `artifacts/upload_receipt.json`. Supplying a path explicitly is useful when testing a copied or sanitized receipt. The receipt is required because the gate's expected project set is defined by Step 3; it is not required to test SQL generation locally.

This QA script connects using the existing Step 4 `.env`, performs a read-only Snowflake identity query, and executes only the parameterized post-check query. It does not call `EXECUTE TASK`, Task A, Task B, Task Graph monitoring, or `TASK_HISTORY()`. It writes a sanitized result under `run_logs/qa/`; the query can still consume warehouse/table-scan credits. Do not set `RUN_SNOWFLAKE_QA=1` unless the current `.env` target is approved for this read-only investigation.

## Execution behavior

The script runs `EXECUTE TASK` for each configured root Task, then immediately checks Snowflake and polls again every eight minutes for up to 60 minutes. It correlates each new execution using the root Task ID, trigger boundary, and graph run group ID. A graph is successful only when its final state is `SUCCEEDED`. Snowflake conditionally skipped child Tasks are accepted when the graph itself is `SUCCEEDED`.

`FAILED`, `CANCELLED`, an ambiguous execution, a connection/authorization error, or a timeout fails Step 4. Errors are sanitized before they are written to logs or receipts.

The worker gives the subprocess a 65-minute limit to allow the monitor to perform its final check.

## Artifacts

The worker writes one aggregate `task_execution_receipt.json` under the active run's `artifacts` folder. The receipt contains the reporting month, operator confirmation metadata, execution mode, ordered task results, each Task identity, root Task ID, trigger query ID, graph run group ID, attempt, graph/node history, timestamps, final state, post-check results where configured, and sanitized error information where applicable.

## CLI

The worker invokes the script with `--reporting-month YYYY-MM`, `--run-id <pipeline run ID>`, and `--artifact-dir <active run artifacts directory>`. When an enabled transition rule requires Step 3 expectations, the worker also supplies the exact successful prerequisite `--step3-upload-receipt <path>`.
