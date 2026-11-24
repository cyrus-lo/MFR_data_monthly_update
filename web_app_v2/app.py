from __future__ import annotations

import calendar
import html
import sqlite3
import sys
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline_core.adapters import build_adapters  # noqa: E402
from pipeline_core.project_selection import (  # noqa: E402
    ProjectSelectionError,
    completed_project_codes,
    normalize_project_codes,
    parse_pasted_project_codes,
    validate_selected_project_codes,
)
from pipeline_core.repository import RunRepository, now_hkt, read_period_flow_snapshot  # noqa: E402
from pipeline_core.step3_receipt import Step3ReceiptError, load_step3_upload_receipt  # noqa: E402
from pipeline_core.step4_readiness import step4_readiness  # noqa: E402
from pipeline_core.step3_sources import (  # noqa: E402
    Step3SourceError,
    choose_folder,
    discover_folder_files,
    latest_preparation_preview,
    upload_source_json,
)
from pipeline_core.risk_check_source import (  # noqa: E402
    Stage6SourceError,
    risk_workbook_files,
    stage6_source_plan,
)
from pipeline_core.worker_health import worker_health  # noqa: E402
from pipeline_core.worker_launcher import ensure_worker_running  # noqa: E402
from web_app_v2.theme import THEMES  # noqa: E402
from web_app_v2.view_models import (  # noqa: E402
    PERIOD_FLOW_STEPS,
    PeriodFlowRow,
    PeriodFlowStepCell,
    PipelineRow,
    active_run_rows,
    build_pipeline_rows,
    format_timestamp,
    resolve_reporting_month,
    build_period_flow_rows,
    status_details,
)


st.set_page_config(page_title="MFR Pipeline Dashboard v2", page_icon="◈", layout="wide")

adapters = build_adapters()
STEP_KEYS = tuple(adapters)
NAVIGATION_KEYS = ("period_flow", "pipelines", "scheduled", "logs", "configuration", "settings")
NAVIGATION_ICONS = {
    "period_flow": "▦",
    "pipelines": "◈",
    "scheduled": "◷",
    "logs": "≡",
    "configuration": "⌘",
    "settings": "⚙",
}


@st.cache_resource
def get_repo() -> RunRepository:
    """Create the shared repository only for functional dashboard views."""
    return RunRepository(ROOT / "data" / "pipeline.db")


if st.session_state.get("v2_interface_style") == "Minimal":
    st.session_state.v2_interface_style = "Default"
if "v2_interface_style" not in st.session_state:
    st.session_state.v2_interface_style = "Default"
if st.session_state.get("v2_applied_interface_style") == "Minimal":
    st.session_state.v2_applied_interface_style = "Default"
if "v2_applied_interface_style" not in st.session_state:
    st.session_state.v2_applied_interface_style = st.session_state.v2_interface_style
if "v2_dialog" not in st.session_state:
    st.session_state.v2_dialog = None
if "v2_period_dialog" not in st.session_state:
    st.session_state.v2_period_dialog = None
if "v2_view" not in st.session_state:
    st.session_state.v2_view = "period_flow"
if st.session_state.v2_view not in NAVIGATION_KEYS:
    st.session_state.v2_view = "period_flow"


def notify(message: str, icon: str = "✅") -> None:
    st.toast(message, icon=icon)


def set_view(view: str) -> None:
    st.session_state.v2_view = view
    st.session_state.v2_period_dialog = None


def clear_period_dialog() -> None:
    st.session_state.v2_period_dialog = None


@st.dialog("Reporting period details", width="large", on_dismiss=clear_period_dialog)
def show_period_run_details_dialog(row: PeriodFlowRow) -> None:
    st.caption(f"Reporting period · {row.month_label} ({row.reporting_month})")
    st.caption("Each step below reflects its latest recorded result for this month; results may come from different runs.")
    st.caption(
        f"Latest run: {row.latest_run.run_id} · {row.latest_run.target} · {row.latest_run.status}. "
        "A run without a step record does not change the matrix status."
    )
    st.dataframe(
        [
            {
                "Workflow step": label,
                "Status": cell.status,
                "Source run": cell.run_id or "—",
                "Run status": cell.run_status or "—",
                "Updated": cell.timestamp or "—",
                "Message": cell.message or cell.detail,
            }
            for (_, _, label), cell in zip(PERIOD_FLOW_STEPS, row.steps)
        ],
        width="stretch",
        hide_index=True,
    )
    st.markdown("**Runs for this reporting month**")
    st.dataframe(
        [
            {
                "Run ID": run.run_id,
                "Target": run.target,
                "Status": run.status,
                "Operator": run.operator or "—",
                "Created": run.created_at or "—",
                "Started": run.started_at or "—",
                "Finished": run.finished_at or "—",
                "Summary": run.summary or "—",
            }
            for run in row.runs
        ],
        width="stretch",
        hide_index=True,
    )
    if st.button("Close", key=f"v2_period_details_close_{row.reporting_month}"):
        clear_period_dialog()
        st.rerun()

@st.dialog("Run workflow · preview", on_dismiss=clear_period_dialog)
def show_period_run_preview_dialog(row: PeriodFlowRow) -> None:
    st.caption(f"Selected reporting period · {row.month_label} ({row.reporting_month})")
    st.warning(
        "Preview only. No workflow will be queued, no worker will be started, and the shared database will not be changed."
    )
    st.markdown(f"This row's action is bound to reporting month **{row.reporting_month}**.")
    st.write("The production Run All sequence currently covers Steps 1–3; this preview does not execute it.")
    if st.button("Close", key=f"v2_period_run_close_{row.reporting_month}"):
        clear_period_dialog()
        st.rerun()


