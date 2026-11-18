from __future__ import annotations

import calendar
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
from pipeline_core.repository import RunRepository, now_hkt  # noqa: E402
from pipeline_core.worker_launcher import ensure_worker_running  # noqa: E402
from pipeline_core.step3_sources import (  # noqa: E402
    Step3SourceError,
    choose_folder,
    discover_folder_files,
    latest_preparation_preview,
    upload_source_json,
)

st.set_page_config(page_title="MFR Monthly Pipeline", page_icon="▣", layout="wide")
database_path = ROOT / "data" / "pipeline.db"
repo = RunRepository(database_path)
adapters = build_adapters()

st.title("MFR monthly pipeline")
st.caption("Internal operations dashboard · HKT · one active run at a time")

with st.sidebar:
    st.header("Run parameters")
    operator = st.text_input("Operator name", placeholder="Required for audit history")
    today = date.today()
    year = st.selectbox("Reporting year", range(today.year - 2, today.year + 3), index=2)
    month_number = st.selectbox(
        "Reporting month",
        range(1, 13),
        index=today.month - 1,
        format_func=lambda value: calendar.month_name[value],
    )
    month = f"{year}-{month_number:02d}"
    st.info(f"Scheduled runs use the previous calendar month. Manual runs use {month}.")
    override = st.checkbox(
        "Override prerequisites / authorize rerun",
        help="Requires an audited reason. Use only for recovery or to rerun a successful month.",
    )
    if override:
        st.text_input("Override reason", key="reason_override", placeholder="Required and recorded in history")


