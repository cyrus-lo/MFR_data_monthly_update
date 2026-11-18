import csv
from pathlib import Path

from mfr_submission_check.check_mfr_submission import LOG_FIELDS, write_execution_log
from pipeline_core.adapters import RUN_LOGS_ROOT, build_adapters


def test_execution_log_appends_report_month_and_run_id(tmp_path: Path) -> None:
    log_file = tmp_path / "sql_execution_log.csv"

    assert write_execution_log(
        log_file,
        "check_MFR_submission.sql",
        "SUCCESS",
        report_month="2026-06",
        run_id="run-june",
        rows=[{"project_code": "1010", "status": "completed"}],
        check_result="All completed",
    )
    assert write_execution_log(
        log_file,
        "check_MFR_submission.sql",
        "QUERY_ERROR",
        report_month="2026-07",
        run_id="run-july",
        error_message="Example failure",
        check_result="Not all completed",
    )

    with log_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert [(row["report_month"], row["run_id"]) for row in rows] == [
        ("2026-06", "run-june"),
        ("2026-07", "run-july"),
    ]


def test_execution_log_migrates_old_schema_before_appending(tmp_path: Path) -> None:
    log_file = tmp_path / "sql_execution_log.csv"
    old_fields = tuple(field for field in LOG_FIELDS if field not in {"report_month", "run_id"})
    with log_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({"timestamp_hkt": "2026-06-30T09:00:00+08:00", "sql_file": "old.sql"})

    assert write_execution_log(
        log_file,
        "check_MFR_submission.sql",
        "SUCCESS",
        report_month="2026-06",
        run_id="new-run",
        check_result="All completed",
    )

    with log_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert tuple(reader.fieldnames or ()) == LOG_FIELDS
    assert rows[0]["report_month"] == ""
    assert rows[0]["run_id"] == ""
    assert rows[1]["report_month"] == "2026-06"
    assert rows[1]["run_id"] == "new-run"


def test_submission_adapter_uses_shared_log_and_run_metadata(tmp_path: Path) -> None:
    command = build_adapters()["submission_check"].command("2026-06", "run-june", tmp_path)

    assert command is not None
    assert command[command.index("--log-file") + 1] == str(RUN_LOGS_ROOT / "sql_execution_log.csv")
    assert command[command.index("--report-month") + 1] == "2026-06"
    assert command[command.index("--run-id") + 1] == "run-june"
    assert command[command.index("--completion-signal-file") + 1] == str(
        RUN_LOGS_ROOT / "mfr_completion_signal.csv"
    )
