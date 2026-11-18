import csv
import hashlib
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

import mfr_snowflake_upload.upload_mfr_files_to_snowflake as step3_uploader

from mfr_excel_download.download_CIS_mfr_files import (
    Project,
    Step2Error,
    copy_workbooks,
    freeze_completion_cells,
    load_projects,
    safe_target,
    write_preparation_log,
    write_file_manifest,
)
from mfr_snowflake_upload.upload_mfr_files_to_snowflake import (
    SnowflakeConfig,
    Step3Error,
    UploadFile,
    destination_stage,
    load_upload_files,
    manifest_artifact_dir,
    put_command,
    snowflake_status,
    write_receipt,
)
from mfr_submission_check.check_mfr_submission import write_project_manifest
from pipeline_core.adapters import build_adapters
from pipeline_core.models import RunStatus, StepStatus
from pipeline_core.project_selection import (
    ProjectSelectionError,
    parse_pasted_project_codes,
    write_download_project_manifest,
)
from pipeline_core.repository import RunRepository
from pipeline_core.step3_sources import (
    Step3SourceError,
    discover_folder_files,
    read_preparation_log,
    write_selected_folder_manifest,
)


def test_step1_project_manifest_round_trips_for_step2(tmp_path: Path) -> None:
    manifest = tmp_path / "artifacts" / "project_manifest.json"
    rows = [
        {"project_code": 969, "status": "completed"},
        {"project_code": 1010, "status": "completed"},
    ]

    assert write_project_manifest(manifest, "2026-06", "run-june", rows)

    assert load_projects(manifest, "2026-06") == [Project("969"), Project("1010")]


def test_step2_rejects_unsafe_archive_member(tmp_path: Path) -> None:
    with pytest.raises(Step2Error, match="escapes"):
        safe_target(tmp_path / "extract", "../../outside.xlsx")


def test_step2_copies_e_mfr_and_sub_e_mfr_workbooks(tmp_path: Path) -> None:
    source = tmp_path / "extracted"
    source.mkdir()
    (source / "eMFR_1010_2026-06.xlsx").write_bytes(b"eMFR")
    (source / "Sub_eMFR_1010_2026-06.xlsx").write_bytes(b"Sub_eMFR")

    copied = copy_workbooks(Project("1010"), source, tmp_path / "prepared")

    assert [path.name for path in copied] == [
        "eMFR_1010_2026-06.xlsx",
        "Sub_eMFR_1010_2026-06.xlsx",
    ]
    assert (tmp_path / "prepared" / "1010" / "Sub_eMFR_1010_2026-06.xlsx").read_bytes() == b"Sub_eMFR"


def install_fake_excel(monkeypatch: pytest.MonkeyPatch, values_by_filename: dict[str, dict[str, object]]) -> list[str]:
    opened: list[str] = []

    class FakeCell:
        def __init__(self, values: dict[str, object], name: str) -> None:
            self.values = values
            self.name = name

        @property
        def Value(self) -> object:
            return self.values[self.name]

        @Value.setter
        def Value(self, value: object) -> None:
            self.values[self.name] = value

    class FakeWorksheet:
        def __init__(self, values: dict[str, object]) -> None:
            self.values = values

        def Range(self, name: str) -> FakeCell:
            return FakeCell(self.values, name)

    class FakeWorkbook:
        def __init__(self, filename: str, values: dict[str, object]) -> None:
            self.filename = filename
            self.ActiveSheet = FakeWorksheet(values)

        def Close(self, SaveChanges: bool) -> None:
            return None

    class FakeWorkbooks:
        def Open(self, path: str, **kwargs: object) -> FakeWorkbook:
            filename = Path(path).name
            opened.append(filename)
            return FakeWorkbook(filename, values_by_filename[filename])

    class FakeExcel:
        def __init__(self) -> None:
            self.Workbooks = FakeWorkbooks()

        def CalculateFullRebuild(self) -> None:
            return None

        def Quit(self) -> None:
            return None

    client = types.ModuleType("win32com.client")
    client.DispatchEx = lambda _: FakeExcel()
    win32com = types.ModuleType("win32com")
    win32com.client = client
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    return opened


