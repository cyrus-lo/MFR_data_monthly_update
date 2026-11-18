from __future__ import annotations

import csv
import io
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import RunStatus, StepStatus
from .project_selection import normalize_project_codes

HKT = timezone(timedelta(hours=8), "HKT")


def now_hkt() -> str:
    return datetime.now(HKT).isoformat(timespec="seconds")


class RunRepository:
    def __init__(self, path: Path | str = Path("data/pipeline.db")) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY, reporting_month TEXT NOT NULL, target TEXT NOT NULL,
                operator TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL,
                override_reason TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
                selection_json TEXT NOT NULL DEFAULT '',
                source_json TEXT NOT NULL DEFAULT '',
                confirmation_json TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS step_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, step_key TEXT NOT NULL,
                status TEXT NOT NULL, exit_code INTEGER, message TEXT NOT NULL DEFAULT '',
                log_path TEXT NOT NULL DEFAULT '', started_at TEXT, finished_at TEXT,
                UNIQUE(run_id, step_key), FOREIGN KEY(run_id) REFERENCES pipeline_runs(id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, step_key TEXT NOT NULL,
                relative_path TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL DEFAULT 0,
                day_of_month INTEGER NOT NULL DEFAULT 1, time_hkt TEXT NOT NULL DEFAULT '09:00',
                last_occurrence TEXT NOT NULL DEFAULT ''
            );
            INSERT OR IGNORE INTO schedule(id) VALUES (1);
            """)
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(pipeline_runs)")
            }
            if "selection_json" not in columns:
                db.execute(
                    "ALTER TABLE pipeline_runs ADD COLUMN selection_json TEXT NOT NULL DEFAULT ''"
                )
            if "source_json" not in columns:
                db.execute(
                    "ALTER TABLE pipeline_runs ADD COLUMN source_json TEXT NOT NULL DEFAULT ''"
                )
            if "confirmation_json" not in columns:
                db.execute(
                    "ALTER TABLE pipeline_runs ADD COLUMN confirmation_json TEXT NOT NULL DEFAULT ''"
                )

    def create_run(self, reporting_month: str, target: str, operator: str,
                   trigger: str = "manual", override_reason: str = "",
                   allow_rerun: bool = False,
                   selected_project_codes: list[str] | None = None,
                   step3_source: dict[str, Any] | None = None,
                   step4_confirmation: dict[str, Any] | None = None,
                   step6_confirmation: dict[str, Any] | None = None) -> str:
        run_id = str(uuid.uuid4())
        selection = normalize_project_codes(selected_project_codes or [])
        if selection and target != "excel_download":
            raise RuntimeError("A project-file selection is supported only when running Step 2 by itself")
        if step3_source and target != "snowflake_upload":
            raise RuntimeError("A Step 3 source is supported only when running Step 3 by itself")
        if step4_confirmation and target != "snowflake_task":
            raise RuntimeError("A Step 4 confirmation is supported only when running Step 4 by itself")
        if step6_confirmation and target != "risk_check":
            raise RuntimeError("A Step 6 confirmation is supported only when running Step 6 by itself")
        if target == "snowflake_task" and not step4_confirmation:
            raise RuntimeError("Step 4 requires confirmation that all Step 3 files are present in Snowflake Stage")
        if target == "snowflake_task" and not step4_confirmation.get("stage_files_confirmed"):
            raise RuntimeError("Step 4 requires confirmation that all Step 3 files are present in Snowflake Stage")
        if target == "risk_check" and not step6_confirmation:
            raise RuntimeError("Step 6 requires confirmation of the MFR source and Risk workbook")
        if target == "risk_check" and not step6_confirmation.get("mfr_source_confirmed"):
            raise RuntimeError("Step 6 requires confirmation of the MFR source and Risk workbook")
        if target == "risk_check" and not step6_confirmation.get("risk_workbook_confirmed"):
            raise RuntimeError("Step 6 requires confirmation of the MFR source and Risk workbook")
        if target == "risk_check" and not str(step6_confirmation.get("step3_run_id", "")).strip():
            raise RuntimeError("Step 6 confirmation must identify a successful Step 3 run")
        selection_json = json.dumps(selection) if target == "excel_download" else ""
        source_json = json.dumps(step3_source, sort_keys=True) if step3_source else ""
        confirmation = step4_confirmation if target == "snowflake_task" else step6_confirmation
        confirmation_json = json.dumps(confirmation, sort_keys=True) if confirmation else ""
        with self.connect() as db:
            active = db.execute("SELECT id FROM pipeline_runs WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')").fetchone()
            if active:
                raise RuntimeError("Another pipeline run is already active")
            if allow_rerun and not override_reason.strip():
                raise RuntimeError("An override reason is required for a rerun")
            duplicate = db.execute(
                "SELECT id FROM pipeline_runs WHERE reporting_month=? AND target=? AND status IN ('QUEUED','RUNNING','SUCCEEDED')",
                (reporting_month, target),
            ).fetchone()
            if duplicate and not allow_rerun:
                raise RuntimeError(f"A run already exists for {reporting_month} / {target}")
            db.execute("INSERT INTO pipeline_runs(id,reporting_month,target,operator,trigger,status,override_reason,selection_json,source_json,confirmation_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, reporting_month, target, operator.strip(), trigger, RunStatus.QUEUED, override_reason, selection_json, source_json, confirmation_json, now_hkt()))
        return run_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def latest_successful_step_run(self, reporting_month: str, step_key: str) -> dict[str, Any] | None:
        """Return the latest completed run containing a successful named step."""
        with self.connect() as db:
            row = db.execute(
                """
                SELECT r.*
                FROM pipeline_runs AS r
                INNER JOIN step_runs AS s ON s.run_id = r.id
                WHERE r.reporting_month=?
                  AND r.status='SUCCEEDED'
                  AND s.step_key=?
                  AND s.status='SUCCEEDED'
                ORDER BY r.finished_at DESC, r.created_at DESC
                LIMIT 1
                """,
                (reporting_month, step_key),
            ).fetchone()
        return dict(row) if row else None

    def latest_step_run(self, reporting_month: str, step_key: str) -> dict[str, Any] | None:
        """Return the latest persisted result for a step in one reporting month."""
        with self.connect() as db:
            row = db.execute(
                """
                SELECT r.status AS run_status, r.summary, r.created_at, r.finished_at,
                       s.status AS step_status, s.message, s.log_path, s.finished_at AS step_finished_at
                FROM pipeline_runs AS r
                INNER JOIN step_runs AS s ON s.run_id = r.id
                WHERE r.reporting_month=? AND s.step_key=?
                ORDER BY s.finished_at DESC, r.created_at DESC
                LIMIT 1
                """,
                (reporting_month, step_key),
            ).fetchone()
        return dict(row) if row else None

    def request_cancel(self, run_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE pipeline_runs SET cancel_requested=1,status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE 'CANCEL_REQUESTED' END WHERE id=? AND status IN ('QUEUED','RUNNING')", (run_id,))

    def claim_next(self) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pipeline_runs WHERE status='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE pipeline_runs SET status=?,started_at=? WHERE id=?", (RunStatus.RUNNING, now_hkt(), row["id"]))
            return dict(row)

    def set_status(self, run_id: str, status: RunStatus | StepStatus, message: str = "", finished: bool = False) -> None:
        with self.connect() as db:
            if isinstance(status, RunStatus):
                db.execute("UPDATE pipeline_runs SET status=?,summary=?,finished_at=CASE WHEN ? THEN ? ELSE finished_at END WHERE id=?", (status, message, int(finished), now_hkt(), run_id))
            else:
                db.execute("UPDATE step_runs SET status=?,message=?,finished_at=CASE WHEN ? THEN ? ELSE finished_at END WHERE run_id=? AND step_key=?", (status, message, int(finished), now_hkt(), run_id, message.split("|", 1)[0]))

    def upsert_step(self, run_id: str, step_key: str, status: StepStatus, **values: Any) -> None:
        fields = {"status": status, **values}
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM step_runs WHERE run_id=? AND step_key=?", (run_id, step_key)).fetchone()
            if exists:
                assignments = ",".join(f"{key}=?" for key in fields)
                db.execute(f"UPDATE step_runs SET {assignments} WHERE run_id=? AND step_key=?", (*fields.values(), run_id, step_key))
            else:
                db.execute(
                    "INSERT INTO step_runs(run_id,step_key,status,exit_code,message,log_path,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, step_key, status, fields.get("exit_code"), fields.get("message", ""), fields.get("log_path", ""), fields.get("started_at"), fields.get("finished_at")),
                )

    def steps_for(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM step_runs WHERE run_id=? ORDER BY id", (run_id,))]

    def list_recent_step_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent step executions with the parent run context."""
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT
                    r.created_at,
                    r.reporting_month,
                    r.operator,
                    r.trigger,
                    r.target,
                    r.override_reason,
                    r.id AS run_id,
                    s.step_key,
                    s.status AS step_status,
                    s.started_at AS step_started_at,
                    s.finished_at AS step_finished_at,
                    s.exit_code,
                    s.message,
                    s.log_path
                FROM step_runs AS s
                INNER JOIN pipeline_runs AS r ON r.id = s.run_id
                ORDER BY r.created_at DESC, s.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_snapshot(
        self,
        step_keys: tuple[str, ...],
        limit: int = 200,
        reporting_month: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded, read-only data used by dashboard versions sharing this database."""
        targets = (*step_keys, "ALL")
        placeholders = ",".join("?" for _ in targets)
        step_placeholders = ",".join("?" for _ in step_keys)
        active_statuses = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED)
        active_placeholders = ",".join("?" for _ in active_statuses)
        run_month_clause = " AND reporting_month=?" if reporting_month else ""
        step_month_clause = " AND r.reporting_month=?" if reporting_month else ""
        run_month_parameters: tuple[object, ...] = (reporting_month,) if reporting_month else ()
        with self.connect() as db:
            runs = db.execute(
                f"""
                SELECT * FROM pipeline_runs
                WHERE target IN ({placeholders}){run_month_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*targets, *run_month_parameters, limit),
            ).fetchall()
            active_runs = db.execute(
                f"""
                SELECT * FROM pipeline_runs
                WHERE status IN ({active_placeholders})
                ORDER BY created_at ASC
                """,
                tuple(active_statuses),
            ).fetchall()
            step_rows = db.execute(
                f"""
                SELECT
                    r.id AS run_id, r.reporting_month, r.target, r.status AS run_status,
                    r.created_at, r.started_at AS run_started_at, r.finished_at AS run_finished_at,
                    s.step_key, s.status AS step_status, s.started_at AS step_started_at,
                    s.finished_at AS step_finished_at, s.exit_code, s.message, s.log_path
                FROM step_runs AS s
                INNER JOIN pipeline_runs AS r ON r.id = s.run_id
                WHERE s.step_key IN ({step_placeholders}){step_month_clause}
                ORDER BY r.created_at DESC, s.id DESC
                LIMIT ?
                """,
                (*step_keys, *run_month_parameters, limit),
            ).fetchall()
            schedule = db.execute("SELECT * FROM schedule WHERE id=1").fetchone()
        return {
            "runs": [dict(row) for row in runs],
            "active_runs": [dict(row) for row in active_runs],
            "step_runs": [dict(row) for row in step_rows],
            "schedule": dict(schedule) if schedule else None,
        }

    def history_csv(self, limit: int = 500) -> str:
        rows = self.list_runs(limit)
        if not rows:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
        return output.getvalue()
