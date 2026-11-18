from __future__ import annotations

import calendar
import csv
import json
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from pipeline_core.adapters import StepAdapter, build_adapters
from pipeline_core.models import RunStatus, StepStatus
from pipeline_core.project_selection import (
    ProjectSelectionError,
    selection_from_json,
    write_download_project_manifest,
)
from pipeline_core.repository import HKT, RunRepository, now_hkt
from pipeline_core.risk_check_source import (
    Stage6SourceError,
    stage6_source_plan,
    sync_stage6_inputs,
    validate_risk_workbook_folder,
)
from pipeline_core.step3_sources import Step3SourceError, write_selected_folder_manifest

ROOT = Path(__file__).resolve().parent.parent
RUN_LOGS_ROOT = ROOT / "run_logs"
SENSITIVE_SETTING = re.compile(r"(?i)(password|pwd|token|secret)\s*[=:]\s*[^\s,;]+")
MANUAL_STEP_KEYS = {"snowflake_task", "risk_sync", "risk_check"}


def step4_failure_message(output: str) -> str:
    """Extract the final safe Step 4 failure from script output for run history."""
    for line in reversed(output.splitlines()):
        if "STEP 4 ERROR:" in line:
            return SENSITIVE_SETTING.sub(r"\1=[REDACTED]", line.strip())
    return "Step returned a non-zero exit code"


def risk_check_message(output: str, succeeded: bool) -> str:
    """Return a concise Step 6 status message including its failure count."""
    match = re.search(r"Number of cases where is_pass is False:\s*(\d+)", output)
    if not match:
        return "Succeeded" if succeeded else "Step returned a non-zero exit code"
    return f"{'Succeeded' if succeeded else 'Failed'} ({match.group(1)} failures)"


