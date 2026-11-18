"""Step 4: trigger and monitor the MFR Snowflake task graph."""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pipeline_core.step3_receipt import Step3ReceiptError, Step3UploadReceipt, load_step3_upload_receipt

from mfr_snowflake_task.postchecks import (
    PostcheckConfigError,
    PostcheckRule,
    PostcheckTable,
    load_postcheck_rules,
    quoted_identifier,
    rule_for_terminal,
    rule_for_transition,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TASK = "CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL"
POLL_SECONDS = 480
MAX_WAIT_SECONDS = 3600
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class Step4Error(RuntimeError):
    """Raised when Step 4 cannot safely trigger or monitor Snowflake."""


class PostcheckFailure(Step4Error):
    """Raised with an auditable post-check result that blocks the next Task."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class Step4Config:
    """Step 4 settings, with Snowflake password authentication matching Step 3."""

    connection_parameters: dict[str, str]
    task_names: list[str]
    execution_mode: str
    postcheck_rules: tuple[PostcheckRule, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reporting-month", required=True, help="YYYY-MM")
    parser.add_argument("--run-id", required=True, help="Pipeline run identifier")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument(
        "--step3-upload-receipt",
        type=Path,
        help="Successful same-month Step 3 upload receipt used by enabled post-checks",
    )
    return parser.parse_args()


def validate_reporting_month(value: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise Step4Error("--reporting-month must use YYYY-MM")


def split_task_name(value: str) -> tuple[str, str, str]:
    parts = value.strip().split(".")
    if len(parts) != 3 or not all(IDENTIFIER.fullmatch(part) for part in parts):
        raise Step4Error("Snowflake task names must be unquoted DATABASE.SCHEMA.TASK identifiers")
    return parts[0], parts[1], parts[2]


def parse_task_names(value: str) -> list[str]:
    task_names = [item.strip() for item in value.split(",") if item.strip()]
    if not task_names:
        raise Step4Error("SF_TASK_NAMES must contain at least one task")
    for task_name in task_names:
        split_task_name(task_name)
    folded = [task_name.casefold() for task_name in task_names]
    if len(folded) != len(set(folded)):
        raise Step4Error("SF_TASK_NAMES must not contain duplicate tasks")
    return task_names


def load_config() -> Step4Config:
    values = dotenv_values(SCRIPT_DIR / ".env")
    required = ("SF_USER", "SF_PASSWORD", "SF_ACCOUNT", "SF_ROLE", "SF_WAREHOUSE", "SF_DATABASE", "SF_SCHEMA")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise Step4Error("Missing Snowflake setting(s): " + ", ".join(missing))
    configured_tasks = values.get("SF_TASK_NAMES") or values.get("SF_TASK_NAME") or DEFAULT_TASK
    task_names = parse_task_names(str(configured_tasks))
    execution_mode = str(values.get("SF_TASK_EXECUTION_MODE") or "sequential").strip().lower()
    if execution_mode not in {"sequential", "parallel"}:
        raise Step4Error("SF_TASK_EXECUTION_MODE must be sequential or parallel")
    configured_rule_file = str(values.get("SF_TASK_POSTCHECKS_FILE") or "").strip()
    rule_file = (SCRIPT_DIR / configured_rule_file).resolve() if configured_rule_file else None
    try:
        postcheck_rules = load_postcheck_rules(rule_file, task_names, execution_mode)
    except PostcheckConfigError as exc:
        raise Step4Error(str(exc)) from exc
    return Step4Config(
        connection_parameters={
            "user": str(values["SF_USER"]),
            # Match Step 3: SF_PASSWORD is a normal Snowflake password passed
            # through the connector's password parameter. OAuth/JWT tokens are
            # not interpreted here as another authentication mode.
            "password": str(values["SF_PASSWORD"]),
            "account": str(values["SF_ACCOUNT"]),
            "role": str(values["SF_ROLE"]),
            "warehouse": str(values["SF_WAREHOUSE"]),
            "database": str(values["SF_DATABASE"]),
            "schema": str(values["SF_SCHEMA"]),
        },
        task_names=task_names,
        execution_mode=execution_mode,
        postcheck_rules=postcheck_rules,
    )


def sanitize_error(error: object) -> str:
    text = str(error)
    return re.sub(r"(?i)(password|pwd|token|secret)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", text)


def qualified_task_sql(task_name: str) -> str:
    database, schema, task = split_task_name(task_name)
    return ".".join(f'"{part}"' for part in (database, schema, task))


def month_end_dates(reporting_month: str) -> tuple[str, str]:
    """Return selected and preceding calendar month-end values in ISO format."""
    year, month = map(int, reporting_month.split("-"))
    current = date(year, month, calendar.monthrange(year, month)[1])
    previous_year, previous_month = (year - 1, 12) if month == 1 else (year, month - 1)
    previous = date(
        previous_year,
        previous_month,
        calendar.monthrange(previous_year, previous_month)[1],
    )
    return current.isoformat(), previous.isoformat()


def row_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    description = getattr(cursor, "description", None) or []
    names = [str(column[0]).lower() for column in description]
    if not names:
        return []
    return [dict(zip(names, row)) for row in rows]


def execute_rows(cursor: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    return row_dicts(cursor)


def current_graphs_sql(database: str, schema: str) -> str:
    return 'SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.CURRENT_TASK_GRAPHS())'


def complete_graphs_sql(database: str, schema: str) -> str:
    return 'SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.COMPLETE_TASK_GRAPHS(RESULT_LIMIT => 100))'


def task_history_sql(database: str, schema: str, root_task_id: str) -> tuple[str, tuple[str]]:
    sql = 'SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.TASK_HISTORY(ROOT_TASK_ID => %s, RESULT_LIMIT => 10000))'
    return sql, (root_task_id,)


def graph_matches(row: dict[str, Any], task_name: str, root_task_id: str) -> bool:
    database, schema, task = split_task_name(task_name)
    return (
        str(row.get("root_task_id", "")) == root_task_id
        and str(row.get("root_task_name", "")).casefold() == task.casefold()
        and str(row.get("database_name", "")).casefold() in {"", database.casefold()}
        and str(row.get("schema_name", "")).casefold() in {"", schema.casefold()}
    )


def graph_group(row: dict[str, Any]) -> str:
    return str(row.get("graph_run_group_id") or row.get("run_id") or "")


def find_active(rows: list[dict[str, Any]], task_name: str, root_task_id: str) -> list[dict[str, Any]]:
    return [row for row in rows if graph_matches(row, task_name, root_task_id) and str(row.get("state", "")).upper() in {"SCHEDULED", "EXECUTING"}]


def select_triggered(rows: list[dict[str, Any]], task_name: str, root_task_id: str, boundary: datetime) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        if not graph_matches(row, task_name, root_task_id):
            continue
        scheduled = row.get("scheduled_time")
        if isinstance(scheduled, datetime):
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if scheduled < boundary - timedelta(minutes=1):
                continue
        candidates.append(row)
    if len(candidates) != 1:
        return None
    return candidates[0]


def write_receipt(artifact_dir: Path, payload: dict[str, Any]) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    receipt = artifact_dir / "task_execution_receipt.json"
    temporary = receipt.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    return receipt


def _task_metadata(cursor: Any, task_name: str) -> tuple[str, str, str, str]:
    database, schema, task = split_task_name(task_name)
    rows = execute_rows(cursor, f'SHOW TASKS LIKE %s IN SCHEMA "{database}"."{schema}"', (task,))
    matches = [row for row in rows if str(row.get("name", row.get('"name"', task))).casefold() == task.casefold()]
    if not matches:
        raise Step4Error(f"Configured Snowflake task was not found: {task_name}")
    root_task_id = str(matches[0].get("id") or matches[0].get('"id"') or "")
    if not root_task_id:
        raise Step4Error(f"Snowflake did not return a root task ID for {task_name}")
    return database, schema, task, root_task_id


def _ensure_task_available(cursor: Any, task_name: str, root_task_id: str) -> None:
    database, schema, _ = split_task_name(task_name)
    active = execute_rows(cursor, current_graphs_sql(database, schema))
    if find_active(active, task_name, root_task_id):
        raise Step4Error(f"The Snowflake task graph is already queued or executing: {task_name}")


def _trigger_task(cursor: Any, task_name: str, root_task_id: str, now: Any, *, preflight: bool = True) -> dict[str, Any]:
    if preflight:
        _ensure_task_available(cursor, task_name, root_task_id)
    boundary = now(timezone.utc)
    cursor.execute(f"EXECUTE TASK {qualified_task_sql(task_name)}")
    return {
        "task_name": task_name,
        "root_task_id": root_task_id,
        "trigger_query_id": str(getattr(cursor, "sfqid", "")),
        "triggered_at_utc": boundary.isoformat(timespec="seconds"),
        "_boundary": boundary,
    }


def _identify_graph(cursor: Any, task: dict[str, Any], now: Any) -> dict[str, Any] | None:
    database, schema, _ = split_task_name(task["task_name"])
    active = execute_rows(cursor, current_graphs_sql(database, schema))
    graph = select_triggered(active, task["task_name"], task["root_task_id"], task["_boundary"])
    if graph:
        return graph
    complete = execute_rows(cursor, complete_graphs_sql(database, schema))
    return select_triggered(complete, task["task_name"], task["root_task_id"], task["_boundary"])


def _monitor_task(cursor: Any, task: dict[str, Any], sleep: Any, now: Any) -> dict[str, Any]:
    database, schema, _ = split_task_name(task["task_name"])
    root_task_id = task["root_task_id"]
    deadline = task["_boundary"] + timedelta(seconds=MAX_WAIT_SECONDS)
    graph = _identify_graph(cursor, task, now)
    while graph is None:
        if now(timezone.utc) >= deadline:
            raise Step4Error(f"Unable to identify the triggered Snowflake task graph before timeout: {task['task_name']}")
        sleep(POLL_SECONDS)
        graph = _identify_graph(cursor, task, now)
    task["graph_run_group_id"] = graph_group(graph)
    task["attempt_number"] = graph.get("attempt_number")
    while str(graph.get("state", "")).upper() not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        if now(timezone.utc) >= deadline:
            raise Step4Error(f"Snowflake task graph monitoring timed out: {task['task_name']}")
        sleep(POLL_SECONDS)
        database, schema, _ = split_task_name(task["task_name"])
        active = execute_rows(cursor, current_graphs_sql(database, schema))
        matching = [row for row in active if graph_group(row) == task["graph_run_group_id"]]
        if not matching:
            complete = execute_rows(cursor, complete_graphs_sql(database, schema))
            matching = [row for row in complete if graph_group(row) == task["graph_run_group_id"]]
        if matching:
            graph = matching[0]
    history_sql, history_params = task_history_sql(database, schema, root_task_id)
    task["task_history"] = execute_rows(cursor, history_sql, history_params)
    task["final_state"] = str(graph.get("state", "")).upper()
    task["completed_at_utc"] = now(timezone.utc).isoformat(timespec="seconds")
    if task["final_state"] != "SUCCEEDED":
        raise Step4Error(f"Snowflake task graph ended in {task['final_state']}: {task['task_name']}")
    task["outcome"] = "SUCCEEDED"
    task.pop("_boundary", None)
    return task


def _sql_string_literal(value: str) -> str:
    """Return a SQL string literal for a value already validated as configuration."""
    return "'" + value.replace("'", "''") + "'"


def _table_branch_sql(table: PostcheckTable) -> tuple[str, tuple[str, str]]:
    quoted_table = quoted_identifier(table.name, "post-check table name")
    quoted_project_column = f'"{table.project_column}"'
    quoted_report_date_column = f'"{table.report_date_column}"'
    quoted_dw_created_time_column = f'"{table.dw_created_time_column}"'
    if table.report_date_type == "varchar_yyyy_mm_dd":
        date_expression = f"TO_VARCHAR({quoted_report_date_column})"
        date_filter = f"{quoted_report_date_column} IN (%s, %s)"
    else:
        date_expression = f"TO_VARCHAR({quoted_report_date_column}, 'YYYY-MM-DD')"
        date_filter = f"{quoted_report_date_column} IN (TO_DATE(%s), TO_DATE(%s))"
    return (
        "\n".join(
            (
                "SELECT table_name, project_code, report_date",
                "FROM (",
                "    SELECT " + _sql_string_literal(table.name) + " AS table_name,",
                f"           TO_VARCHAR({quoted_project_column}) AS project_code,",
                f"           {date_expression} AS report_date,",
                f"           {quoted_dw_created_time_column} AS ingestion_time",
                f"    FROM {quoted_table}",
                f"    WHERE {date_filter}",
                ") AS source_rows",
                "QUALIFY ingestion_time >= DATEADD(second, "
                f"-{table.ingestion_cluster_interval_seconds}, MAX(ingestion_time) OVER (",
                "    PARTITION BY project_code, report_date",
                "))",
            )
        ),
        (),
    )


def build_project_postcheck_sql(
    rule: PostcheckRule,
    expected_project_codes: tuple[str, ...],
    current_date: str,
    prior_date: str,
) -> tuple[str, tuple[Any, ...]]:
    """Build one parameterized query for all configured tables and expected projects."""
    branches: list[str] = []
    parameters: list[Any] = [json.dumps(expected_project_codes)]
    configured_table_values: list[str] = []
    for table in rule.tables:
        branch, _ = _table_branch_sql(table)
        branches.append(branch)
        parameters.extend((current_date, prior_date))
        configured_table_values.append("(" + _sql_string_literal(table.name) + ")")

    table_rows_sql = "\nUNION ALL\n".join(branches)
    sql = f"""
WITH expected_projects AS (
    SELECT VALUE::VARCHAR AS project_code
    FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s)))
),
configured_tables(table_name) AS (
    SELECT COLUMN1 FROM VALUES {", ".join(configured_table_values)}
),
table_rows AS (
    {table_rows_sql}
),
table_counts AS (
    SELECT
        configured_tables.table_name,
        COUNT(DISTINCT IFF(table_rows.report_date = %s, table_rows.project_code, NULL))
            AS actual_distinct_project_count
    FROM configured_tables
    LEFT JOIN table_rows USING (table_name)
    GROUP BY configured_tables.table_name
),
project_counts AS (
    SELECT
        configured_tables.table_name,
        expected_projects.project_code,
        COUNT_IF(table_rows.report_date = %s) AS current_rows,
        COUNT_IF(table_rows.report_date = %s) AS prior_rows
    FROM configured_tables
    CROSS JOIN expected_projects
    LEFT JOIN table_rows
        ON table_rows.table_name = configured_tables.table_name
       AND table_rows.project_code = expected_projects.project_code
    GROUP BY configured_tables.table_name, expected_projects.project_code
)
SELECT
    project_counts.table_name,
    project_counts.project_code,
    project_counts.current_rows,
    project_counts.prior_rows,
    table_counts.actual_distinct_project_count
