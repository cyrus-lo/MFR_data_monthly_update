from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.qa_step4_postcheck as qa


def test_latest_step3_receipt_uses_latest_successful_same_month_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = (
        tmp_path
        / "run_logs"
        / "2026-06"
        / "step3-run"
        / "artifacts"
        / "upload_receipt.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")

    class FakeRepository:
        def __init__(self, path: Path) -> None:
            assert path == tmp_path / "data" / "pipeline.db"

        def latest_successful_step_run(self, reporting_month: str, step_key: str) -> dict[str, str]:
            assert reporting_month == "2026-06"
            assert step_key == "snowflake_upload"
            return {"id": "step3-run"}

    monkeypatch.setattr(qa, "ROOT", tmp_path)
    monkeypatch.setattr(qa, "RunRepository", FakeRepository)

    assert qa.latest_step3_receipt("2026-06") == receipt


def test_qa_requires_explicit_opt_in_before_receipt_or_snowflake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUN_SNOWFLAKE_QA", raising=False)
    args = SimpleNamespace(reporting_month="2026-06", step3_upload_receipt=None, rule_id=None, output=None)

    with pytest.raises(qa.QaError, match="RUN_SNOWFLAKE_QA=1"):
        qa.run(args)