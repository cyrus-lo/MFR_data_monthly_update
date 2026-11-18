# Deferred Design: Step 4 deterministic ingestion-run audit

## Status

**Deferred — design record only.** This document does not authorize or implement Snowflake DDL, changes to Bronze tables, Task A changes, stored-procedure changes, historical backfill, or production runtime changes.

The current Step 4 gate temporarily groups rows by a configurable trailing `DW_CREATED_TIME` interval. That workaround repairs split writes from one logical load, but it cannot reliably distinguish two independent Task A executions that occur close together. This document describes the future replacement: a dedicated Snowflake run-audit object keyed by Snowflake's Task Graph run-group ID.

## Objective

Validate each Step 4 transition using deterministic logical ingestion runs rather than timestamp proximity:

- **Current month:** use the exact Task A graph run just monitored by Step 4.
- **Prior month:** use the newest successful, complete historical run that covers the expected Step 3 project set.
- **Row count:** use counts recorded for the selected logical run, table, project, and reporting date.

This avoids changing the five existing Bronze table schemas. Bronze-table existence/project-completeness checks can remain as direct read-only checks, while batch-volume comparison reads the audit data.

## Proposed Snowflake objects

Names, ownership, retention, and database/schema placement require owner approval before implementation.

### Run header

Suggested table: `CHUNWO_DB.PUBLIC.MFR_INGESTION_RUN_AUDIT`

| Column | Purpose |
| --- | --- |
| `TASK_GRAPH_RUN_GROUP_ID` | UUID from `SYSTEM$TASK_RUNTIME_INFO('CURRENT_TASK_GRAPH_RUN_GROUP_ID')`; primary logical-run key. |
| `ROOT_TASK_NAME` | Root task identity for lineage and filtering. |
| `REPORTING_DATE` | Reporting month-end represented by the loaded data. |
| `RUN_STATUS` | `STARTED`, `SUCCEEDED`, or `FAILED`; success is written only after all expected audit rows are committed. |
| `STARTED_AT`, `COMPLETED_AT` | Operational timestamps, not batch identifiers. |
| `ATTEMPT_NUMBER` | Optional graph retry/audit correlation. |
| `ERROR_SUMMARY` | Sanitized operational failure text, if permitted by data policy. |
| `RECORDED_AT` | Audit write timestamp. |

### Per-table/project result

Suggested table: `CHUNWO_DB.PUBLIC.MFR_INGESTION_RUN_TABLE_PROJECT_AUDIT`

| Column | Purpose |
| --- | --- |
| `TASK_GRAPH_RUN_GROUP_ID` | Foreign/logical key to the run header. |
| `TABLE_NAME` | Fully qualified Bronze table name or approved stable identifier. |
| `PROJECT_CODE` | Project whose result was measured. |
| `REPORTING_DATE` | Month-end being measured. |
| `ROW_COUNT` | Rows belonging to this logical load for this table/project/month. |
| `CAPTURED_AT` | Timestamp at which the count was measured. |
| `SOURCE_STEP3_RUN_ID` | Optional local/ingestion lineage if it can be passed safely. |

A unique constraint or equivalent merge key should prevent duplicate audit rows for `(TASK_GRAPH_RUN_GROUP_ID, TABLE_NAME, PROJECT_CODE, REPORTING_DATE)`.

## Proposed Task A write lifecycle

1. At the beginning of Task A or its controlling stored procedure, obtain:

   ```sql
   SYSTEM$TASK_RUNTIME_INFO('CURRENT_TASK_GRAPH_RUN_GROUP_ID')
   ```

2. Write or merge a `STARTED` header record.
3. Execute existing Bronze loading work without changing Bronze columns.
4. After all intended writes for a table complete, calculate project row counts from the logical load's known source/staging scope. If direct Bronze counting is necessary, do it before declaring the audit run successful and use task-controlled load boundaries rather than a heuristic timestamp gap.
5. Insert/merge one per-table/project audit row for every expected project.
6. Validate that all required tables/projects are represented.
7. Mark the header `SUCCEEDED` only after every expected audit row is durable. On failure, mark it `FAILED` and do not use it as a baseline.

The final status update and audit rows should be transactionally consistent where Snowflake task/procedure architecture permits. The reader must treat incomplete or non-successful headers as unusable.

