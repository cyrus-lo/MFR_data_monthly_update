"""Connect to MySQL and run the phase 2 read-only query smoke test.

The MFR-specific SELECT is intentionally deferred until the schema and
submission rules are provided. Replace READ_ONLY_QUERY in phase 3 without
changing the connection or configuration handling.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import mysql.connector
from dotenv import load_dotenv


REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")

# Phase 2 smoke query. This is deliberately schema-independent and read-only.
# Use --sql-file after the real Phase 3 SQL is provided.
READ_ONLY_QUERY = "SELECT 1 AS connection_ok"
QUERY_PARAMETERS: tuple[object, ...] = ()

# These names are only used when the eventual MFR query returns a status field.
# No completion rule is inferred from the phase 2 smoke query.
REVIEW_STATUSES = {"incomplete", "not_submitted", "pending", "requires_review", "review"}
# Hong Kong uses a fixed UTC+08:00 offset and does not observe daylight saving.
# A fixed offset avoids requiring the optional tzdata package on Windows.
HONG_KONG_TIME = timezone(timedelta(hours=8), "HKT")
LOG_FIELDS = (
    "report_month",
    "run_id",
    "timestamp_hkt",
    "sql_file",
    "execution_status",
    "check_result",
    "row_count",
    "summary",
    "result_json",
    "error_message",
)
RUN_LOGS_ROOT = Path(__file__).resolve().parent.parent / "run_logs"
DEFAULT_LOG_FILE = RUN_LOGS_ROOT / "sql_execution_log.csv"
DEFAULT_SIGNAL_FILE = RUN_LOGS_ROOT / "mfr_completion_signal.csv"
DEFAULT_EXPECTED_PROJECT_COUNT = 20


class ConfigurationError(ValueError):
    """Raised when required database settings are missing or invalid."""


class QueryValidationError(ValueError):
    """Raised when a supplied SQL file is not a read-only query."""


@dataclass(frozen=True)
class CompletionCheck:
    """The pipeline decision derived from the MFR status query."""

    result: str
    expected_project_count: int
    returned_project_count: int
    not_completed_projects: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    def connection_kwargs(self) -> dict[str, object]:
        """Return connector arguments without exposing them in output."""
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
        }


def hkt_timestamp() -> str:
    """Return the current Hong Kong time in ISO 8601 format (UTC+08:00)."""
    return datetime.now(HONG_KONG_TIME).isoformat(timespec="seconds")


def migrate_utc_timestamp_to_hkt(timestamp: str) -> str:
    """Convert legacy UTC timestamps to HKT while preserving unreadable values."""
    if not timestamp:
        return ""
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(HONG_KONG_TIME).isoformat(timespec="seconds")


def load_config() -> DatabaseConfig:
    """Load and validate database settings from the local .env/environment."""
    load_dotenv(Path(__file__).with_name(".env"))

    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        raise ConfigurationError(
            "Missing required database setting(s): " + ", ".join(missing)
        )

    port_text = os.environ["DB_PORT"]
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ConfigurationError("DB_PORT must be an integer between 1 and 65535") from exc

    if not 1 <= port <= 65535:
        raise ConfigurationError("DB_PORT must be an integer between 1 and 65535")

    return DatabaseConfig(
        host=os.environ["DB_HOST"],
        port=port,
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
    )


def load_sql_file(sql_file: Path, expected_database: str | None = None) -> str:
    """Load one read-only SQL statement, allowing a matching leading USE."""
    try:
        query = sql_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise QueryValidationError(f"Unable to read SQL file: {sql_file}") from exc

    # Ignore SQL comments and the final delimiter for validation/execution.
    query = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
    statement = "\n".join(
        line
        for line in query.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ).strip().rstrip(";").strip()

    use_match = re.match(
        r"^\s*USE\s+(?P<database>`[^`]+`|[A-Za-z0-9_$]+)\s*;\s*",
        statement,
        re.IGNORECASE,
    )
    if use_match:
        requested_database = use_match.group("database").strip("`")
        if (
            expected_database
            and requested_database.casefold() != expected_database.casefold()
        ):
            raise QueryValidationError(
                f"SQL USE database '{requested_database}' does not match "
                f"configured database '{expected_database}'"
            )
        statement = statement[use_match.end() :].strip().rstrip(";").strip()

    first_keyword = statement.split(None, 1)[0].upper() if statement else ""
    if first_keyword not in {"SELECT", "WITH"}:
        raise QueryValidationError("SQL file must contain one read-only SELECT or WITH query")
    if re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE|TRUNCATE|DROP|ALTER|CREATE)\b", statement, re.IGNORECASE):
        raise QueryValidationError("SQL file contains a write or schema-changing keyword")
    if ";" in statement:
        raise QueryValidationError("SQL file must contain one SQL statement")
    return statement


def run_read_only_query(
    connection: mysql.connector.MySQLConnection,
    query: str = READ_ONLY_QUERY,
    parameters: Sequence[object] = QUERY_PARAMETERS,
) -> list[dict[str, object]]:
    """Run a parameterized read-only query and return rows as dictionaries."""
    with connection.cursor(dictionary=True) as cursor:
        cursor.execute(query, tuple(parameters))
        return cursor.fetchall()


def get_row_value(row: dict[str, object], field_name: str) -> object | None:
    """Return a row value using a case-insensitive column-name match."""
    for key, value in row.items():
        if key.casefold() == field_name.casefold():
            return value
    return None


def evaluate_completion(
    rows: list[dict[str, object]], expected_project_count: int
) -> CompletionCheck:
    """Fail closed unless every expected project has one completed status."""
    reasons: list[str] = []
    not_completed_projects: list[str] = []
    project_codes: list[str] = []

    for row_number, row in enumerate(rows, start=1):
        project_value = get_row_value(row, "project_code")
        status_value = get_row_value(row, "status")
        project_code = str(project_value).strip() if project_value is not None else ""
        status = str(status_value).strip().casefold() if status_value is not None else "missing"

        if not project_code:
            reasons.append(f"row {row_number} has no project_code")
            project_code = f"<row {row_number}>"
        project_codes.append(project_code)

        if status != "completed":
            not_completed_projects.append(f"{project_code} ({status})")

    if len(rows) != expected_project_count:
        reasons.append(
            f"expected {expected_project_count} projects but query returned {len(rows)}"
        )

    duplicate_codes = sorted(
        code for code in set(project_codes) if project_codes.count(code) > 1
    )
    if duplicate_codes:
        reasons.append("duplicate project_code values: " + ", ".join(duplicate_codes))

    result = "All completed" if not reasons and not not_completed_projects else "Not all completed"
    return CompletionCheck(
        result=result,
        expected_project_count=expected_project_count,
        returned_project_count=len(rows),
        not_completed_projects=tuple(not_completed_projects),
        reasons=tuple(reasons),
    )


def not_completed_due_to_error(
    expected_project_count: int, reason: str
) -> CompletionCheck:
    """Create a fail-closed completion result when the query cannot be evaluated."""
    return CompletionCheck(
        result="Not all completed",
        expected_project_count=expected_project_count,
        returned_project_count=0,
        not_completed_projects=(),
        reasons=(reason,),
    )


def write_completion_signal(
    signal_file: Path, sql_source: str, completion: CompletionCheck
) -> bool:
    """Atomically overwrite the single-row signal consumed by the next step."""
    record = {
        "checked_at_hkt": hkt_timestamp(),
        "sql_file": sql_source,
        "check_result": completion.result,
        "expected_project_count": completion.expected_project_count,
        "returned_project_count": completion.returned_project_count,
        "not_completed_count": len(completion.not_completed_projects),
        "not_completed_projects": "; ".join(completion.not_completed_projects),
        "reason": "; ".join(completion.reasons),
    }
    temporary_file = signal_file.with_suffix(f"{signal_file.suffix}.tmp")

    try:
        signal_file.parent.mkdir(parents=True, exist_ok=True)
        with temporary_file.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=tuple(record))
            writer.writeheader()
            writer.writerow(record)
        temporary_file.replace(signal_file)
    except OSError as exc:
        print(f"SIGNAL WARNING: unable to write {signal_file}: {exc}", file=sys.stderr)
        return False
    return True


def write_project_manifest(
    manifest_file: Path,
    report_month: str,
    run_id: str,
    rows: list[dict[str, object]],
) -> bool:
    """Write the successful Step 1 project list for the current pipeline run."""
    if not report_month or not run_id:
        return True

    projects: list[dict[str, str]] = []
    for row in rows:
        project_code = get_row_value(row, "project_code")
        status = get_row_value(row, "status")
        projects.append({
            "project_code": str(project_code).strip(),
            "status": str(status).strip(),
        })

    payload = {
        "schema_version": 1,
        "reporting_month": report_month,
        "run_id": run_id,
        "projects": projects,
    }
    temporary_file = manifest_file.with_suffix(f"{manifest_file.suffix}.tmp")
    try:
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(manifest_file)
    except OSError as exc:
        print(f"MANIFEST WARNING: unable to write {manifest_file}: {exc}", file=sys.stderr)
        return False
    return True


def summarize_results(rows: list[dict[str, object]]) -> str:
    """Create a concise result summary without assuming the future MFR schema."""
    if not rows:
        return "no matching records returned."

    has_status_column = any(
        key.casefold() == "status" for row in rows for key in row
    )
    if has_status_column:
        return f"MFR status query returned {len(rows)} record(s)."

    review_rows = [
        row
        for row in rows
        if any(
            key.lower() == "status"
            and value is not None
            and str(value).strip().lower() in REVIEW_STATUSES
            for key, value in row.items()
        )
    ]

    if review_rows:
        return f"{len(review_rows)} record(s) require review."
    return (
        f"read-only query returned {len(rows)} record(s); "
        "MFR completion status was not evaluated."
    )


def report_results(rows: list[dict[str, object]]) -> str:
    """Print and return a concise result summary."""
    summary = summarize_results(rows)
    print(f"RESULT: {summary}")
    return summary


def _json_default(value: object) -> str:
    """Serialize common MySQL values such as dates and decimals for the log."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def ensure_log_schema(log_file: Path) -> None:
    """Migrate an existing execution log to the current stable CSV schema."""
    if not log_file.exists() or log_file.stat().st_size == 0:
        return

    with log_file.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        existing_fields = tuple(reader.fieldnames or ())
        existing_rows = list(reader)

    if existing_fields == LOG_FIELDS:
        return

    temporary_file = log_file.with_suffix(f"{log_file.suffix}.tmp")
    with temporary_file.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=LOG_FIELDS)
        writer.writeheader()
        for existing_row in existing_rows:
            migrated_row = {
                field: existing_row.get(field, "") for field in LOG_FIELDS
            }
            if not migrated_row["timestamp_hkt"]:
                migrated_row["timestamp_hkt"] = migrate_utc_timestamp_to_hkt(
                    existing_row.get("timestamp_utc", "")
                )
            writer.writerow(migrated_row)
    temporary_file.replace(log_file)


