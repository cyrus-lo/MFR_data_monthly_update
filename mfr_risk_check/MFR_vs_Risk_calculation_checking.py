"""Step 6 entry point for the MFR-versus-Risk comparison."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from MFR_vs_Risk_calculation_checking import cli_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(cli_main())