def prepare_stage6_inputs(repo: RunRepository, run: dict, run_dir: Path, step3_run: dict) -> Path:
    """Synchronize the confirmed latest Step 3 source before Stage 6 starts."""
    try:
        confirmation = json.loads(run.get("confirmation_json") or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise Stage6SourceError("Stage 6 confirmation is invalid") from exc
    if not confirmation.get("mfr_source_confirmed") or not confirmation.get("risk_workbook_confirmed"):
        raise Stage6SourceError("Stage 6 source and Risk workbook confirmation is required")

    latest_step3 = repo.latest_successful_step_run(run["reporting_month"], "snowflake_upload")
    selected_step3_id = str(confirmation.get("step3_run_id", "")).strip()
    if not selected_step3_id or selected_step3_id != step3_run.get("id"):
        raise Stage6SourceError("Stage 6 confirmation does not identify the required Step 3 run")
    if not latest_step3 or latest_step3.get("id") != selected_step3_id:
        raise Stage6SourceError("The confirmed Step 3 run is no longer the latest successful run")

    risk_dir = ROOT / "mfr_risk_check" / "MFR_vs_Risk_calculation" / "Risk_calculation_files"
    validate_risk_workbook_folder(risk_dir)
    destination = ROOT / "mfr_risk_check" / "MFR_vs_Risk_calculation" / "MFR_files"
    plan = stage6_source_plan(ROOT, run["reporting_month"], selected_step3_id, destination)
    return sync_stage6_inputs(plan, destination, run_dir / "artifacts")


def month_end(month: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("Reporting month must use YYYY-MM")
    year, number = map(int, month.split("-"))
    if not 1 <= number <= 12:
        raise ValueError("Reporting month must use YYYY-MM")
    return f"{year:04d}-{number:02d}-{calendar.monthrange(year, number)[1]:02d}"


def signal_is_complete(path: Path) -> bool:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
    except (OSError, StopIteration, csv.Error):
        return False
    return row.get("check_result", "") == "All completed"


def prerequisite_manifest(run: dict, step_key: str) -> Path:
    """Return the manifest produced by a successful prerequisite run."""
    run_dir = ROOT / "run_logs" / run["reporting_month"] / run["id"] / "artifacts"
    filename = "project_manifest.json" if step_key == "submission_check" else "file_manifest.json"
    return run_dir / filename


def enqueue_monthly_schedule(repo: RunRepository) -> None:
    """Queue the configured Run All occurrence once when its HKT time arrives."""
    with repo.connect() as db:
        schedule = db.execute("SELECT * FROM schedule WHERE id=1").fetchone()
    if not schedule or not schedule["enabled"]:
        return
    current = datetime.now(HKT)
    occurrence = current.strftime("%Y-%m")
    hour, minute = map(int, schedule["time_hkt"].split(":", 1))
    if current.day < schedule["day_of_month"] or (current.day == schedule["day_of_month"] and (current.hour, current.minute) < (hour, minute)):
        return
    if schedule["last_occurrence"] == occurrence:
        return
    previous = current.replace(day=1) - timedelta(days=1)
    reporting_month = previous.strftime("%Y-%m")
    try:
        repo.create_run(reporting_month, "ALL", "scheduler", trigger="scheduled")
    except RuntimeError:
        return
    with repo.connect() as db:
        db.execute("UPDATE schedule SET last_occurrence=? WHERE id=1", (occurrence,))


def execute_run(repo: RunRepository, run: dict, adapters: dict[str, StepAdapter]) -> None:
    run_id = run["id"]
    target = run["target"]
    keys = [target] if target != "ALL" else [
        adapter.spec.key
        for adapter in sorted(adapters.values(), key=lambda item: item.spec.order)
        if adapter.spec.configured and adapter.spec.script is not None and adapter.spec.key not in MANUAL_STEP_KEYS
    ]
    run_dir = ROOT / "run_logs" / run["reporting_month"] / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    completed: set[str] = set()
    prior_runs: dict[str, dict] = {}
    step3_source: dict = {}
    if target == "snowflake_upload" and run.get("source_json"):
        try:
            step3_source = json.loads(run["source_json"])
        except (TypeError, json.JSONDecodeError):
            repo.upsert_step(run_id, target, StepStatus.FAILED, message="Invalid Step 3 source configuration", finished_at=now_hkt())
            repo.set_status(run_id, RunStatus.FAILED, "Invalid Step 3 source configuration", finished=True)
            return
    custom_step3_source = step3_source.get("mode") == "selected_folder"

    if target == "snowflake_task":
        try:
            confirmation = json.loads(run.get("confirmation_json") or "")
        except (TypeError, json.JSONDecodeError):
            confirmation = {}
        if not confirmation.get("stage_files_confirmed"):
            repo.upsert_step(run_id, target, StepStatus.FAILED, message="Step 4 stage-file confirmation is required", finished_at=now_hkt())
            repo.set_status(run_id, RunStatus.FAILED, "Step 4 stage-file confirmation is required", finished=True)
            return

    if target != "ALL":
        prerequisite_keys = tuple(
            key for key in adapters[target].spec.prerequisites
            if not (target == "snowflake_upload" and custom_step3_source and key == "excel_download")
        )
        for key in prerequisite_keys:
            prior = repo.latest_successful_step_run(run["reporting_month"], key)
            if prior:
                prior_runs[key] = prior
                completed.add(key)
        missing = [key for key in prerequisite_keys if key not in prior_runs]
        if missing:
            repo.upsert_step(run_id, target, StepStatus.BLOCKED, message="Missing prerequisite: " + ", ".join(missing))
            repo.set_status(run_id, RunStatus.BLOCKED, "Missing prerequisite step(s): " + ", ".join(missing), finished=True)
            return

    for key in keys:
        adapter = adapters[key]
        if not adapter.spec.configured or adapter.spec.script is None or not adapter.spec.script.exists():
            repo.upsert_step(run_id, key, StepStatus.NOT_CONFIGURED, message="Step is not configured")
            repo.set_status(run_id, RunStatus.BLOCKED, f"{adapter.spec.name} is not configured", finished=True)
            return
        required_prerequisites = tuple(
            prerequisite
            for prerequisite in adapter.spec.prerequisites
            if not (key == "snowflake_upload" and custom_step3_source and prerequisite == "excel_download")
        )
        if any(prerequisite not in completed for prerequisite in required_prerequisites):
            repo.upsert_step(run_id, key, StepStatus.BLOCKED, message="Prerequisite step did not succeed")
            repo.set_status(run_id, RunStatus.BLOCKED, f"Prerequisite blocked {adapter.spec.name}", finished=True)
            return

        log_path = run_dir / f"{key}.log"
        if key == "risk_check":
            try:
                prepare_stage6_inputs(repo, run, run_dir, prior_runs["snowflake_upload"])
            except (KeyError, Stage6SourceError) as exc:
                message = f"Stage 6 input preparation failed: {exc}"
                log_path.write_text(message + "\n", encoding="utf-8")
                repo.upsert_step(
                    run_id,
                    key,
                    StepStatus.FAILED,
                    message=message,
                    log_path=str(log_path),
                    finished_at=now_hkt(),
                )
                repo.set_status(run_id, RunStatus.FAILED, message, finished=True)
                return
        repo.upsert_step(run_id, key, StepStatus.RUNNING, started_at=now_hkt(), log_path=str(log_path))
        input_manifest: Path | None = None
        if key == "excel_download":
            source_manifest = (
                run_dir / "artifacts" / "project_manifest.json"
                if "submission_check" in completed and target == "ALL"
                else prerequisite_manifest(prior_runs["submission_check"], "submission_check")
            )
            input_manifest = run_dir / "artifacts" / "project_manifest.json"
            try:
                write_download_project_manifest(
                    source_manifest,
                    input_manifest,
                    run["reporting_month"],
                    run_id,
                    selection_from_json(run.get("selection_json")),
                )
            except ProjectSelectionError as exc:
                log_path.write_text(f"PROJECT SELECTION ERROR\n{exc}\n", encoding="utf-8")
                repo.upsert_step(run_id, key, StepStatus.FAILED, message=str(exc), finished_at=now_hkt())
                repo.set_status(run_id, RunStatus.FAILED, "Project-file selection could not be applied", finished=True)
                return
        elif key == "snowflake_upload":
            if custom_step3_source:
                try:
                    input_manifest = write_selected_folder_manifest(
                        run_dir / "artifacts",
                        run["reporting_month"],
                        run_id,
                        Path(str(step3_source.get("folder", ""))),
                        list(step3_source.get("files", [])),
                        str(step3_source.get("reason", "")),
                    )
                except (Step3SourceError, TypeError) as exc:
                    log_path.write_text(f"STEP 3 SOURCE ERROR\n{exc}\n", encoding="utf-8")
                    repo.upsert_step(run_id, key, StepStatus.FAILED, message=str(exc), finished_at=now_hkt())
                    repo.set_status(run_id, RunStatus.FAILED, "Selected Step 3 source could not be prepared", finished=True)
                    return
            else:
                input_manifest = (
                    run_dir / "artifacts" / "file_manifest.json"
                    if "excel_download" in completed and target == "ALL"
                    else prerequisite_manifest(prior_runs["excel_download"], "excel_download")
                )
        step3_upload_receipt: Path | None = None
        if key == "snowflake_task":
            step3_run = prior_runs.get("snowflake_upload")
            if step3_run:
                step3_upload_receipt = (
                    ROOT / "run_logs" / step3_run["reporting_month"] / step3_run["id"] / "artifacts" / "upload_receipt.json"
                )
        command = adapter.command(
            run["reporting_month"],
            run_id,
            run_dir,
            input_manifest,
            step3_upload_receipt=step3_upload_receipt,
        )
        assert command is not None
        if key == "submission_check":
            command[command.index("--param") + 1] = month_end(run["reporting_month"])
        try:
            timeout = 3900 if key == "snowflake_task" else 3600
            completed_process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, shell=False)
            output = (completed_process.stdout + "\n" + completed_process.stderr).strip()
            log_path.write_text(output, encoding="utf-8")
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(f"TIMEOUT\n{exc}", encoding="utf-8")
            repo.upsert_step(run_id, key, StepStatus.FAILED, message="Step timed out", finished_at=now_hkt())
            repo.set_status(run_id, RunStatus.FAILED, f"{adapter.spec.name} timed out", finished=True)
            return
        except OSError as exc:
            repo.upsert_step(run_id, key, StepStatus.FAILED, message=str(exc), finished_at=now_hkt())
            repo.set_status(run_id, RunStatus.FAILED, f"Unable to start {adapter.spec.name}", finished=True)
            return

        if key == "submission_check" and completed_process.returncode == 0 and not signal_is_complete(RUN_LOGS_ROOT / "mfr_completion_signal.csv"):
            repo.upsert_step(run_id, key, StepStatus.BLOCKED, exit_code=completed_process.returncode, message="Submission check is not complete", finished_at=now_hkt())
            repo.set_status(run_id, RunStatus.BLOCKED, "MFR submissions are not all completed", finished=True)
            return
        if key == "submission_check" and completed_process.returncode == 0 and not (run_dir / "artifacts" / "project_manifest.json").is_file():
            repo.upsert_step(run_id, key, StepStatus.FAILED, exit_code=completed_process.returncode, message="Submission check did not produce a project manifest", finished_at=now_hkt())
            repo.set_status(run_id, RunStatus.FAILED, "Submission check did not produce a project manifest", finished=True)
            return
        if completed_process.returncode != 0:
            if key == "snowflake_task":
                message = step4_failure_message(output)
            elif key == "risk_check":
                message = risk_check_message(output, False)
            else:
                message = "Step returned a non-zero exit code"
            repo.upsert_step(run_id, key, StepStatus.FAILED, exit_code=completed_process.returncode, message=message, finished_at=now_hkt())
            summary = message if key == "snowflake_task" else f"{adapter.spec.name} failed"
            repo.set_status(run_id, RunStatus.FAILED, summary, finished=True)
            return
        message = risk_check_message(output, True) if key == "risk_check" else "Succeeded"
        repo.upsert_step(run_id, key, StepStatus.SUCCEEDED, exit_code=0, message=message, finished_at=now_hkt())
        completed.add(key)

    repo.set_status(run_id, RunStatus.SUCCEEDED, "Pipeline completed", finished=True)


def run_worker(repo: RunRepository, poll_seconds: int = 2) -> None:
    adapters = build_adapters()
    while True:
        enqueue_monthly_schedule(repo)
        run = repo.claim_next()
        if run:
            execute_run(repo, run, adapters)
        else:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    run_worker(RunRepository())
