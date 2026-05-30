"""
Listet alle Zeilen mit ticker_type == 'Unknown' aus trades_master_complete.csv.
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

unknown = df[df["ticker_type"] == "Unknown"].copy()
unknown = unknown.drop_duplicates(subset=["ticker"], keep="first")
unknown = unknown[["ticker", "ticker_type", "asset_name", "transaction_date", "name"]]
unknown = unknown.sort_values("ticker").reset_index(drop=True)

print("=== Tabelle ===")
print(unknown.to_string(index=False))

print("\n=== Ticker-Liste ===")
print(";".join(unknown["ticker"].tolist()))
