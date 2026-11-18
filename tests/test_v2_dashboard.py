from datetime import date
from pathlib import Path

from pipeline_core.adapters import build_adapters
from pipeline_core.models import RunStatus, StepStatus
from pipeline_core.repository import RunRepository
from web_app_v2.view_models import build_pipeline_rows, resolve_reporting_month


def test_v2_uses_vertical_button_tabs_and_settings_color_theme() -> None:
    app_file = Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py"
    source = app_file.read_text(encoding="utf-8")

    assert "st.radio(" not in source
    assert 'NAVIGATION_KEYS = ("pipelines", "scheduled", "logs", "configuration", "settings")' in source
    assert 'st.selectbox(\n                "Color Theme"' in source
    assert 'st.session_state.v2_interface_style = "Default"' in source
    assert 'st.session_state.get("v2_interface_style") == "Minimal"' in source
    assert 'key="v2-theme-settings"' in source
    assert '"INTERFACE STYLE"' not in source
    assert 'with st.container(key="v2-sidebar")' in source
    assert 'NAVIGATION_ICONS = {' in source
    assert 'elif view == "scheduled":' in source
    assert 'render_scheduled(snapshot)' in source
    assert 'st-key-v2-sidebar' in (Path(__file__).resolve().parents[1] / "web_app_v2" / "theme.py").read_text(encoding="utf-8")


def test_both_dashboards_preview_step3_before_queueing() -> None:
    root = Path(__file__).resolve().parents[1]
    v1_source = (root / "web_app" / "app.py").read_text(encoding="utf-8")
    v2_source = (root / "web_app_v2" / "app.py").read_text(encoding="utf-8")

    assert "show_step3_upload_dialog" in v1_source
    assert 'st.session_state.v1_step3_dialog = True' in v1_source
    assert "show_snowflake_upload_dialog" in v2_source
    assert 'st.session_state.v2_dialog = ("snowflake_upload", row.key)' in v2_source
    nav_block = v2_source[v2_source.index('for view, label in zip(NAVIGATION_KEYS'):v2_source.index('st.markdown(\'<div class="mfr-sidebar-footer"')]
    assert 'help=' not in nav_block
    theme_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "theme.py").read_text(encoding="utf-8")
    assert 'justify-content: flex-start !important' in theme_source
    assert 'text-align: left !important' in theme_source
    assert 'color-scheme: light !important' in theme_source


def test_v2_step4_resets_confirmation_before_opening_dialog() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")

    dialog_start = app_source.index("def show_snowflake_task_dialog")
    dialog_end = app_source.index("def display_dialog", dialog_start)
    dialog_source = app_source[dialog_start:dialog_end]
    assert "st.session_state.v2_step4_stage_confirmed = False" not in dialog_source

    step4_branch = app_source[app_source.index('elif row.key == "snowflake_task":'):]
    reset_position = step4_branch.index("st.session_state.v2_step4_stage_confirmed = False")
    dialog_position = step4_branch.index('st.session_state.v2_dialog = ("snowflake_task", row.key)')
    assert reset_position < dialog_position


def test_v2_step4_dialog_reports_local_readiness_and_worker_state() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    dialog_start = app_source.index("def show_snowflake_task_dialog")
    dialog_end = app_source.index("def display_dialog", dialog_start)
    dialog_source = app_source[dialog_start:dialog_end]

    assert "step4_readiness(ROOT)" in dialog_source
    assert "worker_health(ROOT)" in dialog_source
    assert 'worker_ready = health.state == "running"' in dialog_source
    assert "not readiness.ready" in dialog_source
    assert "not worker_ready" in dialog_source
    assert "Start the shared worker before queueing Step 4." in dialog_source
    assert "Latest Step 4 result" in dialog_source
    assert "load_step3_upload_receipt" in dialog_source
    assert "not a live Snowflake table count" in dialog_source
    assert dialog_source.index("Expected project count") < dialog_source.index("st.checkbox(")


def test_v2_step4_disables_trigger_when_worker_is_not_running() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    dialog_start = app_source.index("def show_snowflake_task_dialog")
    dialog_end = app_source.index("def display_dialog", dialog_start)
    dialog_source = app_source[dialog_start:dialog_end]

    button_start = dialog_source.index('st.button(\n            "Trigger Snowflake Task"')
    button_end = dialog_source.index("):", button_start)
    button_source = dialog_source[button_start:button_end]
    assert "disabled=not confirmed or not latest_step3 or not readiness.ready or not worker_ready" in button_source


