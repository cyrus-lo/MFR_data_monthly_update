from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .models import StepSpec

ROOT = Path(__file__).resolve().parent.parent
RUN_LOGS_ROOT = ROOT / "run_logs"


@dataclass(frozen=True)
class StepAdapter:
    spec: StepSpec

    def command(
        self,
        reporting_month: str,
        run_id: str,
        run_dir: Path,
        input_manifest: Path | None = None,
        step3_upload_receipt: Path | None = None,
    ) -> list[str] | None:
        if not self.spec.configured or self.spec.script is None:
            return None
        if self.spec.key == "submission_check":
            signal = RUN_LOGS_ROOT / "mfr_completion_signal.csv"
            log = RUN_LOGS_ROOT / "sql_execution_log.csv"
            return [
                sys.executable, str(self.spec.script),
                "--sql-file", str(ROOT / "mfr_submission_check" / "check_MFR_submission.sql"),
                "--param", f"{reporting_month}-01",  # replaced by worker with month-end
                "--expected-project-count", "20",
                "--completion-signal-file", str(signal),
                "--log-file", str(log),
                "--report-month", reporting_month,
                "--run-id", run_id,
                "--project-manifest", str(run_dir / "artifacts" / "project_manifest.json"),
            ]
        artifact_dir = run_dir / "artifacts"
        if self.spec.key == "excel_download":
            return [
                sys.executable, str(self.spec.script),
                "--reporting-month", reporting_month,
                "--run-id", run_id,
                "--artifact-dir", str(artifact_dir),
                "--project-manifest", str(input_manifest or artifact_dir / "project_manifest.json"),
            ]
        if self.spec.key == "snowflake_upload":
            return [
                sys.executable, str(self.spec.script),
                "--reporting-month", reporting_month,
                "--run-id", run_id,
                "--artifact-dir", str(artifact_dir),
                "--file-manifest", str(input_manifest or artifact_dir / "file_manifest.json"),
            ]
        if self.spec.key == "snowflake_task":
            command = [
                sys.executable, "-m", "mfr_snowflake_task.trigger_task",
                "--reporting-month", reporting_month,
                "--run-id", run_id,
                "--artifact-dir", str(artifact_dir),
            ]
            if step3_upload_receipt is not None:
                command.extend(("--step3-upload-receipt", str(step3_upload_receipt)))
            return command
        if self.spec.key == "risk_check":
            return [
                sys.executable, str(self.spec.script),
                "--reporting-month", reporting_month,
                "--run-id", run_id,
                "--output-dir", str(ROOT / "mfr_risk_check" / "output"),
            ]
        return [sys.executable, str(self.spec.script), "--reporting-month", reporting_month]


def build_adapters() -> dict[str, StepAdapter]:
    return {
        "submission_check": StepAdapter(StepSpec(
            "submission_check", "Check submission", 1, True,
            script=ROOT / "mfr_submission_check" / "check_mfr_submission.py",
        )),
        "excel_download": StepAdapter(StepSpec(
            "excel_download", "Download Excel files", 2, True,
            ("submission_check",), ROOT / "mfr_excel_download" / "download_CIS_mfr_files.py",
        )),
        "snowflake_upload": StepAdapter(StepSpec(
            "snowflake_upload", "Upload to Snowflake", 3, True,
            ("submission_check", "excel_download"), ROOT / "mfr_snowflake_upload" / "upload_mfr_files_to_snowflake.py",
        )),
        "snowflake_task": StepAdapter(StepSpec(
            "snowflake_task", "Trigger Snowflake task", 4, True,
            ("submission_check", "excel_download", "snowflake_upload"), ROOT / "mfr_snowflake_task" / "trigger_task.py",
        )),
        "risk_sync": StepAdapter(StepSpec(
            "risk_sync", "Sync Risk DB (template)", 5, False,
            script=ROOT / "mfr_risk_sync" / "sync_risk_db_template.ps1",
        )),
        "risk_check": StepAdapter(StepSpec(
            "risk_check", "Compare MFR and Risk", 6, True,
            ("snowflake_upload",),
            script=ROOT / "mfr_risk_check" / "MFR_vs_Risk_calculation_checking.py",
        )),
    }