## Step 4 post-check selection policy

### Current-month selection

Step 4 already discovers Task A's `graph_run_group_id` after Task Graph monitoring succeeds. Pass that identifier to the post-check. The check reads audit rows where:

```sql
TASK_GRAPH_RUN_GROUP_ID = :completed_task_a_graph_run_group_id
AND RUN_STATUS = 'SUCCEEDED'
```

The result must include each expected table/project exactly once. Missing rows, zero current rows, or a failed/incomplete header blocks Task B.

### Prior-month baseline selection

Select the latest successful header for the preceding reporting month that is complete for the expected project set and all configured tables. Completion must be assessed from the per-table/project audit records, not merely a matching header timestamp.

Required properties:

- exact expected project set, without missing or unexpected projects;
- all configured tables represented;
- one audit row per table/project/month;
- `RUN_STATUS = 'SUCCEEDED'`;
- a deterministic ordering, for example `COMPLETED_AT DESC` with run-group ID as a tie-breaker.

If no complete prior baseline exists, fail closed and do not trigger Task B. Operators may choose an approved bootstrap policy later, but it must be explicit rather than silently comparing to all historical Bronze rows.

## Retry, rerun, and partial-failure policy

- A Snowflake graph retry belongs to the graph's run-group semantics exposed by Snowflake; record both the run-group ID and optional attempt data for diagnosis.
- An audit header is not eligible until it is `SUCCEEDED` and complete.
- A partially written or failed run must never become the latest baseline.
- Re-running a month creates a new successful run-group record; it may become the prior baseline only if complete.
- Audit writes must be idempotent for retry safety, using `MERGE` or a unique-key strategy.
- The process must define whether historical facts are immutable or whether an approved reprocess may replace audit counts. Preserve append-only lineage whenever retention/storage policy permits.

## Security, ownership, and operational decisions

Before implementation, determine and document:

1. database/schema and object owner;
2. privileges for Task A owner/procedure role to write audit records;
3. privileges for the Step 4 service role to select audit data;
4. whether sensitive project or operational data requires masking/retention limits;
5. retention, archival, and purge schedule;
6. warehouse/cost implications of per-project audit counts;
7. monitoring/alerting for missing or incomplete audit headers;
8. rollback procedure if audit writes fail or impair Task A.

## Migration and rollout

1. Create the audit objects in a non-production environment.
2. Add Task A audit writes behind an opt-in feature flag or equivalent controlled deployment.
3. Reconcile new audit counts against known full logical loads for several reporting months.
4. Backfill historical baseline rows only after agreeing the authoritative source and verifying results. Do not infer historical logical runs from `DW_CREATED_TIME` gaps without documenting the limitation.
5. Add Step 4 post-check support that can read both the current temporary mode and audit mode during migration, but require an explicit configuration choice.
6. Run read-only QA against selected months; verify no Task B trigger occurs on failed audit completeness.
7. Promote audit mode after reconciliation and rollback readiness are signed off.
8. Keep the interval mode available only for a defined deprecation period, then remove it after audit mode is stable.

## Required repository changes when approved

- Extend [mfr_snowflake_task/postchecks.py](../mfr_snowflake_task/postchecks.py) with an explicit, validated audit selection mode; do not add free-form SQL configuration.
- Update [mfr_snowflake_task/trigger_task.py](../mfr_snowflake_task/trigger_task.py) to pass the completed Task A graph-run ID into the post-check and to select current/prior audit records.
- Update [mfr_snowflake_task/task_postchecks.json](../mfr_snowflake_task/task_postchecks.json) with allowlisted audit-object metadata and a migration-mode flag.
- Add mocked SQL-builder and evaluation tests for complete, incomplete, retried, failed, and missing-prior-baseline runs.
- Add an explicit opt-in read-only integration QA scenario; it must continue to skip Task A and Task B.
- Update operator documentation and receipt schema so the selected run IDs, source baseline, and audit-mode outcome are visible.

## Non-goals for this deferred design

- Altering the existing Bronze table columns.
- Treating timestamp gaps as deterministic run IDs.
- Automatically choosing a prior baseline without completeness validation.
- Automatically weakening the existing row-delta policy.
- Deploying Snowflake objects or modifying Task A without owner approval.
