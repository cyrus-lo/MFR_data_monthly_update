import subprocess
import sys
from pathlib import Path

import pytest

from pipeline_core.models import RunStatus, StepStatus
from pipeline_core.adapters import build_adapters
from pipeline_core.repository import RunRepository
from worker.worker_main import execute_run, month_end, signal_is_complete, step4_failure_message


def test_month_end_handles_leap_year() -> None:
    assert month_end("2024-02") == "2024-02-29"
    assert month_end("2026-06") == "2026-06-30"


def test_month_end_rejects_invalid_month() -> None:
    try:
        month_end("2026-13")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid month was accepted")


def test_signal_requires_exact_completion(tmp_path: Path) -> None:
    signal = tmp_path / "signal.csv"
    signal.write_text("check_result\nAll completed\n", encoding="utf-8")
    assert signal_is_complete(signal)
    signal.write_text("check_result\nNot all completed\n", encoding="utf-8")
    assert not signal_is_complete(signal)


def test_repository_prevents_two_active_runs(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    repo.create_run("2026-06", "submission_check", "Tester")
    try:
        repo.create_run("2026-07", "submission_check", "Tester")
    except RuntimeError as exc:
        assert "active" in str(exc)
    else:
        raise AssertionError("second active run was accepted")


def test_repository_requires_audited_authorization_to_rerun_success(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    original_run = repo.create_run("2026-06", "submission_check", "Tester")
    repo.claim_next()
    repo.set_status(original_run, RunStatus.SUCCEEDED, finished=True)

    with pytest.raises(RuntimeError, match="already exists"):
        repo.create_run("2026-06", "submission_check", "Tester")
    with pytest.raises(RuntimeError, match="override reason"):
        repo.create_run("2026-06", "submission_check", "Tester", allow_rerun=True)

    rerun_id = repo.create_run(
        "2026-06",
        "submission_check",
        "Tester",
        override_reason="Validate the corrected June source data",
        allow_rerun=True,
    )
    assert rerun_id != original_run
    assert len(repo.list_runs()) == 2


def test_repository_lists_recent_step_runs_with_run_context(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    first_run = repo.create_run("2026-06", "submission_check", "June operator")
    repo.claim_next()
    repo.upsert_step(
        first_run,
        "submission_check",
        StepStatus.SUCCEEDED,
        exit_code=0,
        message="Succeeded",
        log_path="run_logs/2026-06/first/submission_check.log",
    )

    rows = repo.list_recent_step_runs()

    assert len(rows) == 1
    assert rows[0]["run_id"] == first_run
    assert rows[0]["reporting_month"] == "2026-06"
    assert rows[0]["operator"] == "June operator"
    assert rows[0]["override_reason"] == ""
    assert rows[0]["step_key"] == "submission_check"
    assert rows[0]["step_status"] == StepStatus.SUCCEEDED
    assert rows[0]["exit_code"] == 0
    assert rows[0]["message"] == "Succeeded"


def test_repository_includes_override_reason_for_each_step(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    reason = "Validate the corrected June source data"
    run_id = repo.create_run(
        "2026-06",
        "ALL",
        "Override tester",
        override_reason=reason,
        allow_rerun=True,
    )
    repo.claim_next()
    repo.upsert_step(run_id, "submission_check", StepStatus.SUCCEEDED, message="Succeeded")
    repo.upsert_step(run_id, "excel_download", StepStatus.SUCCEEDED, message="Succeeded")

    rows = [row for row in repo.list_recent_step_runs() if row["run_id"] == run_id]

    assert {row["step_key"] for row in rows} == {"submission_check", "excel_download"}
    assert {row["override_reason"] for row in rows} == {reason}


def test_step4_requires_stage_confirmation(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    with pytest.raises(RuntimeError, match="Step 4 requires confirmation"):
        repo.create_run("2026-07", "snowflake_task", "Tester")

    def test_step6_requires_source_and_risk_confirmation(tmp_path: Path) -> None:
        repo = RunRepository(tmp_path / "pipeline.db")
        with pytest.raises(RuntimeError, match="Step 6 requires confirmation"):
            repo.create_run("2026-07", "risk_check", "Tester")
        with pytest.raises(RuntimeError, match="Step 6 requires confirmation"):
            repo.create_run(
                "2026-07",
                "risk_check",
                "Tester",
                step6_confirmation={"mfr_source_confirmed": True, "step3_run_id": "step3"},
            )

        run_id = repo.create_run(
            "2026-07",
            "risk_check",
            "Tester",
            step6_confirmation={
                "mfr_source_confirmed": True,
                "risk_workbook_confirmed": True,
                "step3_run_id": "step3",
                "confirmed_at_hkt": "2026-07-23T10:00:00+08:00",
            },
        )
        saved = repo.get_run(run_id)
        assert saved is not None
        assert '"step3_run_id": "step3"' in saved["confirmation_json"]


def test_step4_requires_same_month_successful_step3(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    run_id = repo.create_run(
        "2026-07",
        "snowflake_task",
        "Tester",
        step4_confirmation={"stage_files_confirmed": True, "confirmed_at_hkt": "2026-07-23T10:00:00+08:00"},
    )
    run = repo.claim_next()
    assert run is not None
    execute_run(repo, run, build_adapters())
    saved = repo.get_run(run_id)
    assert saved is not None
    assert saved["status"] == RunStatus.BLOCKED
    assert "snowflake_upload" in saved["summary"]


def test_step4_failure_message_retains_sanitized_script_error() -> None:
    output = "noise\nSTEP 4 ERROR: Missing Snowflake setting(s): SF_ACCOUNT\n"

    assert step4_failure_message(output) == "STEP 4 ERROR: Missing Snowflake setting(s): SF_ACCOUNT"
    assert step4_failure_message("STEP 4 ERROR: password=super-secret") == "STEP 4 ERROR: password=[REDACTED]"


def test_step4_adapter_forwards_step3_upload_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "step3" / "artifacts" / "upload_receipt.json"
    command = build_adapters()["snowflake_task"].command(
        "2026-07",
        "step4-run",
        tmp_path / "step4",
        step3_upload_receipt=receipt,
    )

    assert command is not None
    assert command[:3] == [command[0], "-m", "mfr_snowflake_task.trigger_task"]
    assert command[command.index("--step3-upload-receipt") + 1] == str(receipt)


def test_step4_module_command_imports_shared_pipeline_core() -> None:
    root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "-m", "mfr_snowflake_task.trigger_task", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--step3-upload-receipt" in completed.stdout


def test_run_all_excludes_manual_step_adapters() -> None:
    adapters = build_adapters()
    configured_keys = [
        adapter.spec.key
        for adapter in sorted(adapters.values(), key=lambda item: item.spec.order)
        if adapter.spec.configured and adapter.spec.script is not None and adapter.spec.key not in {"snowflake_task", "risk_sync", "risk_check"}
    ]
    assert configured_keys == ["submission_check", "excel_download", "snowflake_upload"]
