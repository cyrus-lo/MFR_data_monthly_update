from datetime import date
import sqlite3
from pathlib import Path

from streamlit.testing.v1 import AppTest

from pipeline_core.adapters import build_adapters
from pipeline_core.models import RunStatus, StepStatus
from pipeline_core.repository import RunRepository, read_period_flow_snapshot
from web_app_v2.view_models import (
    PERIOD_FLOW_STEPS,
    build_period_flow_rows,
    build_pipeline_rows,
    resolve_reporting_month,
)
import pipeline_core.repository as repository_module


class _AppTestRepository:
    def __init__(self, _path: Path) -> None:
        pass

    def dashboard_snapshot(self, _step_keys: tuple[str, ...], reporting_month: str | None = None) -> dict[str, object]:
        del reporting_month
        return {
            "runs": [],
            "active_runs": [],
            "step_runs": [],
            "schedule": {"enabled": False},
        }

    def latest_successful_step_run(self, _reporting_month: str, _step_key: str) -> None:
        return None


def _run_v2_app(monkeypatch, view: str, period_snapshot: dict[str, list[dict[str, object]]] | None = None) -> AppTest:
    monkeypatch.setattr(repository_module, "RunRepository", _AppTestRepository)
    monkeypatch.setattr(
        repository_module,
        "read_period_flow_snapshot",
        lambda _path: period_snapshot or {"runs": [], "step_runs": []},
    )
    app_path = Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    app.session_state["v2_view"] = view
    return app.run()


def test_v2_step3_dialog_handles_missing_step2_run(monkeypatch) -> None:
    monkeypatch.setattr(repository_module, "RunRepository", _AppTestRepository)
    monkeypatch.setattr(
        repository_module,
        "read_period_flow_snapshot",
        lambda _path: {"runs": [], "step_runs": []},
    )
    app_path = Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    app.session_state["v2_view"] = "pipelines"
    app.session_state["v2_reporting_month"] = "2026-11"
    app.session_state["v2_dialog"] = ("snowflake_upload", "snowflake_upload")

    app.run()

    assert not app.exception
    assert any(
        "No successful Step 2 run exists for this reporting month." in element.value
        for element in app.warning
    )
    assert all(button.key != "v2_upload_latest" for button in app.button)


def test_v2_uses_vertical_button_tabs_and_settings_color_theme() -> None:
    app_file = Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py"
    source = app_file.read_text(encoding="utf-8")

    assert "st.radio(" not in source
    assert 'NAVIGATION_KEYS = ("period_flow", "pipelines", "scheduled", "logs", "configuration", "settings")' in source
    assert 'st.session_state.v2_view = "period_flow"' in source
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


