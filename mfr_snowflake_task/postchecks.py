"""Validated, non-secret configuration for Step 4 task-transition post-checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
DATE_TYPES = {"varchar_yyyy_mm_dd", "date"}
FISCAL_MONTH_TYPES = {"date", "varchar_yyyy_mm_dd"}
COMPARISON_MODES = {"report_date", "fiscal_month"}
CHECK_TYPE = "project_count_and_row_delta"
TERMINAL_CHECK_TYPE = "silver_project_completeness"
DIAGNOSTIC_CHECK_TYPE = "whole_table_diagnostic"


class PostcheckConfigError(ValueError):
    """Raised when a transition post-check configuration is unsafe or incomplete."""


@dataclass(frozen=True)
class PostcheckTable:
    """One allowlisted table and its post-check comparison policy."""

    name: str
    project_column: str
    report_date_column: str
    report_date_type: str
    dw_created_time_column: str = "DW_CREATED_TIME"
    ingestion_cluster_interval_seconds: int = 120
    comparison_mode: str = "report_date"
    fiscal_month_column: str | None = None
    fiscal_month_type: str | None = None


@dataclass(frozen=True)
class PostcheckDiagnostic:
    """A non-blocking whole-table summary included with a terminal check."""

    name: str
    dw_created_time_column: str = "DW_CREATED_TIME"


@dataclass(frozen=True)
class PostcheckRule:
    """A directed post-check that must pass between two sequential root Tasks."""

    id: str
    enabled: bool
    after_task: str
    before_task: str
    tables: tuple[PostcheckTable, ...]
    max_abs_row_count_delta: int
    terminal: bool = False
    enforce_row_count_delta: bool = True
    diagnostics: tuple[PostcheckDiagnostic, ...] = ()


def split_qualified_identifier(value: str, label: str) -> tuple[str, str, str]:
    parts = value.strip().split(".")
    if len(parts) != 3 or not all(IDENTIFIER.fullmatch(part) for part in parts):
        raise PostcheckConfigError(f"{label} must be an unquoted DATABASE.SCHEMA.NAME identifier")
    return parts[0], parts[1], parts[2]


def validate_identifier(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER.fullmatch(normalized):
        raise PostcheckConfigError(f"{label} must be an unquoted Snowflake identifier")
    return normalized


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PostcheckConfigError(f"{label} must be an object")
    return value


def _rule_task(value: object, label: str, task_names: list[str]) -> str:
    raw = str(value or "").strip()
    split_qualified_identifier(raw, label)
    configured = {task_name.casefold(): task_name for task_name in task_names}
    if raw.casefold() not in configured:
        raise PostcheckConfigError(f"{label} is not configured in SF_TASK_NAMES: {raw}")
    return configured[raw.casefold()]


def _parse_table(value: object, index: int) -> PostcheckTable:
    raw = _mapping(value, f"post-check table {index}")
    name = str(raw.get("name", "")).strip()
    split_qualified_identifier(name, f"post-check table {index} name")
    date_type = str(raw.get("report_date_type", "")).strip().lower()
    if date_type not in DATE_TYPES:
        raise PostcheckConfigError(
            f"post-check table {index} report_date_type must be one of: {', '.join(sorted(DATE_TYPES))}"
        )
    interval = raw.get("ingestion_cluster_interval_seconds", 120)
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        raise PostcheckConfigError(
            f"post-check table {index} ingestion_cluster_interval_seconds must be a positive integer"
        )
    comparison_mode = str(raw.get("comparison_mode", "report_date")).strip().lower()
    if comparison_mode not in COMPARISON_MODES:
        raise PostcheckConfigError(
            f"post-check table {index} comparison_mode must be one of: "
            f"{', '.join(sorted(COMPARISON_MODES))}"
        )
    fiscal_month_column = None
    fiscal_month_type = None
    if comparison_mode == "fiscal_month":
        fiscal_month_column = validate_identifier(
            raw.get("fiscal_month_column"), f"post-check table {index} fiscal_month_column"
        )
        fiscal_month_type = str(raw.get("fiscal_month_type", "")).strip().lower()
        if fiscal_month_type not in FISCAL_MONTH_TYPES:
            raise PostcheckConfigError(
                f"post-check table {index} fiscal_month_type must be one of: "
                f"{', '.join(sorted(FISCAL_MONTH_TYPES))}"
            )
    elif "fiscal_month_column" in raw or "fiscal_month_type" in raw:
        raise PostcheckConfigError(
            f"post-check table {index} fiscal month settings require comparison_mode=fiscal_month"
        )
    return PostcheckTable(
        name=name,
        project_column=validate_identifier(raw.get("project_column"), f"post-check table {index} project_column"),
        report_date_column=validate_identifier(raw.get("report_date_column"), f"post-check table {index} report_date_column"),
        report_date_type=date_type,
        dw_created_time_column=validate_identifier(
            raw.get("dw_created_time_column", "DW_CREATED_TIME"),
            f"post-check table {index} dw_created_time_column",
        ),
        ingestion_cluster_interval_seconds=interval,
        comparison_mode=comparison_mode,
        fiscal_month_column=fiscal_month_column,
        fiscal_month_type=fiscal_month_type,
    )


def _parse_diagnostic(value: object, index: int) -> PostcheckDiagnostic:
    raw = _mapping(value, f"post-check diagnostic {index}")
    name = str(raw.get("name", "")).strip()
    split_qualified_identifier(name, f"post-check diagnostic {index} name")
    return PostcheckDiagnostic(
        name=name,
        dw_created_time_column=validate_identifier(
            raw.get("dw_created_time_column", "DW_CREATED_TIME"),
            f"post-check diagnostic {index} dw_created_time_column",
        ),
    )


def load_postcheck_rules(
    rule_file: Path | None,
    task_names: list[str],
    execution_mode: str,
) -> tuple[PostcheckRule, ...]:
    """Load and validate optional transition checks without contacting Snowflake."""
    if rule_file is None:
        return ()
    if not rule_file.is_file():
        raise PostcheckConfigError(f"Step 4 post-check rule file is missing: {rule_file}")
    try:
        payload = json.loads(rule_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostcheckConfigError(f"Unable to read Step 4 post-check rule file: {exc}") from exc
    raw_payload = _mapping(payload, "Step 4 post-check rule file")
    if raw_payload.get("schema_version") != 1:
        raise PostcheckConfigError("Step 4 post-check rule file must use schema_version 1")
    raw_rules = raw_payload.get("rules")
    if not isinstance(raw_rules, list):
        raise PostcheckConfigError("Step 4 post-check rule file rules must be a list")

    rules: list[PostcheckRule] = []
    rule_ids: set[str] = set()
    transitions: set[tuple[str, str]] = set()
    terminal_tasks: set[str] = set()
    for index, raw_rule in enumerate(raw_rules, start=1):
        rule = _mapping(raw_rule, f"post-check rule {index}")
        rule_id = str(rule.get("id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", rule_id):
            raise PostcheckConfigError(f"post-check rule {index} id is invalid")
        if rule_id.casefold() in rule_ids:
            raise PostcheckConfigError(f"post-check rule id is duplicated: {rule_id}")
        rule_ids.add(rule_id.casefold())
        enabled = rule.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PostcheckConfigError(f"post-check rule {rule_id} enabled must be true or false")
        check_type = rule.get("check_type")
        if check_type not in {CHECK_TYPE, TERMINAL_CHECK_TYPE}:
            raise PostcheckConfigError(f"post-check rule {rule_id} has an unsupported check_type")
        expected_projects = _mapping(rule.get("expected_projects"), f"post-check rule {rule_id} expected_projects")
        if expected_projects.get("source") != "step3_upload_receipt":
            raise PostcheckConfigError(
                f"post-check rule {rule_id} expected_projects.source must be step3_upload_receipt"
            )
        after_task = _rule_task(rule.get("after_task"), f"post-check rule {rule_id} after_task", task_names)
        terminal = check_type == TERMINAL_CHECK_TYPE
        if terminal:
            if after_task.casefold() != task_names[-1].casefold():
                raise PostcheckConfigError(f"terminal post-check rule {rule_id} must target the final configured task")
            before_task = ""
            if after_task.casefold() in terminal_tasks:
                raise PostcheckConfigError(f"terminal post-check task is duplicated: {after_task}")
            terminal_tasks.add(after_task.casefold())
        else:
            before_task = _rule_task(rule.get("before_task"), f"post-check rule {rule_id} before_task", task_names)
            transition = (after_task.casefold(), before_task.casefold())
            if transition in transitions:
                raise PostcheckConfigError(f"post-check transition is duplicated: {after_task} -> {before_task}")
            transitions.add(transition)
            after_index = next(index for index, task_name in enumerate(task_names) if task_name.casefold() == after_task.casefold())
            if after_index + 1 >= len(task_names) or task_names[after_index + 1].casefold() != before_task.casefold():
                raise PostcheckConfigError(
                    f"post-check rule {rule_id} must join consecutive sequential tasks: {after_task} -> {before_task}"
                )
        raw_tables = rule.get("tables")
        if not isinstance(raw_tables, list) or not raw_tables:
            raise PostcheckConfigError(f"post-check rule {rule_id} must define at least one table")
        tables = tuple(_parse_table(table, table_index) for table_index, table in enumerate(raw_tables, start=1))
        table_names = [table.name.casefold() for table in tables]
        if len(table_names) != len(set(table_names)):
            raise PostcheckConfigError(f"post-check rule {rule_id} must not contain duplicate tables")
        threshold = rule.get("max_abs_row_count_delta")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
            raise PostcheckConfigError(f"post-check rule {rule_id} max_abs_row_count_delta must be a non-negative integer")
        enforce_delta = rule.get("enforce_row_count_delta", not terminal)
        if not isinstance(enforce_delta, bool):
            raise PostcheckConfigError(f"post-check rule {rule_id} enforce_row_count_delta must be true or false")
        raw_diagnostics = rule.get("diagnostics", [])
        if not isinstance(raw_diagnostics, list):
            raise PostcheckConfigError(f"post-check rule {rule_id} diagnostics must be a list")
        diagnostics = tuple(_parse_diagnostic(item, item_index) for item_index, item in enumerate(raw_diagnostics, start=1))
        rules.append(
            PostcheckRule(
                id=rule_id,
                enabled=enabled,
                after_task=after_task,
                before_task=before_task,
                tables=tables,
                max_abs_row_count_delta=threshold,
                terminal=terminal,
                enforce_row_count_delta=enforce_delta,
                diagnostics=diagnostics,
            )
        )

    if execution_mode == "parallel" and any(rule.enabled for rule in rules):
        raise PostcheckConfigError("Enabled Step 4 transition post-checks require SF_TASK_EXECUTION_MODE=sequential")
    return tuple(rules)


def rule_for_transition(
    rules: tuple[PostcheckRule, ...], after_task: str, before_task: str
) -> PostcheckRule | None:
    """Return the enabled rule, if any, for one adjacent configured transition."""
    for rule in rules:
        if rule.enabled and rule.after_task.casefold() == after_task.casefold() and rule.before_task.casefold() == before_task.casefold():
            return rule
    return None


def rule_for_terminal(rules: tuple[PostcheckRule, ...], task_name: str) -> PostcheckRule | None:
    """Return the enabled terminal rule for the final configured task."""
    for rule in rules:
        if rule.enabled and rule.terminal and rule.after_task.casefold() == task_name.casefold():
            return rule
    return None


def quoted_identifier(value: str, label: str) -> str:
    """Quote a previously validated three-part Snowflake identifier."""
    return ".".join(f'"{part}"' for part in split_qualified_identifier(value, label))
