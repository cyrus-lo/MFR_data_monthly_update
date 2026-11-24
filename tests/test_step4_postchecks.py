import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from mfr_snowflake_task.postchecks import PostcheckConfigError, PostcheckRule, PostcheckTable, load_postcheck_rules
import mfr_snowflake_task.trigger_task as trigger_task
from mfr_snowflake_task.trigger_task import (
    PostcheckFailure,
    Step4Config,
    Step4Error,
    build_project_postcheck_sql,
    evaluate_project_postcheck,
    month_end_dates,
)


TABLES = (
    PostcheckTable(
        "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_PARALLEL",
        "PROJECT_CODE",
        "REPORT_DATE",
        "varchar_yyyy_mm_dd",
    ),
    PostcheckTable(
        "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_ACCRUAL",
        "PROJECT_CODE",
        "REPORT_DATE",
        "date",
    ),
    PostcheckTable(
        "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_CASH_FLOW",
        "PROJECT_CODE",
        "REPORT_DATE",
        "date",
    ),
    PostcheckTable(
        "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_COMMITTED_COST",
        "PROJECT_CODE",
        "REPORT_DATE",
        "date",
    ),
    PostcheckTable(
        "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_PROJECTION",
        "PROJECT_CODE",
        "REPORT_DATE",
        "date",
    ),
)
TASK_A = "CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL"
TASK_B = "CHUNWO_DB.MFR_POC.TASK_TEST_API_TRIGGER"


def rule() -> PostcheckRule:
    return PostcheckRule("bronze-check", True, TASK_A, TASK_B, TABLES, 1)


def successful_rows() -> list[dict[str, object]]:
    return [
        {
            "table_name": table.name,
            "project_code": project_code,
            "current_rows": 1,
            "prior_rows": 1,
            "actual_distinct_project_count": 2,
        }
        for table in TABLES
        for project_code in ("1010", "1014")
    ]


def test_postcheck_builds_one_parameterized_union_query() -> None:
    sql, params = build_project_postcheck_sql(rule(), ("1010", "1014"), "2026-07-31", "2026-06-30")

    assert sql.count("UNION ALL") == 4
    assert "PARSE_JSON(%s)" in sql
    assert "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_PARALLEL" in sql
    assert "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_PROJECTION" in sql
    assert params[0] == json.dumps(("1010", "1014"))
    assert len(params) == 18
    assert "1010" not in sql


def test_postcheck_uses_correct_to_varchar_signature_for_mixed_date_types() -> None:
    sql, _ = build_project_postcheck_sql(
        rule(), ("1010", "1014"), "2026-07-31", "2026-06-30"
    )

    assert sql.count('TO_VARCHAR("REPORT_DATE") AS report_date') == 1
    assert sql.count('TO_VARCHAR("REPORT_DATE", \'YYYY-MM-DD\') AS report_date') == 4


def test_postcheck_selects_latest_120_second_ingestion_cluster_per_table_project_and_report_date() -> None:
    sql, _ = build_project_postcheck_sql(
        rule(), ("1010", "1014"), "2026-07-31", "2026-06-30"
    )

    assert sql.count('"DW_CREATED_TIME" AS ingestion_time') == 5
    assert sql.count("MAX(ingestion_time) OVER") == 5
    assert sql.count("PARTITION BY project_code, report_date") == 5
    assert sql.count("DATEADD(second, -120, MAX(ingestion_time) OVER") == 5
    assert "ingestion_time = MAX(ingestion_time)" not in sql


def test_postcheck_result_records_120_second_ingestion_cluster_policy() -> None:
    result = evaluate_project_postcheck(
        rule(), ("1010", "1014"), successful_rows(), "2026-07-31", "2026-06-30"
    )

    assert result["ingestion_selection"] == "latest_dw_created_time_trailing_window"
    assert all(
        table["ingestion_cluster_interval_seconds"] == 120 for table in result["tables"]
    )