def display_period_dialog(rows: tuple[PeriodFlowRow, ...]) -> None:
    dialog = st.session_state.get("v2_period_dialog")
    if not dialog:
        return
    action, reporting_month = dialog
    row = next((candidate for candidate in rows if candidate.reporting_month == reporting_month), None)
    if not row:
        clear_period_dialog()
        return
    if action == "details":
        show_period_run_details_dialog(row)
    elif action == "run_preview":
        show_period_run_preview_dialog(row)
    else:
        clear_period_dialog()

def status_markup(label: str, tone: str, icon: str) -> str:
    spinner = " mfr-spinner" if label in {"Running", "Queued", "Cancelling"} else ""
    return f'<span class="mfr-status {tone}"><span class="{spinner.strip()}">{icon}</span>{label}</span>'


def shared_schedule_label(snapshot: dict[str, object]) -> str:
    schedule = snapshot.get("schedule")
    if not isinstance(schedule, dict) or not schedule.get("enabled"):
        return "—"
    return f"Shared monthly · day {schedule['day_of_month']} · {schedule['time_hkt']} HKT"


def queue_run(
    target: str,
    selected_project_codes: list[str] | None = None,
    step3_source: dict | None = None,
    step4_confirmation: dict | None = None,
    step6_confirmation: dict | None = None,
) -> bool:
    operator = st.session_state.get("v2_operator", "").strip()
    override = bool(st.session_state.get("v2_override", False))
    reason = st.session_state.get("v2_override_reason", "").strip()
    reporting_month = st.session_state.get("v2_reporting_month")
    if not reporting_month:
        st.error("Select a reporting month before starting a run.")
        return False
    if not operator:
        st.error("Enter an operator name before starting a run.")
        return False
    if override and not reason:
        st.error("An override reason is required.")
        return False
    if target in {"submission_check", "ALL"}:
        worker_start = ensure_worker_running(ROOT)
        if not worker_start.running:
            st.error(worker_start.message)
            return False
        if worker_start.started:
            if worker_start.used_bypass:
                st.warning(worker_start.message)
            else:
                st.info(worker_start.message)
    try:
        run_id = get_repo().create_run(
            reporting_month,
            target,
            operator,
            override_reason=reason,
            allow_rerun=override,
            selected_project_codes=selected_project_codes,
            step3_source=step3_source,
            step4_confirmation=step4_confirmation,
            step6_confirmation=step6_confirmation,
        )
    except RuntimeError as exc:
        st.error(str(exc))
        return False
    notify(f"{target.replace('_', ' ').title()} queued · run {run_id[:8]}", "▶️")
    return True


def confirmed_project_codes(reporting_month: str) -> list[str]:
    """Return projects confirmed ready by the latest successful Step 1 run."""
    step_1_run = get_repo().latest_successful_step_run(reporting_month, "submission_check")
    if not step_1_run:
        raise ProjectSelectionError("Run Step 1 successfully for this reporting month before choosing project files")
    manifest = ROOT / "run_logs" / reporting_month / step_1_run["id"] / "artifacts" / "project_manifest.json"
    return completed_project_codes(manifest, reporting_month)


@st.dialog("Choose Step 2 project files")
def show_excel_download_dialog() -> None:
    reporting_month = st.session_state["v2_reporting_month"]
    try:
        eligible_codes = confirmed_project_codes(reporting_month)
    except ProjectSelectionError as exc:
        st.error(str(exc))
        if st.button("Close", use_container_width=True):
            st.session_state.v2_dialog = None
            st.rerun()
    month_name = f"{calendar.month_name[int(reporting_month[5:])]} {reporting_month[:4]}"
    choice = st.selectbox(
        "Which project files do you want to download?",
        (f"All projects confirmed ready for {month_name}", "Choose specific projects"),
        key="v2_excel_download_choice",
    )
    selected: list[str] = []
    invalid: list[str] = []
    if choice == "Choose specific projects":
        st.caption("Choose from the verified list, paste project codes, or use both. Only projects confirmed ready by Step 1 can be downloaded.")
        checked_codes = st.multiselect(
            "Confirmed ready projects",
            eligible_codes,
            key="v2_excel_checked_projects",
            placeholder="Search and select project codes",
        )
        pasted_codes = parse_pasted_project_codes(st.text_area(
            "Paste project codes (optional)",
            key="v2_excel_pasted_projects",
            placeholder="For example: 1010, 1014\n1016",
        ))
        selected, invalid = validate_selected_project_codes(
            normalize_project_codes([*checked_codes, *pasted_codes]), eligible_codes
        )
        if invalid:
            st.error("These project codes are not confirmed ready for this month: " + ", ".join(invalid))
        elif selected:
            st.success(f"{len(selected)} project file(s) will be downloaded: " + ", ".join(selected))
        else:
            st.warning("Choose or paste at least one confirmed project code.")
    else:
        st.info(f"All {len(eligible_codes)} projects confirmed ready by Step 1 will be downloaded.")

    start_label = "Download selected project files" if selected else "Download all confirmed project files"
    can_start = not invalid and (choice != "Choose specific projects" or bool(selected))
    start_column, close_column = st.columns(2)
    with start_column:
        if st.button(start_label, type="primary", use_container_width=True, disabled=not can_start):
            if queue_run("excel_download", selected if choice == "Choose specific projects" else []):
                st.session_state.v2_dialog = None
                st.rerun()
    with close_column:
        if st.button("Cancel", use_container_width=True):
            st.session_state.v2_dialog = None
            st.rerun()