@st.fragment(run_every=10)
def render_worker_status() -> None:
    """Show queue state; execution is owned by the standalone worker process."""
    with st.sidebar:
        st.header("Worker status")
        st.info("Use MFR-Worker.ps1 to start the shared execution worker.")
        active = [run for run in repo.list_runs(20) if run["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}]
        st.metric("Active / queued", len(active))
        if active:
            st.warning(f"Run {active[0]['id'][:8]} is {active[0]['status']}")
        else:
            st.success("Worker queue is clear")


render_worker_status()


def queue_run(
    target: str,
    override: bool = False,
    selected_project_codes: list[str] | None = None,
    step3_source: dict | None = None,
    step4_confirmation: dict | None = None,
) -> None:
    if not operator.strip():
        st.error("Enter an operator name before starting a run.")
        return
    reason = ""
    if override:
        reason = st.session_state.get("reason_override", "").strip()
        if not reason:
            st.error("An override reason is required.")
            return
    if target in {"submission_check", "ALL"}:
        worker_start = ensure_worker_running(ROOT)
        if not worker_start.running:
            st.error(worker_start.message)
            return
        if worker_start.started:
            if worker_start.used_bypass:
                st.warning(worker_start.message)
            else:
                st.info(worker_start.message)
    try:
        run_id = repo.create_run(
            month,
            target,
            operator,
            override_reason=reason,
            allow_rerun=override,
            selected_project_codes=selected_project_codes,
            step3_source=step3_source,
            step4_confirmation=step4_confirmation,
        )
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.success(f"Queued {target.replace('_', ' ')} · run {run_id[:8]}")


def confirmed_project_codes() -> list[str]:
    """Return the current month's projects confirmed ready by Step 1."""
    step_1_run = repo.latest_successful_step_run(month, "submission_check")
    if not step_1_run:
        raise ProjectSelectionError("Run Step 1 successfully for this reporting month before choosing project files")
    manifest = ROOT / "run_logs" / month / step_1_run["id"] / "artifacts" / "project_manifest.json"
    return completed_project_codes(manifest, month)


def render_excel_download_selection() -> tuple[list[str] | None, bool]:
    """Return the business user's validated Step 2 project-file choice."""
    month_name = f"{calendar.month_name[month_number]} {year}"
    with st.expander("Step 2 — choose project files", expanded=False):
        try:
            eligible_codes = confirmed_project_codes()
        except ProjectSelectionError as exc:
            st.info(str(exc))
            return None, False

        choice = st.selectbox(
            "Which project files do you want to download?",
            (f"All projects confirmed ready for {month_name}", "Choose specific projects"),
            key="v1_excel_download_choice",
        )
        if choice != "Choose specific projects":
            st.info(f"All {len(eligible_codes)} projects confirmed ready by Step 1 will be downloaded.")
            return None, True

        checked_codes = st.multiselect(
            "Confirmed ready projects",
            eligible_codes,
            key="v1_excel_checked_projects",
            placeholder="Search and select project codes",
        )
        pasted_codes = parse_pasted_project_codes(st.text_area(
            "Paste project codes (optional)",
            key="v1_excel_pasted_projects",
            placeholder="For example: 1010, 1014\n1016",
        ))
        selected, invalid = validate_selected_project_codes(
            normalize_project_codes([*checked_codes, *pasted_codes]), eligible_codes
        )
        if invalid:
            st.error("These project codes are not confirmed ready for this month: " + ", ".join(invalid))
            return None, False
        if not selected:
            st.warning("Choose or paste at least one confirmed project code.")
            return None, False
        st.success(f"{len(selected)} project file(s) will be downloaded: " + ", ".join(selected))
        return selected, True


@st.dialog("Review Step 2 files before Step 3")
def show_step3_upload_dialog() -> None:
    """Preview the latest Step 2 preparation results before queuing Step 3."""
    preview = latest_preparation_preview(repo, ROOT, month)
    if preview.rows:
        st.dataframe(
            [{field: row.get(field, "") for field in ("timestamp_hkt", "filename", "status")} for row in preview.rows],
            use_container_width=True,
            hide_index=True,
        )
    if preview.successful:
        st.success(preview.summary)
        if st.button("Upload latest successful Step 2 files", type="primary", key="v1_upload_latest"):
            queue_run("snowflake_upload", override)
            st.session_state.v1_step3_dialog = False
            st.rerun()
        if st.button("Cancel", key="v1_cancel_step3_preview"):
            st.session_state.v1_step3_dialog = False
            st.rerun()
        return

    st.warning(preview.summary)
    st.caption("The normal Step 2 manifest upload is unavailable. You may select a folder containing approved Excel files as an exception.")
    if st.button("Browse for upload folder", key="v1_browse_upload_folder"):
        selected = choose_folder()
        if selected:
            st.session_state.v1_upload_folder = selected
            st.rerun()
    folder = st.text_input("Upload folder (on the dashboard host)", key="v1_upload_folder", placeholder="Select Browse or enter a full Windows folder path").strip()
    if not folder:
        if st.button("Cancel", key="v1_cancel_step3_warning"):
            st.session_state.v1_step3_dialog = False
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
    reason = st.text_input("Exception reason (optional)", key="v1_step3_folder_reason")
    upload_column, cancel_column = st.columns(2)
    with upload_column:
        if st.button("Upload files from selected folder", type="primary", key="v1_upload_selected_folder", disabled=not records):
            source = upload_source_json("selected_folder", folder=folder, reason=reason, files=records)
            queue_run("snowflake_upload", override, step3_source=source)
            st.session_state.v1_step3_dialog = False
            st.rerun()
    with cancel_column:
        if st.button("Cancel", key="v1_cancel_step3_folder"):
            st.session_state.v1_step3_dialog = False
            st.rerun()


@st.dialog("Confirm Snowflake Stage before Step 4")
def show_step4_task_dialog() -> None:
    latest_step3 = repo.latest_successful_step_run(month, "snowflake_upload")
    if not latest_step3:
        st.warning(f"No successful Step 3 upload exists for {month}.")
        st.caption("Run Step 3 successfully for this reporting month before triggering the Snowflake task.")
    else:
        st.success(f"Latest successful Step 3 run: {latest_step3['id'][:8]} · {latest_step3['finished_at']}")
    confirmed = st.checkbox("I confirm that all files from the successful Step 3 upload are present in the Snowflake Stage.", key="v1_step4_stage_confirmed")
    run_button, cancel_button = st.columns(2)
    with run_button:
        if st.button("Trigger Snowflake Task", type="primary", key="v1_trigger_step4", disabled=not confirmed or not latest_step3):
            queue_run("snowflake_task", step4_confirmation={"stage_files_confirmed": True, "confirmed_at_hkt": now_hkt()})
            st.session_state.v1_step4_dialog = False
            st.session_state.v1_step4_stage_confirmed = False
            st.rerun()
    with cancel_button:
        if st.button("Cancel", key="v1_cancel_step4"):
            st.session_state.v1_step4_dialog = False
            st.session_state.v1_step4_stage_confirmed = False
            st.rerun()


def step_statuses() -> tuple[dict[str, str], dict[str, str]]:
    """Return selected-month history and live-run context for each pipeline step."""
    selected_month_statuses: dict[str, str] = {}
    for row in repo.list_recent_step_runs(200):
        if row["reporting_month"] == month and row["step_key"] not in selected_month_statuses:
            selected_month_statuses[row["step_key"]] = row["step_status"]

    active_statuses: dict[str, str] = {}
    active_runs = [
        run
        for run in repo.list_runs(50)
        if run["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
    ]
    for run in active_runs:
        active_steps = {step["step_key"]: step for step in repo.steps_for(run["id"])}
        for key in adapters:
            if run["target"] not in {"ALL", key}:
                continue
            step = active_steps.get(key)
            if step and step["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
                status = step["status"]
            elif run["target"] == key:
                status = run["status"]
            else:
                status = f"{run['status']} (Run all)"
            active_statuses[key] = f"{status} · {run['reporting_month']}"
    return selected_month_statuses, active_statuses


def render_run_controls() -> None:
    """Render per-step controls and open dialogs from the full app rerun."""
    st.subheader("Run controls")
    if st.button("Run all configured steps", type="primary"):
        queue_run("ALL", override)

    selected_project_codes, selection_is_valid = render_excel_download_selection()

    description_column, action_column, status_column = st.columns([4, 2, 3])
    with description_column:
        st.caption("DESCRIPTION")
    with action_column:
        st.caption("ACTION")
    with status_column:
        st.caption("STATUS")

    selected_month_statuses, active_statuses = step_statuses()
    for key, adapter in sorted(adapters.items(), key=lambda item: item[1].spec.order):
        description_column, action_column, status_column = st.columns([4, 2, 3])
        configured = adapter.spec.configured and adapter.spec.script is not None and adapter.spec.script.exists()

        with description_column:
            st.markdown(f"**{adapter.spec.order}. {adapter.spec.name}**")
        with action_column:
            if configured:
                if st.button("Run step", use_container_width=True, key=f"run_{key}"):
                    if key == "excel_download" and not selection_is_valid:
                        st.error("Choose valid Step 2 project files before starting the download.")
                    else:
                        if key == "snowflake_upload":
                            st.session_state.v1_step3_dialog = True
                        elif key == "snowflake_task":
                            st.session_state.v1_step4_dialog = True
                        else:
                            queue_run(
                                key,
                                override,
                                selected_project_codes if key == "excel_download" else None,
                            )
            else:
                st.button("Not configured", disabled=True, use_container_width=True, key=f"disabled_{key}")
        with status_column:
            if not configured:
                st.markdown("**Not configured**")
                continue

            selected_status = selected_month_statuses.get(key, "Not run")
            active_status = active_statuses.get(key)
            if active_status:
                st.markdown(f"**{active_status}**")
                if selected_status != active_status.split(" · ", 1)[0]:
                    st.caption(f"Selected month ({month}): {selected_status}")
            else:
                st.markdown(f"**{selected_status}**")


render_run_controls()

if st.session_state.get("v1_step3_dialog"):
    show_step3_upload_dialog()
if st.session_state.get("v1_step4_dialog"):
    show_step4_task_dialog()

@st.fragment(run_every=10)
def render_recent_runs() -> None:
    """Refresh run history every ten seconds without resetting run parameters."""
    st.divider()
    st.subheader("Recent runs")
    details_tab, table_tab = st.tabs(["Run details", "Table view"])
    with details_tab:
        runs = repo.list_runs(50)
        if not runs:
            st.info("No runs have been queued yet. Start with step 1 while the remaining scripts are being prepared.")
        else:
            for run in runs:
                with st.expander(f"{run['created_at']} · {run['target']} · {run['status']} · {run['reporting_month']}"):
                    st.write(f"Operator: {run['operator']} · Trigger: {run['trigger']}")
                    if run["summary"]:
                        st.write(run["summary"])
                    steps = repo.steps_for(run["id"])
                    if steps:
                        st.dataframe(steps, use_container_width=True, hide_index=True)
                    if run["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
                        if st.button("Request cancellation", key=f"cancel_{run['id']}"):
                            repo.request_cancel(run["id"])
                            st.rerun()
    with table_tab:
        step_rows = repo.list_recent_step_runs(50)
        if step_rows:
            st.dataframe(step_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No step executions have been recorded yet.")


render_recent_runs()

st.divider()
st.subheader("Monthly scheduler")
with st.form("schedule"):
    schedule_enabled = st.checkbox("Enable one monthly Run All schedule", value=False)
    schedule_day = st.number_input("Day of month", min_value=1, max_value=28, value=1)
    schedule_time = st.time_input("Time in HKT")
    if st.form_submit_button("Save schedule"):
        with repo.connect() as db:
            db.execute("UPDATE schedule SET enabled=?,day_of_month=?,time_hkt=? WHERE id=1", (int(schedule_enabled), schedule_day, schedule_time.strftime("%H:%M")))
        st.success("Schedule saved. The worker will enqueue it when running.")

st.caption("Run All ends after Step 3. Steps 4–6 are standalone/manual: Step 4 requires Stage confirmation, Step 5 is a future Risk DB sync template, and Step 6 compares files in the app-managed Risk folders.")
