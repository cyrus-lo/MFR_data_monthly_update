# MFR monthly pipeline

This repository now contains the first implementation slice of the internal pipeline dashboard.

## Python environment

Use Python 3.10 or newer. From the repository root, create the project environment if needed (replace `3.12` with an installed Python 3.10+ version) and install the root requirements with that environment's interpreter:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

After installing or updating dependencies, restart the running V1 and V2 dashboard processes so they load the updated packages. A dashboard-only Streamlit dependency change does not require restarting the worker.

## Run the dashboards

The Streamlit interfaces queue and monitor work. Start **one** shared worker from the workspace root before using the dashboards when possible:

```powershell
.\MFR-Worker.ps1 Start
.\MFR-App.ps1 Start       # v1 at http://localhost:8501
.\MFR-App-V2.ps1 Start    # v2 at http://localhost:8502
```

The worker is the sole queue consumer and executes all steps from `data/pipeline.db`. Both UIs use that same database and `run_logs/`, so a run queued from one interface appears in the other. The repository enforces one active run globally; do not start a second worker manually.

If the worker is stopped, either dashboard automatically attempts to start it when **Check submission** or **Run All** is queued. The launcher is tried normally first; only a recognized PowerShell execution-policy denial triggers a retry using `-ExecutionPolicy Bypass` for that one PowerShell process. This does not change a persistent policy. If startup still fails, start the worker manually and review `data/mfr_worker_error.log`.

Use `Status` or `Stop` with the same helper names to inspect or stop each process. Stopping either UI does not stop the worker. Stop the worker separately only after all queued/running work is complete.

V2 provides selectable Minimal, Snowflake-inspired, and Databricks-inspired visual styles. These are original visual themes only, not Snowflake or Databricks product integrations. Its `Run` controls queue real MFR stages. Config, per-stage Schedule, and Delete dialogs are clearly marked non-persistent prototypes because the current backend uses fixed adapters, one shared monthly Run All schedule, and no deletion workflow.

## Current status

Steps 1–6 are registered in the dashboard: MySQL submission validation, CIS workbook download/preparation, Snowflake stage upload, standalone Snowflake Task Graph execution, a future Risk DB sync template, and MFR-versus-Risk comparison. Run All intentionally stops after Step 3 because Steps 4–6 are manual stages. Step 4 requires Snowflake Stage confirmation, Step 5 remains unavailable until its dev-environment details are supplied, and Step 6 compares local workbooks.

The worker uses one active-run lock, Year/Month-only reporting-month input, fail-closed Step 1 gating, same-month prerequisites, audited override reasons, per-run logs, SQLite history, and one-occurrence-per-month scheduling. Step 1 appends every SQL execution to `run_logs/sql_execution_log.csv`, overwrites `run_logs/mfr_completion_signal.csv`, and writes a successful run-scoped project manifest. Steps 2 and 3 use only the manifests and artifacts below `run_logs/<reporting-month>/<run-id>/artifacts/`; no user Downloads path is used. An override with a required reason permits an audited rerun of a previously successful month; it never permits a second active run.

## Step configuration and prerequisites

Copy `mfr_excel_download/.env.example` to `mfr_excel_download/.env` to configure the CIS endpoint/timeout. Copy `mfr_snowflake_upload/.env.example` to `mfr_snowflake_upload/.env` and provide the approved Snowflake account, role, warehouse, database, schema, `SF_EMFR_STAGE_NAME`, `SF_SUB_EMFR_STAGE_NAME`, and credentials. The two stage values are mandatory, must be distinct, and route `eMFR_*.xlsx` and `Sub_eMFR_*.xlsx` respectively. These ignored files must not be committed.

Copy `mfr_snowflake_task/.env.example` to `mfr_snowflake_task/.env` for the separate Step 4 Snowflake login. Configure one or more comma-separated fully qualified tasks with `SF_TASK_NAMES`; each fully qualified task may use a different schema. Choose `SF_TASK_EXECUTION_MODE=sequential` or `parallel`. Sequential mode runs tasks in listed order and stops before later tasks after a failure; parallel mode is for independent tasks and monitors all triggered graphs. Step 4 checks each graph immediately after it is triggered, then every eight minutes for up to 60 minutes, and writes one aggregate JSON execution receipt. It does not pass the UI reporting month to Snowflake.

