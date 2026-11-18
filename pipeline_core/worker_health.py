"""Shared worker liveness checks for dashboard status messages."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkerHealth:
    state: str
    message: str


def process_is_running(process_id: int) -> bool:
    """Check process liveness without relying on POSIX-only signal semantics."""
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(process_id, 0)
    except (ProcessLookupError, OSError):
        return False
    except PermissionError:
        return True
    return True


def worker_health(root: Path) -> WorkerHealth:
    """Return a safe dashboard-facing worker state from the PID file."""
    pid_file = root / "data" / "mfr_worker.pid"
    if not pid_file.is_file():
        return WorkerHealth("stopped", "Shared worker is stopped. Start it with .\\MFR-Worker.ps1 Start.")
    try:
        process_id = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return WorkerHealth("stale", "Shared worker PID file is invalid. Start it with .\\MFR-Worker.ps1 Start.")
    if process_id <= 0:
        return WorkerHealth("stale", "Shared worker PID file is invalid. Start it with .\\MFR-Worker.ps1 Start.")
    if not process_is_running(process_id):
        return WorkerHealth("stale", "Shared worker PID is stale. Start it with .\\MFR-Worker.ps1 Start.")
    return WorkerHealth("running", f"Shared worker is running (process {process_id}).")