def test_postcheck_accepts_all_five_tables_and_expected_projects() -> None:
    result = evaluate_project_postcheck(
        rule(), ("1010", "1014"), successful_rows(), "2026-07-31", "2026-06-30"
    )

    assert result["outcome"] == "SUCCEEDED"
    assert result["expected_distinct_project_count"] == 2
    assert len(result["tables"]) == 5
    assert all(table["outcome"] == "SUCCEEDED" for table in result["tables"])


def test_postcheck_fails_for_row_delta_above_one() -> None:
    rows = successful_rows()
    rows[0] = {**rows[0], "current_rows": 3, "prior_rows": 1}

    with pytest.raises(PostcheckFailure, match="Task B was not triggered") as error:
        evaluate_project_postcheck(rule(), ("1010", "1014"), rows, "2026-07-31", "2026-06-30")

    assert error.value.result["outcome"] == "FAILED"
    assert "allowed difference: 1" in str(error.value)


def test_postcheck_fails_for_missing_project_and_count_mismatch() -> None:
    rows = successful_rows()
    rows[0] = {**rows[0], "current_rows": 0, "actual_distinct_project_count": 1}
    rows[1] = {**rows[1], "actual_distinct_project_count": 1}

    with pytest.raises(PostcheckFailure, match="has 1 of 2 expected projects"):
        evaluate_project_postcheck(rule(), ("1010", "1014"), rows, "2026-07-31", "2026-06-30")


def test_postcheck_month_end_handles_january_rollover() -> None:
    assert month_end_dates("2026-01") == ("2026-01-31", "2025-12-31")


def monthly_silver_rule() -> PostcheckRule:
    monthly_tables = tuple(
        PostcheckTable(
            f"CHUNWO_DB.MFR.MFR_2_SILVER_MONTHLY_{name}",
            "PROJECT_CODE",
            "REPORT_DATE",
            "date",
            comparison_mode="fiscal_month",
            fiscal_month_column="FISCAL_YEAR_MONTH",
            fiscal_month_type="varchar_yyyy_mm_dd",
        )
        for name in ("ACCRUAL", "CASH_FLOW", "COMMITTED_COST", "PROJECTION")
    )
    main_table = PostcheckTable(
        "CHUNWO_DB.MFR.MFR_2_SILVER",
        "PROJECT_CODE",
        "REPORT_DATE",
        "varchar_yyyy_mm_dd",
    )
    return PostcheckRule(
        "silver-check",
        True,
        "CHUNWO_DB.PUBLIC.TASK_MFR_BRONZE_TO_SILVER",
        "",
        (main_table, *monthly_tables),
        1,
        terminal=True,
    )


def silver_rows(
    *, current_rows: int = 91, prior_rows: int = 91
) -> list[dict[str, object]]:
    rule_value = monthly_silver_rule()
    return [
        {
            "table_name": table.name,
            "project_code": "1010",
            "current_rows": 92 if table.comparison_mode == "report_date" else current_rows,
            "prior_rows": 91 if table.comparison_mode == "report_date" else prior_rows,
            "actual_distinct_project_count": 1,
        }
        for table in rule_value.tables
    ]


def test_silver_sql_compares_four_fiscal_tables_within_current_report_snapshot() -> None:
    sql, params = build_project_postcheck_sql(
        monthly_silver_rule(), ("1010",), "2026-10-31", "2026-09-30"
    )

    assert "CHUNWO_DB.MFR.MFR_2_SILVER" in sql
    assert "CHUNWO_DB.MFR.MFR_2_SILVER_MONTHLY_ACCRUAL" in sql
    assert sql.count('TO_VARCHAR("FISCAL_YEAR_MONTH") AS period_value') == 4
    assert sql.count('"REPORT_DATE" = TO_DATE(%s)') == 4
    assert "2026-10-01" not in sql
    assert "PARTITION BY project_code, report_date" in sql
    assert "DATEADD(second, -120, MAX(ingestion_time) OVER" in sql
    assert params[0] == json.dumps(("1010",))
    assert sql.count("%s") == len(params)
    assert params.count("2026-10-31") == 9
    assert params.count("2026-09-30") == 2
    assert params.count("2026-10-01") == 1
    assert params.count("2026-09-01") == 1


