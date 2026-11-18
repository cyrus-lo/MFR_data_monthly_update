import json
from pathlib import Path

import pytest

from pipeline_core.step3_receipt import Step3ReceiptError, load_step3_upload_receipt


def write_receipt(path: Path, files: list[dict[str, str]], reporting_month: str = "2026-07") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "step3-run-id",
                "reporting_month": reporting_month,
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def test_step3_receipt_returns_deduplicated_successful_project_codes(tmp_path: Path) -> None:
    receipt_path = tmp_path / "upload_receipt.json"
    write_receipt(
        receipt_path,
        [
            {"project_code": "1010", "status": "UPLOADED"},
            {"project_code": "1010", "status": "SKIPPED"},
            {"project_code": "1014", "status": "UPLOADED"},
        ],
    )

    receipt = load_step3_upload_receipt(receipt_path, "2026-07")

    assert receipt.run_id == "step3-run-id"
    assert receipt.project_codes == ("1010", "1014")
    assert receipt.distinct_project_count == 2


def test_step3_receipt_rejects_unsuccessful_or_wrong_month_records(tmp_path: Path) -> None:
    receipt_path = tmp_path / "upload_receipt.json"
    write_receipt(receipt_path, [{"project_code": "1010", "status": "FAILED"}])

    with pytest.raises(Step3ReceiptError, match="unsuccessful"):
        load_step3_upload_receipt(receipt_path, "2026-07")

    write_receipt(receipt_path, [{"project_code": "1010", "status": "UPLOADED"}], "2026-06")
    with pytest.raises(Step3ReceiptError, match="does not match"):
        load_step3_upload_receipt(receipt_path, "2026-07")