@st.dialog("Pipeline configuration")
def show_config_dialog(row: PipelineRow) -> None:
    st.markdown(f"### {row.order}. {row.name}")
    st.caption("Read-only prototype — pipeline definitions remain fixed in the shared adapter contract.")
    st.text_input("Pipeline key", value=row.key, disabled=True)
    st.text_input("Prerequisites", value=", ".join(row.prerequisites) or "None", disabled=True)
    st.text_input("Readiness", value="Configured" if row.configured else "Not configured", disabled=True)
    if row.key == "snowflake_task":
        readiness = step4_readiness(ROOT)
        health = worker_health(ROOT)
        st.text_input("Step 4 local readiness", value=readiness.message, disabled=True)
        st.text_input("Shared worker", value=health.message, disabled=True)
    st.button("Save changes", disabled=True, use_container_width=True, help="Persistent adapter editing is not implemented.")
    if st.button("Close", use_container_width=True):
        st.session_state.v2_dialog = None
        st.rerun()


@st.dialog("Schedule preview")
def show_schedule_dialog(row: PipelineRow) -> None:
    st.markdown(f"### {row.name}")
    st.warning("Preview only: individual pipeline scheduling is not saved or supported by the current backend.")
    first, second = st.columns(2)
    with first:
        st.selectbox("Frequency", ["Daily", "Weekly", "Monthly", "Cron expression"], key=f"v2_frequency_{row.key}")
    with second:
        st.time_input("Preferred time (HKT)", key=f"v2_schedule_time_{row.key}")
    st.text_input("Cron expression (preview)", placeholder="0 2 * * *", key=f"v2_cron_{row.key}")
    if st.button("Preview schedule", use_container_width=True):
        st.info("Preview generated. No schedule has been saved or changed.")
    if st.button("Close", use_container_width=True):
        st.session_state.v2_dialog = None
        st.rerun()


@st.dialog("Delete pipeline")
def show_delete_dialog(row: PipelineRow) -> None:
    st.error(f"This is a non-destructive prototype. {row.name} and its history will not be removed.")
    confirmation = st.text_input(f"Type {row.key} to acknowledge", key=f"v2_delete_confirm_{row.key}")
    if st.button("Delete pipeline", type="primary", use_container_width=True, disabled=confirmation != row.key):
        st.warning("No pipeline was deleted. Persistent deletion is intentionally unavailable in v2.")
    if st.button("Cancel", use_container_width=True):
        st.session_state.v2_dialog = None
        st.rerun()


@st.dialog("Review Step 2 files before Step 3")
def show_snowflake_upload_dialog() -> None:
    reporting_month = st.session_state["v2_reporting_month"]
    preview = latest_preparation_preview(get_repo(), ROOT, reporting_month)
    if preview.rows:
        st.dataframe(
            [{field: row.get(field, "") for field in ("timestamp_hkt", "filename", "status")} for row in preview.rows],
            use_container_width=True,
            hide_index=True,
        )
    if preview.successful:
        st.success(preview.summary)
        if st.button("Upload latest successful Step 2 files", type="primary", use_container_width=True, key="v2_upload_latest"):
            if queue_run("snowflake_upload"):
                st.session_state.v2_dialog = None
                st.rerun()
        if st.button("Cancel", use_container_width=True, key="v2_cancel_step3_preview"):
            st.session_state.v2_dialog = None
            st.rerun()
        return

    st.warning(preview.summary)
    st.caption("The normal Step 2 manifest upload is unavailable. Select a folder containing approved Excel files as an exception.")
    if st.button("Browse for upload folder", key="v2_browse_upload_folder"):
        selected = choose_folder()
        if selected:
            st.session_state.v2_upload_folder = selected
            st.rerun()
    folder = st.text_input(
        "Upload folder (on the dashboard host)",
        key="v2_upload_folder",
        placeholder="Select Browse or enter a full Windows folder path",
    ).strip()
    if not folder:
        if st.button("Cancel", use_container_width=True, key="v2_cancel_step3_warning"):
            st.session_state.v2_dialog = None
            st.rerun()
        return
    try:
        records, rejected = discover_folder_files(Path(folder))
    except Step3SourceError as exc:
        st.error(str(exc))
        return
    if records:
        st.dataframe(
            [{"timestamp_hkt": row["timestamp_hkt"], "filename": row["filename"], "status": row["status"]} for row in records],
            use_container_width=True,
            hide_index=True,
        )
        st.info(f"{len(records)} approved file(s) are ready from the selected folder.")
    if rejected:
        st.warning("Some files were excluded: " + "; ".join(rejected[:5]))
    reason = st.text_input("Exception reason (optional)", key="v2_step3_folder_reason")
    upload_column, cancel_column = st.columns(2)
    with upload_column:
        if st.button("Upload files from selected folder", type="primary", use_container_width=True, key="v2_upload_selected_folder", disabled=not records):
            source = upload_source_json("selected_folder", folder=folder, reason=reason, files=records)
            if queue_run("snowflake_upload", step3_source=source):
                st.session_state.v2_dialog = None
                st.rerun()
    with cancel_column:
        if st.button("Cancel", use_container_width=True, key="v2_cancel_step3_folder"):
            st.session_state.v2_dialog = None
            st.rerun()


