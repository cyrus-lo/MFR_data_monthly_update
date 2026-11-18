import os
from pathlib import Path

from pipeline_core.step4_readiness import step4_readiness
from pipeline_core.worker_health import worker_health


ENVIRONMENT = """\
SF_USER=service_user
SF_PASSWORD=secret
SF_ACCOUNT=account
SF_ROLE=role
SF_WAREHOUSE=warehouse
SF_DATABASE=CHUNWO_DB
SF_SCHEMA=PUBLIC
SF_TASK_NAMES=CHUNWO_DB.PUBLIC.TASK_ONE
SF_TASK_EXECUTION_MODE=sequential
"""


def make_step4_tree(tmp_path: Path, environment: str = ENVIRONMENT) -> Path:
    task_dir = tmp_path / "mfr_snowflake_task"
    task_dir.mkdir()
    (task_dir / "trigger_task.py").write_text("# trigger\n", encoding="utf-8")
    (task_dir / ".env").write_text(environment, encoding="utf-8")
    return tmp_path


def test_step4_readiness_requires_local_trigger_script(tmp_path: Path) -> None:
    result = step4_readiness(tmp_path)

    assert not result.ready
    assert result.state == "not_configured"


def test_step4_readiness_reports_missing_env_without_exposing_values(tmp_path: Path) -> None:
    root = tmp_path
    task_dir = root / "mfr_snowflake_task"
    task_dir.mkdir()
    (task_dir / "trigger_task.py").write_text("# trigger\n", encoding="utf-8")

    result = step4_readiness(root)

    assert not result.ready
    assert result.state == "configuration_incomplete"
    assert ".env is missing" in result.message


def test_step4_readiness_reports_missing_required_setting(tmp_path: Path) -> None:
    root = make_step4_tree(tmp_path, ENVIRONMENT.replace("SF_WAREHOUSE=warehouse\n", ""))

    result = step4_readiness(root)

    assert not result.ready
    assert result.message == "Missing Step 4 setting(s): SF_WAREHOUSE"


def test_step4_readiness_validates_task_configuration(tmp_path: Path) -> None:
    root = make_step4_tree(tmp_path, ENVIRONMENT.replace("CHUNWO_DB.PUBLIC.TASK_ONE", "not a task"))

    result = step4_readiness(root)

    assert not result.ready
    assert "DATABASE.SCHEMA.TASK" in result.message


def test_step4_readiness_accepts_valid_non_secret_configuration(tmp_path: Path) -> None:
    result = step4_readiness(make_step4_tree(tmp_path))

    assert result.ready
    assert result.state == "ready"
    assert "secret" not in result.message.lower()


def test_step4_readiness_rejects_enabled_postchecks_in_parallel_mode(tmp_path: Path) -> None:
        environment = ENVIRONMENT.replace("SF_TASK_NAMES=CHUNWO_DB.PUBLIC.TASK_ONE", "SF_TASK_NAMES=CHUNWO_DB.PUBLIC.TASK_A,CHUNWO_DB.PUBLIC.TASK_B")
        environment = environment.replace("SF_TASK_EXECUTION_MODE=sequential", "SF_TASK_EXECUTION_MODE=parallel\nSF_TASK_POSTCHECKS_FILE=rules.json")
        root = make_step4_tree(tmp_path, environment)
        (root / "mfr_snowflake_task" / "rules.json").write_text(
                """{
    "schema_version": 1,
    "rules": [{
        "id": "check",
        "enabled": true,
        "check_type": "project_count_and_row_delta",
        "after_task": "CHUNWO_DB.PUBLIC.TASK_A",
        "before_task": "CHUNWO_DB.PUBLIC.TASK_B",
        "expected_projects": {"source": "step3_upload_receipt"},
        "max_abs_row_count_delta": 1,
        "tables": [{
            "name": "CHUNWO_DB.PUBLIC.TEST_TABLE",
            "project_column": "PROJECT_CODE",
            "report_date_column": "REPORT_DATE",
            "report_date_type": "date"
        }]
    }]
}
""",
                encoding="utf-8",
        )

        result = step4_readiness(root)

        assert not result.ready
        assert "require SF_TASK_EXECUTION_MODE=sequential" in result.message


def test_worker_health_reports_stopped_and_stale_worker(tmp_path: Path) -> None:
    assert worker_health(tmp_path).state == "stopped"

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "mfr_worker.pid").write_text("99999999", encoding="utf-8")
    result = worker_health(tmp_path)

    assert result.state == "stale"
    assert "Start it" in result.message


def test_worker_health_reports_a_live_process(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "mfr_worker.pid").write_text(str(os.getpid()), encoding="utf-8")

    assert worker_health(tmp_path).state == "running"