FROM project_counts
INNER JOIN table_counts USING (table_name)
ORDER BY project_counts.table_name, project_counts.project_code
""".strip()
    parameters.extend((current_date, current_date, prior_date))
    return sql, tuple(parameters)


def _int_value(value: object, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise Step4Error(f"Snowflake post-check returned an invalid {label}") from exc


def evaluate_project_postcheck(
    rule: PostcheckRule,
    expected_project_codes: tuple[str, ...],
    rows: list[dict[str, Any]],
    current_date: str,
    prior_date: str,
) -> dict[str, Any]:
    """Validate the one-query result and return receipt-ready data or raise a gate failure."""    
    expected_projects = set(expected_project_codes)
    expected_tables = {table.name.casefold(): table for table in rule.tables}
    grouped: dict[str, list[dict[str, Any]]] = {table.name.casefold(): [] for table in rule.tables}
    seen_pairs: set[tuple[str, str]] = set()
    for row in rows:
        table_name = str(row.get("table_name", ""))
        project_code = str(row.get("project_code", "")).strip()
        table_key = table_name.casefold()
        pair = (table_key, project_code)
        if table_key not in expected_tables or project_code not in expected_projects or pair in seen_pairs:
            raise Step4Error("Snowflake post-check returned an unexpected or duplicate table/project result")    
        seen_pairs.add(pair)
        grouped[table_key].append(row)

    expected_result_count = len(rule.tables) * len(expected_project_codes)
    if len(rows) != expected_result_count:
        raise Step4Error("Snowflake post-check did not return every expected table/project result")

    table_results: list[dict[str, Any]] = []
    violations: list[str] = []
    for table in rule.tables:
        table_rows = grouped[table.name.casefold()]    
        if {str(row.get("project_code", "")).strip() for row in table_rows} != expected_projects:
            raise Step4Error("Snowflake post-check returned an incomplete project result set")
        actual_counts = {_int_value(row.get("actual_distinct_project_count"), "distinct-project count") for row in table_rows}
        if len(actual_counts) != 1:
            raise Step4Error("Snowflake post-check returned inconsistent table distinct-project counts")
        actual_count = actual_counts.pop()
        project_results: list[dict[str, Any]] = []
        missing_projects: list[str] = []
        delta_breaches: list[dict[str, Any]] = []
        for row in sorted(table_rows, key=lambda item: str(item["project_code"])):
            project_code = str(row["project_code"])
            current_rows = _int_value(row.get("current_rows"), "current row count")
            prior_rows = _int_value(row.get("prior_rows"), "prior row count")
            absolute_delta = abs(current_rows - prior_rows)
            project_result = {
                "project_code": project_code,
                "current_rows": current_rows,
                "prior_rows": prior_rows,
                "absolute_row_count_delta": absolute_delta,
                "outcome": "SUCCEEDED",
            }
            if current_rows == 0:
                project_result["outcome"] = "FAILED"
                missing_projects.append(project_code)
            if rule.enforce_row_count_delta and absolute_delta > rule.max_abs_row_count_delta:
                project_result["outcome"] = "FAILED"
                delta_breaches.append(project_result.copy())
            project_results.append(project_result)
        table_result = {
            "table_name": table.name,
            "ingestion_selection": "latest_dw_created_time_trailing_window",
            "ingestion_cluster_interval_seconds": table.ingestion_cluster_interval_seconds,
            "expected_distinct_project_count": len(expected_project_codes),
            "actual_distinct_project_count": actual_count,
            "missing_current_projects": missing_projects,
            "project_results": project_results,
            "outcome": "SUCCEEDED",
        }
        if actual_count != len(expected_project_codes):
            table_result["outcome"] = "FAILED"
            violations.append(
                f"{table.name} has {actual_count} of {len(expected_project_codes)} expected projects"
            )
        if missing_projects:
            table_result["outcome"] = "FAILED"
            violations.append(f"{table.name} is missing current-month project {missing_projects[0]}")
        if delta_breaches:
            table_result["outcome"] = "FAILED"
            first = delta_breaches[0]
            violations.append(
                f"{table.name} project {first['project_code']} has current/prior row counts "
                f"{first['current_rows']}/{first['prior_rows']} (allowed difference: {rule.max_abs_row_count_delta})"
            )
        table_results.append(table_result)

    result = {
        "rule_id": rule.id,
        "after_task": rule.after_task,
        "before_task": rule.before_task,
        "reporting_date": current_date,
        "prior_reporting_date": prior_date,
        "expected_distinct_project_count": len(expected_project_codes),
        "ingestion_selection": "latest_dw_created_time_trailing_window",
        "tables": table_results,
        "outcome": "SUCCEEDED" if not violations else "FAILED",
    }
    if violations:
        result["error"] = "Post-check failed; Task B was not triggered: " + violations[0]
        raise PostcheckFailure(str(result["error"]), result)
    return result


def build_diagnostic_sql(rule: PostcheckRule) -> str:
    """Build one read-only whole-table summary query for terminal diagnostics."""
    branches = []
    for diagnostic in rule.diagnostics:
        table = quoted_identifier(diagnostic.name, "post-check diagnostic name")
        timestamp = f'"{diagnostic.dw_created_time_column}"'
        branches.append(
            "SELECT "
            + _sql_string_literal(diagnostic.name)
            + " AS table_name, MIN("
            + timestamp
            + ") AS min_dw_created_time, MAX("
            + timestamp
            + ") AS max_dw_created_time, COUNT(*) AS row_count FROM "
            + table
        )
    return "\nUNION ALL\n".join(branches)


def evaluate_diagnostics(rule: PostcheckRule, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = {diagnostic.name.casefold() for diagnostic in rule.diagnostics}
    actual = {str(row.get("table_name", "")).casefold() for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise Step4Error("Snowflake diagnostic query did not return every configured table")
    results = []
    for row in rows:
        results.append(
            {
                "table_name": str(row["table_name"]),
                "min_dw_created_time": row.get("min_dw_created_time"),
                "max_dw_created_time": row.get("max_dw_created_time"),
                "row_count": _int_value(row.get("row_count"), "diagnostic row count"),
                "outcome": "INFORMATIONAL",
            }
        )
    return results


def _run_project_postcheck(
    cursor: Any,
    rule: PostcheckRule,
    receipt: Step3UploadReceipt,
    reporting_month: str,
    now: Any,
) -> dict[str, Any]:
    current_date, prior_date = month_end_dates(reporting_month)
    base_result = {
        "rule_id": rule.id,
        "after_task": rule.after_task,
        "before_task": rule.before_task,
        "evaluated_at_utc": now(timezone.utc).isoformat(timespec="seconds"),
        "step3_source_run_id": receipt.run_id,
        "step3_upload_receipt": str(receipt.path),
        "expected_distinct_project_count": receipt.distinct_project_count,
        "reporting_date": current_date,
        "prior_reporting_date": prior_date,
    }
    try:
        sql, params = build_project_postcheck_sql(rule, receipt.project_codes, current_date, prior_date)
        evaluated = evaluate_project_postcheck(
            rule,
            receipt.project_codes,
            execute_rows(cursor, sql, params),
            current_date,
            prior_date,
        )
    except PostcheckFailure as exc:
        exc.result.update({key: value for key, value in base_result.items() if key not in exc.result})
        raise
    except Exception as exc:
        failed = {**base_result, "outcome": "FAILED", "error": sanitize_error(exc)}
        raise PostcheckFailure(
            "Post-check failed; Task B was not triggered: " + sanitize_error(exc), failed
        ) from exc
    return {**base_result, **evaluated}


def _run_terminal_postcheck(
    cursor: Any,
    rule: PostcheckRule,
    receipt: Step3UploadReceipt,
    reporting_month: str,
    now: Any,
) -> dict[str, Any]:
    current_date, prior_date = month_end_dates(reporting_month)
    base_result = {
        "rule_id": rule.id,
        "phase": "terminal_silver",
        "after_task": rule.after_task,
        "evaluated_at_utc": now(timezone.utc).isoformat(timespec="seconds"),
        "step3_source_run_id": receipt.run_id,
        "step3_upload_receipt": str(receipt.path),
        "expected_distinct_project_count": receipt.distinct_project_count,
        "reporting_date": current_date,
        "prior_reporting_date": prior_date,
    }
    try:
        sql, params = build_project_postcheck_sql(rule, receipt.project_codes, current_date, prior_date)
        evaluated = evaluate_project_postcheck(
            rule, receipt.project_codes, execute_rows(cursor, sql, params), current_date, prior_date
        )
        diagnostics = []
        if rule.diagnostics:
            try:
                diagnostics = evaluate_diagnostics(rule, execute_rows(cursor, build_diagnostic_sql(rule)))
            except Exception as exc:
                diagnostics = [{"outcome": "INFORMATIONAL_ERROR", "error": sanitize_error(exc)}]
        evaluated["phase"] = "terminal_silver"
        evaluated["diagnostics"] = diagnostics
        return {**base_result, **evaluated}
    except PostcheckFailure as exc:
        exc.result.update({key: value for key, value in base_result.items() if key not in exc.result})
        raise
    except Exception as exc:
        failed = {**base_result, "outcome": "FAILED", "error": sanitize_error(exc)}
        raise PostcheckFailure("Terminal Silver post-check failed: " + sanitize_error(exc), failed) from exc


def run(args: argparse.Namespace, sleep=time.sleep, now=datetime.now) -> Path:
    validate_reporting_month(args.reporting_month)
    config = load_config()
    enabled_postchecks = tuple(rule for rule in config.postcheck_rules if rule.enabled)
    step3_receipt: Step3UploadReceipt | None = None
    if enabled_postchecks:
        if args.step3_upload_receipt is None:
            raise Step4Error("Enabled Step 4 post-checks require --step3-upload-receipt")
        try:
            step3_receipt = load_step3_upload_receipt(args.step3_upload_receipt, args.reporting_month)
        except Step3ReceiptError as exc:
            raise Step4Error(str(exc)) from exc
    started = now(timezone.utc)
    payload: dict[str, Any] = {
        "schema_version": 3,
        "run_id": args.run_id,
        "reporting_month": args.reporting_month,
        "task_names": config.task_names,
        "execution_mode": config.execution_mode,
        "triggered_at_utc": started.isoformat(timespec="seconds"),
        "tasks": [],
        "post_checks": [],
    }
    try:
        import snowflake.connector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise Step4Error("snowflake-connector-python is required for Step 4") from exc

    connection = None
    cursor = None
    try:
        connection = snowflake.connector.connect(
            **config.connection_parameters,
        )
        cursor = connection.cursor()
        task_specs = []
        for task_name in config.task_names:
            _, _, _, root_task_id = _task_metadata(cursor, task_name)
            task_specs.append((task_name, root_task_id))
        if config.execution_mode == "parallel":
            # Preflight all tasks before issuing any EXECUTE TASK statements.
            for task_name, root_task_id in task_specs:
                _ensure_task_available(cursor, task_name, root_task_id)
            triggered = [
                _trigger_task(cursor, task_name, root_task_id, now, preflight=False)
                for task_name, root_task_id in task_specs
            ]
            payload["tasks"] = [_monitor_task(cursor, task, sleep, now) for task in triggered]
        else:
            for index, (task_name, root_task_id) in enumerate(task_specs):
                task = _trigger_task(cursor, task_name, root_task_id, now)
                payload["tasks"].append(_monitor_task(cursor, task, sleep, now))
                if index + 1 >= len(task_specs):
                    continue
                next_task_name = task_specs[index + 1][0]
                rule = rule_for_transition(config.postcheck_rules, task_name, next_task_name)
                if rule is None:
                    continue
                assert step3_receipt is not None
                try:
                    payload["post_checks"].append(
                        _run_project_postcheck(cursor, rule, step3_receipt, args.reporting_month, now)
                    )
                except PostcheckFailure as exc:
                    payload["post_checks"].append(exc.result)
                    raise
            terminal_rule = rule_for_terminal(config.postcheck_rules, config.task_names[-1])
            if terminal_rule is not None:
                assert step3_receipt is not None
                try:
                    payload["post_checks"].append(
                        _run_terminal_postcheck(cursor, terminal_rule, step3_receipt, args.reporting_month, now)
                    )
                except PostcheckFailure as exc:
                    payload["post_checks"].append(exc.result)
                    raise
        payload["outcome"] = "SUCCEEDED"
        return write_receipt(args.artifact_dir, payload)
    except Step4Error:
        payload["outcome"] = "FAILED"
        payload["error"] = sanitize_error(sys.exc_info()[1])
        write_receipt(args.artifact_dir, payload)
        raise
    except Exception as exc:
        payload["outcome"] = "FAILED"
        payload["error"] = sanitize_error(exc)
        write_receipt(args.artifact_dir, payload)
        raise Step4Error(f"Snowflake task execution failed: {sanitize_error(exc)}") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def main() -> int:
    try:
        receipt = run(parse_args())
    except Step4Error as exc:
        print(f"STEP 4 ERROR: {sanitize_error(exc)}", file=sys.stderr)
        return 1
    print(f"SUCCESS: Snowflake task graph completed; wrote {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
