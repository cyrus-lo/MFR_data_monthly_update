from pathlib import Path

import pandas as pd

import MFR_vs_Risk_calculation_checking as step6


def comparison_frame(is_pass: list[bool]) -> pd.DataFrame:
    return pd.DataFrame({
        "project_code": [1010] * len(is_pass),
        "risk_category": ["test"] * len(is_pass),
        "details_risk_code": ["CASE_01"] * len(is_pass),
        "DIMENSION": ["Total Income"] * len(is_pass),
        "details_comparison_left": [1.0] * len(is_pass),
        "CAL_VALUE": [1.0] * len(is_pass),
        "diff_CAL_VALUE": [0.0] * len(is_pass),
        "is_pass": is_pass,
    })


def test_step6_defaults_use_the_app_managed_folder_tree() -> None:
    args = step6.build_argument_parser().parse_args([])

    assert args.mfr_input_dir == step6.STEP6_ROOT / "MFR_vs_Risk_calculation" / "MFR_files"
    assert args.risk_input_dir == step6.STEP6_ROOT / "MFR_vs_Risk_calculation" / "Risk_calculation_files"
    assert args.output_dir == step6.STEP6_ROOT / "output"


def test_step6_writes_csv_and_accepts_only_all_pass_rows(tmp_path: Path) -> None:
    passed = comparison_frame([True])
    failed = comparison_frame([False])

    output_path = step6.write_comparison_csv(passed, tmp_path)

    assert output_path.parent == tmp_path
    assert output_path.name.startswith("risk_check_")
    assert list(pd.read_csv(output_path).columns) == step6.COMPARISON_OUTPUT_COLUMNS
    assert step6.comparison_succeeded(passed)
    assert not step6.comparison_succeeded(failed)
    assert not step6.comparison_succeeded(pd.DataFrame())


def test_step6_appends_cumulative_failure_count_log(tmp_path: Path) -> None:
    comparison = comparison_frame([True, False])
    detailed = step6.write_comparison_csv(comparison, tmp_path)
    summary = tmp_path / "risk_check_failure_count.csv"

    step6.append_failure_count(comparison, summary, detailed, "run-1", "2026-07")
    step6.append_failure_count(comparison, summary, detailed, "run-2", "2026-07")

    rows = pd.read_csv(summary)
    assert len(rows) == 2
    assert list(rows.columns) == step6.FAILURE_COUNT_COLUMNS
    assert rows.loc[0, "failure_count"] == 1
    assert rows.loc[0, "status"] == "FAILED"


def test_step6_cli_preserves_a_failure_csv(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(step6, "main", lambda *_: (pd.DataFrame(),) * 4 + (comparison_frame([False]),))

    exit_code = step6.cli_main(["--output-dir", str(tmp_path)])

    assert exit_code == 1
    assert len(list(tmp_path.glob("risk_check_20*.csv"))) == 1
    assert (tmp_path / "risk_check_failure_count.csv").is_file()


def test_step6_cli_succeeds_for_all_pass_rows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(step6, "main", lambda *_: (pd.DataFrame(),) * 4 + (comparison_frame([True]),))

    exit_code = step6.cli_main(["--output-dir", str(tmp_path)])

    assert exit_code == 0


def test_step6_cli_fails_when_no_comparison_is_generated(tmp_path: Path) -> None:
    empty_mfr_dir = tmp_path / "MFR_files"
    empty_risk_dir = tmp_path / "Risk_calculation_files"
    empty_mfr_dir.mkdir()
    empty_risk_dir.mkdir()

    exit_code = step6.cli_main([
        "--mfr-input-dir", str(empty_mfr_dir),
        "--risk-input-dir", str(empty_risk_dir),
        "--output-dir", str(tmp_path / "output"),
    ])

    assert exit_code == 1
    assert not (tmp_path / "output").exists()