def test_silver_evaluation_passes_equal_fiscal_month_counts_and_preserves_main_snapshot_check() -> None:
    result = evaluate_project_postcheck(
        monthly_silver_rule(),
        ("1010",),
        silver_rows(),
        "2026-10-31",
        "2026-09-30",
    )

    assert result["outcome"] == "SUCCEEDED"
    assert result["comparison_mode"] == "mixed"
    assert result["current_fiscal_month"] == "2026-10-01"
    assert result["prior_fiscal_month"] == "2026-09-01"
    assert result["tables"][0]["comparison_mode"] == "report_date"
    assert result["tables"][0]["project_results"][0]["current_rows"] == 92
    assert all(table["comparison_mode"] == "fiscal_month" for table in result["tables"][1:])
    assert all(table["report_date_snapshot"] == "2026-10-31" for table in result["tables"][1:])


def test_silver_fiscal_delta_of_one_passes_but_two_fails() -> None:
    result = evaluate_project_postcheck(
        monthly_silver_rule(),
        ("1010",),
        silver_rows(current_rows=92, prior_rows=91),
        "2026-10-31",
        "2026-09-30",
    )
    assert result["outcome"] == "SUCCEEDED"

    with pytest.raises(PostcheckFailure, match="allowed difference: 1"):
        evaluate_project_postcheck(
            monthly_silver_rule(),
            ("1010",),
            silver_rows(current_rows=93, prior_rows=91),
            "2026-10-31",
            "2026-09-30",
        )


def test_silver_fiscal_missing_prior_month_fails_even_when_delta_is_one() -> None:
    with pytest.raises(PostcheckFailure, match="no rows for prior fiscal month 2026-09-01") as error:
        evaluate_project_postcheck(
            monthly_silver_rule(),
            ("1010",),
            silver_rows(current_rows=1, prior_rows=0),
            "2026-10-31",
            "2026-09-30",
        )

    assert error.value.result["outcome"] == "FAILED"
    assert error.value.result["tables"][1]["missing_prior_projects"] == ["1010"]
    assert "Task B was not triggered" not in str(error.value)


def test_silver_fiscal_missing_current_month_fails() -> None:
    with pytest.raises(PostcheckFailure, match="no rows for current fiscal month 2026-10-01"):
        evaluate_project_postcheck(
            monthly_silver_rule(),
            ("1010",),
            silver_rows(current_rows=0, prior_rows=91),
            "2026-10-31",
            "2026-09-30",
        )


@pytest.mark.parametrize(
    ("reporting_month", "current", "prior"),
    [
        ("2026-01", "2026-01-01", "2025-12-01"),
        ("2026-04", "2026-04-01", "2026-03-01"),
    ],
)
def test_fiscal_month_periods_roll_over_calendar_year_and_april_boundary(
    reporting_month: str, current: str, prior: str
) -> None:
    from mfr_snowflake_task.trigger_task import month_start_dates

    assert month_start_dates(reporting_month) == (current, prior)


def test_terminal_rule_config_uses_fiscal_month_only_for_four_monthly_tables() -> None:
    config_path = Path(__file__).parents[1] / "mfr_snowflake_task" / "task_postchecks.json"
    rules = load_postcheck_rules(
        config_path,
        [
            "CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL",
            "CHUNWO_DB.PUBLIC.TASK_MFR_BRONZE_TO_SILVER",
        ],
        "sequential",
    )
    silver = next(item for item in rules if item.id == "silver-project-completeness-terminal")
    modes = {table.name.rsplit(".", 1)[-1]: table.comparison_mode for table in silver.tables}

    assert modes["MFR_2_SILVER"] == "report_date"
    assert len([mode for mode in modes.values() if mode == "fiscal_month"]) == 4
    assert all(
        table.fiscal_month_column == "FISCAL_YEAR_MONTH"
        and table.fiscal_month_type == "varchar_yyyy_mm_dd"
        for table in silver.tables
        if table.comparison_mode == "fiscal_month"
    )