def test_period_flow_rows_use_latest_step_status_per_month_and_include_source_runs(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    first_run = repo.create_run("2026-10", "ALL", "October operator")
    assert repo.claim_next() is not None
    repo.upsert_step(first_run, "submission_check", StepStatus.SUCCEEDED, message="First success")
    repo.upsert_step(first_run, "excel_download", StepStatus.SUCCEEDED, message="Downloaded")
    repo.set_status(first_run, RunStatus.SUCCEEDED, "Run All completed", finished=True)

    rerun_id = repo.create_run("2026-10", "submission_check", "Rerun operator")
    assert repo.claim_next() is not None
    repo.upsert_step(rerun_id, "submission_check", StepStatus.FAILED, message="Latest attempt failed")
    repo.set_status(rerun_id, RunStatus.FAILED, "Submission check failed", finished=True)

    rows = build_period_flow_rows(read_period_flow_snapshot(repo.path))

    assert len(rows) == 1
    row = rows[0]
    assert len(PERIOD_FLOW_STEPS) == 6
    assert len(row.steps) == len(PERIOD_FLOW_STEPS)
    assert row.reporting_month == "2026-10"
    assert row.steps[0].status == "Failed"
    assert row.steps[0].run_id == rerun_id
    assert row.steps[0].message == "Latest attempt failed"
    assert row.steps[1].status == "Success"
    assert row.steps[1].run_id == first_run
    assert row.steps[2].status == "Not started"
    assert row.steps[4].status == "Template"
    assert row.steps[4].tone == "template"
    assert {run.run_id for run in row.runs} == {first_run, rerun_id}
    assert row.latest_run.run_id == rerun_id


def test_period_flow_new_queued_standalone_run_does_not_replace_latest_recorded_step(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    completed_id = repo.create_run("2026-10", "snowflake_upload", "Operator")
    assert repo.claim_next() is not None
    repo.upsert_step(completed_id, "snowflake_upload", StepStatus.SUCCEEDED, message="Uploaded")
    repo.set_status(completed_id, RunStatus.SUCCEEDED, "Upload complete", finished=True)

    queued_id = repo.create_run("2026-10", "snowflake_upload", "Operator", allow_rerun=True, override_reason="Retry")

    row = build_period_flow_rows(read_period_flow_snapshot(repo.path))[0]

    assert row.steps[2].status == "Success"
    assert row.steps[2].run_id == completed_id
    assert row.latest_run.run_id == queued_id


def test_period_flow_queued_all_run_does_not_invent_step_statuses(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    queued_id = repo.create_run("2026-10", "ALL", "Operator")

    row = build_period_flow_rows(read_period_flow_snapshot(repo.path))[0]

    assert all(step.status == "Not started" for step in row.steps if step.step_key != "risk_sync")
    assert all(step.run_id is None for step in row.steps if step.step_key != "risk_sync")
    assert row.latest_run.run_id == queued_id
    assert row.latest_run.status == "QUEUED"


def test_period_flow_snapshot_is_read_only_and_not_capped_at_200_runs(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "pipeline.db")
    for index in range(205):
        year, month = divmod(index, 12)
        reporting_month = f"{2000 + year:04d}-{month + 1:02d}"
        run_id = repo.create_run(reporting_month, "submission_check", "History test")
        assert repo.claim_next() is not None
        repo.set_status(run_id, RunStatus.FAILED, "Seeded history", finished=True)

    before_bytes = repo.path.read_bytes()
    snapshot = read_period_flow_snapshot(repo.path)
    after_bytes = repo.path.read_bytes()
    rows = build_period_flow_rows(snapshot)

    assert len(snapshot["runs"]) == 205
    assert len(rows) == 205
    assert before_bytes == after_bytes


def test_period_flow_snapshot_returns_empty_for_uninitialized_database(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    before_bytes = database_path.read_bytes()

    assert read_period_flow_snapshot(database_path) == {"runs": [], "step_runs": []}
    assert database_path.read_bytes() == before_bytes


def test_period_flow_rows_order_mixed_timezone_timestamps_by_instant() -> None:
    snapshot = {
        "runs": [
            {"id": "earlier", "reporting_month": "2026-10", "target": "ALL", "status": "SUCCEEDED",
             "created_at": "2026-10-01T10:00:00+08:00", "_row_order": 2},
            {"id": "later", "reporting_month": "2026-10", "target": "ALL", "status": "RUNNING",
             "created_at": "2026-10-01T02:00:00.500000Z", "_row_order": 1},
        ],
        "step_runs": [
            {"run_id": "earlier", "reporting_month": "2026-10", "step_key": "submission_check",
             "step_status": "SUCCEEDED", "run_status": "SUCCEEDED",
             "run_created_at": "2026-10-01T10:00:00+08:00", "run_order": 2, "step_order": 1,
             "step_finished_at": "2026-10-01T10:00:00+08:00"},
            {"run_id": "later", "reporting_month": "2026-10", "step_key": "submission_check",
             "step_status": "FAILED", "run_status": "FAILED",
             "run_created_at": "2026-10-01T02:00:00.500000Z", "run_order": 1, "step_order": 1,
             "step_finished_at": "2026-10-01T02:00:00.500000Z"},
        ],
    }

    row = build_period_flow_rows(snapshot)[0]

    assert row.latest_run.run_id == "later"
    assert row.steps[0].run_id == "later"
    assert row.steps[0].status == "Failed"
    assert row.updated == "2026-10-01 02:00:00.500000Z"


def test_period_flow_uses_read_only_history_and_does_not_queue_workflows() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    render_start = app_source.index("def render_period_flow_preview")
    render_end = app_source.index("def render_logs", render_start)
    render_source = app_source[render_start:render_end]
    run_dialog_start = app_source.index("def show_period_run_preview_dialog")
    run_dialog_end = app_source.index("def status_markup", run_dialog_start)
    run_dialog_source = app_source[run_dialog_start:run_dialog_end]
    theme_source = (Path(__file__).resolve().parents[1] / "web_app_v2" / "theme.py").read_text(encoding="utf-8")

    assert "read_period_flow_snapshot(" in render_source
    assert "build_period_flow_rows(snapshot)" in render_source
    assert "sample_period_flow_rows" not in app_source
    assert 'st.session_state.v2_period_dialog = ("run_preview", row.reporting_month)' in render_source
    assert 'st.session_state.v2_period_dialog = ("details", row.reporting_month)' in render_source
    assert "display_period_dialog(all_rows)" in render_source
    assert "queue_run(" not in render_source
    assert "ensure_worker_running(" not in render_source
    assert "row.reporting_month" in run_dialog_source
    assert "queue_run(" not in run_dialog_source
    assert "Create period" not in app_source
    assert "v2_create_period" not in theme_source


def test_period_flow_dialogs_survive_fragment_refresh_and_use_stretch_tables() -> None:
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "web_app_v2" / "app.py").read_text(encoding="utf-8")
    theme_source = (root / "web_app_v2" / "theme.py").read_text(encoding="utf-8")

    assert 'st.session_state.v2_period_dialog = None' in app_source
    assert '@st.dialog("Reporting period details", width="large", on_dismiss=clear_period_dialog)' in app_source
    assert '@st.dialog("Run workflow · preview", on_dismiss=clear_period_dialog)' in app_source
    assert 'st.session_state.v2_period_dialog = ("details", row.reporting_month)' in app_source
    assert 'st.session_state.v2_period_dialog = ("run_preview", row.reporting_month)' in app_source
    assert 'display_period_dialog(all_rows)' in app_source
    assert app_source.count('width="stretch",\n        hide_index=True,') == 2
    assert ".st-key-v2_search [data-testid=\"stWidgetLabel\"]" in theme_source


def test_period_flow_renders_all_months_with_missing_step_runs_and_no_search(monkeypatch) -> None:
    snapshot = {
        "runs": [
            {
                "id": "october-run",
                "reporting_month": "2026-10",
                "target": "ALL",
                "status": "SUCCEEDED",
                "operator": "AppTest",
                "created_at": "2026-10-15T10:00:00+08:00",
                "started_at": "2026-10-15T10:01:00+08:00",
                "finished_at": "2026-10-15T10:02:00+08:00",
                "summary": "Completed",
            },
            {
                "id": "november-run",
                "reporting_month": "2026-11",
                "target": "ALL",
                "status": "QUEUED",
                "operator": "AppTest",
                "created_at": "2026-11-15T10:00:00+08:00",
                "started_at": None,
                "finished_at": None,
                "summary": "",
            },
        ],
        "step_runs": [],
    }

    app = _run_v2_app(monkeypatch, "period_flow", snapshot)

    assert not app.exception
    assert {button.key for button in app.button} >= {
        "v2_period_view_2026-10",
        "v2_period_view_2026-11",
    }
    rendered_markdown = "\n".join(element.value for element in app.markdown)
    assert "October 2026" in rendered_markdown
    assert "November 2026" in rendered_markdown
    assert "Not started" in rendered_markdown
    assert "Template" in rendered_markdown
    assert all(widget.key != "v2_period_search" for widget in app.text_input)

    app.button(key="v2_period_view_2026-10").click().run()
    assert app.session_state["v2_period_dialog"] == ("details", "2026-10")
    app.button(key="v2_period_details_close_2026-10").click().run()
    assert app.session_state["v2_period_dialog"] is None

    app.button(key="v2_period_run_2026-10").click().run()
    assert app.session_state["v2_period_dialog"] == ("run_preview", "2026-10")
    assert not app.exception


def test_pipelines_search_still_filters_matching_and_nonmatching_rows(monkeypatch) -> None:
    app = _run_v2_app(monkeypatch, "pipelines")
    assert not app.exception

    app.text_input(key="v2_search").set_value("risk_check").run()
    assert not app.exception
    visible_run_buttons = {
        button.key for button in app.button
        if button.key and button.key.startswith("v2_run_")
    }
    assert visible_run_buttons == {"v2_run_risk_check"}

    app.text_input(key="v2_search").set_value("no-such-pipeline").run()
    assert not app.exception
    visible_run_buttons = {
        button.key for button in app.button
        if button.key and button.key.startswith("v2_run_")
    }
    assert not visible_run_buttons
    assert any("No pipelines match the current search." in element.value for element in app.info)


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