import argparse
import csv
import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import pytz

# --- Configuration ---
# MFR Files Configuration
STEP6_ROOT = Path(__file__).resolve().parent / "mfr_risk_check"
INPUT_DIR = STEP6_ROOT / "MFR_vs_Risk_calculation" / "MFR_files"
SHEET_TO_READ = 0 
FILE_NAME_COLUMN = 'FILE_NAME'

# Risk File Configuration
RISK_DIR = STEP6_ROOT / "MFR_vs_Risk_calculation" / "Risk_calculation_files"
# RISK_FILE_PATTERN = "risk_calculation_*.xlsx"
RISK_FILE_PATTERN = "*.xlsx"
OUTPUT_DIR = STEP6_ROOT / "output"

OUTPUT_COLUMNS = [
    'ITEM_GROUP_CODE', 'ITEM_GROUP_DESC', 'ITEM', 'TRADE',
    'BUDGET_TENDER', 'BUDGET_1ST_WORKING_BUDGET', 'BUDGET_ADJUSTMENT_COST_VARIATION',
    'BUSINESS_PLAN', 'AUDIT_REPORT_WIP', 'PROJECTION_AS_AT', 
    'COMMITTED_VALUE_COST_AS_AT', 'ACCRUAL_BEFORE_RETENTION_AS_AT', 
    'CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT', 'FILENAME', 'CELL_A1',
    'PRINT_DATE', 'PROJECT_CODE', 'PROJECT_NAME', 'REPORT_DATE',
    'START_DATE', 'COMPLETE_DATE', 'TARGET_COMPLETE_DATE',
    'TIME_CONSUMED', 'TARGET_COMPLETED', 'DW_CREATED_TIME'
]

COMPARISON_OUTPUT_COLUMNS = [
    'project_code', 'risk_category', 'details_risk_code', 'DIMENSION',
    'details_comparison_left', 'CAL_VALUE', 'diff_CAL_VALUE', 'is_pass',
]

# --- 1: Read MFR Excel (Local Environment) ---
def read_excel_and_add_filename(file_path: Path, sheet_name: int | str = 0) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    try:
        # Using context manager ensures the file is strictly closed after reading
        with pd.ExcelFile(file_path) as xls:
            df = pd.read_excel(xls, header=None, sheet_name=sheet_name)
        return df, None
    except Exception as e:
        return None, str(e)

# --- 2: Logic for Version Concatenation (MFR) ---
def process_version_lookup_and_concat(df: pd.DataFrame) -> pd.DataFrame:
    VERSION_PATTERN = r"^(\d+\.\d+)\.\d+$"
    col0_str = df['ITEM'].astype(str)
    
    is_xyz_format = col0_str.str.contains(VERSION_PATTERN, regex=True, na=False)
    potential_key = col0_str.str.replace(VERSION_PATTERN, r"\1", regex=True)
    df["lookup_key"] = potential_key.where(is_xyz_format, None)

    lookup_table = df[['ITEM', 'TRADE']].rename(columns={'ITEM': "JOIN_KEY", 'TRADE': "JOIN_VAL"})
    lookup_table["JOIN_KEY"] = lookup_table["JOIN_KEY"].astype(str)
    lookup_table = lookup_table.drop_duplicates(subset=["JOIN_KEY"])

    df = df.merge(lookup_table, left_on="lookup_key", right_on="JOIN_KEY", how="left")
    df = df.rename(columns={"JOIN_VAL": "lookup_value"})
    df = df.drop(columns=["JOIN_KEY"])
    
    df['TRADE'] = np.where(
        df["lookup_value"].notna(),
        df["lookup_value"].astype(str) + " " + df['TRADE'].astype(str),
        df['TRADE'].astype(str)
    )
    
    df.drop(columns=['lookup_key', 'lookup_value'], inplace=True)
    df = df.sort_values(by='TRADE', ascending=True)
    return df

