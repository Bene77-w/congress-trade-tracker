"""
Listet alle Zeilen mit ticker_type != 'Stock' aus trades_master.csv.
Jeder Ticker nur einmal (niedrigster Index gewinnt).
Output: Tabelle + Semikolon-Liste.
"""

import pandas as pd
from pathlib import Path

MASTER = Path(__file__).parent.parent / "data" / "processed" / "trades_master.csv"

df = pd.read_csv(MASTER, low_memory=False)

non_stock = df[df["ticker_type"] != "Stock"].copy()
non_stock = non_stock.drop_duplicates(subset=["ticker"], keep="first")
non_stock = non_stock[["ticker", "ticker_type", "asset_name", "transaction_date", "name"]]
non_stock = non_stock.sort_values(["ticker_type", "ticker"]).reset_index(drop=True)

print("=== Tabelle ===")
print(non_stock.to_string(index=False))

print("\n=== Ticker-Liste ===")
print(";".join(non_stock["ticker"].tolist()))
