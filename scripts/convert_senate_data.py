import pandas as pd
import os

def convert_senate_data():
    # Paths
    input_file = r'data/raw/us_senate_all_committees.xlsx'
    output_file = r'data/raw/us_senate_all_committees.csv'
    
    print(f"Reading from {input_file}...")
    
    try:
        # Read Excel file
        df = pd.read_excel(input_file)
        
        # Rename columns to match House format
        # House format: Committee, Subcommittee
        # Senate format (observed): main_committee, sub_committee
        
        # Check if columns exist
        required_cols = ['main_committee', 'subcommittee']
        if not all(col in df.columns for col in required_cols):
            print(f"Error: Expected columns {required_cols} not found. Available columns: {df.columns.tolist()}")
            return
            
        # Select and rename
        df_new = df[['main_committee', 'subcommittee']].copy()
        df_new.columns = ['Committee', 'Subcommittee']
        
        # Clean up data (optional but good practice)
        # Remove empty rows if any
        df_new = df_new.dropna(how='all')
        
        # Save to CSV
        print(f"Writing to {output_file}...")
        df_new.to_csv(output_file, index=False)
        print("Conversion successful!")
        
        # Show preview
        print("\nPreview:")
        print(df_new.head().to_string())
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    convert_senate_data()