# --- 3: Main Transformation (MFR) ---
def transform_data(df: pd.DataFrame, file_name: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    if df.empty: return df, None

    try:
        # 1. Extract Metadata
        output_dict = {}
        output_dict['FILENAME'] = file_name
        output_dict['CELL_A1'] = str(df.iloc[0, 0]) if not pd.isna(df.iloc[0, 0]) else None
        
        cell_1_16 = str(df.iloc[1, 16]) if not pd.isna(df.iloc[1, 16]) else ""
        if cell_1_16.startswith('Print Date:'):
            output_dict['PRINT_DATE'] = cell_1_16.replace('Print Date:', '').strip()

        output_dict['PROJECT_CODE'] = str(df.iloc[2, 1])
        output_dict['PROJECT_NAME'] = str(df.iloc[3, 1])
        output_dict['REPORT_DATE'] = str(df.iloc[4, 1])
        output_dict['START_DATE'] = str(df.iloc[5, 1])
        
        cell_6_1 = str(df.iloc[6, 1]) if not pd.isna(df.iloc[6, 1]) else ""
        suffix_5 = 'Time Consumed (%):'
        output_dict['COMPLETE_DATE'] = cell_6_1[:-len(suffix_5)].rstrip() if cell_6_1.endswith(suffix_5) else cell_6_1.rstrip()

        cell_7_1 = str(df.iloc[7, 1]) if not pd.isna(df.iloc[7, 1]) else ""
        suffix_6 = 'Target Completed (%):'
        output_dict['TARGET_COMPLETE_DATE'] = cell_7_1[:-len(suffix_6)].rstrip() if cell_7_1.endswith(suffix_6) else cell_7_1.rstrip()

        output_dict['TIME_CONSUMED'] = df.iloc[6, 2]
        output_dict['TARGET_COMPLETED'] = df.iloc[7, 2]

        # 2. Drop Header Rows
        df.drop(index=range(15), inplace=True)

        # 3. Create Clean DF by Position
        df_clean = pd.DataFrame()
        max_col_idx = df.shape[1] - 1
        
        mapping = {
            'ITEM': 0, 'TRADE': 1, 'BUDGET_TENDER': 2, 'BUDGET_1ST_WORKING_BUDGET': 3,
            'BUDGET_ADJUSTMENT_COST_VARIATION': 4, 'BUSINESS_PLAN': 6, 'AUDIT_REPORT_WIP': 7,
            'PROJECTION_AS_AT': 9, 'COMMITTED_VALUE_COST_AS_AT': 10,
            'ACCRUAL_BEFORE_RETENTION_AS_AT': 13, 'CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT': 14
        }
        
        for name, idx in mapping.items():
            if idx <= max_col_idx:
                df_clean[name] = df.iloc[:, idx]
            else:
                df_clean[name] = None 

        df = df_clean

        # 4. Apply Metadata & Types
        for key, value in output_dict.items():
            df[key] = value

        float_cols = ['BUDGET_TENDER', 'BUDGET_1ST_WORKING_BUDGET', 'TIME_CONSUMED', 'TARGET_COMPLETED']
        for c in float_cols:
            if c in df.columns:
                 df[c] = pd.to_numeric(df[c], errors='coerce')

        # 5. Item Group Logic
        numeric_col = pd.to_numeric(df['ITEM'], errors='coerce')
        is_integer = numeric_col.notna() & (numeric_col == numeric_col.astype(float).round(0))
        
        df['ITEM_GROUP_CODE'] = numeric_col.where(is_integer)
        df['ITEM_GROUP_CODE'].ffill(inplace=True)

        group_map = df[df['ITEM_GROUP_CODE'].notna() & ~df['ITEM_GROUP_CODE'].duplicated(keep='first')]
        mapping_dict = dict(zip(group_map['ITEM_GROUP_CODE'], group_map['TRADE']))
        
        df['ITEM_GROUP_DESC'] = df['ITEM_GROUP_CODE'].map(mapping_dict)
        df['ITEM_GROUP_CODE'] = df['ITEM_GROUP_CODE'].fillna(-1).astype(int)

        condition_1 = ~((df['ITEM_GROUP_CODE'] == pd.to_numeric(df['ITEM'], errors='coerce')) & df['ITEM_GROUP_CODE'].notna())
        condition_2 = df['ITEM'].notna()
        df = df[condition_1 & condition_2].copy()

        # 6. Version Lookup
        df = process_version_lookup_and_concat(df)

        # 7. Clean ITEM_GROUP_DESC
        df['ITEM_GROUP_DESC'] = df['ITEM_GROUP_DESC'].replace('Less : Cost', 'Cost')

        # 8. Delete section headers records
        section_headers = ['1.2', '1.12', '2.1', '2.2', '2.3', '2.4', '2.6', '2.11']
        df = df[~df['ITEM'].astype(str).isin(section_headers)]

        return df, None

    except Exception as e:
        return None, str(e)

# --- 4: Risk File Ingestion Helper ---
def ingest_risk_file(directory: Path, pattern: str) -> pd.DataFrame:
    if not directory.exists() or not directory.is_dir():
        print(f"Error: Risk directory does not exist -> {directory}")
        return pd.DataFrame()

    risk_files = list(directory.glob(pattern))
    
    if not risk_files:
        print(f"Error: No files matching '{pattern}' found in {directory}.")
        return pd.DataFrame()
    
    if len(risk_files) > 1:
        print(f"Warning: Found {len(risk_files)} Risk files. Using the first one: {risk_files[0].name}")
        
    target_file = risk_files[0]
    print(f"Reading Risk file: {target_file.name}...")
    
    try:
        with pd.ExcelFile(target_file) as xls:
            df_risk = pd.read_excel(xls)
        print(f"  -> Success. Loaded {len(df_risk)} rows into df_risk.")
        return df_risk
    except Exception as e:
        print(f"  -> Error reading Risk file: {e}")
        return pd.DataFrame()

# --- 5: Aggregation Logic ---
def create_mfr_aggregate(df_mfr: pd.DataFrame) -> pd.DataFrame:
    print("\n--- [STEP 3] CREATING MFR AGGREGATE (PANDAS) ---")
    if df_mfr.empty:
        return pd.DataFrame()
        
    # 1. Convert REPORT_DATE to datetime to find the "max" (latest month) globally
    df_mfr['REPORT_DATE_DT'] = pd.to_datetime(df_mfr['REPORT_DATE'], errors='coerce')
    
    # 2. Filter for the latest month PER project across the entire dataframe
    max_dates = df_mfr.groupby('PROJECT_CODE')['REPORT_DATE_DT'].transform('max')
    df_latest = df_mfr[df_mfr['REPORT_DATE_DT'] == max_dates].copy()

    if df_latest.empty:
        print("  -> No valid data for the latest month.")
        return pd.DataFrame()

    # 3. Ensure numeric types for aggregation (No rounding applied)
    sum_cols = [
        'ACCRUAL_BEFORE_RETENTION_AS_AT', 'COMMITTED_VALUE_COST_AS_AT', 
        'CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT', 'AUDIT_REPORT_WIP', 'PROJECTION_AS_AT'
    ]
    for col in sum_cols + ['TARGET_COMPLETED']:
        if col in df_latest.columns:
            # Force to numeric, fill NaNs with 0 to allow clean math
            df_latest[col] = pd.to_numeric(df_latest[col], errors='coerce').fillna(0.0)

    # 4. Set up reusable aggregation dictionary
    agg_dict = {
        'TARGET_COMPLETED': 'max',
        'ACCRUAL_BEFORE_RETENTION_AS_AT': 'sum',
        'COMMITTED_VALUE_COST_AS_AT': 'sum',
        'CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT': 'sum',
        'AUDIT_REPORT_WIP': 'sum',
        'PROJECTION_AS_AT': 'sum'
    }

    # Helper function to group and aggregate based on a filter mask
    def get_agg_df(mask, dim_name):
        if not mask.any():
            return pd.DataFrame()
        df_agg = df_latest[mask].groupby('PROJECT_CODE', as_index=False).agg(agg_dict)
        df_agg.insert(1, 'DIMENSION', dim_name)
        return df_agg

    # 5. Define Masks based on rules
    item_str = df_latest['ITEM'].astype(str)
    desc_str = df_latest['ITEM_GROUP_DESC'].astype(str)
    
    # regex r'^2\.X\..*' means: starts exactly with "2.X." followed by anything
    mask_materials = item_str.str.match(r'^2\.2\..*') & (df_latest['ITEM_GROUP_CODE'] == 2)
    
    mask_prelim = item_str.str.match(r'^2\.1\..*') & ~item_str.isin(['2.1.1', '2.1.2', '2.1.3', '2.1.13']) & (df_latest['ITEM_GROUP_CODE'] == 2)
    
    mask_plant = item_str.str.match(r'^2\.3\..*') & (df_latest['ITEM_GROUP_CODE'] == 2)
    
    # Subcontractor: Starts with 2.4. OR is exactly 2.5
    mask_subcon = (item_str.str.match(r'^2\.4\..*') | (item_str == '2.5')) & (df_latest['ITEM_GROUP_CODE'] == 2)

    # Subcontractor for CASE 14: Starts with 2.4. (excludes item 2.5)
    mask_subcon_c14 = item_str.str.match(r'^2\.4\..*') & (df_latest['ITEM_GROUP_CODE'] == 2)

    mask_income = desc_str == 'Income'
    
    mask_cost = desc_str == 'Cost'

    # 6. Generate Aggregated DataFrames
    df_materials = get_agg_df(mask_materials, 'Materials')
    df_prelim    = get_agg_df(mask_prelim, 'Preliminaries')
    df_plant     = get_agg_df(mask_plant, 'Plant & Machinery')
    df_subcon    = get_agg_df(mask_subcon, 'Subcontractor')
    df_subcon_c14 = get_agg_df(mask_subcon_c14, 'Subcontractor (2.4 only)')
    df_income    = get_agg_df(mask_income, 'Total Income')
    df_cost      = get_agg_df(mask_cost, 'Total Cost')

    # 7. Calculate 'Gross Profit' (Income - Cost) per project
    # Get all unique projects from the latest month to ensure we capture them even if Income or Cost is missing
    df_gp = df_latest[['PROJECT_CODE']].drop_duplicates()
    
    # Merge Income and Cost side-by-side
    df_gp = df_gp.merge(df_income.drop(columns=['DIMENSION'], errors='ignore'), on='PROJECT_CODE', how='left')
    df_gp = df_gp.merge(df_cost.drop(columns=['DIMENSION'], errors='ignore'), on='PROJECT_CODE', how='left', suffixes=('_inc', '_cost'))
    
    # Perform math: Income - Cost
    for col in sum_cols:
        # If a project has no income or no cost, fillna(0) ensures the math still works
        inc_val = df_gp[f'{col}_inc'].fillna(0.0)
        cost_val = df_gp[f'{col}_cost'].fillna(0.0)
        df_gp[col] = inc_val - cost_val
        
    # TARGET_COMPLETED is just the max of Income's target and Cost's target
    df_gp['TARGET_COMPLETED'] = df_gp[['TARGET_COMPLETED_inc', 'TARGET_COMPLETED_cost']].max(axis=1)
    
    # Clean up and order Gross Profit DataFrame
    df_gp.insert(1, 'DIMENSION', 'Gross Profit')
    cols_to_keep = ['PROJECT_CODE', 'DIMENSION', 'TARGET_COMPLETED'] + sum_cols
    df_gp = df_gp[cols_to_keep]

    # 8. Combine all dimensions into one final DataFrame
    all_aggs = [df_materials, df_prelim, df_plant, df_subcon, df_subcon_c14, df_income, df_cost, df_gp]
    # Filter out empty dataframes before concatenating
    final_agg = pd.concat([d for d in all_aggs if not d.empty], ignore_index=True)
    
    print(f"  -> Success. Created aggregate with {len(final_agg)} rows.")
    return final_agg

def create_mfr_risk(df_mfr_aggregate: pd.DataFrame) -> pd.DataFrame:
    print("\n--- [STEP 4] CREATING MFR RISK DATAFRAME ---")
    if df_mfr_aggregate.empty:
        print("Error: df_mfr_aggregate is empty. Skipping risk calculation.")
        return pd.DataFrame()

    risk_dfs = []

    # Helper function to prevent Division by Zero errors (returns NaN if denominator is 0)
    def safe_divide(numerator, denominator):
        return np.where(denominator == 0, np.nan, numerator / denominator)

    # ==========================================
    # CASE 1: Total Income
    # Logic: Accrual / Committed Value
    # ==========================================
    df_c1 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Income'].copy()
    if not df_c1.empty:
        df_c1['CASE'] = 'CASE_01'
        df_c1['CAL_VALUE'] = safe_divide(
            df_c1['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c1['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c1['THRESHOLD'] = df_c1['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c1[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 2: Total Cost
    # Logic: Accrual / Committed Value
    # ==========================================
    df_c2 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Cost'].copy()
    if not df_c2.empty:
        df_c2['CASE'] = 'CASE_02'
        df_c2['CAL_VALUE'] = safe_divide(
            df_c2['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c2['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c2['THRESHOLD'] = df_c2['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c2[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 3: Cost Sub-components
    # Logic: Accrual / Committed Value
    # ==========================================
    
    # CASE 3.1: Preliminaries
    df_c3_01 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Preliminaries'].copy()
    if not df_c3_01.empty:
        df_c3_01['CASE'] = 'CASE_03_01'
        df_c3_01['CAL_VALUE'] = safe_divide(
            df_c3_01['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c3_01['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c3_01['THRESHOLD'] = df_c3_01['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c3_01[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 3.2: Materials
    df_c3_02 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Materials'].copy()
    if not df_c3_02.empty:
        df_c3_02['CASE'] = 'CASE_03_02'
        df_c3_02['CAL_VALUE'] = safe_divide(
            df_c3_02['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c3_02['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c3_02['THRESHOLD'] = df_c3_02['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c3_02[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 3.3: Plant & Machinery
    df_c3_03 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Plant & Machinery'].copy()
    if not df_c3_03.empty:
        df_c3_03['CASE'] = 'CASE_03_03'
        df_c3_03['CAL_VALUE'] = safe_divide(
            df_c3_03['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c3_03['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c3_03['THRESHOLD'] = df_c3_03['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c3_03[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 3.4: Subcontractor
    df_c3_04 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Subcontractor'].copy()
    if not df_c3_04.empty:
        df_c3_04['CASE'] = 'CASE_03_04'
        df_c3_04['CAL_VALUE'] = safe_divide(
            df_c3_04['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c3_04['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c3_04['THRESHOLD'] = df_c3_04['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c3_04[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 4: Gross Profit
    # Logic: Accrual / Committed Value
    # ==========================================
    df_c4 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Gross Profit'].copy()
    if not df_c4.empty:
        df_c4['CASE'] = 'CASE_04'
        df_c4['CAL_VALUE'] = safe_divide(
            df_c4['ACCRUAL_BEFORE_RETENTION_AS_AT'], 
            df_c4['COMMITTED_VALUE_COST_AS_AT']
        ) * 100
        df_c4['THRESHOLD'] = df_c4['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c4[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 5: Materials Cash Flow vs Accrual
    # ==========================================
    df_c5 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Materials'].copy()
    if not df_c5.empty:
        df_c5['CASE'] = 'CASE_05'
        df_c5['CAL_VALUE'] = df_c5['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT']
        df_c5['THRESHOLD'] = df_c5['ACCRUAL_BEFORE_RETENTION_AS_AT']
        risk_dfs.append(df_c5[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 6: Plant & Machinery Cash Flow vs Accrual
    # ==========================================
    df_c6 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Plant & Machinery'].copy()
    if not df_c6.empty:
        df_c6['CASE'] = 'CASE_06'
        df_c6['CAL_VALUE'] = df_c6['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT']
        df_c6['THRESHOLD'] = df_c6['ACCRUAL_BEFORE_RETENTION_AS_AT']
        risk_dfs.append(df_c6[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 7: Materials Committed vs WIP
    # Logic: Committed / WIP
    # ==========================================
    df_c7 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Materials'].copy()
    if not df_c7.empty:
        df_c7['CASE'] = 'CASE_07'
        df_c7['CAL_VALUE'] = safe_divide(
            df_c7['COMMITTED_VALUE_COST_AS_AT'], 
            df_c7['AUDIT_REPORT_WIP']
        ) * 100
        df_c7['THRESHOLD'] = df_c7['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c7[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 8: Plant & Machinery Committed vs WIP
    # Logic: Committed / WIP
    # ==========================================
    df_c8 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Plant & Machinery'].copy()
    if not df_c8.empty:
        df_c8['CASE'] = 'CASE_08'
        df_c8['CAL_VALUE'] = safe_divide(
            df_c8['COMMITTED_VALUE_COST_AS_AT'], 
            df_c8['AUDIT_REPORT_WIP']
        ) * 100
        df_c8['THRESHOLD'] = df_c8['TARGET_COMPLETED'] * 100
        risk_dfs.append(df_c8[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 9: Total Income Projection vs WIP
    # Logic: Projection - Audit Report (WIP)
    # ==========================================
    df_c9 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Income'].copy()
    if not df_c9.empty:
        df_c9['CASE'] = 'CASE_09'
        df_c9['CAL_VALUE'] = df_c9['PROJECTION_AS_AT'] - df_c9['AUDIT_REPORT_WIP']
        df_c9['THRESHOLD'] = 0
        risk_dfs.append(df_c9[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 10: Total Cost Projection vs WIP
    # Logic: Projection - Audit Report (WIP)
    # ==========================================
    df_c10 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Cost'].copy()
    if not df_c10.empty:
        df_c10['CASE'] = 'CASE_10'
        df_c10['CAL_VALUE'] = df_c10['PROJECTION_AS_AT'] - df_c10['AUDIT_REPORT_WIP']
        df_c10['THRESHOLD'] = 0
        risk_dfs.append(df_c10[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # CASE 11: Gross Profit Projection vs WIP
    # Logic: Projection - Audit Report (WIP)
    # ==========================================
    df_c11 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Gross Profit'].copy()
    if not df_c11.empty:
        df_c11['CASE'] = 'CASE_11'
        df_c11['CAL_VALUE'] = df_c11['PROJECTION_AS_AT'] - df_c11['AUDIT_REPORT_WIP']
        df_c11['THRESHOLD'] = 0
        risk_dfs.append(df_c11[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # DEFERRED PAYMENT RISKS (CASES 12 - 14)
    # Logic: Accrual - Cash Flow
    # ==========================================

    # CASE 12: Materials
    df_c12 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Materials'].copy()
    if not df_c12.empty:
        df_c12['CASE'] = 'CASE_12'
        df_c12['CAL_VALUE'] = df_c12['ACCRUAL_BEFORE_RETENTION_AS_AT']
        df_c12['THRESHOLD'] = df_c12['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT'] + 5000.0
        # df_c12['CAL_VALUE'] = df_c12['ACCRUAL_BEFORE_RETENTION_AS_AT'] - df_c12['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT']
        # df_c12['THRESHOLD'] = 5000.0
        risk_dfs.append(df_c12[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 13: Plant & Machinery
    df_c13 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Plant & Machinery'].copy()
    if not df_c13.empty:
        df_c13['CASE'] = 'CASE_13'
        df_c13['CAL_VALUE'] = df_c13['ACCRUAL_BEFORE_RETENTION_AS_AT']
        df_c13['THRESHOLD'] = df_c13['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT'] + 5000.0
        # df_c13['CAL_VALUE'] = df_c13['ACCRUAL_BEFORE_RETENTION_AS_AT'] - df_c13['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT']
        # df_c13['THRESHOLD'] = 5000.0
        risk_dfs.append(df_c13[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 14: Sub-contractor (Trade Only 2.4, Excludes 2.5)
    df_c14 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Subcontractor (2.4 only)'].copy()
    if not df_c14.empty:
        df_c14['CASE'] = 'CASE_14'
        df_c14['CAL_VALUE'] = df_c14['ACCRUAL_BEFORE_RETENTION_AS_AT']
        df_c14['THRESHOLD'] = df_c14['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT'] + 10000.0
        # df_c14['CAL_VALUE'] = df_c14['ACCRUAL_BEFORE_RETENTION_AS_AT'] - df_c14['CASH_FLOW_ACTUAL_RECEIVED_PAID_AS_AT']
        # df_c14['THRESHOLD'] = 10000.0
        risk_dfs.append(df_c14[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])


    # ==========================================
    # OVER BUDGET RISKS (CASES 15 - 18)
    # Logic: Comparing Committed/Projection vs WIP
    # ==========================================

    # CASE 15: Total Cost (Committed vs WIP)
    df_c15 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Cost'].copy()
    if not df_c15.empty:
        df_c15['CASE'] = 'CASE_15'
        df_c15['CAL_VALUE'] = df_c15['COMMITTED_VALUE_COST_AS_AT']
        df_c15['THRESHOLD'] = df_c15['AUDIT_REPORT_WIP']
        risk_dfs.append(df_c15[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 16: Total Cost (Projection vs WIP)
    df_c16 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Cost'].copy()
    if not df_c16.empty:
        df_c16['CASE'] = 'CASE_16'
        df_c16['CAL_VALUE'] = df_c16['PROJECTION_AS_AT']
        df_c16['THRESHOLD'] = df_c16['AUDIT_REPORT_WIP']
        risk_dfs.append(df_c16[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 17: Total Income (Committed vs WIP)
    df_c17 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Income'].copy()
    if not df_c17.empty:
        df_c17['CASE'] = 'CASE_17'
        df_c17['CAL_VALUE'] = df_c17['COMMITTED_VALUE_COST_AS_AT']
        df_c17['THRESHOLD'] = df_c17['AUDIT_REPORT_WIP']
        risk_dfs.append(df_c17[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])

    # CASE 18: Total Income (Projection vs WIP)
    df_c18 = df_mfr_aggregate[df_mfr_aggregate['DIMENSION'] == 'Total Income'].copy()
    if not df_c18.empty:
        df_c18['CASE'] = 'CASE_18'
        df_c18['CAL_VALUE'] = df_c18['PROJECTION_AS_AT']
        df_c18['THRESHOLD'] = df_c18['AUDIT_REPORT_WIP']
        risk_dfs.append(df_c18[['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD']])



    # --- Combine all cases into one DataFrame ---
    if not risk_dfs:
        print("  -> No risk cases matched the criteria.")
        return pd.DataFrame(columns=['PROJECT_CODE', 'CASE', 'DIMENSION', 'CAL_VALUE', 'THRESHOLD'])

    df_risk = pd.concat(risk_dfs, ignore_index=True)

    df_risk['PROJECT_CODE'] = df_risk['PROJECT_CODE'].astype('int64')

    # Sort by the composite keys to keep the dataframe organized
    df_risk = df_risk.sort_values(by=['PROJECT_CODE', 'CASE']).reset_index(drop=True)
    
    print(f"  -> Success. Created df_mfr_risk with {len(df_risk)} records.")
    return df_risk


# --- 6: Comparison Logic ---
def create_comparison(df_mfr_risk: pd.DataFrame, df_risk: pd.DataFrame) -> pd.DataFrame:
    print("\n--- [STEP 5] CREATING COMPARISON DATAFRAME ---")
    
    if df_mfr_risk.empty and df_risk.empty:
        print("  -> Both dataframes are empty. Skipping comparison.")
        return pd.DataFrame()

    # 1. Filter df_risk for the latest report_date
    if not df_risk.empty and 'report_date' in df_risk.columns:
        # Convert to datetime to reliably find the max date
        df_risk['report_date_dt'] = pd.to_datetime(df_risk['report_date'], errors='coerce')
        max_date = df_risk['report_date_dt'].max()
        df_risk_latest = df_risk[df_risk['report_date_dt'] == max_date].copy()
    else:
        df_risk_latest = df_risk.copy()

    # 2. Perform Full Outer Join
    df_merged = pd.merge(
        df_mfr_risk, 
        df_risk_latest, 
        left_on=['PROJECT_CODE', 'CASE'], 
        right_on=['project_code', 'details_risk_code'], 
        how='outer'
    )

    # 3. Coalesce keys (Essential for Full Outer Joins)
    # If project_code is missing from df_risk, fill it with PROJECT_CODE from df_mfr_risk
    if 'project_code' in df_merged.columns and 'PROJECT_CODE' in df_merged.columns:
        df_merged['project_code'] = df_merged['project_code'].combine_first(df_merged['PROJECT_CODE'])
        
    if 'details_risk_code' in df_merged.columns and 'CASE' in df_merged.columns:
        df_merged['details_risk_code'] = df_merged['details_risk_code'].combine_first(df_merged['CASE'])

    # 4. Ensure all requested columns exist to prevent KeyErrors
    expected_cols = [
        'project_code', 'risk_category', 'details_risk_code', 'DIMENSION', 
        'details_comparison_left', 'CAL_VALUE', 'details_comparison_right', 
        'details_comparison_threshold', 'THRESHOLD', 'details_comparison_result'
    ]
    
    for col in expected_cols:
        if col not in df_merged.columns:
            df_merged[col] = np.nan

    # 5. Select final columns
    df_comparison = df_merged[expected_cols].copy()
    
    # Force to numeric to ensure clean subtraction (handles strings/blanks safely)
    df_comparison['details_comparison_left'] = pd.to_numeric(df_comparison['details_comparison_left'], errors='coerce')
    df_comparison['CAL_VALUE'] = pd.to_numeric(df_comparison['CAL_VALUE'], errors='coerce')
    df_comparison['details_comparison_threshold'] = pd.to_numeric(df_comparison['details_comparison_threshold'], errors='coerce')
    df_comparison['details_comparison_right'] = pd.to_numeric(df_comparison['details_comparison_right'], errors='coerce')
    df_comparison['THRESHOLD'] = pd.to_numeric(df_comparison['THRESHOLD'], errors='coerce')
    
    
    # Check if exactly 0 (rounding to 6 decimals prevents tiny floating-point mismatch errors)    
    df_comparison['diff_CAL_VALUE'] = df_comparison['details_comparison_left'] - df_comparison['CAL_VALUE']
    # Check if difference is 0 OR if both base values are null
    df_comparison['is_pass'] = (df_comparison['diff_CAL_VALUE'].round(6) == 0) | (df_comparison['details_comparison_left'].isna() & df_comparison['CAL_VALUE'].isna())
    
    comparison_target = df_comparison['details_comparison_threshold'].combine_first(df_comparison['details_comparison_right'])
    df_comparison['diff_THRESHOLD'] = comparison_target - df_comparison['THRESHOLD']
    df_comparison['is_THRESHOLD_pass'] = df_comparison['diff_THRESHOLD'].round(6) == 0


    # Sort for clean output
    df_comparison = df_comparison.sort_values(by=['project_code', 'details_risk_code']).reset_index(drop=True)

    print(f"  -> Success. Created df_comparison with {len(df_comparison)} records.")
    return df_comparison



# --- Main Execution Loop ---
def main(
    mfr_input_dir: Path = INPUT_DIR,
    risk_input_dir: Path = RISK_DIR,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    print("==================================================")
    print("      STARTING DATA INGESTION            ")
    print("==================================================")
    
    # --- STEP A: MFR FILES INGESTION ---
    print("\n[STEP 1] INGESTING MFR FILES...")
    all_dfs = []
    df_mfr_excel = pd.DataFrame(columns=OUTPUT_COLUMNS) 
    
    if not mfr_input_dir.exists() or not mfr_input_dir.is_dir():
        print(f"Error: MFR Directory does not exist -> {mfr_input_dir}")
    else:
        excel_files = list(mfr_input_dir.glob('*.xlsx'))
        
        if not excel_files:
            print("No Excel files found in the MFR directory.")
        else:
            for idx, file_path in enumerate(excel_files, 1):
                file_name = file_path.name
                print(f"Processing {idx}/{len(excel_files)}: {file_name}...")
                
                temp_df, error_msg = read_excel_and_add_filename(file_path, SHEET_TO_READ)
                
                if error_msg or temp_df is None:
                    print(f"  -> Read Failed: {error_msg}")
                    continue

                temp_df, trans_error = transform_data(temp_df, file_name)

                if trans_error or temp_df is None or temp_df.empty:
                    print(f"  -> Transform Error or empty data: {trans_error}")
                    continue

                try:
                    hk_tz = pytz.timezone('Asia/Hong_Kong')
                    temp_df['DW_CREATED_TIME'] = datetime.datetime.now(hk_tz)
                except:
                    temp_df['DW_CREATED_TIME'] = datetime.datetime.now(datetime.timezone.utc)

                temp_df = temp_df.reindex(columns=OUTPUT_COLUMNS)
                all_dfs.append(temp_df)
                print(f"  -> Success. Added {len(temp_df)} rows.")

            if all_dfs:
                df_mfr_excel = pd.concat(all_dfs, ignore_index=True)
                print(f"\nMFR Ingestion Complete. Total rows in df_mfr_excel: {len(df_mfr_excel)}")

    # --- STEP B: RISK FILE INGESTION ---
    print("\n[STEP 2] INGESTING RISK FILE...")
    df_risk = ingest_risk_file(risk_input_dir, RISK_FILE_PATTERN)

    # --- STEP C: CREATE AGGREGATE ---
    df_mfr_aggregate = create_mfr_aggregate(df_mfr_excel)

    # --- STEP D: CREATE RISK CASES ---
    df_mfr_risk = create_mfr_risk(df_mfr_aggregate)

    # --- STEP E: CREATE COMPARISON ---
    df_comparison = create_comparison(df_mfr_risk, df_risk)

    print("\n==================================================")
    print("      DATA INGESTION FINISHED            ")
    print("==================================================")
    
    return df_mfr_excel, df_risk, df_mfr_aggregate, df_mfr_risk, df_comparison

def write_comparison_csv(df_comparison: pd.DataFrame, output_dir: Path) -> Path:
    """Write the established comparison CSV contract to the Step 6 output folder."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = output_dir / f'risk_check_{timestamp}.csv'
    df_comparison[COMPARISON_OUTPUT_COLUMNS].to_csv(output_path, index=False)
    return output_path


def comparison_succeeded(df_comparison: pd.DataFrame) -> bool:
    """A Step 6 run is successful only when every generated comparison row passes."""
    return not df_comparison.empty and bool(df_comparison['is_pass'].eq(True).all())


FAILURE_COUNT_COLUMNS = [
    "timestamp", "run_id", "reporting_month", "comparison_file",
    "total_cases", "failure_count", "incomplete_count", "status",
]


def append_failure_count(
    df_comparison: pd.DataFrame,
    output_path: Path,
    comparison_path: Path,
    run_id: str = "",
    reporting_month: str = "",
) -> None:
    """Append one auditable Step 6 result to the cumulative CSV log."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "reporting_month": reporting_month,
        "comparison_file": comparison_path.name,
        "total_cases": len(df_comparison),
        "failure_count": int((df_comparison["is_pass"] == False).sum()),
        "incomplete_count": int((df_comparison["is_pass"] != True).sum()),
        "status": "SUCCEEDED" if comparison_succeeded(df_comparison) else "FAILED",
    }
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_COUNT_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare eMFR Excel files against a Risk workbook.")
    parser.add_argument("--mfr-input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--risk-input-dir", type=Path, default=RISK_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--reporting-month", help="Recorded by the pipeline worker for audit correlation.")
    parser.add_argument("--run-id", help="Recorded by the pipeline worker for audit correlation.")
    return parser


def cli_main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    print(f"Step 6 inputs: MFR={args.mfr_input_dir}; Risk={args.risk_input_dir}")
    if args.reporting_month:
        print(f"Reporting month: {args.reporting_month}")
    if args.run_id:
        print(f"Run ID: {args.run_id}")

    try:
        _, _, _, _, df_comparison = main(args.mfr_input_dir, args.risk_input_dir)
    except Exception as exc:
        print(f"Step 6 failed while creating the comparison: {exc}")
        return 1

    if df_comparison.empty:
        print("\nNo comparison data was generated.")
        return 1

    output_path = write_comparison_csv(df_comparison, args.output_dir)
    failed_count = int((df_comparison['is_pass'] == False).sum())
    incomplete_count = int((df_comparison['is_pass'] != True).sum())
    failure_count_path = args.output_dir / "risk_check_failure_count.csv"
    append_failure_count(
        df_comparison, failure_count_path, output_path,
        args.run_id or "", args.reporting_month or "",
    )
    print(f"\nSaved comparison results to: {output_path}")
    print(f"Saved failure count log to: {failure_count_path}")
    print(f"Number of cases where is_pass is False: {failed_count}")

    if comparison_succeeded(df_comparison):
        print("Step 6 succeeded: all comparison cases passed.")
        return 0

    print(f"Step 6 failed: {incomplete_count} comparison case(s) did not pass.")
    return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())