"""Step 3: upload the Step 2-approved MFR workbooks to a Snowflake stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

SCRIPT_DIR = Path(__file__).resolve().parent
STAGE_PATTERN = re.compile(r"^@[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,2}(?:/[A-Za-z0-9_./-]+)?$")
ALLOWED_PREFIXES = ("eMFR_", "Sub_eMFR_")


class Step3Error(RuntimeError):
    """Raised when a manifest or Snowflake upload cannot complete safely."""


@dataclass(frozen=True)
class UploadFile:
    project_code: str
    path: Path
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class SnowflakeConfig:
    connection_parameters: dict[str, str]
    emfr_stage_name: str
    sub_emfr_stage_name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reporting-month", required=True, help="YYYY-MM")
    parser.add_argument("--run-id", required=True, help="pipeline run identifier")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--file-manifest", required=True, type=Path)
    return parser.parse_args()


def validate_reporting_month(reporting_month: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reporting_month):
        raise Step3Error("--reporting-month must use YYYY-MM")


def load_config() -> SnowflakeConfig:
    values = dotenv_values(SCRIPT_DIR / ".env")
    required = (
        "SF_USER", "SF_PASSWORD", "SF_ACCOUNT", "SF_WAREHOUSE", "SF_DATABASE", "SF_SCHEMA", "SF_ROLE",
        "SF_EMFR_STAGE_NAME", "SF_SUB_EMFR_STAGE_NAME",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise Step3Error("Missing Snowflake setting(s): " + ", ".join(missing))
    emfr_stage_name = str(values["SF_EMFR_STAGE_NAME"]).strip()
    sub_emfr_stage_name = str(values["SF_SUB_EMFR_STAGE_NAME"]).strip()
    for setting, stage_name in (
        ("SF_EMFR_STAGE_NAME", emfr_stage_name),
        ("SF_SUB_EMFR_STAGE_NAME", sub_emfr_stage_name),
    ):
        if not STAGE_PATTERN.fullmatch(stage_name):
            raise Step3Error(f"{setting} must be a valid @stage identifier")
    if emfr_stage_name == sub_emfr_stage_name:
        raise Step3Error("SF_EMFR_STAGE_NAME and SF_SUB_EMFR_STAGE_NAME must be different")
    return SnowflakeConfig(
        connection_parameters={
            "user": str(values["SF_USER"]),
            "password": str(values["SF_PASSWORD"]),
            "account": str(values["SF_ACCOUNT"]),
            "warehouse": str(values["SF_WAREHOUSE"]),
            "database": str(values["SF_DATABASE"]),
            "schema": str(values["SF_SCHEMA"]),
            "role": str(values["SF_ROLE"]),
        },
        emfr_stage_name=emfr_stage_name,
        sub_emfr_stage_name=sub_emfr_stage_name,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def manifest_artifact_dir(manifest_file: Path) -> Path:
    """Return the Step 2 artifact directory that owns a file manifest."""
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return manifest_file.resolve().parent
    source_root = payload.get("source_root")
    if source_root:
        return Path(str(source_root)).expanduser().resolve()
    return manifest_file.resolve().parent


def load_upload_files(manifest_file: Path, artifact_dir: Path, reporting_month: str) -> list[UploadFile]:
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step3Error(f"Unable to read file manifest: {exc}") from exc
    if payload.get("schema_version") != 1 or payload.get("reporting_month") != reporting_month:
        raise Step3Error("File manifest does not match this reporting month")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise Step3Error("File manifest has no upload files")
    source_artifact_dir = manifest_artifact_dir(manifest_file)

    upload_files: list[UploadFile] = []
    seen: set[str] = set()
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise Step3Error("File manifest contains an invalid file entry")
        relative_path = str(raw_file.get("relative_path", "")).replace("\\", "/")
        source_path = source_artifact_dir / relative_path
        if not relative_path or relative_path in seen or not path_inside(source_path, source_artifact_dir):
            raise Step3Error("File manifest contains an unsafe or duplicate path")
        seen.add(relative_path)
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise Step3Error(f"Manifest workbook is missing or empty: {relative_path}")
        if not source_path.name.startswith(ALLOWED_PREFIXES) or source_path.suffix.casefold() != ".xlsx":
            raise Step3Error(f"Manifest file is not an approved workbook: {relative_path}")
        expected_hash = str(raw_file.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or sha256_file(source_path) != expected_hash:
            raise Step3Error(f"Manifest checksum does not match: {relative_path}")
        upload_files.append(UploadFile(str(raw_file.get("project_code", "")), source_path, relative_path, expected_hash))
    return upload_files


def snowflake_status(rows: list[object]) -> tuple[str, str]:
    """Return the documented PUT status/message columns while tolerating simple mocks."""
    if not rows:
        return "NO_RESULT", "Snowflake returned no PUT result"
    row = rows[0]
    if not isinstance(row, (tuple, list)):
        return "UNKNOWN", str(row)
    if len(row) >= 8:
        return str(row[6]).upper(), str(row[7])
    if len(row) >= 2:
        return str(row[-2]).upper(), str(row[-1])
    return "UNKNOWN", str(row)


def put_command(path: Path, stage_name: str) -> str:
    normalized_path = path.resolve().as_posix().replace("'", "''")
    return f"PUT 'file://{normalized_path}' {stage_name} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"


def destination_stage(upload_file: UploadFile, config: SnowflakeConfig) -> str:
    """Select the required stage from the approved workbook filename prefix."""
    if upload_file.path.name.startswith("eMFR_"):
        return config.emfr_stage_name
    if upload_file.path.name.startswith("Sub_eMFR_"):
        return config.sub_emfr_stage_name
    raise Step3Error(f"No Snowflake stage is configured for workbook: {upload_file.relative_path}")


def write_receipt(
    artifact_dir: Path,
    reporting_month: str,
    run_id: str,
    records: list[dict[str, str]],
    source_details: dict[str, object] | None = None,
) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    receipt = artifact_dir / "upload_receipt.json"
    temporary = receipt.with_suffix(".json.tmp")
    payload: dict[str, object] = {
        "schema_version": 1,
        "reporting_month": reporting_month,
        "run_id": run_id,
        "uploaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": records,
    }
    if source_details:
        payload["source"] = source_details
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    return receipt


def run(args: argparse.Namespace) -> Path:
    validate_reporting_month(args.reporting_month)
    config = load_config()
    artifact_dir = args.artifact_dir.resolve()
    source_artifact_dir = manifest_artifact_dir(args.file_manifest)
    files = load_upload_files(args.file_manifest, source_artifact_dir, args.reporting_month)
    try:
        manifest_payload = json.loads(args.file_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest_payload = {}
    source_details = {
        "mode": manifest_payload.get("source_mode", "step2_manifest"),
        "manifest": str(args.file_manifest.resolve()),
    }
    for field in ("source_folder", "reason"):
        if manifest_payload.get(field):
            source_details[field] = manifest_payload[field]
    try:
        import snowflake.connector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise Step3Error("snowflake-connector-python is required for Step 3") from exc

    connection = None
    cursor = None
    receipts: list[dict[str, str]] = []
    try:
        connection = snowflake.connector.connect(**config.connection_parameters)
        cursor = connection.cursor()
        for upload_file in files:
            stage_name = destination_stage(upload_file, config)
            cursor.execute(put_command(upload_file.path, stage_name))
            status, message = snowflake_status(cursor.fetchall())
            receipts.append({
                "project_code": upload_file.project_code,
                "source": upload_file.relative_path,
                "destination_stage": stage_name,
                "status": status,
                "message": message,
            })
            if status not in {"UPLOADED", "SKIPPED"}:
                raise Step3Error(f"Snowflake did not upload {upload_file.relative_path}: {status}")
    except Step3Error:
        raise
    except Exception as exc:
        raise Step3Error(f"Snowflake upload failed: {exc}") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
    return write_receipt(artifact_dir, args.reporting_month, args.run_id, receipts, source_details)


def main() -> int:
    try:
        receipt = run(parse_args())
    except Step3Error as exc:
        print(f"STEP 3 ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"SUCCESS: uploaded workbook manifest and wrote {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
