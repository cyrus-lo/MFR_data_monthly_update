"""Read the safe project-set contract produced by a successful Step 3 upload."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class Step3ReceiptError(ValueError):
    """Raised when a Step 3 upload receipt cannot be used as an audit input."""


@dataclass(frozen=True)
class Step3UploadReceipt:
    """The reporting-month project set that successfully reached Snowflake Stage."""

    path: Path
    run_id: str
    reporting_month: str
    project_codes: tuple[str, ...]

    @property
    def distinct_project_count(self) -> int:
        return len(self.project_codes)


def load_step3_upload_receipt(path: Path, reporting_month: str) -> Step3UploadReceipt:
    """Return distinct successful-upload project codes from a Step 3 receipt."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step3ReceiptError(f"Unable to read Step 3 upload receipt: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise Step3ReceiptError("Step 3 upload receipt has an unsupported schema")
    if payload.get("reporting_month") != reporting_month:
        raise Step3ReceiptError("Step 3 upload receipt does not match this reporting month")
    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        raise Step3ReceiptError("Step 3 upload receipt has no run ID")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise Step3ReceiptError("Step 3 upload receipt has no uploaded files")

    project_codes: set[str] = set()
    for file_record in files:
        if not isinstance(file_record, dict):
            raise Step3ReceiptError("Step 3 upload receipt has an invalid file record")
        status = str(file_record.get("status", "")).upper()
        if status not in {"UPLOADED", "SKIPPED"}:
            raise Step3ReceiptError("Step 3 upload receipt contains an unsuccessful upload")
        project_code = str(file_record.get("project_code", "")).strip()
        if not project_code:
            raise Step3ReceiptError("Step 3 upload receipt has a file without a project code")
        project_codes.add(project_code)

    if not project_codes:
        raise Step3ReceiptError("Step 3 upload receipt has no distinct project codes")
    return Step3UploadReceipt(
        path=path.resolve(),
        run_id=run_id,
        reporting_month=reporting_month,
        project_codes=tuple(sorted(project_codes)),
    )
