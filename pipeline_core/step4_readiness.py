"""Non-secret local readiness checks for the standalone Step 4 task trigger."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from mfr_snowflake_task.postchecks import PostcheckConfigError, load_postcheck_rules

REQUIRED_SETTINGS = (
    "SF_USER",
    "SF_PASSWORD",
    "SF_ACCOUNT",
    "SF_ROLE",
    "SF_WAREHOUSE",
    "SF_DATABASE",
    "SF_SCHEMA",
)
DEFAULT_TASK = "CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class Step4Readiness:
    """A safe-to-display result of local Step 4 validation."""

    ready: bool
    state: str
    message: str


def parse_task_names(value: str) -> list[str]:
    """Validate comma-separated unquoted DATABASE.SCHEMA.TASK identifiers."""
    task_names = [item.strip() for item in value.split(",") if item.strip()]
    if not task_names:
        raise ValueError("SF_TASK_NAMES must contain at least one task")
    for task_name in task_names:
        parts = task_name.split(".")
        if len(parts) != 3 or not all(IDENTIFIER.fullmatch(part) for part in parts):
            raise ValueError("Snowflake task names must be unquoted DATABASE.SCHEMA.TASK identifiers")
    if len({task_name.casefold() for task_name in task_names}) != len(task_names):
        raise ValueError("SF_TASK_NAMES must not contain duplicate tasks")
    return task_names


def step4_readiness(root: Path) -> Step4Readiness:
    """Check only local, non-secret prerequisites; never contact Snowflake."""
    task_dir = root / "mfr_snowflake_task"
    script = task_dir / "trigger_task.py"
    if not script.is_file():
        return Step4Readiness(False, "not_configured", "Step 4 trigger script is unavailable.")

    env_file = task_dir / ".env"
    if not env_file.is_file():
        return Step4Readiness(
            False,
            "configuration_incomplete",
            "Step 4 .env is missing. Copy mfr_snowflake_task/.env.example to .env and complete it.",
        )

    values = dotenv_values(env_file)
    missing = [setting for setting in REQUIRED_SETTINGS if not values.get(setting)]
    if missing:
        return Step4Readiness(
            False,
            "configuration_incomplete",
            "Missing Step 4 setting(s): " + ", ".join(missing),
        )

    configured_tasks = values.get("SF_TASK_NAMES") or values.get("SF_TASK_NAME") or DEFAULT_TASK
    try:
        parse_task_names(str(configured_tasks))
    except ValueError as exc:
        return Step4Readiness(False, "configuration_incomplete", str(exc))

    execution_mode = str(values.get("SF_TASK_EXECUTION_MODE") or "sequential").strip().lower()
    if execution_mode not in {"sequential", "parallel"}:
        return Step4Readiness(
            False,
            "configuration_incomplete",
            "SF_TASK_EXECUTION_MODE must be sequential or parallel.",
        )
    configured_rule_file = str(values.get("SF_TASK_POSTCHECKS_FILE") or "").strip()
    rule_file = (task_dir / configured_rule_file).resolve() if configured_rule_file else None
    try:
        load_postcheck_rules(rule_file, parse_task_names(str(configured_tasks)), execution_mode)
    except PostcheckConfigError as exc:
        return Step4Readiness(False, "configuration_incomplete", str(exc))
    return Step4Readiness(
        True,
        "ready",
        "Step 4 is locally ready. Snowflake connection, permissions, and task lookup are checked when it runs.",
    )