def test_step2_only_prepares_e_mfr_and_logs_unchanged_sub_e_mfr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    e_mfr = tmp_path / "prepared" / "1010" / "eMFR_1010_2026-06.xlsx"
    sub_e_mfr = tmp_path / "prepared" / "1010" / "Sub_eMFR_1010_2026-06.xlsx"
    e_mfr.parent.mkdir(parents=True)
    e_mfr.write_bytes(b"eMFR original")
    sub_e_mfr.write_bytes(b"Sub_eMFR unchanged")
    sub_e_mfr_before = sub_e_mfr.read_bytes()
    log_file = tmp_path / "artifacts" / "mfr_preparation_log.csv"
    opened = install_fake_excel(monkeypatch, {
        e_mfr.name: {"C7": "completed", "C8": "approved"},
        sub_e_mfr.name: {"C7": None, "C8": None},
    })

    freeze_completion_cells(
        [e_mfr, sub_e_mfr],
        reporting_month="2026-06",
        run_id="run-june",
        log_file=log_file,
        artifact_dir=tmp_path,
    )

    assert opened == [e_mfr.name]
    assert sub_e_mfr.read_bytes() == sub_e_mfr_before
    with log_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["status"] == "SUCCESS_PREPARED"
    assert rows[0]["filename"] == "prepared/1010/eMFR_1010_2026-06.xlsx"
    assert datetime.fromisoformat(rows[0]["timestamp_hkt"]).utcoffset().total_seconds() == 8 * 60 * 60

    write_preparation_log(
        log_file,
        "2026-06",
        "run-june",
        "prepared/1010/Sub_eMFR_1010_2026-06.xlsx",
        "SUCCESS_UNCHANGED",
    )
    with log_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["status"] == "SUCCESS_UNCHANGED"


def test_step2_allows_blank_calculated_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "prepared" / "1010" / "eMFR_1010_2026-06.xlsx"
    second = tmp_path / "prepared" / "1014" / "eMFR_1014_2026-06.xlsx"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    log_file = tmp_path / "artifacts" / "mfr_preparation_log.csv"
    opened = install_fake_excel(monkeypatch, {
        first.name: {"C7": "completed", "C8": "approved"},
        second.name: {"C7": None, "C8": ""},
    })

    freeze_completion_cells(
        [first, second],
        reporting_month="2026-06",
        run_id="run-june",
        log_file=log_file,
        artifact_dir=tmp_path,
    )

    with log_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["status"] for row in rows] == ["SUCCESS_PREPARED", "SUCCESS_PREPARED"]
    assert rows[1]["filename"] == "prepared/1014/eMFR_1014_2026-06.xlsx"
    assert rows[1]["error_message"] == ""
    assert opened == [first.name, second.name]


