"""
Reporting: Verteilung der ticker_type-Kategorien in trades_master_complete.csv.

Liest die via Stage `tickerclassify` (utils/ticker_classifier.classify)
klassifizierte Spalte aus data/processed/trades_master_complete.csv und zeigt:
  1. Verteilung pro Kategorie (Anzahl, Anteil, Beispiel-Ticker)
  2. Top 20 'unknown'-Ticker nach Haeufigkeit (Kandidaten fuer Regex-Luecken)
  3. OpenFIGI-Cache-Statistiken (alte ticker_types-Tabelle, Legacy)

WICHTIG (Rebuild Teil 3): ticker_type-Werte sind jetzt kleinbuchstabig
(TickerType-Enum-Values, z.B. 'stock', 'etf', 'unknown') statt der
frueheren Großbuchstaben-Strings. Manual-Review-Tickers liegen
zusaetzlich in manual_review_tickers.csv.
"""

import os
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import settings
from utils.openfigi_client import OpenFigiClient

TRADES_MASTER = BASE_DIR / settings.DATA_PROCESSED_PATH / settings.TRADES_MASTER_COMPLETE_CSV


def report_trades_distribution():
    print(f"Lade {TRADES_MASTER} ...")
    df = pd.read_csv(TRADES_MASTER, usecols=['ticker', 'ticker_type'], dtype=str)
    total = len(df)
    print(f"Total Trades: {total:,}\n")

    counts = df['ticker_type'].fillna('(NaN)').value_counts()

    print("=" * 90)
    print(f"{'Ticker-Type':<22} {'Anzahl':>10}  {'Anteil':>7}  Beispiel-Ticker")
    print("=" * 90)
    for ticker_type, cnt in counts.items():
        pct = cnt / total * 100
        examples = (
            df[df['ticker_type'].fillna('(NaN)') == ticker_type]['ticker']
            .dropna().unique()[:6]
        )
        print(f"{ticker_type:<22} {cnt:>10,}  {pct:>6.2f}%  {', '.join(examples)}")
    print("=" * 90)
    print()

    return df


def report_top_unknown(df: pd.DataFrame, top_n: int = 20):
    unknown_mask = df['ticker_type'].fillna('').str.lower() == 'unknown'
    unknown_df = df[unknown_mask]
    if unknown_df.empty:
        print("Keine 'Unknown'-Ticker gefunden.\n")
        return

    top = unknown_df['ticker'].value_counts().head(top_n)
    print(f"Top {top_n} 'Unknown'-Ticker (Kandidaten fuer Regex-Luecken)")
    print("-" * 60)
    print(f"{'Ticker':<25} {'Anzahl Trades':>15}")
    print("-" * 60)
    for ticker, cnt in top.items():
        print(f"{ticker:<25} {cnt:>15,}")
    print()


def report_openfigi_cache():
    cache_db = BASE_DIR / settings.DATA_CACHE_PATH / settings.TICKER_TYPE_CACHE_DB
    if not cache_db.exists():
        print(f"OpenFIGI-Cache nicht vorhanden: {cache_db}")
        return

    client = OpenFigiClient(cache_db_path=str(cache_db))
    stats = client.get_cache_stats()

    print(f"OpenFIGI-Cache ({cache_db})")
    print("-" * 60)
    print(f"Gesamt-Eintraege: {stats['total']:,}\n")

    print("Security-Types im Cache:")
    for t, cnt in stats['by_type'].items():
        print(f"  {t:<30} {cnt:>8,}")
    print()

    print("Source-Verteilung:")
    for s, cnt in stats['by_source'].items():
        print(f"  {s:<30} {cnt:>8,}")
    print()


def main():
    df = report_trades_distribution()
    report_top_unknown(df)
    report_openfigi_cache()


if __name__ == '__main__':
    main()
