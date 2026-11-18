# Step 6 — MFR versus Risk comparison

Step 6 compares eMFR workbooks with the Risk workbook and creates a comparison CSV.

## App-managed folder layout

- [MFR_files](MFR_vs_Risk_calculation/MFR_files): place the eMFR `.xlsx` workbooks here.
- [Risk_calculation_files](MFR_vs_Risk_calculation/Risk_calculation_files): place exactly one exported Risk `.xlsx` workbook here. Until Step 5 is configured, copy this file manually. Step 5 will later use this as its Docker-copy destination.
- [output](output): generated `risk_check_<timestamp>.csv` files are written here.

When Stage 6 is started from the v2 dashboard, the confirmation dialog shows the latest successful Stage 3 run for the selected reporting month. After confirmation, the worker reads the Stage 3 upload receipt and copies its manifest-listed `eMFR_*.xlsx` files from their recorded project folders into `MFR_files`. Existing active Excel files are moved to a dated subfolder under `MFR_files/archive/` first. Archived files are not used as Stage 6 inputs.

The Stage 3 recovery flow may record a manually selected source folder. Stage 6 follows that recorded source as part of the same audited receipt/manifest contract. Only `eMFR_*.xlsx` files are copied; `Sub_eMFR_*.xlsx` files are not used by the comparison.

Stage 6 requires exactly one Risk `.xlsx` file directly in the Risk input folder. If none or multiple are present, the confirmation cannot be completed and the worker will stop before running the comparison.

## Run

Run Step 6 from the dashboard, or execute [MFR_vs_Risk_calculation_checking.py](MFR_vs_Risk_calculation_checking.py) from the workspace root.

The run succeeds only when the comparison CSV exists and every row has `is_pass=True`. A mismatch still produces a CSV for review, but the process returns a failure status.