def test_v2_run_all_requires_confirmation_before_queueing() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")

    dialog_start = app_source.index("def show_run_all_dialog")
    dialog_end = app_source.index("def display_dialog", dialog_start)
    dialog_source = app_source[dialog_start:dialog_end]
    assert '@st.dialog("Confirm Run All")' in app_source
    assert 'f"Run all configured steps for {reporting_month}?"' in dialog_source
    assert 'st.button("Confirm Run All"' in dialog_source
    assert 'st.button("Cancel"' in dialog_source
    assert 'if queue_run("ALL"):' in dialog_source

    display_start = app_source.index("def display_dialog")
    controls_start = app_source.index("def render_controls")
    display_source = app_source[display_start:controls_start]
    assert 'if action == "run_all":' in display_source
    assert "show_run_all_dialog()" in display_source

    controls_source = app_source[controls_start:app_source.index("def render_pipeline_table", controls_start)]
    assert 'st.session_state.v2_dialog = ("run_all", "ALL")' in controls_source
    assert controls_source.count('queue_run("ALL")') == 0


def test_worker_launcher_checks_for_an_immediate_exit() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "MFR-Worker.ps1").read_text(encoding="utf-8")

    assert '"-m", "worker.worker_main"' in launcher
    assert "-WorkingDirectory $Root" in launcher
    assert "$process.WaitForExit(500)" in launcher
    assert "Remove-Item $PidFile -Force -ErrorAction SilentlyContinue" in launcher
    assert "exited during startup" in launcher


def test_v2_exposes_manual_risk_steps_with_stage6_confirmation_dialog() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    adapters = build_adapters()
    risk_dialog_start = app_source.index("def show_risk_check_dialog")
    risk_dialog_end = app_source.index('@st.dialog("Confirm Run All")', risk_dialog_start)
    risk_dialog_source = app_source[risk_dialog_start:risk_dialog_end]
    action_alert = (
        'st.error(\n'
        '        "Action required: Download the latest updated Risk workbook generated by the server, "\n'
        '        "then manually place exactly one .xlsx file in Risk_calculation_files before running Stage 6."\n'
        '    )'
    )

    assert not adapters["risk_sync"].spec.configured
    assert adapters["risk_check"].spec.configured
    assert 'elif row.key == "risk_check"' in app_source
    assert "show_risk_check_dialog" in app_source
    assert "This stage will compare MFR data (A) with Risk data (B)." in app_source
    assert "(A) MFR workbooks" in app_source
    assert "latest successful Step 3 upload run" in app_source
    assert "(B) Risk workbook" in app_source
    assert "Exactly one exported .xlsx workbook downloaded from the server" in app_source
    assert "Example: 22_09_2026_15_57_52.xlsx" in app_source
    assert action_alert in risk_dialog_source
    assert risk_dialog_source.index("Example: 22_09_2026_15_57_52.xlsx") < risk_dialog_source.index(action_alert)
    assert risk_dialog_source.index(action_alert) < risk_dialog_source.index("if not latest_step3:")
    assert 'key="v2_stage6_confirm_run"' in app_source
    assert 'key="v2_run_step6"' not in app_source


def test_v2_dark_mode_controls_keep_unicode_actions_and_scoped_styles() -> None:
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    theme_source = (root / "web_app_v2" / "theme.py").read_text(encoding="utf-8")

    assert 'key="v2-run-controls"' in app_source
    assert 'key=f"v2-actions-{row.key}"' in app_source
    assert '"Reporting year"' in app_source
    assert '"Reporting month"' in app_source
    for label in ("▶", "⚙", "◫", "🗑"):
        assert f'st.button("{label}"' in app_source

    assert '.st-key-v2-run-controls [data-baseweb="select"] > div' in theme_source
    assert '[data-baseweb="popover"], [role="listbox"]' in theme_source
    assert '[role="option"]' in theme_source
    assert '"Default": Theme(' in theme_source
    assert '.st-key-v2-theme-settings [data-testid="stExpander"] summary' in theme_source
    assert '.st-key-v2-theme-settings [data-testid="stSelectbox"] [role="group"]' in theme_source
    assert 'div[class*="st-key-v2_run_"] .stButton button' in theme_source
    assert 'div[class*="st-key-v2_delete_"] .stButton button' in theme_source
    assert '"Segoe UI Symbol", "Segoe UI Emoji"' in theme_source


