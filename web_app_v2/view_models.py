from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from typing import Any

from pipeline_core.adapters import StepAdapter


@dataclass(frozen=True)
class ReportingMonthDecision:
    selected_month: str | None
    error: str | None = None
    reason: str = ""


def _next_reporting_month(reporting_month: str) -> str:
    year, month = (int(part) for part in reporting_month.split("-"))
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def _step_status(row: dict[str, Any]) -> str | None:
    value = row.get("step_status", row.get("status"))
    return str(value) if value is not None else None


def resolve_reporting_month(
    snapshot: dict[str, Any], today: date | None = None
) -> ReportingMonthDecision:
    """Resolve the next reporting month from persisted workflow sequence state.

    ``today`` is retained for call-site compatibility but intentionally ignored:
    the default must be based on reporting-month activity, not the calendar date.
    """
    del today
    activity_rows = (*snapshot.get("runs", []), *snapshot.get("step_runs", []))
    months = sorted({
        str(row["reporting_month"])
        for row in activity_rows
        if row.get("reporting_month")
    })
    if not months:
        return ReportingMonthDecision(
            selected_month=None,
            reason="No reporting-month history is available; select a reporting month manually.",
        )

    latest_steps: dict[tuple[str, str], dict[str, Any]] = {}
    for row in snapshot.get("step_runs", []):
        month = row.get("reporting_month")
        step_key = row.get("step_key")
        if month and step_key:
            latest_steps.setdefault((str(month), str(step_key)), row)

    required_steps = (
        "submission_check",
        "excel_download",
        "snowflake_upload",
        "snowflake_task",
    )
    eligible_months = [
        month
        for month in months
        if all(
            _step_status(latest_steps.get((month, step_key), {})) == "SUCCEEDED"
            for step_key in required_steps
        )
    ]
    if not eligible_months:
        return ReportingMonthDecision(
            selected_month=None,
            reason="No reporting month has completed Steps 1–4; select a reporting month manually.",
        )

    activity_months = set(months)
    unfinished_months = [
        month
        for month in eligible_months
        if _step_status(latest_steps.get((month, "risk_check"), {})) != "SUCCEEDED"
    ]
    if unfinished_months:
        for current_month in unfinished_months:
            later_months = sorted(candidate for candidate in activity_months if candidate > current_month)
            if later_months:
                later_month = later_months[0]
                return ReportingMonthDecision(
                    selected_month=None,
                    error=(
                        f"Reporting sequence error: {current_month} Step 6 (Compare MFR and Risk) "
                        f"is unfinished, but {later_month} already has pipeline activity. "
                        "Review the reporting-month sequence before continuing."
                    ),
                )
        current_month = unfinished_months[-1]
        return ReportingMonthDecision(
            selected_month=current_month,
            reason=f"{current_month} is ready for the unfinished Step 6 comparison.",
        )

    latest_completed = eligible_months[-1]
    if _step_status(latest_steps.get((latest_completed, "risk_check"), {})) == "SUCCEEDED":
        return ReportingMonthDecision(
            selected_month=_next_reporting_month(latest_completed),
            reason=f"{latest_completed} Step 6 is complete; the next reporting month is ready to select.",
        )
    raise AssertionError("eligible_months must contain a completed or unfinished Step 6 state")


def default_reporting_month(
    snapshot: dict[str, Any], today: date | None = None
) -> str | None:
    """Return the resolver's selected month for compatibility with older callers."""
    return resolve_reporting_month(snapshot, today).selected_month


@dataclass(frozen=True)
class PipelineRow:
    key: str
    name: str
    order: int
    configured: bool
    prerequisites: tuple[str, ...]
    status: str
    tone: str
    icon: str
    last_run: str
    active_run_id: str | None = None
    active_status: str | None = None


_STATUS_DETAILS: dict[str, tuple[str, str, str]] = {
    "QUEUED": ("Queued", "queued", "◌"),
    "RUNNING": ("Running", "running", "◌"),
    "SUCCEEDED": ("Success", "success", "✓"),
    "FAILED": ("Failed", "failed", "✕"),
    "BLOCKED": ("Blocked", "failed", "!"),
    "CANCEL_REQUESTED": ("Cancelling", "queued", "◌"),
    "CANCELLED": ("Cancelled", "idle", "—"),
    "INTERRUPTED": ("Interrupted", "failed", "!"),
    "NOT_CONFIGURED": ("Not configured", "paused", "Ⅱ"),
    "SKIPPED": ("Skipped", "idle", "—"),
}


