from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HKT = timezone(timedelta(hours=8), "HKT")
PREPARATION_LOG_FIELDS = ("reporting_month", "run_id", "timestamp_hkt", "filename", "status", "error_message")
PREPARATION_DISPLAY_FIELDS = ("timestamp_hkt", "filename", "status")
SUCCESS_STATUSES = {"SUCCESS_PREPARED", "SUCCESS_UNCHANGED"}
APPROVED_PREFIXES = ("eMFR_", "Sub_eMFR_")


class Step3SourceError(ValueError):
    """Raised when a Step 3 preview or custom source is invalid."""


@dataclass(frozen=True)
class PreparationPreview:
    reporting_month: str
    step2_run_id: str | None
    log_file: Path | None
    rows: list[dict[str, str]]
    error: str | None

    @property
    def successful(self) -> bool:
        return bool(self.rows) and self.error is None and all(
            row.get("status", "") in SUCCESS_STATUSES for row in self.rows
        )

    @property
    def summary(self) -> str:
        if self.successful:
            return f"All {len(self.rows)} files are successful."
        if self.error:
            return self.error
        failed = sum(row.get("status", "") not in SUCCESS_STATUSES for row in self.rows)
        return f"{failed} file(s) require attention before the normal upload can proceed."


def preparation_timestamp() -> str:
    return datetime.now(HKT).isoformat(timespec="seconds")


def read_preparation_log(log_file: Path, reporting_month: str) -> list[dict[str, str]]:
    try:
        with log_file.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != PREPARATION_LOG_FIELDS:
                raise Step3SourceError("The Step 2 preparation log has an invalid header.")
            rows = [{field: str(row.get(field, "")) for field in PREPARATION_LOG_FIELDS} for row in reader]
    except OSError as exc:
        raise Step3SourceError(f"Unable to read the Step 2 preparation log: {exc}") from exc
    if not rows:
        raise Step3SourceError("The Step 2 preparation log has no file records.")
    for row in rows:
        if row["reporting_month"] != reporting_month:
            raise Step3SourceError("The Step 2 preparation log does not match the selected reporting month.")
        if not row["filename"]:
            raise Step3SourceError("The Step 2 preparation log contains a blank filename.")
    return rows


def latest_preparation_preview(repo: Any, root: Path, reporting_month: str) -> PreparationPreview:
    step2_run = repo.latest_successful_step_run(reporting_month, "excel_download")
    if not step2_run:
        return PreparationPreview(reporting_month, None, None, [], "No successful Step 2 run exists for this reporting month.")
    log_file = root / "run_logs" / reporting_month / step2_run["id"] / "artifacts" / "mfr_preparation_log.csv"
    try:
        rows = read_preparation_log(log_file, reporting_month)
    except Step3SourceError as exc:
        return PreparationPreview(reporting_month, step2_run["id"], log_file, [], str(exc))
    return PreparationPreview(reporting_month, step2_run["id"], log_file, rows, None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_folder_files(folder: Path) -> tuple[list[dict[str, Any]], list[str]]:
    root = folder.expanduser().resolve()
    if not root.is_dir():
        raise Step3SourceError("The selected folder does not exist or is not a directory.")
    records: list[dict[str, Any]] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("*.xlsx")):
        if path.is_symlink():
            rejected.append(f"{path.name}: symbolic links are not allowed")
            continue
        if not path.name.startswith(APPROVED_PREFIXES):
            continue
        try:
            relative_path = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rejected.append(f"{path.name}: path is outside the selected folder")
            continue
        if relative_path in seen:
            rejected.append(f"{relative_path}: duplicate file")
            continue
        seen.add(relative_path)
        if not path.is_file() or path.stat().st_size == 0:
            rejected.append(f"{relative_path}: file is missing or empty")
            continue
        records.append({
            "project_code": path.parent.name,
            "relative_path": relative_path,
            "filename": relative_path,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "timestamp_hkt": preparation_timestamp(),
            "status": "READY",
        })
    return records, rejected


def write_selected_folder_manifest(
    artifact_dir: Path,
    reporting_month: str,
    run_id: str,
    folder: Path,
    records: list[dict[str, Any]],
    reason: str = "",
) -> Path:
    root = folder.expanduser().resolve()
    if not records:
        raise Step3SourceError("The selected folder contains no approved non-empty Excel files.")
    files: list[dict[str, Any]] = []
    for record in records:
        relative_path = str(record.get("relative_path", "")).replace("\\", "/")
        source = root / relative_path
        if not source.is_file() or source.stat().st_size == 0:
            raise Step3SourceError(f"Selected workbook is missing or empty: {relative_path}")
        current_hash = sha256_file(source)
        if current_hash != str(record.get("sha256", "")):
            raise Step3SourceError(f"Selected workbook changed after preview: {relative_path}")
        files.append({
            "project_code": str(record.get("project_code", "")),
            "relative_path": relative_path,
            "size": source.stat().st_size,
            "sha256": current_hash,
        })
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = artifact_dir / "file_manifest.json"
    payload = {
        "schema_version": 1,
        "reporting_month": reporting_month,
        "run_id": run_id,
        "source_mode": "selected_folder",
        "source_folder": str(root),
        "source_root": str(root),
        "reason": reason.strip(),
        "files": files,
    }
    temporary = manifest.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return manifest


def choose_folder() -> str:
    """Open a native folder picker on the Windows machine hosting Streamlit."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select the folder containing MFR Excel files")
        root.destroy()
        return selected
    except Exception:
        return ""


def upload_source_json(mode: str, *, step2_run_id: str = "", folder: str = "", reason: str = "", files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if mode not in {"step2_manifest", "selected_folder"}:
        raise Step3SourceError("Unsupported Step 3 source mode.")
    result: dict[str, Any] = {"mode": mode}
    if step2_run_id:
        result["step2_run_id"] = step2_run_id
    if folder:
        result["folder"] = str(Path(folder).expanduser().resolve())
    if reason.strip():
        result["reason"] = reason.strip()
    if files is not None:
        result["files"] = files
    return result
