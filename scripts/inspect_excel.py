
import pandas as pd
import os

files = [
    r"c:\Users\benedikt\OneDrive\BMB Tradingprogramm\congress_tracker\data\raw\subcommittee_sector_industry_mapping_v3.xlsx",
    r"c:\Users\benedikt\OneDrive\BMB Tradingprogramm\congress_tracker\data\raw\yf_secotors_with_industries.xlsx"
]

for file_path in files:
    print(f"\n--- Inspecting {os.path.basename(file_path)} ---")
    try:
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
        else:
            df = pd.read_excel(file_path)
            print("Columns:")
            print(df.columns.tolist())
            print("\nFirst 3 rows:")
            print(df.head(3).to_string())
    except Exception as e:
        print(f"Error reading Excel file: {e}")
