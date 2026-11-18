"""One-off, read-only Snowflake QA for the Step 4 transition post-check.

This script deliberately skips Task A, Task B, task monitoring, and EXECUTE TASK.
It runs only the existing parameterized post-check query so SQL/data issues can
be investigated without starting a Task Graph.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mfr_snowflake_task.trigger_task import (  # noqa: E402
    PostcheckFailure,
    Step4Error,
    build_diagnostic_sql,
    build_project_postcheck_sql,
    evaluate_diagnostics,
    evaluate_project_postcheck,
    execute_rows,
    load_config,
    month_end_dates,
    sanitize_error,
)
from pipeline_core.repository import RunRepository  # noqa: E402
from pipeline_core.step3_receipt import (  # noqa: E402
    Step3ReceiptError,
    Step3UploadReceipt,
    load_step3_upload_receipt,
)


class QaError(RuntimeError):
    """Raised when the one-off QA run cannot safely proceed."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reporting-month", required=True, help="YYYY-MM")
    parser.add_argument(
        "--step3-upload-receipt",
        type=Path,
        help=(
            "Step 3 upload receipt. If omitted, use the latest successful "
            "same-month Step 3 run from the local pipeline database."
        ),
    )
    parser.add_argument("--rule-id", help="Configured post-check rule ID")
    parser.add_argument(
        "--allow-disabled-rule",
        action="store_true",
        help="QA-only: permit selecting a disabled rule; still never executes Tasks",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Sanitized result path; defaults to run_logs/qa/.",
    )
    return parser.parse_args(argv)


def validate_reporting_month(value: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise QaError("--reporting-month must use YYYY-MM")


def latest_step3_receipt(reporting_month: str) -> Path:
    repository = RunRepository(ROOT / "data" / "pipeline.db")
    step3_run = repository.latest_successful_step_run(reporting_month, "snowflake_upload")
    if not step3_run:
        raise QaError(
            "No successful same-month Step 3 run was found for "
            f"{reporting_month}; provide --step3-upload-receipt explicitly"
        )
    receipt = (
        ROOT
        / "run_logs"
        / reporting_month
        / step3_run["id"]
        / "artifacts"
        / "upload_receipt.json"
    )
    if not receipt.is_file():
        raise QaError(f"Latest successful Step 3 run has no upload receipt: {receipt}")
    return receipt


def resolve_step3_receipt(reporting_month: str, supplied: Path | None) -> Path:
    """Use an explicit receipt or default to the latest successful same-month Step 3."""
    receipt = supplied.resolve() if supplied else latest_step3_receipt(reporting_month)
    if not receipt.is_file():
        raise QaError(f"Step 3 upload receipt does not exist: {receipt}")
    return receipt


def select_rule(config: Any, rule_id: str | None, allow_disabled: bool) -> Any:
    rules = tuple(config.postcheck_rules if allow_disabled else (rule for rule in config.postcheck_rules if rule.enabled))
    if rule_id:
        for rule in rules:
            if rule.id == rule_id:
                return rule
        raise QaError(f"Configured post-check rule was not found: {rule_id}")
    if not rules:
        raise QaError("No enabled Step 4 post-check rule is configured")
    if len(rules) > 1:
        raise QaError("Multiple enabled post-check rules require --rule-id")
    return rules[0]


def identity_rows(cursor: Any) -> dict[str, Any]:
    rows = execute_rows(
        cursor,
        """
        SELECT
            CURRENT_ACCOUNT() AS account_name,
            CURRENT_USER() AS user_name,
            CURRENT_ROLE() AS role_name,
            CURRENT_DATABASE() AS database_name,
            CURRENT_SCHEMA() AS schema_name
        """.strip(),
    )
    if len(rows) != 1:
        raise QaError("Snowflake identity check returned an unexpected result")
    return rows[0]


def default_output(reporting_month: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "run_logs" / "qa" / f"step4_postcheck_{reporting_month}_{stamp}.json"


def write_result(path: Path, result: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> Path:
    if os.environ.get("RUN_SNOWFLAKE_QA") != "1":
        raise QaError(
            "QA is disabled. Set RUN_SNOWFLAKE_QA=1 explicitly; this script is never "
            "called by the App or Worker."
        )
    validate_reporting_month(args.reporting_month)
    receipt_path = resolve_step3_receipt(args.reporting_month, args.step3_upload_receipt)
    try:
        receipt: Step3UploadReceipt = load_step3_upload_receipt(
            receipt_path, args.reporting_month
        )
    except Step3ReceiptError as exc:
        raise QaError(str(exc)) from exc

    config = load_config()
    rule = select_rule(config, args.rule_id, args.allow_disabled_rule)
    current_date, prior_date = month_end_dates(args.reporting_month)
    output_path = (args.output or default_output(args.reporting_month)).resolve()
    result: dict[str, Any] = {
        "schema_version": 1,
        "qa_type": "step4_postcheck_only",
        "reporting_month": args.reporting_month,
        "step3_receipt": str(receipt_path),
        "step3_source_run_id": receipt.run_id,
        "expected_distinct_project_count": receipt.distinct_project_count,
        "rule_id": rule.id,
        "reporting_date": current_date,
        "prior_reporting_date": prior_date,
        "task_execution": "SKIPPED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    connection = None
    cursor = None
    try:
        import snowflake.connector  # type: ignore[import-not-found]

        connection = snowflake.connector.connect(**config.connection_parameters)
        cursor = connection.cursor()
        result["snowflake_identity"] = identity_rows(cursor)
        sql, params = build_project_postcheck_sql(
            rule, receipt.project_codes, current_date, prior_date
        )
        rows = execute_rows(cursor, sql, params)
        result["post_check"] = evaluate_project_postcheck(
            rule, receipt.project_codes, rows, current_date, prior_date
        )
        if rule.terminal and rule.diagnostics:
            try:
                result["diagnostics"] = evaluate_diagnostics(
                    rule, execute_rows(cursor, build_diagnostic_sql(rule))
                )
            except Exception as exc:
                result["diagnostics"] = [
                    {"outcome": "INFORMATIONAL_ERROR", "error": sanitize_error(exc)}
                ]
        result["outcome"] = "SUCCEEDED"
    except PostcheckFailure as exc:
        result["post_check"] = exc.result
        result["outcome"] = "FAILED"
        result["error"] = sanitize_error(exc)
    except Exception as exc:
        result["outcome"] = "FAILED"
        result["error"] = sanitize_error(exc)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
    return write_result(output_path, result)


def main(argv: list[str] | None = None) -> int:
    try:
        path = run(parse_args(argv))
    except (QaError, OSError, Step4Error) as exc:
        print(f"QA ERROR: {sanitize_error(exc)}", file=sys.stderr)
        return 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"QA result: {path}")
    print(f"Outcome: {payload.get('outcome', 'FAILED')}")
    if payload.get("error"):
        print(f"Error: {payload['error']}")
    return 0 if payload.get("outcome") == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())