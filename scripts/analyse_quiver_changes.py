"""
Analyse: Welche Trades hat Quiver geaendert/entfernt seit dem letzten Full Rebuild?

Vergleicht congress-trading-all.xlsx (aktuelle Quelle) mit trades_master.csv.
Identifiziert:
  1. Entfernte Trades (im Master, aber nicht mehr in Quiver)
  2. Neue Trades (in Quiver, aber nicht im Master)
  3. Netto-Differenz

Usage:
  python scripts/analyse_quiver_changes.py
"""

import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import settings
from utils.trade_id import TRADE_ID_SOURCE_COLUMNS

# Spalten-Mapping (Quiver → intern)
COLUMN_MAP = {
    'Name': 'name', 'Ticker': 'ticker', 'Company': 'asset_name',
    'Traded': 'transaction_date', 'Filed': 'disclosure_date',
    'Transaction': 'transaction_type', 'Trade_Size_USD': 'amount',
    'Comments': 'comment', 'Subholding': 'subholding',
    'Party': 'Member_Party', 'Chamber': 'chamber', 'State': 'Member_State',
}

# Key-Spalten fuer Vergleich — kanonische Trade-Identitaet (dieselbe Spaltenmenge,
# aus der auch der trade_id-Hash gebildet wird).
KEY_COLS = TRADE_ID_SOURCE_COLUMNS


def make_keys(df: pd.DataFrame) -> pd.Series:
    """Erstellt String-Keys fuer Vergleich."""
    parts = []
    for col in KEY_COLS:
        if col in df.columns:
            parts.append(df[col].astype(str))
    return parts[0].str.cat(parts[1:], sep='|', na_rep='')


def main():
    quiver_path = os.path.join(BASE_DIR, settings.DATA_RAW_PATH, settings.QUIVER_TRADES_FILE)
    master_path = os.path.join(BASE_DIR, settings.DATA_PROCESSED_PATH, settings.TRADES_MASTER_COMPLETE_CSV)

    # 1. Laden
    print("Lade Quiver-Daten...")
    ext = os.path.splitext(quiver_path)[1].lower()
    if ext in ('.xlsx', '.xls'):
        quiver = pd.read_excel(quiver_path)
    else:
        quiver = pd.read_csv(quiver_path, low_memory=False)
    quiver = quiver.rename(columns=COLUMN_MAP)
    quiver['transaction_date'] = pd.to_datetime(quiver['transaction_date'], errors='coerce')

    print("Lade Master-Daten...")
    master = pd.read_csv(master_path, low_memory=False)
    master['transaction_date'] = pd.to_datetime(master['transaction_date'], errors='coerce')

    print(f"\nQuiver: {len(quiver):,} Trades")
    print(f"Master: {len(master):,} Trades")
    print(f"Netto-Differenz: {len(quiver) - len(master):+,}")

    # 2. Keys erstellen
    quiver_keys = make_keys(quiver)
    master_keys = make_keys(master)

    quiver_key_set = set(quiver_keys)
    master_key_set = set(master_keys)

    # 3. Neue Trades (in Quiver, nicht im Master)
    new_mask = ~quiver_keys.isin(master_key_set)
    new_trades = quiver[new_mask].copy()

    # 4. Entfernte Trades (im Master, nicht mehr in Quiver)
    removed_mask = ~master_keys.isin(quiver_key_set)
    removed_trades = master[removed_mask].copy()

    # Ausgabe
    print(f"\n{'='*60}")
    print(f"NEUE Trades (in Quiver, nicht im Master): {len(new_trades)}")
    print(f"ENTFERNTE Trades (im Master, nicht in Quiver): {len(removed_trades)}")
    print(f"{'='*60}")

    # Entfernte Trades Details
    if not removed_trades.empty:
        show_cols = ['name', 'ticker', 'asset_name', 'transaction_type',
                     'amount', 'transaction_date', 'chamber']
        show_cols = [c for c in show_cols if c in removed_trades.columns]

        print(f"\n--- ENTFERNTE TRADES ({len(removed_trades)}) ---")
        for i, (_, row) in enumerate(removed_trades[show_cols].iterrows()):
            print(f"\n  [{i+1}] {row.get('name', '?')} | {row.get('ticker', '?')} | "
                  f"{row.get('asset_name', '?')}")
            print(f"      {row.get('transaction_type', '?')} | {row.get('amount', '?')} | "
                  f"{row.get('transaction_date', '?')} | {row.get('chamber', '?')}")

    # Neue Trades Details (Kurzfassung)
    if not new_trades.empty:
        show_cols = ['name', 'ticker', 'asset_name', 'transaction_type',
                     'amount', 'transaction_date', 'chamber']
        show_cols = [c for c in show_cols if c in new_trades.columns]

        print(f"\n--- NEUE TRADES ({len(new_trades)}) ---")
        for i, (_, row) in enumerate(new_trades[show_cols].iterrows()):
            print(f"\n  [{i+1}] {row.get('name', '?')} | {row.get('ticker', '?')} | "
                  f"{row.get('asset_name', '?')}")
            print(f"      {row.get('transaction_type', '?')} | {row.get('amount', '?')} | "
                  f"{row.get('transaction_date', '?')} | {row.get('chamber', '?')}")

    # Zusammenfassung nach Politiker
    if not removed_trades.empty:
        print(f"\n--- ENTFERNTE nach Politiker ---")
        by_name = removed_trades.groupby('name').size().sort_values(ascending=False)
        for name, count in by_name.items():
            print(f"  {name}: {count} Trades")

    if not new_trades.empty:
        print(f"\n--- NEUE nach Politiker ---")
        by_name = new_trades.groupby('name').size().sort_values(ascending=False)
        for name, count in by_name.items():
            print(f"  {name}: {count} Trades")


if __name__ == '__main__':
    main()
