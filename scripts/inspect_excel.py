
import pandas as pd
from config.settings import BASE_DIR

files = [
    BASE_DIR / "data" / "raw" / "subcommittee_sector_industry_mapping_v3.xlsx",
    BASE_DIR / "data" / "raw" / "yf_secotors_with_industries.xlsx",
]

for file_path in files:
    print(f"\n--- Inspecting {file_path.name} ---")
    try:
        if not file_path.exists():
            print(f"File not found: {file_path}")
        else:
            df = pd.read_excel(file_path)
            print("Columns:")
            print(df.columns.tolist())
            print("\nFirst 3 rows:")
            print(df.head(3).to_string())
    except Exception as e:
        print(f"Error reading Excel file: {e}")
