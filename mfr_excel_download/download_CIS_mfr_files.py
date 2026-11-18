"""Step 2: download, prepare, and manifest CIS MFR Excel workbooks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import shutil
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests
from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA1
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Util import Counter
from dotenv import dotenv_values

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_URL_TEMPLATE = (
    "https://cis.chunwo.com:8083/reports/service/api/FinanceReportApi/"
    "GetMFRReportFile?ProjectID={project_id}&ReportMonth={reporting_month}"
)
PROJECT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
KEY_LEN = {1: 16, 2: 24, 3: 32}
SALT_LEN = {1: 8, 2: 12, 3: 16}
HKT = timezone(timedelta(hours=8), "HKT")
PREPARATION_LOG_FIELDS = (
    "reporting_month",
    "run_id",
    "timestamp_hkt",
    "filename",
    "status",
    "error_message",
)


class Step2Error(RuntimeError):
    """Raised when a download or workbook preparation cannot complete safely."""


@dataclass(frozen=True)
class Project:
    code: str


@dataclass(frozen=True)
class DownloadConfig:
    url_template: str
    timeout_seconds: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reporting-month", required=True, help="YYYY-MM")
    parser.add_argument("--run-id", required=True, help="pipeline run identifier")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--project-manifest", required=True, type=Path)
    return parser.parse_args()


def validate_reporting_month(reporting_month: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reporting_month):
        raise Step2Error("--reporting-month must use YYYY-MM")


def load_config() -> DownloadConfig:
    values = dotenv_values(SCRIPT_DIR / ".env")
    template = values.get("CIS_MFR_REPORT_URL_TEMPLATE", DEFAULT_URL_TEMPLATE)
    try:
        timeout_seconds = int(values.get("CIS_REQUEST_TIMEOUT_SECONDS", "120"))
    except ValueError as exc:
        raise Step2Error("CIS_REQUEST_TIMEOUT_SECONDS must be a whole number") from exc
    if not template or "{project_id}" not in template or "{reporting_month}" not in template:
        raise Step2Error("CIS_MFR_REPORT_URL_TEMPLATE must include {project_id} and {reporting_month}")
    if not 1 <= timeout_seconds <= 900:
        raise Step2Error("CIS_REQUEST_TIMEOUT_SECONDS must be between 1 and 900")
    return DownloadConfig(template, timeout_seconds)


def load_projects(manifest_file: Path, reporting_month: str) -> list[Project]:
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step2Error(f"Unable to read project manifest: {exc}") from exc
    if payload.get("schema_version") != 1 or payload.get("reporting_month") != reporting_month:
        raise Step2Error("Project manifest does not match this reporting month")
    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise Step2Error("Project manifest has no projects")

    projects: list[Project] = []
    seen: set[str] = set()
    for raw_project in raw_projects:
        if not isinstance(raw_project, dict):
            raise Step2Error("Project manifest contains an invalid project entry")
        code = str(raw_project.get("project_code", "")).strip()
        status = str(raw_project.get("status", "")).strip().casefold()
        if not PROJECT_CODE_PATTERN.fullmatch(code) or status != "completed":
            raise Step2Error("Project manifest contains an invalid or incomplete project")
        if code in seen:
            raise Step2Error(f"Project manifest contains duplicate project code: {code}")
        seen.add(code)
        projects.append(Project(code))
    return projects


def normalized_project_id(code: str) -> str:
    """Use CIS's four-digit numeric identifier while preserving non-numeric codes."""
    return code.zfill(4) if code.isdigit() else code


def safe_target(directory: Path, member_name: str) -> Path:
    target = (directory / member_name).resolve()
    root = directory.resolve()
    if target != root and root not in target.parents:
        raise Step2Error(f"Archive member escapes its destination: {member_name}")
    return target


def _get_aes_info(extra: bytes) -> tuple[int, int]:
    offset = 0
    while offset <= len(extra) - 4:
        header_id, size = struct.unpack_from("<HH", extra, offset)
        if header_id == 0x9901:
            if size < 7 or offset + 4 + size > len(extra):
                raise Step2Error("Invalid AES archive metadata")
            return extra[offset + 8], struct.unpack_from("<H", extra, offset + 9)[0]
        offset += 4 + size
    raise Step2Error("Encrypted archive is missing AES metadata")


