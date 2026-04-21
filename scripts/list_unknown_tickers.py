"""
Listet alle Zeilen mit ticker_type == 'Unknown' aus trades_master.csv.
Jeder Ticker nur einmal (niedrigster Index gewinnt).
Output: Tabelle + Semikolon-Liste.
"""

import pandas as pd
from pathlib import Path

MASTER = Path(__file__).parent.parent / "data" / "processed" / "trades_master.csv"

df = pd.read_csv(MASTER, low_memory=False)

unknown = df[df["ticker_type"] == "Unknown"].copy()
unknown = unknown.drop_duplicates(subset=["ticker"], keep="first")
unknown = unknown[["ticker", "ticker_type", "asset_name", "transaction_date", "name"]]
unknown = unknown.sort_values("ticker").reset_index(drop=True)

print("=== Tabelle ===")
print(unknown.to_string(index=False))

print("\n=== Ticker-Liste ===")
print(";".join(unknown["ticker"].tolist()))