@st.dialog("Confirm Snowflake Stage before Step 4")
def show_snowflake_task_dialog() -> None:
    reporting_month = st.session_state["v2_reporting_month"]
    readiness = step4_readiness(ROOT)
    health = worker_health(ROOT)
    worker_ready = health.state == "running"
    latest_step3 = get_repo().latest_successful_step_run(reporting_month, "snowflake_upload")
    if not latest_step3:
        st.warning(f"No successful Step 3 upload exists for {reporting_month}.")
        st.caption("Run Step 3 successfully for this reporting month before triggering the Snowflake task.")
    else:
        st.success(f"Latest successful Step 3 run: {latest_step3['id'][:8]} · {latest_step3['finished_at']}")
    if readiness.ready:
        st.info(readiness.message)
    else:
        st.error(readiness.message)
    if worker_ready:
        st.caption(health.message)
    else:
        st.warning(health.message + " Start the shared worker before queueing Step 4.")
    latest_step4 = get_repo().latest_step_run(reporting_month, "snowflake_task")
    if latest_step4:
        label, _, _ = status_details(latest_step4.get("step_status"))
        st.caption(f"Latest Step 4 result: {label} · {latest_step4.get('message') or latest_step4.get('summary')}")
        log_path = latest_step4.get("log_path")
        if log_path:
            st.caption(f"Latest Step 4 log: {log_path}")
            receipt = Path(str(log_path)).parent / "artifacts" / "task_execution_receipt.json"
            if receipt.is_file():
                st.caption(f"Latest Step 4 receipt: {receipt}")
    if latest_step3:
        step3_receipt = (
            ROOT / "run_logs" / reporting_month / latest_step3["id"] / "artifacts" / "upload_receipt.json"
        )
        try:
            expected_projects = load_step3_upload_receipt(step3_receipt, reporting_month)
        except Step3ReceiptError:
            st.warning("Expected project count is unavailable because the latest successful Step 3 upload receipt is missing or invalid.")
        else:
            st.info(
                "Expected project count: "
                f"{expected_projects.distinct_project_count} — from the latest successful Step 3 run "
                "for this reporting month; not a live Snowflake table count."
            )
    confirmed = st.checkbox(
        "I confirm that all files from the successful Step 3 upload are present in the Snowflake Stage.",
        key="v2_step4_stage_confirmed",
    )
    run_column, cancel_column = st.columns(2)
    with run_column:
        if st.button(
            "Trigger Snowflake Task",
            type="primary",
            use_container_width=True,
            disabled=not confirmed or not latest_step3 or not readiness.ready or not worker_ready,
            key="v2_trigger_step4",
        ):
            if queue_run("snowflake_task", step4_confirmation={"stage_files_confirmed": True, "confirmed_at_hkt": now_hkt()}):
                st.session_state.v2_dialog = None
                st.rerun()
    with cancel_column:
        if st.button("Cancel", use_container_width=True, key="v2_cancel_step4"):
            st.session_state.v2_dialog = None
            st.rerun()


@st.dialog("Confirm MFR versus Risk comparison")
def show_risk_check_dialog() -> None:
    reporting_month = st.session_state["v2_reporting_month"]
    risk_dir = ROOT / "mfr_risk_check" / "MFR_vs_Risk_calculation" / "Risk_calculation_files"
    latest_step3 = get_repo().latest_successful_step_run(reporting_month, "snowflake_upload")
    source_plan = None

    st.info(
        "This stage will compare MFR data (A) with Risk data (B).\n\n"
        "(A) MFR workbooks\n"
        "Manifest-listed eMFR_*.xlsx workbooks from the latest successful Step 3 upload run "
        "for the selected reporting month.\n\n"
        "(B) Risk workbook\n"
        "Exactly one exported .xlsx workbook downloaded from the server and placed in "
        "Risk_calculation_files.\n"
        "Example: 22_09_2026_15_57_52.xlsx"
    )
    st.error(
        "Action required: Download the latest updated Risk workbook generated by the server, "
        "then manually place exactly one .xlsx file in Risk_calculation_files before running Stage 6."
    )

    if not latest_step3:
        st.error(f"No successful Stage 3 upload exists for {reporting_month}.")
    else:
        st.success(f"Latest successful Stage 3 run: {latest_step3['id'][:8]} · {latest_step3['finished_at']}")
        try:
            source_plan = stage6_source_plan(
                ROOT,
                reporting_month,
                latest_step3["id"],
                ROOT / "mfr_risk_check" / "MFR_vs_Risk_calculation" / "MFR_files",
            )
        except Stage6SourceError as exc:
            st.error(f"MFR source is not ready: {exc}")
        else:
            st.caption(f"Source: {source_plan.source_root}")
            st.caption(f"{source_plan.file_count} eMFR workbook(s) are ready to copy.")

    risk_files = risk_workbook_files(risk_dir) if risk_dir.is_dir() else []
    if len(risk_files) == 1:
        st.success(f"Risk workbook found: {risk_files[0].name}")
    elif not risk_files:
        st.error(f"No exported Risk workbook was found in {risk_dir}.")
    else:
        st.error("Multiple exported Risk workbooks were found. Keep exactly one: " + ", ".join(path.name for path in risk_files))

    confirmed = st.checkbox(
        "I confirm that the source eMFR files may be synchronized and the intended exported Risk workbook is ready.",
        key="v2_step6_confirmed",
    )
    run_column, cancel_column = st.columns(2)
    with run_column:
        if st.button(
            "Run Stage 6",
            type="primary",
            use_container_width=True,
            disabled=not confirmed or source_plan is None or len(risk_files) != 1,
            key="v2_stage6_confirm_run",
        ):
            assert latest_step3 is not None
            if queue_run(
                "risk_check",
                step6_confirmation={
                    "mfr_source_confirmed": True,
                    "risk_workbook_confirmed": True,
                    "step3_run_id": latest_step3["id"],
                    "confirmed_at_hkt": now_hkt(),
                },
            ):
                st.session_state.v2_dialog = None
                st.rerun()
    with cancel_column:
        if st.button("Cancel", use_container_width=True, key="v2_cancel_step6"):
            st.session_state.v2_dialog = None
            st.rerun()