def decrypt_winzip_aes(zip_path: Path, extract_dir: Path, password: bytes) -> None:
    """Safely extract WinZip AES entries and verify their authentication code."""
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            target = safe_target(extract_dir, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.compress_type != 99:
                with archive.open(info, "r") as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                continue

            strength, actual_compression = _get_aes_info(info.extra)
            if strength not in KEY_LEN:
                raise Step2Error("Unsupported AES archive strength")
            key_length, salt_length = KEY_LEN[strength], SALT_LEN[strength]
            archive.fp.seek(info.header_offset + 26)
            filename_length, extra_length = struct.unpack("<HH", archive.fp.read(4))
            archive.fp.seek(filename_length + extra_length, 1)
            raw = archive.fp.read(info.compress_size)
            if len(raw) < salt_length + 12:
                raise Step2Error(f"Encrypted archive entry is truncated: {info.filename}")
            salt, password_verify = raw[:salt_length], raw[salt_length:salt_length + 2]
            encrypted, authentication = raw[salt_length + 2:-10], raw[-10:]
            derived = PBKDF2(password, salt, dkLen=(2 * key_length) + 2, count=1000)
            encryption_key, authentication_key = derived[:key_length], derived[key_length:2 * key_length]
            if not hmac.compare_digest(password_verify, derived[-2:]):
                raise Step2Error(f"Incorrect password for encrypted archive: {info.filename}")
            calculated_authentication = HMAC.new(authentication_key, encrypted, SHA1).digest()[:10]
            if not hmac.compare_digest(authentication, calculated_authentication):
                raise Step2Error(f"Encrypted archive authentication failed: {info.filename}")
            counter = Counter.new(128, initial_value=1, little_endian=True)
            data = AES.new(encryption_key, AES.MODE_CTR, counter=counter).decrypt(encrypted)
            if actual_compression == zipfile.ZIP_DEFLATED:
                data = zlib.decompress(data, -15)
            elif actual_compression != zipfile.ZIP_STORED:
                raise Step2Error(f"Unsupported nested archive compression: {info.filename}")
            target.write_bytes(data)


def download_zip(
    session: requests.Session,
    url: str,
    destination: Path,
    timeout_seconds: int,
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=timeout_seconds) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        output.write(chunk)
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise Step2Error("CIS returned an empty download")
        with zipfile.ZipFile(temporary, "r"):
            pass
        temporary.replace(destination)
    except (OSError, requests.RequestException, zipfile.BadZipFile) as exc:
        temporary.unlink(missing_ok=True)
        raise Step2Error(f"Download failed for {destination.name}: {exc}") from exc


def extract_outer_archive(archive_path: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                target = safe_target(destination, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile) as exc:
        raise Step2Error(f"Unable to extract {archive_path.name}: {exc}") from exc


def extract_nested_archives(directory: Path) -> None:
    archive_patterns = ("eMFR_xlsx_*.zip", "Sub_eMFR_xlsx_*.zip")
    archive_paths = [path for pattern in archive_patterns for path in directory.rglob(pattern)]
    for archive_path in sorted(set(archive_paths)):
        password = hashlib.sha256(archive_path.stem.encode("utf-8")).hexdigest()[:16].upper().encode("utf-8")
        decrypt_winzip_aes(archive_path, archive_path.parent, password)


def copy_workbooks(project: Project, source_directory: Path, prepared_directory: Path) -> list[Path]:
    e_mfr_candidates = sorted(
        path for path in source_directory.rglob("*.xlsx")
        if path.name.startswith("eMFR_")
    )
    sub_e_mfr_candidates = sorted(
        path for path in source_directory.rglob("*.xlsx")
        if path.name.startswith("Sub_eMFR_")
    )
    if not e_mfr_candidates:
        raise Step2Error(f"No eMFR workbook was extracted for project {project.code}")
    if not sub_e_mfr_candidates:
        raise Step2Error(f"No Sub_eMFR workbook was extracted for project {project.code}")
    candidates = [*e_mfr_candidates, *sub_e_mfr_candidates]
    project_directory = prepared_directory / normalized_project_id(project.code)
    project_directory.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for workbook in candidates:
        destination = project_directory / workbook.name
        if destination.exists():
            raise Step2Error(f"Duplicate workbook name for project {project.code}: {workbook.name}")
        try:
            shutil.copy2(workbook, destination)
        except OSError as exc:
            raise Step2Error(f"Unable to copy {workbook.name}: {exc}") from exc
        copied.append(destination)
    return copied


def preparation_timestamp() -> str:
    """Return the current Hong Kong time in ISO 8601 format."""
    return datetime.now(HKT).isoformat(timespec="seconds")


def write_preparation_log(
    log_file: Path,
    reporting_month: str,
    run_id: str,
    filename: str,
    status: str,
    error_message: str = "",
) -> None:
    """Append one workbook preparation result to the run-scoped CSV log."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not log_file.exists() or log_file.stat().st_size == 0
    with log_file.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=PREPARATION_LOG_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow({
            "reporting_month": reporting_month,
            "run_id": run_id,
            "timestamp_hkt": preparation_timestamp(),
            "filename": filename,
            "status": status,
            "error_message": error_message,
        })
        output.flush()


def _log_filename(path: Path, artifact_dir: Path | None) -> str:
    if artifact_dir is None:
        return path.name
    try:
        return path.resolve().relative_to(artifact_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def freeze_completion_cells(
    workbooks: Iterable[Path],
    *,
    reporting_month: str | None = None,
    run_id: str | None = None,
    log_file: Path | None = None,
    artifact_dir: Path | None = None,
) -> None:
    """Save evaluated C7/C8 values only in eMFR workbooks.

    Sub_eMFR workbooks are intentionally not opened or modified. When logging
    metadata is supplied, each eMFR success or failure is recorded immediately.
    """
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise Step2Error("Windows desktop Excel and pywin32 are required for Step 2") from exc

    excel = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        for workbook_path in workbooks:
            if not workbook_path.name.startswith("eMFR_"):
                continue
            workbook = None
            try:
                workbook = excel.Workbooks.Open(str(workbook_path.resolve()), UpdateLinks=0, ReadOnly=False)
                worksheet = workbook.ActiveSheet
                excel.CalculateFullRebuild()
                for cell_name in ("C7", "C8"):
                    cell = worksheet.Range(cell_name)
                    value = cell.Value
                    cell.Value = value
                workbook.Close(SaveChanges=True)
                workbook = None
                if log_file is not None and reporting_month is not None and run_id is not None:
                    write_preparation_log(
                        log_file,
                        reporting_month,
                        run_id,
                        _log_filename(workbook_path, artifact_dir),
                        "SUCCESS_PREPARED",
                    )
            except Exception as exc:
                if workbook is not None:
                    workbook.Close(SaveChanges=False)
                error = exc if isinstance(exc, Step2Error) else Step2Error(
                    f"Unable to prepare {workbook_path.name}: {exc}"
                )
                if log_file is not None and reporting_month is not None and run_id is not None:
                    write_preparation_log(
                        log_file,
                        reporting_month,
                        run_id,
                        _log_filename(workbook_path, artifact_dir),
                        "FAILED",
                        str(error),
                    )
                if isinstance(exc, Step2Error):
                    raise
                raise Step2Error(f"Unable to prepare {workbook_path.name}: {exc}") from exc
    except Step2Error:
        raise
    except Exception as exc:
        raise Step2Error(f"Unable to start desktop Excel: {exc}") from exc
    finally:
        if excel is not None:
            excel.Quit()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_file_manifest(
    artifact_dir: Path,
    reporting_month: str,
    run_id: str,
    files_by_project: dict[str, list[Path]],
) -> Path:
    records: list[dict[str, object]] = []
    for project_code, files in files_by_project.items():
        for path in files:
            records.append({
                "project_code": project_code,
                "source_zip": f"downloads/{normalized_project_id(project_code)}_{reporting_month}.zip",
                "relative_path": path.relative_to(artifact_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    manifest = artifact_dir / "file_manifest.json"
    temporary = manifest.with_suffix(".json.tmp")
    payload = {
        "schema_version": 1,
        "reporting_month": reporting_month,
        "run_id": run_id,
        "files": records,
    }
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return manifest


def run(args: argparse.Namespace) -> Path:
    validate_reporting_month(args.reporting_month)
    config = load_config()
    projects = load_projects(args.project_manifest, args.reporting_month)
    artifact_dir = args.artifact_dir.resolve()
    downloads_dir = artifact_dir / "downloads"
    extracted_dir = artifact_dir / "extracted"
    prepared_dir = artifact_dir / "prepared"
    preparation_log = artifact_dir / "mfr_preparation_log.csv"
    preparation_log.unlink(missing_ok=True)
    for directory in (downloads_dir, extracted_dir, prepared_dir):
        directory.mkdir(parents=True, exist_ok=True)

    files_by_project: dict[str, list[Path]] = {}
    with requests.Session() as session:
        for project in projects:
            project_id = normalized_project_id(project.code)
            url = config.url_template.format(project_id=project_id, reporting_month=args.reporting_month)
            archive_path = downloads_dir / f"{project_id}_{args.reporting_month}.zip"
            print(f"Downloading project {project.code}.")
            download_zip(session, url, archive_path, config.timeout_seconds)
            project_extract_dir = extracted_dir / project_id
            project_extract_dir.mkdir(parents=True, exist_ok=True)
            extract_outer_archive(archive_path, project_extract_dir)
            extract_nested_archives(project_extract_dir)
            try:
                copied_files = copy_workbooks(project, project_extract_dir, prepared_dir)
            except Exception as exc:
                write_preparation_log(
                    preparation_log,
                    args.reporting_month,
                    args.run_id,
                    f"project/{project_id}",
                    "FAILED",
                    str(exc),
                )
                if isinstance(exc, Step2Error):
                    raise
                raise Step2Error(f"Unable to copy workbooks for project {project.code}: {exc}") from exc
            files_by_project[project.code] = copied_files
            for workbook_path in copied_files:
                if workbook_path.name.startswith("Sub_eMFR_"):
                    write_preparation_log(
                        preparation_log,
                        args.reporting_month,
                        args.run_id,
                        _log_filename(workbook_path, artifact_dir),
                        "SUCCESS_UNCHANGED",
                    )

    prepared_files = [path for files in files_by_project.values() for path in files]
    freeze_completion_cells(
        prepared_files,
        reporting_month=args.reporting_month,
        run_id=args.run_id,
        log_file=preparation_log,
        artifact_dir=artifact_dir,
    )
    return write_file_manifest(artifact_dir, args.reporting_month, args.run_id, files_by_project)


def main() -> int:
    try:
        manifest = run(parse_args())
    except Step2Error as exc:
        print(f"STEP 2 ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"SUCCESS: prepared workbooks and wrote {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
