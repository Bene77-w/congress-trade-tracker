"""
Listet alle Zeilen mit ticker_type != 'Stock' aus trades_master_complete.csv.
Jeder Ticker nur einmal (niedrigster Index gewinnt).
Output: Tabelle + Semikolon-Liste.
"""

import sys
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from config import settings  # noqa: E402

MASTER = ROOT / "data" / "processed" / settings.TRADES_MASTER_COMPLETE_CSV

df = pd.read_csv(MASTER, low_memory=False)

non_stock = df[df["ticker_type"] != "Stock"].copy()
non_stock = non_stock.drop_duplicates(subset=["ticker"], keep="first")
non_stock = non_stock[["ticker", "ticker_type", "asset_name", "transaction_date", "name"]]
non_stock = non_stock.sort_values(["ticker_type", "ticker"]).reset_index(drop=True)

print("=== Tabelle ===")
print(non_stock.to_string(index=False))

print("\n=== Ticker-Liste ===")
print(";".join(non_stock["ticker"].tolist()))