@st.dialog("Confirm Run All")
def show_run_all_dialog() -> None:
    reporting_month = st.session_state["v2_reporting_month"]
    st.warning(f"Run all configured steps for {reporting_month}?")
    st.caption("Run All ends after Step 3. Steps 4–6 remain standalone/manual stages.")
    confirm_column, cancel_column = st.columns(2)
    with confirm_column:
        if st.button("Confirm Run All", type="primary", use_container_width=True, key="v2_confirm_run_all"):
            if queue_run("ALL"):
                st.session_state.v2_dialog = None
                st.rerun()
    with cancel_column:
        if st.button("Cancel", use_container_width=True, key="v2_cancel_run_all"):
            st.session_state.v2_dialog = None
            st.rerun()


def display_dialog(rows: list[PipelineRow]) -> None:
    dialog = st.session_state.get("v2_dialog")
    if not dialog:
        return
    action, key = dialog
    if action == "run_all":
        show_run_all_dialog()
        return
    row = next((candidate for candidate in rows if candidate.key == key), None)
    if not row:
        st.session_state.v2_dialog = None
        return
    if action == "config":
        show_config_dialog(row)
    elif action == "excel_download":
        show_excel_download_dialog()
    elif action == "snowflake_upload":
        show_snowflake_upload_dialog()
    elif action == "snowflake_task":
        show_snowflake_task_dialog()
    elif action == "risk_check":
        show_risk_check_dialog()
    elif action == "schedule":
        show_schedule_dialog(row)
    elif action == "delete":
        show_delete_dialog(row)


def render_controls() -> None:
    decision = resolve_reporting_month(get_repo().dashboard_snapshot(STEP_KEYS), date.today())
    if decision.error:
        st.error(decision.error)
    elif decision.reason:
        st.caption(decision.reason)
    default_month = decision.selected_month or st.session_state.get("v2_reporting_month")
    if "v2_reporting_month" not in st.session_state and default_month:
        st.session_state.v2_reporting_month = default_month
    today = date.today()
    years = list(range(today.year - 2, today.year + 3))
    initial_month_value = st.session_state.get("v2_reporting_month")
    if initial_month_value:
        initial_year, initial_month = (int(part) for part in initial_month_value.split("-"))
        st.session_state.setdefault("v2_reporting_year", initial_year)
        st.session_state.setdefault("v2_reporting_month_number", initial_month)

    def sync_reporting_month() -> None:
        year = st.session_state.get("v2_reporting_year")
        month_number = st.session_state.get("v2_reporting_month_number")
        if year is not None and month_number is not None:
            st.session_state.v2_reporting_month = f"{year}-{month_number:02d}"

    with st.container(key="v2-run-controls", border=True):
        st.markdown('<p class="mfr-run-controls-title">Run parameters</p>', unsafe_allow_html=True)
        st.caption("Shared by both dashboard versions")
        first, second, third, fourth = st.columns([1.1, .95, .95, 2.05])
        with first:
            st.text_input("Operator name", key="v2_operator", placeholder="Required for audit history")
        with second:
            if initial_month_value:
                st.selectbox(
                    "Reporting year",
                    years,
                    key="v2_reporting_year",
                    on_change=sync_reporting_month,
                )
            else:
                st.selectbox(
                    "Reporting year",
                    years,
                    index=None,
                    placeholder="Select year",
                    key="v2_reporting_year",
                    on_change=sync_reporting_month,
                )
        with third:
            if initial_month_value:
                month_number = st.selectbox(
                    "Reporting month", range(1, 13),
                    format_func=lambda value: calendar.month_name[value],
                    key="v2_reporting_month_number",
                    on_change=sync_reporting_month,
                )
            else:
                month_number = st.selectbox(
                    "Reporting month", range(1, 13),
                    index=None,
                    placeholder="Select month",
                    format_func=lambda value: calendar.month_name[value] if value else "Select month",
                    key="v2_reporting_month_number",
                    on_change=sync_reporting_month,
                )
        year = st.session_state.get("v2_reporting_year")
        if year is not None and month_number is not None:
            st.session_state.v2_reporting_month = f"{year}-{month_number:02d}"
        with fourth:
            st.checkbox("Override prerequisites / authorize rerun", key="v2_override")
            if st.session_state.get("v2_override"):
                st.text_input(
                    "Override reason",
                    key="v2_override_reason",
                    placeholder="Override reason · required and audited",
                    label_visibility="collapsed",
                )
        action_column, helper_column = st.columns([1, 4])
        with action_column:
            if st.button("Run all configured steps", type="primary", use_container_width=True, help="Queues the existing fixed MFR pipeline sequence."):
                st.session_state.v2_dialog = ("run_all", "ALL")
        with helper_column:
                st.caption(f"Run All ends after Step 3. Steps 4–6 are standalone/manual. One shared worker may execute only one run at a time.")


