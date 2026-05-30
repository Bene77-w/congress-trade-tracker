"""Diagnose: Vergleicht trade_ids zwischen Quiver-Snapshot und Master.

Beantwortet die Frage: Warum hat der Master ggf. mehr Trades als
der aktuelle Quiver-Snapshot? Welche trade_ids sind "Waisen"
(im Master, aber nicht mehr im aktuellen Quiver)?
"""
from __future__ import annotations

import os
import sys

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from config import settings  # noqa: E402
from scripts.import_trades import QUIVER_COLUMN_MAP  # noqa: E402
from utils.trade_id import compute_trade_ids  # noqa: E402

QUIVER_PATH = os.path.join(
    REPO_ROOT, 'data', 'raw', settings.QUIVER_TRADES_FILE
)
MASTER_PATH = os.path.join(
    REPO_ROOT, 'data', 'processed', 'trades_master_complete.csv'
)


def _load_quiver_with_ids() -> pd.DataFrame:
    ext = os.path.splitext(QUIVER_PATH)[1].lower()
    if ext in ('.xlsx', '.xlsm', '.xls'):
        df = pd.read_excel(QUIVER_PATH)
    else:
        df = pd.read_csv(QUIVER_PATH, low_memory=False)
    df = df.rename(columns=QUIVER_COLUMN_MAP)
    if 'chamber' in df.columns:
        df['chamber'] = df['chamber'].fillna('')

    # Identische Date-Normalisierung wie in TradeImporter.normalize_dates
    if 'transaction_date' in df.columns:
        df['transaction_date'] = pd.to_datetime(
            df['transaction_date'], errors='coerce')
    if 'disclosure_date' in df.columns:
        df['disclosure_date'] = pd.to_datetime(
            df['disclosure_date'], errors='coerce')
    if 'transaction_date' in df.columns and 'disclosure_date' in df.columns:
        missing = df['transaction_date'].isna() & df['disclosure_date'].notna()
        df.loc[missing, 'transaction_date'] = df.loc[missing, 'disclosure_date']
        future = (
            df['transaction_date'].notna()
            & df['disclosure_date'].notna()
            & (df['transaction_date'] > df['disclosure_date'])
        )
        df.loc[future, 'transaction_date'] = df.loc[future, 'disclosure_date']

    df['trade_id'] = compute_trade_ids(df)
    return df


def main() -> None:
    print(f"Lade Quiver: {QUIVER_PATH}")
    q = _load_quiver_with_ids()
    print(f"  {len(q):,} Quiver-Zeilen")
    print(f"  {q['trade_id'].nunique():,} unique trade_ids "
          f"(=> {len(q) - q['trade_id'].nunique()} Quiver-interne Duplikate)")

    print(f"\nLade Master: {MASTER_PATH}")
    m = pd.read_csv(MASTER_PATH, low_memory=False,
                    usecols=lambda c: c in {
                        'trade_id', 'name', 'ticker', 'asset_name',
                        'transaction_type', 'amount', 'transaction_date',
                        'subholding', 'comment', 'disclosure_date',
                        'csv_upload_time', 'last_modified', 'ImportDate',
                    })
    print(f"  {len(m):,} Master-Zeilen")
    print(f"  {m['trade_id'].nunique():,} unique trade_ids im Master")

    q_ids = set(q['trade_id'])
    m_ids = set(m['trade_id'])

    only_master = m_ids - q_ids
    only_quiver = q_ids - m_ids
    both = q_ids & m_ids

    print("\n=== Mengen-Vergleich ===")
    print(f"  Schnitt (in beiden):          {len(both):,}")
    print(f"  Nur im Master (Waisen):       {len(only_master):,}")
    print(f"  Nur in Quiver (neu erwartet): {len(only_quiver):,}")

    if only_master:
        print("\n=== Waisen-Trades (im Master, NICHT im Quiver-Snapshot) ===")
        orphans = m[m['trade_id'].isin(only_master)].copy()
        cols = [c for c in [
            'trade_id', 'name', 'ticker', 'transaction_type',
            'amount', 'transaction_date', 'disclosure_date',
            'subholding', 'ImportDate'
        ] if c in orphans.columns]
        with pd.option_context('display.max_columns', None,
                               'display.width', 200):
            print(orphans[cols].to_string(index=False))

    print("\n=== Hochrechnung der Master-Endgrösse ===")
    print(f"  len(master) vorher: {len(m):,}")
    print(f"  + Quiver-only:      {len(only_quiver):,}")
    print(f"  = nach Merge:       {len(m) + len(only_quiver):,}")
    print("  (DEDUP_STRATEGY='last_wins' => Waisen bleiben erhalten,")
    print("   Quiver-only werden angehaengt; daher Wachstum = Quiver-only.)")


if __name__ == '__main__':
    main()
