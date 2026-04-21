import pandas as pd

csv_file = 'data/processed/trades_master.csv'
df = pd.read_csv(csv_file, nrows=0)

print("Spaltennamen in trades_master.csv:\n")
for i, col in enumerate(df.columns, 1):
    print(f"{i:2d}. {col}")

print(f"\nGesamt: {len(df.columns)} Spalten")
