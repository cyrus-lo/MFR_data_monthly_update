"""Resolve and synchronize the audited Stage 3 source for Stage 6."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HKT = timezone(timedelta(hours=8), "HKT")
APPROVED_PREFIX = "eMFR_"


class Stage6SourceError(ValueError):
    """Raised when the audited Stage 3 source cannot be used safely."""


@dataclass(frozen=True)
class Stage6SourceFile:
    """One manifest-listed eMFR workbook to copy into the Stage 6 input root."""

    project_code: str
    relative_path: str
    source_path: Path
    destination_name: str


@dataclass(frozen=True)
class Stage6SourcePlan:
    """Validated, read-only plan for synchronizing one successful Step 3 source."""

    reporting_month: str
    step3_run_id: str
    receipt_path: Path
    manifest_path: Path
    source_root: Path
    files: tuple[Stage6SourceFile, ...]

    @property
    def file_count(self) -> int:
        return len(self.files)


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage6SourceError(f"Unable to read {description}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Stage6SourceError(f"{description.title()} must contain a JSON object")
    return payload


def _normalized_relative_path(value: object, description: str) -> str:
    relative_path = str(value or "").replace("\\", "/").strip()
    if not relative_path or relative_path.startswith("/") or relative_path == ".":
        raise Stage6SourceError(f"{description} is missing a safe relative path")
    if any(part in {"", ".", ".."} for part in relative_path.split("/")):
        raise Stage6SourceError(f"{description} contains an unsafe relative path")
    return relative_path


def _path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_root(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    configured_root = manifest.get("source_root")
    root = (
        Path(str(configured_root)).expanduser().resolve()
        if configured_root
        else manifest_path.resolve().parent
    )
    if not root.is_dir():
        raise Stage6SourceError(f"Stage 3 source folder does not exist: {root}")
    return root


def _receipt_sources(receipt: dict[str, Any]) -> set[str]:
    raw_files = receipt.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise Stage6SourceError("Step 3 upload receipt has no uploaded files")

    sources: set[str] = set()
    for index, record in enumerate(raw_files, 1):
        if not isinstance(record, dict):
            raise Stage6SourceError(f"Step 3 upload receipt file {index} is invalid")
        status = str(record.get("status", "")).upper()
        if status not in {"UPLOADED", "SKIPPED"}:
            raise Stage6SourceError(
                f"Step 3 upload receipt contains an unsuccessful file status: {status or 'blank'}"
            )
        sources.add(_normalized_relative_path(record.get("source"), f"Step 3 upload receipt file {index}"))
    return sources


def stage6_source_plan(
    root: Path,
    reporting_month: str,
    step3_run_id: str,
    destination: Path,
) -> Stage6SourcePlan:
    """Validate and describe the source recorded by a successful Step 3 receipt."""
    receipt_path = (
        root / "run_logs" / reporting_month / step3_run_id / "artifacts" / "upload_receipt.json"
    ).resolve()
    receipt = _read_json(receipt_path, "Step 3 upload receipt")
    if receipt.get("schema_version") != 1:
        raise Stage6SourceError("Step 3 upload receipt has an unsupported schema")
    if receipt.get("reporting_month") != reporting_month:
        raise Stage6SourceError("Step 3 upload receipt does not match the selected reporting month")
    if str(receipt.get("run_id", "")).strip() != step3_run_id:
        raise Stage6SourceError("Step 3 upload receipt does not match the selected run")

    source_details = receipt.get("source")
    if not isinstance(source_details, dict):
        raise Stage6SourceError("Step 3 upload receipt has no source manifest")
    manifest_value = source_details.get("manifest")
    if not manifest_value:
        raise Stage6SourceError("Step 3 upload receipt has no source manifest path")
    manifest_path = Path(str(manifest_value)).expanduser().resolve()
    if not manifest_path.is_file():
        raise Stage6SourceError(f"Step 3 source manifest does not exist: {manifest_path}")

    manifest = _read_json(manifest_path, "Step 3 source manifest")
    if manifest.get("schema_version") != 1:
        raise Stage6SourceError("Step 3 source manifest has an unsupported schema")
    if manifest.get("reporting_month") != reporting_month:
        raise Stage6SourceError("Step 3 source manifest does not match the selected reporting month")
    manifest_run_id = str(manifest.get("run_id", "")).strip()
    if not manifest_run_id:
        raise Stage6SourceError("Step 3 source manifest has no run ID")

    source_root = _source_root(manifest_path, manifest)
    destination_root = destination.expanduser().resolve()
    if _path_inside(source_root, destination_root) or _path_inside(destination_root, source_root):
        raise Stage6SourceError("Stage 3 source and Stage 6 destination must be different folders")

    receipt_sources = _receipt_sources(receipt)
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise Stage6SourceError("Step 3 source manifest has no files")

    files: list[Stage6SourceFile] = []
    destination_names: set[str] = set()
    for index, record in enumerate(raw_files, 1):
        if not isinstance(record, dict):
            raise Stage6SourceError(f"Step 3 source manifest file {index} is invalid")
        relative_path = _normalized_relative_path(
            record.get("relative_path"), f"Step 3 source manifest file {index}"
        )
        if relative_path not in receipt_sources:
            continue
        source_path = (source_root / relative_path).resolve()
        if not _path_inside(source_path, source_root):
            raise Stage6SourceError(f"Step 3 source manifest file is outside its source root: {relative_path}")
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise Stage6SourceError(f"Stage 3 source workbook is missing or empty: {relative_path}")
        expected_size = record.get("size")
        if expected_size is not None:
            try:
                size_matches = int(expected_size) == source_path.stat().st_size
            except (TypeError, ValueError):
                size_matches = False
            if not size_matches:
                raise Stage6SourceError(f"Stage 3 source workbook size changed: {relative_path}")
        expected_hash = str(record.get("sha256", "")).strip().lower()
        if expected_hash and _sha256_file(source_path) != expected_hash:
            raise Stage6SourceError(f"Stage 3 source workbook checksum changed: {relative_path}")
        if not source_path.name.startswith(APPROVED_PREFIX) or source_path.suffix.casefold() != ".xlsx":
            continue
        if source_path.name in destination_names:
            raise Stage6SourceError(
                f"Multiple Stage 3 source workbooks would have the same destination name: {source_path.name}"
            )
        destination_names.add(source_path.name)
        files.append(
            Stage6SourceFile(
                project_code=str(record.get("project_code") or source_path.parent.name).strip(),
                relative_path=relative_path,
                source_path=source_path,
                destination_name=source_path.name,
            )
        )

    if not files:
        raise Stage6SourceError("Step 3 source contains no uploaded eMFR_*.xlsx workbooks")

    return Stage6SourcePlan(
        reporting_month=reporting_month,
        step3_run_id=step3_run_id,
        receipt_path=receipt_path,
        manifest_path=manifest_path,
        source_root=source_root,
        files=tuple(files),
    )


def risk_workbook_files(root: Path) -> list[Path]:
    """Return direct, non-archived Risk workbooks in the Stage 6 input folder."""
    return sorted(path for path in root.expanduser().resolve().glob("*.xlsx") if path.is_file())


def validate_risk_workbook_folder(root: Path) -> list[Path]:
    """Require exactly one exported Risk workbook for a deterministic comparison."""
    folder = root.expanduser().resolve()
    if not folder.is_dir():
        raise Stage6SourceError(f"Risk workbook folder does not exist: {folder}")
    files = risk_workbook_files(folder)
    if not files:
        raise Stage6SourceError(f"No exported Risk workbook found in: {folder}")
    if len(files) > 1:
        names = ", ".join(path.name for path in files)
        raise Stage6SourceError(f"Multiple exported Risk workbooks found; keep exactly one: {names}")
    return files


def sync_stage6_inputs(
    plan: Stage6SourcePlan,
    destination: Path,
    artifact_dir: Path,
    archive_label: str | None = None,
) -> Path:
    """Archive active MFR workbooks and copy the validated eMFR source set."""
    destination_root = destination.expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    active_files = sorted(path for path in destination_root.glob("*.xlsx") if path.is_file())

    stamp = archive_label or datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
    archive_root = destination_root / "archive"
    archive_dir = archive_root / f"{stamp}_{plan.step3_run_id[:8]}"
    suffix = 1
    while archive_dir.exists():
        archive_dir = archive_root / f"{stamp}_{plan.step3_run_id[:8]}_{suffix}"
        suffix += 1

    moved: list[tuple[Path, Path]] = []
    copied: list[Path] = []
    try:
        if active_files:
            archive_dir.mkdir(parents=True, exist_ok=False)
        for source in active_files:
            archived = archive_dir / source.name
            shutil.move(str(source), str(archived))
            moved.append((source, archived))
        for source_file in plan.files:
            target = destination_root / source_file.destination_name
            shutil.copy2(source_file.source_path, target)
            copied.append(target)
    except (OSError, shutil.Error) as exc:
        for target in copied:
            target.unlink(missing_ok=True)
        for source, archived in reversed(moved):
            if archived.exists():
                shutil.move(str(archived), str(source))
        if archive_dir.exists() and not any(archive_dir.iterdir()):
            archive_dir.rmdir()
        raise Stage6SourceError(f"Unable to synchronize Stage 6 MFR workbooks: {exc}") from exc

    artifact_dir = artifact_dir.expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    audit_path = artifact_dir / "stage6_input_sync.json"
    payload = {
        "schema_version": 1,
        "reporting_month": plan.reporting_month,
        "step3_run_id": plan.step3_run_id,
        "receipt": str(plan.receipt_path),
        "manifest": str(plan.manifest_path),
        "source_root": str(plan.source_root),
        "destination": str(destination_root),
        "archive": str(archive_dir) if moved else "",
        "files": [
            {
                "project_code": source_file.project_code,
                "source": source_file.relative_path,
                "destination": source_file.destination_name,
            }
            for source_file in plan.files
        ],
    }
    temporary = audit_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(audit_path)
    return audit_path