`SF_TASK_POSTCHECKS_FILE=task_postchecks.json` optionally enables a validated transition gate for consecutive sequential Tasks. The supplied rule runs after Task A Graph `SUCCEEDED` and before Task B is triggered. It uses one parameterized query to check the configured Bronze tables against the distinct project set from the latest successful same-month Step 3 `upload_receipt.json`. For each table, project, and report date, the configured `ingestion_cluster_interval_seconds` value currently includes every row within 120 seconds of the latest `DW_CREATED_TIME`; it prevents one logical load with several near-simultaneous writes from being incorrectly reduced to only the final write. This is a temporary heuristic, not a deterministic run boundary: two independent loads within 120 seconds can be merged and one slow load spanning more than 120 seconds can be split. The confirmation dialog shows this expected Step 3 project count and explicitly labels it as not a live Snowflake count. The check fails Step 4 and does not trigger Task B when a table project count differs, an expected project is missing, or a project’s current-versus-prior calendar-month row-count difference exceeds one. Enabled transition rules are not compatible with parallel mode. The deferred deterministic alternative is documented in [docs/step4-run-audit-design.md](docs/step4-run-audit-design.md); it does not alter Snowflake objects in this revision.

Step 2 requires a Windows worker with desktop Microsoft Excel and `pywin32`. It downloads and copies both `eMFR_*.xlsx` and `Sub_eMFR_*.xlsx` files, but recalculates and saves active-sheet C7/C8 values only in `eMFR_*.xlsx`; blank calculated results are valid and are saved as blank values, while `Sub_eMFR_*.xlsx` files remain unchanged. Each run writes `artifacts/mfr_preparation_log.csv` with per-file HKT timestamps, success statuses, and failure messages. Install the root requirements before starting the worker.

Before standalone Step 3 upload, v1 and v2 show the selected month’s latest successful Step 2 preparation log and a summary. A clean log enables upload from the checksum-protected Step 2 manifest. If the log is unavailable or has failures, an operator may optionally choose a host-local folder containing approved non-empty Excel files; Step 1 success remains required and the selected source is audited.

## Choosing Step 2 project files

When running **Step 2 — Download Excel files** by itself, operators can choose **All projects confirmed ready** or **Choose specific projects**. Specific projects can be selected from the Step 1-confirmed checklist, pasted as comma- or line-separated project codes, or both. The dashboard validates every choice against the successful Step 1 result and records the final selection in pipeline history.

**Run all configured steps** always uses every project confirmed ready by Step 1. This keeps the normal monthly pipeline complete. A standalone Step 2 subset creates its own filtered project manifest, so a later Step 3 run uploads only workbooks produced for that selection.

## Folder convention

- `mfr_submission_check/`: step 1
- `mfr_excel_download/`: step 2
- `mfr_snowflake_upload/`: step 3
- `mfr_snowflake_task/`: step 4 standalone Snowflake Task Graph trigger
- `mfr_risk_sync/`: step 5 future Risk DB sync template; it is not configured yet
- `mfr_risk_check/`: step 6 MFR-versus-Risk comparison
- `pipeline_core/`: shared contracts, state, and repository
- `worker/`: process that executes queued runs
- `web_app/`: original Streamlit interface (v1)
- `web_app_v2/`: separately runnable Streamlit interface (v2)
- `data/`, `run_logs/`: local runtime state

## Step 6 comparison files

Step 6 owns its input/output folders inside this repository. Place exactly one exported Risk workbook in `mfr_risk_check/MFR_vs_Risk_calculation/Risk_calculation_files/`. When Stage 6 is run from the v2 dashboard, the app shows a confirmation reminder and the worker copies the manifest-listed `eMFR_*.xlsx` files from the latest successful Stage 3 source for the selected reporting month into `mfr_risk_check/MFR_vs_Risk_calculation/MFR_files/`. Existing active Excel files are moved to a dated archive under `MFR_files/archive/` first. Step 6 succeeds only when every comparison row has `is_pass=True`; mismatches still produce a CSV for review but mark the run as failed.
