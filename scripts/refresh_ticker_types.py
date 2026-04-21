"""
Berechnet die ticker_type-Spalte in trades_master.csv mit dem aktuellen
OpenFIGI-Cache + Regex-Regeln neu. Restliche Spalten bleiben unveraendert.

Grund: Die Spalte wurde am 3. April erstellt, der OpenFIGI-Cache ist seitdem
gewachsen (z.B. ETF-Klassifikationen nachgefuellt). Ohne Refresh landen ETFs
als "Stock" in der Spalte -> Filter 0 wuerde sie nicht ausfiltern.

Usage:
  python scripts/refresh_ticker_types.py --dry-run  # nur Diff anzeigen
  python scripts/refresh_ticker_types.py            # Diff + CSV ueberschreiben
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scripts.import_trades import TradeImporter, MASTER_CSV_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Nur Diff anzeigen, CSV nicht ueberschreiben')
    args = parser.parse_args()

    print(f"Lade {MASTER_CSV_PATH} ...")
    df = pd.read_csv(MASTER_CSV_PATH, low_memory=False)
    print(f"  {len(df):,} Zeilen, {len(df.columns)} Spalten\n")

    if 'ticker_type' not in df.columns:
        print("[!] Spalte 'ticker_type' fehlt. Abbruch.")
        sys.exit(1)

    old_types = df['ticker_type'].fillna('(NaN)').copy()
    df = df.drop(columns=['ticker_type'])

    print("Berechne ticker_type neu via Regex + Whitelist + OpenFIGI-Cache ...")
    importer = TradeImporter()
    df = importer.derive_ticker_types(df)
    raw_new = df['ticker_type'].fillna('(NaN)')

    # Regression-Schutz: Wenn neues Label 'Unknown' ist und altes Label informativ war
    # (z.B. delisted Stock wie CELG, ALXN, TWTR -> OpenFIGI findet sie heute nicht mehr),
    # altes Label behalten. Nur aufwaerts-Klassifikationen (Stock -> ETF) durchlassen.
    regression_mask = (raw_new == 'Unknown') & (~old_types.isin(['Unknown', '(NaN)']))
    n_regressions = int(regression_mask.sum())
    new_types = raw_new.where(~regression_mask, old_types)
    df['ticker_type'] = new_types.replace('(NaN)', pd.NA)

    print(f"  Regression-Schutz: {n_regressions:,} Zeilen behalten Legacy-Label")
    print(f"    (OpenFIGI kennt Ticker nicht mehr -> vermutlich delisted/akquiriert)\n")

    # Diff-Auswertung
    changed_mask = old_types != new_types
    n_changed = int(changed_mask.sum())
    print(f"Geaenderte Zeilen: {n_changed:,} von {len(df):,} ({n_changed / len(df) * 100:.2f}%)\n")

    if n_changed > 0:
        print("Transition-Matrix (alt -> neu, Top-20):")
        print("-" * 70)
        transitions = (
            pd.DataFrame({'old': old_types[changed_mask], 'new': new_types[changed_mask]})
            .value_counts().head(20)
        )
        print(f"{'Alt':<22} {'Neu':<22} {'Anzahl':>10}")
        print("-" * 70)
        for (old, new), cnt in transitions.items():
            print(f"{old:<22} {new:<22} {cnt:>10,}")
        print()

    print("Neue Verteilung:")
    new_counts = df['ticker_type'].fillna('(NaN)').value_counts()
    for tt, cnt in new_counts.items():
        pct = cnt / len(df) * 100
        print(f"  {tt:<22} {cnt:>10,}  ({pct:>5.2f}%)")
    print()

    if args.dry_run:
        print("[Dry-Run] CSV wurde NICHT geschrieben.")
        return

    # Spaltenreihenfolge wiederherstellen (ticker_type nach ticker)
    col_order = list(df.columns)
    if 'ticker_type' in col_order and 'ticker' in col_order:
        col_order.remove('ticker_type')
        idx = col_order.index('ticker') + 1
        col_order.insert(idx, 'ticker_type')
        df = df[col_order]

    # Lock-File checken
    lock_file = Path(MASTER_CSV_PATH).parent / f'.~lock.{Path(MASTER_CSV_PATH).name}#'
    if lock_file.exists():
        print(f"[!] Warnung: Lock-File {lock_file.name} existiert. CSV evtl. in LibreOffice geoeffnet.")
        print("    Schreiben wird trotzdem versucht (Linux ignoriert Locks).")

    print(f"Schreibe {MASTER_CSV_PATH} ...")
    df.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
    print(f"  {len(df):,} Zeilen geschrieben.")


if __name__ == '__main__':
    main()