def render_pipeline_table(rows: list[PipelineRow], snapshot: dict[str, object]) -> None:
    schedule = shared_schedule_label(snapshot)
    query = st.session_state.get("v2_search", "").strip().lower()
    visible_rows = [
        row for row in rows
        if not query or query in row.name.lower() or query in row.key.lower() or query in row.status.lower()
    ]
    with st.container(key="v2-pipeline-overview", border=True):
        st.markdown('<p class="mfr-panel-title">Pipelines</p>', unsafe_allow_html=True)
        header = st.columns([2.2, 1.25, 1.7, 2.1, 1.8])
        for column, label in zip(header, ["Pipeline Name", "Status", "Last Run", "Next Scheduled Run", "Actions"]):
            with column:
                st.caption(f"**{label}**")
        st.divider()
        if not visible_rows:
            st.info("No pipelines match the current search.")
        for row in visible_rows:
            name_column, status_column, last_column, scheduled_column, actions_column = st.columns([2.2, 1.25, 1.7, 2.1, 1.8])
            with name_column:
                st.markdown(
                    f'<p class="mfr-pipeline-name">{row.name}</p>'
                    f'<p class="mfr-pipeline-detail">Stage {row.order} · {row.key}</p>',
                    unsafe_allow_html=True,
                )
            with status_column:
                st.markdown(status_markup(row.status, row.tone, row.icon), unsafe_allow_html=True)
            with last_column:
                st.write(row.last_run)
            with scheduled_column:
                st.write(schedule)
                st.markdown(
                    f'<span class="mfr-schedule-detail">'
                    f'{"Shared Run All schedule" if schedule != "—" else "No schedule configured"}'
                    f'</span>',
                    unsafe_allow_html=True,
                )
            with actions_column:
                with st.container(key=f"v2-actions-{row.key}"):
                    run_column, config_column, schedule_column, delete_column = st.columns(4)
                    with run_column:
                        if st.button("▶", key=f"v2_run_{row.key}", help=f"Run {row.name}", disabled=not row.configured):
                            if row.key == "excel_download":
                                st.session_state.v2_dialog = ("excel_download", row.key)
                            elif row.key == "snowflake_upload":
                                st.session_state.v2_dialog = ("snowflake_upload", row.key)
                            elif row.key == "snowflake_task":
                                st.session_state.v2_step4_stage_confirmed = False
                                st.session_state.v2_dialog = ("snowflake_task", row.key)
                            elif row.key == "risk_check":
                                st.session_state.v2_step6_confirmed = False
                                st.session_state.v2_dialog = ("risk_check", row.key)
                            else:
                                queue_run(row.key)
                    with config_column:
                        if st.button("⚙", key=f"v2_config_{row.key}", help=f"View {row.name} configuration"):
                            st.session_state.v2_dialog = ("config", row.key)
                    with schedule_column:
                        if st.button("◫", key=f"v2_schedule_{row.key}", help=f"Preview a schedule for {row.name}"):
                            st.session_state.v2_dialog = ("schedule", row.key)
                    with delete_column:
                        if st.button("🗑", key=f"v2_delete_{row.key}", help=f"Open delete confirmation for {row.name}"):
                            st.session_state.v2_dialog = ("delete", row.key)
            st.divider()


def progress_markup(tone: str) -> str:
    progress_tone = {
        "running": "running",
        "queued": "queued",
        "success": "complete",
        "failed": "failed",
        "paused": "queued",
        "idle": "queued",
    }.get(tone, "queued")
    return f'<div class="mfr-progress {progress_tone}"><span></span></div>'


def render_active_runs(snapshot: dict[str, object]) -> None:
    active = active_run_rows(snapshot, adapters)
    with st.container(key="v2-active-runs", border=True):
        st.markdown('<p class="mfr-section-title">Active Runs</p>', unsafe_allow_html=True)
        if not active:
            st.info("No queued or running MFR pipeline executions.")
            return
        columns = st.columns([1, 2, 1.2, 2])
        for column, label in zip(columns, ["ID", "Name", "Status", "Progression"]):
            with column:
                st.caption(f"**{label}**")
        st.divider()
        for run in active:
            id_column, name_column, status_column, progress_column = st.columns([1, 2, 1.2, 2])
            with id_column:
                st.write(run["id"])
            with name_column:
                st.write(run["name"])
            with status_column:
                st.markdown(status_markup(run["status"], run["tone"], run["icon"]), unsafe_allow_html=True)
            with progress_column:
                st.markdown(progress_markup(run["tone"]), unsafe_allow_html=True)


def render_system_status(snapshot: dict[str, object]) -> None:
    active_count = len(snapshot["active_runs"])
    total = len(adapters)
    health = worker_health(ROOT)
    with st.container(key="v2-system-status", border=True):
        st.markdown('<p class="mfr-system-title">System Status</p>', unsafe_allow_html=True)
        if active_count:
            st.markdown('<p class="mfr-health">◌ queue activity detected</p>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="mfr-health">✓ queue is clear</p>', unsafe_allow_html=True)
        st.write(f"{total - sum(not adapter.spec.configured for adapter in adapters.values())}/{total} pipeline stages configured")
        if health.state == "running":
            st.success(health.message)
        else:
            st.warning(health.message)


PERIOD_FLOW_COLUMN_WIDTHS = [1.48, 1, 1, 1, 1, 1, 1, 1.05]


def period_step_markup(cell: PeriodFlowStepCell) -> str:
    return (
        '<div class="mfr-period-step-cell">'
        f'<span class="mfr-flow-pill {html.escape(cell.tone, quote=True)}">{html.escape(cell.status)}</span>'
        f'<span class="mfr-flow-detail">{html.escape(cell.detail)}</span>'
        "</div>"
    )


