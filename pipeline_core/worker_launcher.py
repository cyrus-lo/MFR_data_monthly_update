"""Start the shared worker from a dashboard-triggered initial pipeline run."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .worker_health import worker_health


@dataclass(frozen=True)
class WorkerLaunchResult:
    running: bool
    started: bool
    used_bypass: bool
    message: str


def _is_windows() -> bool:
    return os.name == "nt"


def _is_execution_policy_block(output: str) -> bool:
    normalized = output.casefold()
    policy_messages = (
        "running scripts is disabled on this system",
        "cannot be loaded because running scripts is disabled",
        "execution of scripts is disabled",
        "not digitally signed. you cannot run this script on the current system",
    )
    return any(message in normalized for message in policy_messages) or (
        "pssecurityexception" in normalized and "execution policy" in normalized
    )


def _worker_command(script: Path, use_bypass: bool) -> list[str]:
    command = ["powershell.exe", "-NoProfile"]
    if use_bypass:
        command.extend(("-ExecutionPolicy", "Bypass"))
    command.extend(("-File", str(script), "Start"))
    return command


def ensure_worker_running(root: Path) -> WorkerLaunchResult:
    """Start the worker if needed, retrying only a recognized policy denial.

    The bypass applies only to the PowerShell process used for this one launch;
    no persistent execution-policy setting is modified.
    """
    root = root.resolve()
    health = worker_health(root)
    if health.state == "running":
        return WorkerLaunchResult(True, False, False, health.message)

    if not _is_windows():
        return WorkerLaunchResult(
            False,
            False,
            False,
            f"{health.message} Automatic worker startup requires Windows PowerShell.",
        )

    launcher = root / "MFR-Worker.ps1"
    if not launcher.is_file():
        return WorkerLaunchResult(
            False,
            False,
            False,
            f"Worker launcher not found: {launcher}",
        )

    used_bypass = False
    try:
        completed = subprocess.run(
            _worker_command(launcher, use_bypass=False),
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        if _is_execution_policy_block(output):
            used_bypass = True
            completed = subprocess.run(
                _worker_command(launcher, use_bypass=True),
                cwd=root,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
                check=False,
            )
            output = f"{completed.stdout}\n{completed.stderr}"
    except subprocess.TimeoutExpired:
        return WorkerLaunchResult(
            False,
            False,
            used_bypass,
            "Starting the shared worker timed out. Check data/mfr_worker_error.log.",
        )
    except OSError as exc:
        return WorkerLaunchResult(
            False,
            False,
            used_bypass,
            f"Unable to start PowerShell for the shared worker: {exc}",
        )

    if completed.returncode != 0:
        detail = " ".join(output.split())[-500:]
        if used_bypass:
            message = "PowerShell still blocked worker startup after a process-scoped bypass retry."
        else:
            message = "Unable to start the shared worker."
        if detail:
            message += f" {detail}"
        message += " Check data/mfr_worker_error.log or start the worker manually."
        return WorkerLaunchResult(False, False, used_bypass, message)

    health = worker_health(root)
    if health.state != "running":
        return WorkerLaunchResult(
            False,
            False,
            used_bypass,
            f"PowerShell returned success, but the worker is not running. {health.message}",
        )

    message = (
        "Shared worker started with a process-scoped PowerShell execution-policy bypass."
        if used_bypass
        else "Shared worker started automatically."
    )
    return WorkerLaunchResult(True, True, used_bypass, message)