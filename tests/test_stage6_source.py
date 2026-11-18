import hashlib
import json
from pathlib import Path

import pytest

from pipeline_core.risk_check_source import (
    Stage6SourceError,
    stage6_source_plan,
    sync_stage6_inputs,
    validate_risk_workbook_folder,
)


def write_source_contract(
    root: Path,
    reporting_month: str = "2026-07",
    run_id: str = "step3-run",
    selected_folder: bool = False,
) -> tuple[Path, Path]:
    source_root = root / "selected-source" if selected_folder else root / "step2-artifacts"
    workbook = source_root / ("1010" if selected_folder else "prepared/1010") / "eMFR_1010_2026-07.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"eMFR workbook")
    manifest = source_root / "file_manifest.json"
    manifest_payload = {
        "schema_version": 1,
        "reporting_month": reporting_month,
        "run_id": "step2-run",
        "files": [{
            "project_code": "1010",
            "relative_path": workbook.relative_to(source_root).as_posix(),
            "size": workbook.stat().st_size,
            "sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
        }],
    }
    if selected_folder:
        manifest_payload["source_root"] = str(source_root)
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    receipt = root / "run_logs" / reporting_month / run_id / "artifacts" / "upload_receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "reporting_month": reporting_month,
        "run_id": run_id,
        "source": {"manifest": str(manifest)},
        "files": [{
            "project_code": "1010",
            "source": workbook.relative_to(source_root).as_posix(),
            "status": "UPLOADED",
        }],
    }), encoding="utf-8")
    return workbook, receipt


def test_stage6_source_plan_uses_normal_prepared_manifest(tmp_path: Path) -> None:
    workbook, _ = write_source_contract(tmp_path)
    plan = stage6_source_plan(tmp_path, "2026-07", "step3-run", tmp_path / "MFR_files")

    assert plan.file_count == 1
    assert plan.files[0].source_path == workbook.resolve()
    assert plan.files[0].destination_name == workbook.name


def test_stage6_source_plan_uses_selected_folder_source_root(tmp_path: Path) -> None:
    workbook, _ = write_source_contract(tmp_path, selected_folder=True)
    plan = stage6_source_plan(tmp_path, "2026-07", "step3-run", tmp_path / "MFR_files")

    assert plan.source_root == (tmp_path / "selected-source").resolve()
    assert plan.files[0].source_path == workbook.resolve()


def test_stage6_sync_archives_active_files_and_copies_only_emfr(tmp_path: Path) -> None:
    source_workbook, _ = write_source_contract(tmp_path)
    destination = tmp_path / "MFR_files"
    destination.mkdir()
    old_workbook = destination / "eMFR_old.xlsx"
    old_workbook.write_bytes(b"old")
    plan = stage6_source_plan(tmp_path, "2026-07", "step3-run", destination)

    audit = sync_stage6_inputs(plan, destination, tmp_path / "stage6-artifacts", archive_label="20260820_1655")

    assert (destination / source_workbook.name).read_bytes() == b"eMFR workbook"
    assert not old_workbook.exists()
    archives = list((destination / "archive").glob("20260820_1655_step3-ru*"))
    assert len(archives) == 1
    assert (archives[0] / old_workbook.name).read_bytes() == b"old"
    assert audit.is_file()


def test_stage6_requires_exactly_one_risk_workbook(tmp_path: Path) -> None:
    risk_folder = tmp_path / "Risk_calculation_files"
    risk_folder.mkdir()
    with pytest.raises(Stage6SourceError, match="No exported Risk workbook"):
        validate_risk_workbook_folder(risk_folder)

    (risk_folder / "risk.xlsx").write_bytes(b"risk")
    assert validate_risk_workbook_folder(risk_folder) == [risk_folder / "risk.xlsx"]

    (risk_folder / "risk-2.xlsx").write_bytes(b"risk")
    with pytest.raises(Stage6SourceError, match="Multiple exported Risk workbooks"):
        validate_risk_workbook_folder(risk_folder)