@st.fragment(run_every=10)
def render_period_flow_preview() -> None:
    """Render the real, read-only reporting-period history from the shared database."""
    try:
        snapshot = read_period_flow_snapshot(ROOT / "data" / "pipeline.db")
        all_rows = build_period_flow_rows(snapshot)
    except FileNotFoundError:
        all_rows = ()
        read_error = "The shared pipeline database has not been created yet."
    except sqlite3.Error:
        all_rows = ()
        read_error = "The shared pipeline database could not be read. Check the application logs or database availability."
    else:
        read_error = ""

    with st.container(key="v2-period-flow"):
        st.markdown(
            '<div class="mfr-flow-intro">'
            '<div><p class="mfr-flow-overline">Period overview</p>'
            '<p class="mfr-flow-description">One row per reporting month. Each step shows its latest recorded result and source run.</p></div>'
            '<span class="mfr-preview-label">READ-ONLY HISTORY</span>'
            "</div>",
            unsafe_allow_html=True,
        )

        metrics = (
            ("Reporting periods", len(all_rows)),
            ("Runs in view", sum(len(row.runs) for row in all_rows)),
            ("Active runs", sum(
                run.status in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
                for row in all_rows for run in row.runs
            )),
        )
        metric_columns = st.columns(3, gap="small")
        for column, (label, value) in zip(metric_columns, metrics):
            with column:
                with st.container(border=True):
                    st.metric(label, value)

        st.caption("Read-only shared run history. Per-step results may come from different runs; no workflow is queued from this view.")
        header_columns = st.columns(PERIOD_FLOW_COLUMN_WIDTHS, gap="small")
        with header_columns[0]:
            st.markdown(
                '<div class="mfr-period-heading"><span class="mfr-period-heading-index">REPORTING MONTH</span>'
                '<span class="mfr-period-heading-name">Latest run</span></div>',
                unsafe_allow_html=True,
            )
        for column, (_, step_number, step_name) in zip(header_columns[1:7], PERIOD_FLOW_STEPS):
            with column:
                st.markdown(
                    '<div class="mfr-period-heading">'
                    f'<span class="mfr-period-heading-index">{step_number}</span>'
                    f'<span class="mfr-period-heading-name">{step_name}</span>'
                    "</div>",
                    unsafe_allow_html=True,
                )
        with header_columns[7]:
            st.markdown('<div class="mfr-period-heading"><span class="mfr-period-heading-index">ACTIONS</span></div>', unsafe_allow_html=True)

        with st.container(key="v2-period-grid"):
            if read_error:
                st.warning(read_error)
            elif not all_rows:
                st.info("No reporting-month runs have been recorded yet.")
            for row in all_rows:
                with st.container(key=f"v2-period-row-{row.reporting_month}", border=True):
                    row_columns = st.columns(PERIOD_FLOW_COLUMN_WIDTHS, gap="small")
                    with row_columns[0]:
                        latest_run = row.latest_run
                        latest_label, latest_tone, _ = status_details(latest_run.status)
                        st.markdown(
                            f'<p class="mfr-period-month">{html.escape(row.month_label)}</p>'
                            f'<p class="mfr-period-code">{html.escape(row.reporting_month)}</p>'
                            f'<span class="mfr-flow-pill {html.escape(latest_tone, quote=True)}">{html.escape(latest_label)}</span>'
                            f'<p class="mfr-period-updated">Run {html.escape(latest_run.run_id[:8])}<br>{html.escape(latest_run.target)}</p>'
                            f'<p class="mfr-period-updated">Updated<br>{html.escape(row.updated)}</p>',
                            unsafe_allow_html=True,
                        )
                    for column, cell in zip(row_columns[1:7], row.steps):
                        with column:
                            st.markdown(period_step_markup(cell), unsafe_allow_html=True)
                    with row_columns[7]:
                        if st.button(
                            "View details",
                            key=f"v2_period_view_{row.reporting_month}",
                            help="View the latest recorded result for each step and all runs for this reporting month.",
                            width="stretch",
                        ):
                            st.session_state.v2_period_dialog = ("details", row.reporting_month)
                        if st.button(
                            "Run",
                            key=f"v2_period_run_{row.reporting_month}",
                            help=f"Preview only. No workflow will be queued for {row.reporting_month}.",
                            type="primary",
                            width="stretch",
                        ):
                            st.session_state.v2_period_dialog = ("run_preview", row.reporting_month)

        st.caption("Step 5 is a not-configured template. Steps without a recorded result are shown as Not started.")
    display_period_dialog(all_rows)


def render_logs() -> None:
    st.markdown("#### Activity Logs")
    history = get_repo().list_recent_step_runs(100)
    if history:
        st.dataframe(history, use_container_width=True, hide_index=True)
    else:
        st.info("No step execution records exist yet.")


def render_configuration() -> None:
    st.markdown("#### Configuration")
    st.info("Pipeline definitions are currently fixed Python adapters. This v2 screen is read-only.")
    records = [
        {
            "stage": adapter.spec.order,
            "pipeline": adapter.spec.name,
            "key": key,
            "configured": adapter.spec.configured and adapter.spec.script is not None and adapter.spec.script.exists(),
            "prerequisites": ", ".join(adapter.spec.prerequisites) or "None",
        }
        for key, adapter in sorted(adapters.items(), key=lambda item: item[1].spec.order)
    ]
    st.dataframe(records, use_container_width=True, hide_index=True)
    readiness = step4_readiness(ROOT)
    level = st.success if readiness.ready else st.warning
    level("Step 4 local readiness: " + readiness.message)