def test_dashboard_snapshot_has_empty_shared_state(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")

    snapshot = repo.dashboard_snapshot(("submission_check", "excel_download"))

    assert snapshot["runs"] == []
    assert snapshot["active_runs"] == []
    assert snapshot["step_runs"] == []
    assert snapshot["schedule"] is not None


def test_dashboard_snapshot_and_v2_rows_prefer_active_status(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    run_id = repo.create_run("2026-07", "submission_check", "V2 tester")
    claimed = repo.claim_next()
    assert claimed is not None
    repo.upsert_step(run_id, "submission_check", StepStatus.RUNNING)

    snapshot = repo.dashboard_snapshot(tuple(build_adapters()))
    rows = {row.key: row for row in build_pipeline_rows(build_adapters(), snapshot)}

    assert snapshot["active_runs"][0]["id"] == run_id
    assert rows["submission_check"].status == "Running"
    assert rows["submission_check"].active_run_id == run_id
    assert rows["excel_download"].status == "Idle"

    repo.set_status(run_id, RunStatus.SUCCEEDED, finished=True)
    repo.upsert_step(run_id, "submission_check", StepStatus.SUCCEEDED)
    completed_rows = {
        row.key: row
        for row in build_pipeline_rows(build_adapters(), repo.dashboard_snapshot(tuple(build_adapters())))
    }
    assert completed_rows["submission_check"].status == "Success"


def test_dashboard_snapshot_filters_history_by_reporting_month(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    june_run = repo.create_run("2026-06", "submission_check", "June tester")
    repo.claim_next()
    repo.upsert_step(june_run, "submission_check", StepStatus.SUCCEEDED)
    repo.set_status(june_run, RunStatus.SUCCEEDED, finished=True)

    july_run = repo.create_run("2026-07", "submission_check", "July tester")
    repo.claim_next()
    repo.upsert_step(july_run, "submission_check", StepStatus.BLOCKED)
    repo.set_status(july_run, RunStatus.BLOCKED, "MFR submissions are not all completed", finished=True)

    snapshot = repo.dashboard_snapshot(("submission_check",), reporting_month="2026-07")

    assert [run["reporting_month"] for run in snapshot["runs"]] == ["2026-07"]
    assert [step["run_id"] for step in snapshot["step_runs"]] == [july_run]


def _completed_prerequisites(month: str) -> list[dict[str, str]]:
    return [
        {"reporting_month": month, "step_key": step, "step_status": "SUCCEEDED"}
        for step in ("submission_check", "excel_download", "snowflake_upload", "snowflake_task")
    ]


def test_reporting_month_selects_unfinished_august_without_september_activity() -> None:
    snapshot = {
        "runs": [{"reporting_month": "2026-08"}],
        "step_runs": _completed_prerequisites("2026-08"),
    }

    decision = resolve_reporting_month(snapshot, date(2026, 9, 22))

    assert decision.selected_month == "2026-08"
    assert decision.error is None


def test_reporting_month_selects_september_after_august_step6_finishes() -> None:
    snapshot = {
        "runs": [{"reporting_month": "2026-08"}],
        "step_runs": _completed_prerequisites("2026-08")
        + [{"reporting_month": "2026-08", "step_key": "risk_check", "step_status": "SUCCEEDED"}],
    }

    decision = resolve_reporting_month(snapshot, date(2026, 9, 22))

    assert decision.selected_month == "2026-09"
    assert decision.error is None


def test_reporting_month_flags_later_activity_before_step6_finishes() -> None:
    snapshot = {
        "runs": [{"reporting_month": "2026-08"}, {"reporting_month": "2026-09"}],
        "step_runs": _completed_prerequisites("2026-08")
        + [{"reporting_month": "2026-09", "step_key": "submission_check", "step_status": "QUEUED"}],
    }

    decision = resolve_reporting_month(snapshot, date(2026, 9, 22))

    assert decision.selected_month is None
    assert decision.error is not None
    assert "2026-08" in decision.error
    assert "2026-09" in decision.error


def test_reporting_month_ignores_step5_activity() -> None:
    snapshot = {
        "runs": [{"reporting_month": "2026-08"}],
        "step_runs": _completed_prerequisites("2026-08")
        + [{"reporting_month": "2026-08", "step_key": "risk_sync", "step_status": "RUNNING"}],
    }

    decision = resolve_reporting_month(snapshot, date(2026, 9, 22))

    assert decision.selected_month == "2026-08"
    assert decision.error is None


def test_reporting_month_requires_steps1_to4_before_step6_selection() -> None:
    snapshot = {
        "runs": [{"reporting_month": "2026-08"}],
        "step_runs": _completed_prerequisites("2026-08")[:-1],
    }

    decision = resolve_reporting_month(snapshot, date(2026, 9, 22))

    assert decision.selected_month is None
    assert decision.error is None
    assert "manually" in decision.reason


def test_reporting_month_requires_manual_selection_without_history() -> None:
    decision = resolve_reporting_month({"runs": [], "step_runs": []}, date(2026, 9, 22))

    assert decision.selected_month is None
    assert decision.error is None
    assert "manually" in decision.reason


def test_reporting_month_does_not_use_today_for_selection() -> None:
    snapshot = {"runs": [{"reporting_month": "2025-12"}], "step_runs": _completed_prerequisites("2025-12")}

    assert resolve_reporting_month(snapshot, date(2026, 1, 5)).selected_month == "2025-12"


def test_v2_reporting_month_changes_are_synced_by_widget_callbacks() -> None:
    source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    controls = source[source.index("def render_controls"):source.index("def render_pipeline_table")]

    assert controls.count("on_change=sync_reporting_month") == 4
    assert 'key="v2_reporting_month_number"' in controls