def test_postcheck_config_rejects_parallel_enabled_rule(tmp_path: Path) -> None:
    rule_file = tmp_path / "rules.json"
    rule_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "check",
                        "enabled": True,
                        "check_type": "project_count_and_row_delta",
                        "after_task": TASK_A,
                        "before_task": TASK_B,
                        "expected_projects": {"source": "step3_upload_receipt"},
                        "max_abs_row_count_delta": 1,
                        "tables": [
                            {
                                "name": "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_PARALLEL",
                                "project_column": "PROJECT_CODE",
                                "report_date_column": "REPORT_DATE",
                                "report_date_type": "varchar_yyyy_mm_dd",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PostcheckConfigError, match="require SF_TASK_EXECUTION_MODE=sequential"):
        load_postcheck_rules(rule_file, [TASK_A, TASK_B], "parallel")


@pytest.mark.parametrize("interval", [0, -1, True, "120"])
def test_postcheck_config_rejects_invalid_ingestion_cluster_interval(
    tmp_path: Path, interval: object
) -> None:
    rule_file = tmp_path / "rules.json"
    rule_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "check",
                        "enabled": False,
                        "check_type": "project_count_and_row_delta",
                        "after_task": TASK_A,
                        "before_task": TASK_B,
                        "expected_projects": {"source": "step3_upload_receipt"},
                        "max_abs_row_count_delta": 1,
                        "tables": [
                            {
                                "name": "CHUNWO_DB.PUBLIC.MFR_1_BRONZE_PARALLEL",
                                "project_column": "PROJECT_CODE",
                                "report_date_column": "REPORT_DATE",
                                "report_date_type": "varchar_yyyy_mm_dd",
                                "ingestion_cluster_interval_seconds": interval,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PostcheckConfigError, match="ingestion_cluster_interval_seconds"):
        load_postcheck_rules(rule_file, [TASK_A, TASK_B], "sequential")


def test_failed_postcheck_does_not_trigger_task_b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = tmp_path / "upload_receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "step3-run",
                "reporting_month": "2026-07",
                "files": [{"project_code": "1010", "status": "UPLOADED"}],
            }
        ),
        encoding="utf-8",
    )
    config = Step4Config({}, [TASK_A, TASK_B], "sequential", (rule(),))
    monkeypatch.setattr(trigger_task, "load_config", lambda: config)
    monkeypatch.setattr(trigger_task, "_task_metadata", lambda _cursor, task_name: (*task_name.split("."), "root"))
    triggered: list[str] = []

    def fake_trigger(_cursor: object, task_name: str, root_task_id: str, _now: object, *, preflight: bool = True) -> dict[str, object]:
        del root_task_id, preflight
        triggered.append(task_name)
        return {"task_name": task_name}

    monkeypatch.setattr(trigger_task, "_trigger_task", fake_trigger)
    monkeypatch.setattr(trigger_task, "_monitor_task", lambda _cursor, task, _sleep, _now: {**task, "final_state": "SUCCEEDED", "outcome": "SUCCEEDED"})

    def fail_gate(*_args: object) -> dict[str, object]:
        raise PostcheckFailure(
            "Post-check failed; Task B was not triggered: test failure",
            {"rule_id": "bronze-check", "outcome": "FAILED", "error": "test failure"},
        )

    monkeypatch.setattr(trigger_task, "_run_project_postcheck", fail_gate)

    class FakeCursor:
        def close(self) -> None:
            return None

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def close(self) -> None:
            return None

    connector = types.ModuleType("snowflake.connector")
    connector.connect = lambda **_kwargs: FakeConnection()
    snowflake = types.ModuleType("snowflake")
    snowflake.connector = connector
    monkeypatch.setitem(sys.modules, "snowflake", snowflake)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)

    args = SimpleNamespace(
        reporting_month="2026-07",
        run_id="step4-run",
        artifact_dir=tmp_path / "artifacts",
        step3_upload_receipt=receipt,
    )
    with pytest.raises(Step4Error, match="Task B was not triggered"):
        trigger_task.run(args)

    saved = json.loads((args.artifact_dir / "task_execution_receipt.json").read_text(encoding="utf-8"))
    assert triggered == [TASK_A]
    assert saved["outcome"] == "FAILED"
    assert saved["post_checks"][0]["outcome"] == "FAILED"
