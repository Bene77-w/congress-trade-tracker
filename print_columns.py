import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config import settings  # noqa: E402

csv_file = ROOT / 'data' / 'processed' / settings.TRADES_MASTER_COMPLETE_CSV
df = pd.read_csv(csv_file, nrows=0)

print("Spaltennamen in trades_master_complete.csv:\n")
for i, col in enumerate(df.columns, 1):
    print(f"{i:2d}. {col}")

print(f"\nGesamt: {len(df.columns)} Spalten")
