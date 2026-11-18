from datetime import datetime, timezone
from pathlib import Path

import pytest

import mfr_snowflake_task.trigger_task as trigger_task
from mfr_snowflake_task.trigger_task import (
    Step4Config,
    Step4Error,
    graph_matches,
    load_config,
    parse_task_names,
    sanitize_error,
    split_task_name,
    write_receipt,
)


def test_step4_loads_snowflake_password_like_step3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        trigger_task,
        "dotenv_values",
        lambda _path: {
            "SF_USER": "UPLOAD_FILE",
            "SF_PASSWORD": "normal-password-for-test",
            "SF_ACCOUNT": "account",
            "SF_ROLE": "TRIGGER_TASK",
            "SF_WAREHOUSE": "COMPUTE_WH",
            "SF_DATABASE": "CHUNWO_DB",
            "SF_SCHEMA": "MFR_POC",
            "SF_TASK_NAMES": "CHUNWO_DB.MFR_POC.TEST_TASK",
            "SF_TASK_EXECUTION_MODE": "sequential",
        },
    )

    config = load_config()

    assert isinstance(config, Step4Config)
    assert config.connection_parameters == {
        "user": "UPLOAD_FILE",
        "password": "normal-password-for-test",
        "account": "account",
        "role": "TRIGGER_TASK",
        "warehouse": "COMPUTE_WH",
        "database": "CHUNWO_DB",
        "schema": "MFR_POC",
    }
    assert config.task_names == ["CHUNWO_DB.MFR_POC.TEST_TASK"]
    assert config.execution_mode == "sequential"


def test_step4_validates_fully_qualified_task_name() -> None:
    assert split_task_name("CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL") == (
        "CHUNWO_DB",
        "PUBLIC",
        "TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL",
    )
    with pytest.raises(Step4Error):
        split_task_name("CHUNWO_DB.PUBLIC.TASK; DROP TABLE X")


def test_step4_parses_multiple_task_names_without_duplicates() -> None:
    assert parse_task_names("DB.PUBLIC.TASK_A, DB.PUBLIC.TASK_B") == [
        "DB.PUBLIC.TASK_A",
        "DB.PUBLIC.TASK_B",
    ]
    with pytest.raises(Step4Error):
        parse_task_names("DB.PUBLIC.TASK_A,db.public.task_a")


def test_step4_matches_root_graph_identity() -> None:
    row = {
        "root_task_id": "root-1",
        "root_task_name": "TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL",
        "database_name": "CHUNWO_DB",
        "schema_name": "PUBLIC",
    }
    assert graph_matches(row, "CHUNWO_DB.PUBLIC.TASK_MFR_EXCEL_TO_TABLE_MFR_PARALLEL", "root-1")
    assert not graph_matches(row, "CHUNWO_DB.PUBLIC.OTHER_TASK", "root-1")


def test_step4_uses_table_wrapped_information_schema_functions() -> None:
    assert trigger_task.current_graphs_sql("CHUNWO_DB", "MFR_POC") == (
        "SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.CURRENT_TASK_GRAPHS())"
    )
    assert trigger_task.complete_graphs_sql("CHUNWO_DB", "MFR_POC") == (
        "SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.COMPLETE_TASK_GRAPHS(RESULT_LIMIT => 100))"
    )
    assert trigger_task.task_history_sql("CHUNWO_DB", "MFR_POC", "root-1") == (
        "SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.TASK_HISTORY("
        "ROOT_TASK_ID => %s, RESULT_LIMIT => 10000))",
        ("root-1",),
    )


def test_step4_preflight_accepts_an_idle_task(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    def fake_execute_rows(cursor: object, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        del cursor, params
        queries.append(sql)
        return []

    monkeypatch.setattr(trigger_task, "execute_rows", fake_execute_rows)

    trigger_task._ensure_task_available(object(), "CHUNWO_DB.MFR_POC.TASK_TEST_API_TRIGGER", "root-1")

    assert queries == [trigger_task.current_graphs_sql("CHUNWO_DB", "MFR_POC")]


def test_step4_identifies_a_completed_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = datetime(2026, 7, 27, tzinfo=timezone.utc)
    graph = {
        "root_task_id": "root-1",
        "root_task_name": "TASK_TEST_API_TRIGGER",
        "database_name": "CHUNWO_DB",
        "schema_name": "MFR_POC",
        "graph_run_group_id": "group-1",
        "state": "SUCCEEDED",
        "scheduled_time": boundary,
    }
    responses = iter([[], [graph]])

    def fake_execute_rows(cursor: object, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        del cursor, sql, params
        return next(responses)

    monkeypatch.setattr(trigger_task, "execute_rows", fake_execute_rows)

    identified = trigger_task._identify_graph(
        object(),
        {
            "task_name": "CHUNWO_DB.MFR_POC.TASK_TEST_API_TRIGGER",
            "root_task_id": "root-1",
            "_boundary": boundary,
        },
        datetime.now,
    )

    assert identified == graph


def test_step4_monitors_graph_to_completion_without_unpacking_error(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = datetime(2026, 7, 27, tzinfo=timezone.utc)
    running_graph = {
        "root_task_id": "root-1",
        "root_task_name": "TASK_TEST_API_TRIGGER",
        "database_name": "CHUNWO_DB",
        "schema_name": "MFR_POC",
        "graph_run_group_id": "group-1",
        "attempt_number": 1,
        "state": "EXECUTING",
        "scheduled_time": boundary,
    }
    completed_graph = {**running_graph, "state": "SUCCEEDED"}
    calls: list[tuple[str, tuple[object, ...]]] = []
    current_graphs = iter([[running_graph], [completed_graph]])

    def fake_execute_rows(cursor: object, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        del cursor
        calls.append((sql, params))
        if "CURRENT_TASK_GRAPHS" in sql:
            return next(current_graphs)
        if "TASK_HISTORY" in sql:
            return [{"name": "TASK_TEST_API_TRIGGER", "state": "SUCCEEDED"}]
        raise AssertionError(f"Unexpected query: {sql}")

    monkeypatch.setattr(trigger_task, "execute_rows", fake_execute_rows)
    task = {
        "task_name": "CHUNWO_DB.MFR_POC.TASK_TEST_API_TRIGGER",
        "root_task_id": "root-1",
        "_boundary": boundary,
    }
    sleeps: list[int] = []

    def record_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    def current_time(requested_timezone: timezone) -> datetime:
        assert requested_timezone is timezone.utc
        return boundary

    completed = trigger_task._monitor_task(
        object(),
        task,
        record_sleep,
        current_time,
    )

    assert completed["outcome"] == "SUCCEEDED"
    assert completed["final_state"] == "SUCCEEDED"
    assert completed["task_history"] == [{"name": "TASK_TEST_API_TRIGGER", "state": "SUCCEEDED"}]
    assert "_boundary" not in completed
    assert trigger_task.POLL_SECONDS == 480
    assert sleeps == [480]
    assert calls[-1][1] == ("root-1",)


def test_step4_sanitizes_credentials_and_writes_receipt(tmp_path: Path) -> None:
    assert "password=[REDACTED]" in sanitize_error("password=secret")
    receipt = write_receipt(tmp_path, {"outcome": "SUCCEEDED", "graph_run_group_id": "group-1"})
    assert receipt.name == "task_execution_receipt.json"
    assert '"graph_run_group_id": "group-1"' in receipt.read_text(encoding="utf-8")
