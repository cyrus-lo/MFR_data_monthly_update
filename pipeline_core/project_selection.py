"""Validation and audit helpers for a business user's Step 2 project choice."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


class ProjectSelectionError(ValueError):
    """Raised when a stored project selection cannot be applied safely."""


def completed_project_codes(manifest_file: Path, reporting_month: str) -> list[str]:
    """Read the ordered list of Step 1-confirmed project codes for one month."""
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectSelectionError(f"Unable to read the Step 1 project list: {exc}") from exc

    if payload.get("schema_version") != 1 or payload.get("reporting_month") != reporting_month:
        raise ProjectSelectionError("The Step 1 project list is not for the selected reporting month")
    projects = payload.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ProjectSelectionError("The Step 1 project list has no projects")

    codes: list[str] = []
    for project in projects:
        if not isinstance(project, dict):
            raise ProjectSelectionError("The Step 1 project list contains an invalid project")
        code = str(project.get("project_code", "")).strip()
        status = str(project.get("status", "")).strip().casefold()
        if not code or status != "completed" or code in codes:
            raise ProjectSelectionError("The Step 1 project list contains an invalid project")
        codes.append(code)
    return codes


def parse_pasted_project_codes(text: str) -> list[str]:
    """Parse comma-, semicolon-, or line-separated business-user input."""
    return normalize_project_codes(re.split(r"[\s,;]+", text.strip()) if text.strip() else [])


def normalize_project_codes(codes: Iterable[object]) -> list[str]:
    """Trim and de-duplicate project codes while preserving operator order."""
    normalized: list[str] = []
    for code in codes:
        value = str(code).strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def validate_selected_project_codes(
    selected_codes: Iterable[object], eligible_codes: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Return normalized choices and any codes not confirmed ready by Step 1."""
    selected = normalize_project_codes(selected_codes)
    eligible = set(eligible_codes)
    return selected, [code for code in selected if code not in eligible]


def selection_from_json(value: str | None) -> list[str]:
    """Read the auditable project choice stored on a pipeline run."""
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProjectSelectionError("The saved project selection is invalid") from exc
    if not isinstance(payload, list):
        raise ProjectSelectionError("The saved project selection is invalid")
    return normalize_project_codes(payload)


def write_download_project_manifest(
    source_manifest: Path,
    destination_manifest: Path,
    reporting_month: str,
    run_id: str,
    selected_codes: Iterable[object],
) -> Path:
    """Write the immutable Step 2 input manifest, optionally narrowed by selection."""
    try:
        payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectSelectionError(f"Unable to read the Step 1 project list: {exc}") from exc

    eligible_codes = completed_project_codes(source_manifest, reporting_month)
    selected, invalid = validate_selected_project_codes(selected_codes, eligible_codes)
    if invalid:
        raise ProjectSelectionError(
            "Selected project(s) are not confirmed ready by Step 1: " + ", ".join(invalid)
        )
    selected_set = set(selected)
    selected_projects = [
        project for project in payload["projects"]
        if not selected_set or str(project["project_code"]).strip() in selected_set
    ]
    if not selected_projects:
        raise ProjectSelectionError("Choose at least one project file to download")

    result = {
        "schema_version": 1,
        "reporting_month": reporting_month,
        "run_id": run_id,
        "source_step_1_run_id": payload.get("run_id", ""),
        "selection": selected or "all_confirmed_projects",
        "projects": selected_projects,
    }
    destination_manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_manifest.with_suffix(destination_manifest.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(destination_manifest)
    return destination_manifest
