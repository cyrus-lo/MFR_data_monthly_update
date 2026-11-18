from pathlib import Path
import subprocess

from pipeline_core import worker_launcher
from pipeline_core.worker_health import WorkerHealth


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_worker_launcher_skips_start_when_worker_is_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        worker_launcher,
        "worker_health",
        lambda _root: WorkerHealth("running", "Shared worker is running."),
    )
    monkeypatch.setattr(
        worker_launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected process start")),
    )

    result = worker_launcher.ensure_worker_running(tmp_path)

    assert result.running
    assert not result.started
    assert not result.used_bypass


def test_worker_launcher_starts_normally_without_policy_bypass(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "MFR-Worker.ps1").write_text("", encoding="utf-8")
    monkeypatch.setattr(worker_launcher, "_is_windows", lambda: True)
    health_states = iter((
        WorkerHealth("stopped", "Shared worker is stopped."),
        WorkerHealth("running", "Shared worker is running."),
    ))
    monkeypatch.setattr(worker_launcher, "worker_health", lambda _root: next(health_states))
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return _completed(0)

    monkeypatch.setattr(worker_launcher.subprocess, "run", fake_run)

    result = worker_launcher.ensure_worker_running(tmp_path)

    assert result.running and result.started
    assert not result.used_bypass
    assert len(commands) == 1
    assert "-ExecutionPolicy" not in commands[0]
    assert "Set-ExecutionPolicy" not in commands[0]


def test_worker_launcher_retries_only_after_execution_policy_denial(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "MFR-Worker.ps1").write_text("", encoding="utf-8")
    monkeypatch.setattr(worker_launcher, "_is_windows", lambda: True)
    health_states = iter((
        WorkerHealth("stopped", "Shared worker is stopped."),
        WorkerHealth("running", "Shared worker is running."),
    ))
    monkeypatch.setattr(worker_launcher, "worker_health", lambda _root: next(health_states))
    commands: list[list[str]] = []
    responses = iter((
        _completed(1, stderr="running scripts is disabled on this system (PSSecurityException)"),
        _completed(0),
    ))

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return next(responses)

    monkeypatch.setattr(worker_launcher.subprocess, "run", fake_run)

    result = worker_launcher.ensure_worker_running(tmp_path)

    assert result.running and result.started and result.used_bypass
    assert len(commands) == 2
    assert "-ExecutionPolicy" not in commands[0]
    assert commands[1][commands[1].index("-ExecutionPolicy") + 1] == "Bypass"
    assert "Set-ExecutionPolicy" not in commands[1]


def test_worker_launcher_does_not_retry_other_powershell_errors(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "MFR-Worker.ps1").write_text("", encoding="utf-8")
    monkeypatch.setattr(worker_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(
        worker_launcher,
        "worker_health",
        lambda _root: WorkerHealth("stopped", "Shared worker is stopped."),
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return _completed(1, stderr="The worker failed for an unrelated reason.")

    monkeypatch.setattr(worker_launcher.subprocess, "run", fake_run)

    result = worker_launcher.ensure_worker_running(tmp_path)

    assert not result.running
    assert not result.used_bypass
    assert len(commands) == 1
    assert "-ExecutionPolicy" not in commands[0]


def test_both_dashboards_start_worker_for_step_one_and_run_all() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative_path in ("web_app/app.py", "web_app_v2/app.py"):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "from pipeline_core.worker_launcher import ensure_worker_running" in source
        assert 'if target in {"submission_check", "ALL"}:' in source