def test_step2_file_manifest_is_accepted_by_step3(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    workbook = artifact_dir / "prepared" / "0969" / "eMFR_0969.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"workbook fixture")

    manifest = write_file_manifest(
        artifact_dir,
        "2026-06",
        "run-june",
        {"969": [workbook]},
    )

    files = load_upload_files(manifest, artifact_dir, "2026-06")
    assert [item.relative_path for item in files] == ["prepared/0969/eMFR_0969.xlsx"]
    assert files[0].sha256 == hashlib.sha256(b"workbook fixture").hexdigest()


def test_step3_rejects_tampered_workbook(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    workbook = artifact_dir / "prepared" / "0969" / "eMFR_0969.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"original")
    manifest = write_file_manifest(artifact_dir, "2026-06", "run-june", {"969": [workbook]})
    workbook.write_bytes(b"changed")

    with pytest.raises(Step3Error, match="checksum"):
        load_upload_files(manifest, artifact_dir, "2026-06")


def test_step3_uses_the_step2_artifact_directory_that_owns_the_manifest(tmp_path: Path) -> None:
    step2_artifacts = tmp_path / "run-2" / "artifacts"
    step3_artifacts = tmp_path / "run-3" / "artifacts"
    workbook = step2_artifacts / "prepared" / "1010" / "eMFR_1010_2026-06.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"prepared by step 2")
    manifest = write_file_manifest(step2_artifacts, "2026-06", "step-2-run", {"1010": [workbook]})

    source_artifacts = manifest_artifact_dir(manifest)
    files = load_upload_files(manifest, source_artifacts, "2026-06")

    assert source_artifacts == step2_artifacts.resolve()
    assert files[0].path == workbook.resolve()
    assert not (step3_artifacts / "prepared" / "1010" / workbook.name).exists()


def test_step3_put_command_and_status_contract(tmp_path: Path) -> None:
    workbook = tmp_path / "eMFR_0969.xlsx"
    workbook.write_bytes(b"x")

    assert "AUTO_COMPRESS=FALSE OVERWRITE=TRUE" in put_command(workbook, "@MFR_UPLOAD_FILES")
    assert snowflake_status([("a", "b", 1, 1, "NONE", "NONE", "UPLOADED", "")])[0] == "UPLOADED"


def test_step3_routes_workbook_types_to_distinct_stages(tmp_path: Path) -> None:
    config = SnowflakeConfig({}, "@MFR_EMFR_FILES", "@MFR_SUB_EMFR_FILES")
    emfr = UploadFile("1010", tmp_path / "eMFR_1010.xlsx", "prepared/1010/eMFR_1010.xlsx", "a" * 64)
    sub_emfr = UploadFile("1010", tmp_path / "Sub_eMFR_1010.xlsx", "prepared/1010/Sub_eMFR_1010.xlsx", "b" * 64)

    assert destination_stage(emfr, config) == "@MFR_EMFR_FILES"
    assert destination_stage(sub_emfr, config) == "@MFR_SUB_EMFR_FILES"


def test_step3_rejects_unknown_workbook_stage_route(tmp_path: Path) -> None:
    config = SnowflakeConfig({}, "@MFR_EMFR_FILES", "@MFR_SUB_EMFR_FILES")
    workbook = UploadFile("1010", tmp_path / "other.xlsx", "prepared/1010/other.xlsx", "a" * 64)

    with pytest.raises(Step3Error, match="No Snowflake stage"):
        destination_stage(workbook, config)


def test_step3_requires_valid_distinct_workbook_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "SF_USER": "user",
        "SF_PASSWORD": "password",
        "SF_ACCOUNT": "account",
        "SF_WAREHOUSE": "warehouse",
        "SF_DATABASE": "database",
        "SF_SCHEMA": "schema",
        "SF_ROLE": "role",
        "SF_EMFR_STAGE_NAME": "@MFR_EMFR_FILES",
        "SF_SUB_EMFR_STAGE_NAME": "@MFR_SUB_EMFR_FILES",
    }
    monkeypatch.setattr(step3_uploader, "dotenv_values", lambda _: values)

    config = step3_uploader.load_config()

    assert config.emfr_stage_name == "@MFR_EMFR_FILES"
    assert config.sub_emfr_stage_name == "@MFR_SUB_EMFR_FILES"


@pytest.mark.parametrize(
    ("emfr_stage", "sub_emfr_stage", "message"),
    [
        ("", "@MFR_SUB_EMFR_FILES", "Missing Snowflake setting"),
        ("invalid stage", "@MFR_SUB_EMFR_FILES", "SF_EMFR_STAGE_NAME must be a valid"),
        ("@MFR_FILES", "@MFR_FILES", "must be different"),
    ],
)
def test_step3_rejects_missing_invalid_or_equal_stages(
    monkeypatch: pytest.MonkeyPatch,
    emfr_stage: str,
    sub_emfr_stage: str,
    message: str,
) -> None:
    values = {
        "SF_USER": "user",
        "SF_PASSWORD": "password",
        "SF_ACCOUNT": "account",
        "SF_WAREHOUSE": "warehouse",
        "SF_DATABASE": "database",
        "SF_SCHEMA": "schema",
        "SF_ROLE": "role",
        "SF_EMFR_STAGE_NAME": emfr_stage,
        "SF_SUB_EMFR_STAGE_NAME": sub_emfr_stage,
    }
    monkeypatch.setattr(step3_uploader, "dotenv_values", lambda _: values)

    with pytest.raises(Step3Error, match=message):
        step3_uploader.load_config()


def test_step3_preparation_log_and_selected_folder_manifest(tmp_path: Path) -> None:
    log_file = tmp_path / "mfr_preparation_log.csv"
    log_file.write_text(
        "reporting_month,run_id,timestamp_hkt,filename,status,error_message\n"
        "2026-06,step-2,2026-07-23T10:00:00+08:00,prepared/1010/eMFR.xlsx,SUCCESS_PREPARED,\n",
        encoding="utf-8",
    )
    assert read_preparation_log(log_file, "2026-06")[0]["status"] == "SUCCESS_PREPARED"

    folder = tmp_path / "selected"
    workbook = folder / "1010" / "eMFR_1010.xlsx"
    unchanged = folder / "1010" / "Sub_eMFR_1010.xlsx"
    ignored = folder / "1010" / "notes.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"eMFR")
    unchanged.write_bytes(b"Sub")
    ignored.write_bytes(b"ignored")

    records, rejected = discover_folder_files(folder)
    assert {record["filename"] for record in records} == {"1010/eMFR_1010.xlsx", "1010/Sub_eMFR_1010.xlsx"}
    assert rejected == []

    artifact_dir = tmp_path / "step-3-artifacts"
    manifest = write_selected_folder_manifest(artifact_dir, "2026-06", "step-3", folder, records)
    assert manifest_artifact_dir(manifest) == folder.resolve()
    files = load_upload_files(manifest, artifact_dir, "2026-06")
    assert {item.path.name for item in files} == {workbook.name, unchanged.name}


def test_step3_selected_folder_requires_non_empty_selection(tmp_path: Path) -> None:
    with pytest.raises(Step3SourceError, match="no approved"):
        write_selected_folder_manifest(tmp_path / "artifacts", "2026-06", "run", tmp_path, [])


def test_step3_receipt_records_upload_source(tmp_path: Path) -> None:
    receipt = write_receipt(
        tmp_path,
        "2026-06",
        "run",
        [{"source": "prepared/1010/eMFR.xlsx", "destination_stage": "@MFR_EMFR_FILES"}],
        {"mode": "selected_folder", "source_folder": "C:/MFR", "reason": "Recovery"},
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["source"]["mode"] == "selected_folder"
    assert payload["source"]["reason"] == "Recovery"
    assert payload["files"][0]["destination_stage"] == "@MFR_EMFR_FILES"


def test_step3_receipt_creates_missing_artifact_directory(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "run" / "artifacts"

    receipt = write_receipt(artifact_dir, "2026-06", "run", [])

    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["run_id"] == "run"
    assert not (artifact_dir / "upload_receipt.json.tmp").exists()


def test_enabled_adapters_receive_artifact_arguments(tmp_path: Path) -> None:
    adapter = build_adapters()["excel_download"]
    command = adapter.command("2026-06", "run-june", tmp_path, tmp_path / "project_manifest.json")

    assert command is not None
    assert command[command.index("--artifact-dir") + 1] == str(tmp_path / "artifacts")
    assert command[command.index("--project-manifest") + 1] == str(tmp_path / "project_manifest.json")


def test_risk_step_adapters_have_expected_manual_and_cli_contracts(tmp_path: Path) -> None:
    adapters = build_adapters()

    assert adapters["risk_sync"].spec.order == 5
    assert not adapters["risk_sync"].spec.configured
    assert adapters["risk_check"].spec.order == 6
    assert adapters["risk_check"].spec.configured

    command = adapters["risk_check"].command("2026-07", "risk-run", tmp_path)
    assert command is not None
    assert command[command.index("--reporting-month") + 1] == "2026-07"
    assert command[command.index("--run-id") + 1] == "risk-run"


def test_repository_audits_step3_source_configuration(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    run_id = repo.create_run(
        "2026-06",
        "snowflake_upload",
        "tester",
        step3_source={"mode": "selected_folder", "folder": str(tmp_path), "reason": "Recovery"},
    )
    assert json.loads(repo.get_run(run_id)["source_json"])["mode"] == "selected_folder"


def test_repository_returns_successful_step_prerequisite(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    run_id = repo.create_run("2026-06", "submission_check", "tester")
    repo.claim_next()
    repo.upsert_step(run_id, "submission_check", StepStatus.SUCCEEDED)
    repo.set_status(run_id, RunStatus.SUCCEEDED, finished=True)

    result = repo.latest_successful_step_run("2026-06", "submission_check")
    assert result is not None
    assert result["id"] == run_id


def test_step2_selection_writes_filtered_immutable_manifest(tmp_path: Path) -> None:
    source_manifest = tmp_path / "step-1" / "project_manifest.json"
    destination_manifest = tmp_path / "step-2" / "project_manifest.json"
    assert write_project_manifest(
        source_manifest,
        "2026-06",
        "step-1-run",
        [
            {"project_code": "1010", "status": "completed"},
            {"project_code": "1014", "status": "completed"},
        ],
    )

    write_download_project_manifest(
        source_manifest,
        destination_manifest,
        "2026-06",
        "step-2-run",
        ["1014"],
    )

    payload = json.loads(destination_manifest.read_text(encoding="utf-8"))
    assert payload["run_id"] == "step-2-run"
    assert payload["source_step_1_run_id"] == "step-1-run"
    assert payload["selection"] == ["1014"]
    assert payload["projects"] == [{"project_code": "1014", "status": "completed"}]


def test_step2_selection_rejects_projects_not_confirmed_by_step1(tmp_path: Path) -> None:
    source_manifest = tmp_path / "step-1" / "project_manifest.json"
    assert write_project_manifest(
        source_manifest,
        "2026-06",
        "step-1-run",
        [{"project_code": "1010", "status": "completed"}],
    )

    with pytest.raises(ProjectSelectionError, match="not confirmed ready"):
        write_download_project_manifest(
            source_manifest,
            tmp_path / "step-2" / "project_manifest.json",
            "2026-06",
            "step-2-run",
            ["9999"],
        )


def test_pasted_project_codes_are_normalized_and_deduplicated() -> None:
    assert parse_pasted_project_codes("1010, 1014\n1010; 1016") == ["1010", "1014", "1016"]


def test_repository_audits_step2_project_selection(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    run_id = repo.create_run(
        "2026-06",
        "excel_download",
        "tester",
        selected_project_codes=["1010", "1014", "1010"],
    )

    assert json.loads(repo.get_run(run_id)["selection_json"]) == ["1010", "1014"]