def status_details(status: str | None, configured: bool = True) -> tuple[str, str, str]:
    """Map durable backend state to display text without changing its meaning."""
    if not configured:
        return _STATUS_DETAILS["NOT_CONFIGURED"]
    if not status:
        return "Idle", "idle", "Ⅱ"
    return _STATUS_DETAILS.get(status, (status.replace("_", " ").title(), "idle", "—"))


def format_timestamp(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("T", " ").replace("+08:00", " HKT")


def build_pipeline_rows(
    adapters: dict[str, StepAdapter], snapshot: dict[str, Any]
) -> list[PipelineRow]:
    """Build four stable pipeline rows from real adapter and persisted run data."""
    latest_run_by_target: dict[str, dict[str, Any]] = {}
    for run in snapshot["runs"]:
        latest_run_by_target.setdefault(run["target"], run)

    latest_step_by_key: dict[str, dict[str, Any]] = {}
    for step_run in snapshot["step_runs"]:
        latest_step_by_key.setdefault(step_run["step_key"], step_run)

    active_for_key: dict[str, dict[str, Any]] = {}
    for run in snapshot["active_runs"]:
        selected_month = snapshot.get("reporting_month")
        if selected_month and run.get("reporting_month") != selected_month:
            continue
        if run["target"] == "ALL":
            for key in adapters:
                active_for_key.setdefault(key, run)
        elif run["target"] in adapters:
            active_for_key.setdefault(run["target"], run)

    rows: list[PipelineRow] = []
    for key, adapter in sorted(adapters.items(), key=lambda item: item[1].spec.order):
        spec = adapter.spec
        configured = spec.configured and spec.script is not None and spec.script.exists()
        active_run = active_for_key.get(key)
        latest_step = latest_step_by_key.get(key)
        latest_target_run = latest_run_by_target.get(key)
        if active_run:
            status = active_run["status"]
            if latest_step and latest_step["run_id"] == active_run["id"]:
                status = latest_step["step_status"]
            last_run = format_timestamp(active_run.get("started_at") or active_run.get("created_at"))
        elif latest_step:
            status = latest_step["step_status"]
            last_run = format_timestamp(
                latest_step.get("step_finished_at") or latest_step.get("step_started_at") or latest_step.get("created_at")
            )
        elif latest_target_run:
            status = latest_target_run["status"]
            last_run = format_timestamp(
                latest_target_run.get("finished_at") or latest_target_run.get("started_at") or latest_target_run.get("created_at")
            )
        else:
            status = None
            last_run = "—"
        label, tone, icon = status_details(status, configured)
        if key == "risk_check" and latest_step and latest_step.get("message"):
            message = str(latest_step["message"])
            if message.startswith(("Succeeded (", "Failed (")):
                label = message
        rows.append(
            PipelineRow(
                key=key,
                name=spec.name,
                order=spec.order,
                configured=configured,
                prerequisites=spec.prerequisites,
                status=label,
                tone=tone,
                icon=icon,
                last_run=last_run,
                active_run_id=active_run["id"] if active_run else None,
                active_status=status if active_run else None,
            )
        )
    return rows


def active_run_rows(snapshot: dict[str, Any], adapters: dict[str, StepAdapter]) -> list[dict[str, str]]:
    """Return only persisted active work, expanded to named MFR stages where available."""
    names = {key: adapter.spec.name for key, adapter in adapters.items()}
    step_by_run = {row["run_id"]: row for row in snapshot["step_runs"]}
    rows: list[dict[str, str]] = []
    for run in snapshot["active_runs"]:
        step = step_by_run.get(run["id"])
        key = step["step_key"] if step else run["target"]
        status = step["step_status"] if step else run["status"]
        label, tone, icon = status_details(status)
        rows.append(
            {
                "id": run["id"][:8],
                "name": names.get(key, "All MFR stages" if key == "ALL" else key.replace("_", " ").title()),
                "status": label,
                "tone": tone,
                "icon": icon,
            }
        )
    return rows
