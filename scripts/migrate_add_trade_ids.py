"""
Einmal-Migration (Rebuild Teil 3): vergibt trade_id fuer den bestehenden
trades_master.csv und etabliert die complete/curated-Trennung.

Ablauf:
  1. trades_master.csv einlesen
  2. trade_id via compute_trade_ids berechnen
  3. Bestehenden Master als trades_master_pre_migration_<ts>.csv sichern
  4. Neu geschriebenen Master als trades_master_complete.csv speichern
  5. Kuratierte Sicht (settings.CURATED_COLUMNS) als trades_master.csv speichern

Idempotent: Wenn 'trade_id' bereits in der CSV existiert, Rueckfrage am
Terminal (umgehbar mit --force).

Aufruf:
  python scripts/migrate_add_trade_ids.py --dry-run   # nur Diff anzeigen
  python scripts/migrate_add_trade_ids.py             # echte Migration
  python scripts/migrate_add_trade_ids.py --force     # auch wenn ID schon da
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import settings
from utils.trade_id import TRADE_ID_SOURCE_COLUMNS, compute_trade_ids
from scripts.import_trades import (
    MASTER_CSV_PATH,
    MASTER_COMPLETE_CSV_PATH,
    ARCHIVE_BACKUP_DIR,
    TradeImporter,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true',
                        help='Nur Diff anzeigen, nichts schreiben')
    parser.add_argument('--force', action='store_true',
                        help='Migration auch ausfuehren wenn trade_id schon existiert')
    args = parser.parse_args()

    if not os.path.isfile(MASTER_CSV_PATH):
        print(f"[X] {MASTER_CSV_PATH} nicht gefunden — nichts zu migrieren.")
        sys.exit(1)

    print(f"Lade {MASTER_CSV_PATH} ...")
    df = pd.read_csv(MASTER_CSV_PATH, low_memory=False)
    print(f"  {len(df):,} Zeilen, {len(df.columns)} Spalten")

    if 'trade_id' in df.columns and not args.force:
        ans = input("  [!] Spalte 'trade_id' existiert bereits. Neu vergeben? [j/N] ")
        if ans.strip().lower() != 'j':
            print("  Abbruch.")
            return

    # 1. trade_id berechnen.
    # WICHTIG: Datums-Spalten vor dem Hashen pipeline-aequivalent nach
    # Timestamp konvertieren — die Pipeline hasht NACH normalize_dates()
    # (str(Timestamp) = 'YYYY-MM-DD 00:00:00'), die CSV speichert aber
    # 'YYYY-MM-DD'. Ohne die Konvertierung entstehen andere IDs als im
    # Master -> der naechste Delta-Lauf saehe ALLE Trades als neu
    # (Massen-Duplikate). Empirisch gegen bestehende Master-IDs verifiziert.
    print("\nBerechne trade_id ...")
    hash_df = df.copy()
    for _col in ('transaction_date', 'disclosure_date'):
        if _col in hash_df.columns and _col in TRADE_ID_SOURCE_COLUMNS:
            hash_df[_col] = pd.to_datetime(hash_df[_col], errors='coerce')
    df['trade_id'] = compute_trade_ids(hash_df)
    n_unique = df['trade_id'].nunique()
    n_dup = len(df) - n_unique
    print(f"  {n_unique:,} unique IDs, {n_dup:,} Duplikate "
          f"(DEDUP_STRATEGY='{settings.DEDUP_STRATEGY}')")

    # 2. Duplikat-Behandlung gemaess settings.DEDUP_STRATEGY
    if n_dup > 0:
        if settings.DEDUP_STRATEGY == 'last_wins':
            df = df[~df['trade_id'].duplicated(keep='last')].reset_index(drop=True)
            print(f"  -> {len(df):,} Zeilen nach Dedup (last_wins)")
        else:
            print(f"  -> {len(df):,} Zeilen behalten (keep_all)")

    if args.dry_run:
        print("\n[Dry-Run] Es wurde nichts geschrieben.")
        return

    # 3. Backup des Original-Masters
    os.makedirs(ARCHIVE_BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_path = os.path.join(ARCHIVE_BACKUP_DIR,
                               f'trades_master_pre_migration_{ts}.csv')
    pd.read_csv(MASTER_CSV_PATH, low_memory=False).to_csv(
        backup_path, index=False, encoding='utf-8')
    print(f"\nBackup: {backup_path}")

    # 4. Complete-Master schreiben
    df.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
    print(f"Complete: {MASTER_COMPLETE_CSV_PATH} ({len(df):,} Trades)")

    # 5. Curated-Master via _export_curated_master
    importer = TradeImporter()
    curated = importer._export_curated_master(df)
    curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
    print(f"Curated:  {MASTER_CSV_PATH} ({len(curated):,} Trades, "
          f"{len(curated.columns)} Spalten)")

    print(f"\n[OK] Migration abgeschlossen.")


if __name__ == '__main__':
    main()