def render_settings(snapshot: dict[str, object]) -> None:
    st.markdown("#### Settings")
    st.caption("Settings › Themes › Color Theme")
    with st.container(key="v2-theme-settings"):
        with st.expander("Themes", expanded=True):
            st.selectbox(
                "Color Theme",
                list(THEMES),
                key="v2_interface_style",
                help="Changes the v2 visual appearance only. Pipeline execution and shared data do not change.",
            )
            if st.session_state.v2_interface_style != st.session_state.v2_applied_interface_style:
                st.session_state.v2_applied_interface_style = st.session_state.v2_interface_style
                st.rerun(scope="app")
            selected = THEMES[st.session_state.v2_interface_style]
            st.success(f"Current color theme: {st.session_state.v2_interface_style}")
            st.caption(selected.product_badge)
            st.caption("Default uses navy and white. Snowflake and Databricks are original visual-style treatments, not product integrations.")

    st.markdown("#### Workflow settings")
    schedule = snapshot.get("schedule")
    st.info("The existing backend supports one shared monthly Run All schedule. Individual schedule buttons in v2 are preview-only.")
    if schedule:
        st.dataframe([schedule], use_container_width=True, hide_index=True)


def render_scheduled(snapshot: dict[str, object]) -> None:
    st.markdown("#### Scheduled Tasks")
    st.caption("Scheduled Tasks")
    st.info("The current backend supports one shared monthly Run All schedule. Individual pipeline schedules are preview-only.")
    schedule = snapshot.get("schedule")
    if not schedule:
        st.warning("No shared monthly schedule is configured.")
        return

    enabled = bool(schedule.get("enabled")) if isinstance(schedule, dict) else False
    status = "Enabled" if enabled else "Paused"
    st.markdown(f"**Shared Run All schedule · {status}**")
    st.dataframe([schedule], use_container_width=True, hide_index=True)
    st.caption("The schedule runs the configured monthly pipeline using Hong Kong time. Edit controls are available in the shared v1 scheduler.")


@st.fragment(run_every=10)
def render_live_page(view: str) -> None:
    if view == "pipelines":
        render_controls()
    selected_month = st.session_state.get("v2_reporting_month")
    snapshot = get_repo().dashboard_snapshot(STEP_KEYS, reporting_month=selected_month)
    if selected_month:
        snapshot["reporting_month"] = selected_month
    rows = build_pipeline_rows(adapters, snapshot)
    if view == "logs":
        render_logs()
    elif view == "configuration":
        render_configuration()
    elif view == "scheduled":
        render_scheduled(snapshot)
    elif view == "settings":
        render_settings(snapshot)
    else:
        render_pipeline_table(rows, snapshot)
        left_column, right_column = st.columns([3.2, 1.1])
        with left_column:
            render_active_runs(snapshot)
        with right_column:
            render_system_status(snapshot)
    display_dialog(rows)


theme = THEMES[st.session_state.v2_interface_style]
st.markdown(theme.css, unsafe_allow_html=True)

is_period_flow = st.session_state.v2_view == "period_flow"
left_sidebar, content = st.columns([0.75, 4.25] if is_period_flow else [1, 4.25], gap="small")
with left_sidebar:
    with st.container(key="v2-sidebar"):
        st.markdown(f'<div class="mfr-brand"><span class="mfr-brand-mark">◈</span><span>{"MFR Workflows" if theme.key == "databricks" else "MFR Pipelines"}</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="mfr-nav-heading">Navigation</div>', unsafe_allow_html=True)
        for view, label in zip(NAVIGATION_KEYS, theme.sidebar_labels):
            active = st.session_state.v2_view == view
            icon = NAVIGATION_ICONS[view]
            st.button(
                f"{icon}  {label}",
                key=f"v2_nav_{view}",
                use_container_width=True,
                type="primary" if active else "secondary",
                on_click=set_view,
                args=(view,),
            )
        st.markdown('<div class="mfr-sidebar-footer">v2 · shared data with v1</div>', unsafe_allow_html=True)

with content:
    if is_period_flow:
        title_column, badge_column = st.columns([2.85, .05])
        new_column = search_column = None
    else:
        title_column, badge_column, new_column, search_column = st.columns([2.5, .05, 1.15, 1.35])
    with title_column:
        page_title = "Period flow" if is_period_flow else theme.title
        page_subtitle = (
            "Monthly reporting status across the six MFR workflow steps"
            if is_period_flow
            else "Run and monitor MFR data workflows"
        )
        st.markdown(
            f'<h1 class="mfr-heading">{page_title}</h1><p class="mfr-subheading">{page_subtitle}</p>',
            unsafe_allow_html=True,
        )
    with badge_column:
        st.markdown(f'<span class="mfr-badge"><span class="mfr-badge-mark">◈</span>{theme.product_badge}</span>', unsafe_allow_html=True)
    if new_column is not None:
        with new_column:
            if st.button("＋ New Pipeline", key="v2_new_pipeline", use_container_width=True):
                notify("Pipeline creation is a visual prototype; the four MFR stages remain fixed.", "ℹ️")
    if search_column is not None:
        with search_column:
            st.text_input("Search", key="v2_search", placeholder="Search pipeline")
    if is_period_flow:
        render_period_flow_preview()
    else:
        render_live_page(st.session_state.v2_view)
