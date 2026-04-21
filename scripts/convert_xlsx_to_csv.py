import pandas as pd
import os

def main():
    MAPPINGS_DIR = 'data/mappings'

    print("=== Converting xlsx to CSV ===\n")

    # 1. YF Sectors & Industries
    print("1. Converting yf_secotors_with_industries.xlsx...")
    yf_xlsx = f'{MAPPINGS_DIR}/yf_secotors_with_industries.xlsx'
    yf_csv = f'{MAPPINGS_DIR}/yf_sectors_with_industries.csv'

    df_yf = pd.read_excel(yf_xlsx)
    df_yf.to_csv(yf_csv, index=False, encoding='utf-8')

    file_size_yf = os.path.getsize(yf_csv) / 1024
    print(f"   OK {yf_csv}")
    print(f"      {len(df_yf)} rows, {len(df_yf.columns)} columns, {file_size_yf:.1f} KB\n")

    # 2. Subcommittee Mappings (Industry_Mapping Sheet)
    print("2. Converting subcommittee_sector_industry_mapping_v3.xlsx (Industry_Mapping sheet)...")
    mapping_xlsx = f'{MAPPINGS_DIR}/subcommittee_sector_industry_mapping_v3.xlsx'
    mapping_csv = f'{MAPPINGS_DIR}/subcommittee_sector_industry_mapping.csv'

    df_mapping = pd.read_excel(mapping_xlsx, sheet_name='Industry_Mapping')
    df_mapping.to_csv(mapping_csv, index=False, encoding='utf-8')

    file_size_mapping = os.path.getsize(mapping_csv) / 1024
    print(f"   OK {mapping_csv}")
    print(f"      {len(df_mapping)} rows, {len(df_mapping.columns)} columns, {file_size_mapping:.1f} KB\n")

    # 3. Region Mapping (optional)
    print("3. Converting subcommittee_sector_industry_mapping_v3.xlsx (Region_Mapping sheet)...")
    region_csv = f'{MAPPINGS_DIR}/subcommittee_region_mapping.csv'

    df_region = pd.read_excel(mapping_xlsx, sheet_name='Region_Mapping')
    df_region.to_csv(region_csv, index=False, encoding='utf-8')

    file_size_region = os.path.getsize(region_csv) / 1024
    print(f"   OK {region_csv}")
    print(f"     {len(df_region)} rows, {len(df_region.columns)} columns, {file_size_region:.1f} KB\n")

    print("=== Conversion Complete ===")
    print(f"\nTotal: 3 CSV files created ({file_size_yf + file_size_mapping + file_size_region:.1f} KB)")

if __name__ == '__main__':
    main()