def write_execution_log(
    log_file: Path,
    sql_source: str,
    execution_status: str,
    report_month: str = "",
    run_id: str = "",
    rows: list[dict[str, object]] | None = None,
    summary: str = "",
    error_message: str = "",
    check_result: str = "",
) -> bool:
    """Append one stable-schema execution record to the CSV audit log."""
    rows_for_log = rows if rows is not None else []
    record = {
        "report_month": report_month,
        "run_id": run_id,
        "timestamp_hkt": hkt_timestamp(),
        "sql_file": sql_source,
        "execution_status": execution_status,
        "check_result": check_result,
        "row_count": len(rows) if rows is not None else "",
        "summary": summary,
        "result_json": json.dumps(rows_for_log, default=_json_default, ensure_ascii=False),
        "error_message": error_message,
    }

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        ensure_log_schema(log_file)
        has_header = log_file.exists() and log_file.stat().st_size > 0
        with log_file.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=LOG_FIELDS)
            if not has_header:
                writer.writeheader()
            writer.writerow(record)
    except OSError as exc:
        print(f"LOGGING WARNING: unable to write {log_file}: {exc}", file=sys.stderr)
        return False
    return True


def parse_args() -> argparse.Namespace:
    """Parse optional Phase 3 SQL-file arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sql-file",
        type=Path,
        help="read a parameterized SELECT/WITH query from this file",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="query parameter in order; repeat for each SQL placeholder",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help=f"CSV execution log path (default: {DEFAULT_LOG_FILE.name})",
    )
    parser.add_argument(
        "--completion-signal-file",
        type=Path,
        default=DEFAULT_SIGNAL_FILE,
        help=f"latest completion signal CSV path (default: {DEFAULT_SIGNAL_FILE.name})",
    )
    parser.add_argument(
        "--report-month",
        default="",
        help="reporting month in YYYY-MM format for the audit log",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="pipeline run identifier for the audit log",
    )
    parser.add_argument(
        "--project-manifest",
        type=Path,
        help="write the successful run-scoped project manifest to this JSON file",
    )
    parser.add_argument(
        "--expected-project-count",
        type=int,
        default=int(os.getenv("MFR_EXPECTED_PROJECT_COUNT", DEFAULT_EXPECTED_PROJECT_COUNT)),
        help="number of target projects required before issuing All completed",
    )
    return parser.parse_args()


def main() -> int:
    """Validate settings, connect, run the read-only query, and close cleanly."""
    args = parse_args()
    if args.expected_project_count < 1:
        print("CONFIGURATION ERROR: --expected-project-count must be at least 1", file=sys.stderr)
        return 2

    sql_source = args.sql_file.name if args.sql_file else "<default smoke query>"
    try:
        config = load_config()
    except ConfigurationError as exc:
        error_message = f"CONFIGURATION ERROR: {exc}"
        print(error_message, file=sys.stderr)
        completion = not_completed_due_to_error(args.expected_project_count, error_message)
        write_completion_signal(args.completion_signal_file, sql_source, completion)
        write_execution_log(
            args.log_file,
            sql_source,
            "CONFIGURATION_ERROR",
            report_month=args.report_month,
            run_id=args.run_id,
            error_message=error_message,
            check_result=completion.result,
        )
        return 2

    query = READ_ONLY_QUERY
    parameters: tuple[object, ...] = QUERY_PARAMETERS
    if args.sql_file:
        try:
            query = load_sql_file(args.sql_file, config.database)
        except QueryValidationError as exc:
            error_message = f"QUERY CONFIGURATION ERROR: {exc}"
            print(error_message, file=sys.stderr)
            completion = not_completed_due_to_error(args.expected_project_count, error_message)
            write_completion_signal(args.completion_signal_file, sql_source, completion)
            write_execution_log(
                args.log_file,
                sql_source,
                "QUERY_CONFIGURATION_ERROR",
                report_month=args.report_month,
                run_id=args.run_id,
                error_message=error_message,
                check_result=completion.result,
            )
            return 2
        parameters = tuple(args.param)

    connection = None
    try:
        try:
            connection = mysql.connector.connect(**config.connection_kwargs())
        except mysql.connector.Error as exc:
            message = getattr(exc, "msg", str(exc)) or "unknown MySQL error"
            message = message.replace(config.password, "[REDACTED]")
            error_message = f"DATABASE CONNECTION ERROR: {message}"
            print(error_message, file=sys.stderr)
            completion = not_completed_due_to_error(args.expected_project_count, error_message)
            write_completion_signal(args.completion_signal_file, sql_source, completion)
            write_execution_log(
                args.log_file,
                sql_source,
                "DATABASE_CONNECTION_ERROR",
                report_month=args.report_month,
                run_id=args.run_id,
                error_message=error_message,
                check_result=completion.result,
            )
            return 1

        try:
            rows = run_read_only_query(connection, query, parameters)
        except mysql.connector.Error as exc:
            message = getattr(exc, "msg", str(exc)) or "unknown MySQL error"
            message = message.replace(config.password, "[REDACTED]")
            error_message = f"QUERY ERROR: {message}"
            print(error_message, file=sys.stderr)
            completion = not_completed_due_to_error(args.expected_project_count, error_message)
            write_completion_signal(args.completion_signal_file, sql_source, completion)
            write_execution_log(
                args.log_file,
                sql_source,
                "QUERY_ERROR",
                report_month=args.report_month,
                run_id=args.run_id,
                error_message=error_message,
                check_result=completion.result,
            )
            return 1

        print("SUCCESS: MySQL connection and read-only query completed.")
        summary = report_results(rows)
        completion = evaluate_completion(rows, args.expected_project_count)
        signal_written = write_completion_signal(
            args.completion_signal_file, sql_source, completion
        )
        print(f"CHECK RESULT: {completion.result}")
        if completion.not_completed_projects:
            print("NOT COMPLETED: " + ", ".join(completion.not_completed_projects))
        if completion.reasons:
            print("CHECK DETAILS: " + "; ".join(completion.reasons))
        write_execution_log(
            args.log_file,
            sql_source,
            "SUCCESS",
            report_month=args.report_month,
            run_id=args.run_id,
            rows=rows,
            summary=summary,
            check_result=completion.result,
        )
        if completion.result == "All completed" and args.project_manifest:
            if not write_project_manifest(
                args.project_manifest, args.report_month, args.run_id, rows
            ):
                print("MANIFEST ERROR: the project manifest could not be written", file=sys.stderr)
                return 1
        print(f"LOG: appended execution result to {args.log_file}")
        if not signal_written:
            print("SIGNAL ERROR: the pipeline signal could not be updated", file=sys.stderr)
            return 1
        print(f"SIGNAL: wrote latest result to {args.completion_signal_file}")
        return 0
    except Exception as exc:
        message = getattr(exc, "msg", str(exc)) or "unknown MySQL error"
        message = message.replace(config.password, "[REDACTED]")
        error_message = f"UNEXPECTED ERROR: {message}"
        print(error_message, file=sys.stderr)
        completion = not_completed_due_to_error(args.expected_project_count, error_message)
        write_completion_signal(args.completion_signal_file, sql_source, completion)
        write_execution_log(
            args.log_file,
            sql_source,
            "UNEXPECTED_ERROR",
            report_month=args.report_month,
            run_id=args.run_id,
            error_message=error_message,
            check_result=completion.result,
        )
        return 1
    finally:
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())