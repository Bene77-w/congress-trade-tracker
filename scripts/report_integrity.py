"""Datenintegritaets-Kennzahlen des Masters (Stufe 1, Vorher-/Nachher-Vergleich).

Liest nur, schreibt nichts. Anteile je Jahr (transaction_date) immer mit allen
Trades des Jahres als Nenner, in Prozent.

    python scripts/report_integrity.py [--master PFAD] [--xlsx PFAD] [--cache PFAD]
"""

import argparse
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import settings
from scoring.post_trade_returns import (
    _build_price_lookup, _load_price_cache, _price_at_or_after_fast,
)

RETURN_COLUMNS = ['post_return_90d', 'signed_ar_90d']
MASTER_COLUMNS = ['transaction_date', 'ticker', 'ticker_type', 'figi',
                  'sector_yf', 'Member_Committees_Full', 'signals_triggered',
                  'asset_class_excluded', 'trade_score'] + RETURN_COLUMNS


def _blank(series: pd.Series) -> pd.Series:
    return series.fillna('').astype(str).str.strip().isin(['', 'nan', '[]'])


def _per_year(flags: pd.DataFrame, year: pd.Series) -> pd.DataFrame:
    """Anteil True je Jahr und gesamt (Prozent), dazu n."""
    table = flags.groupby(year).mean().mul(100)
    table.loc['gesamt'] = flags.mean().mul(100)
    table.insert(0, 'n', year.value_counts().reindex(table.index[:-1]).tolist()
                 + [len(year)])
    return table.round(1)


def _section(title: str, body) -> None:
    print(f"\n== {title} ==")
    print(body.to_string() if hasattr(body, 'to_string') else body)


def report(master_path: str, xlsx_path: str, cache_path: str) -> None:
    df = pd.read_csv(master_path, usecols=MASTER_COLUMNS, low_memory=False)
    year = pd.to_datetime(df['transaction_date'], errors='coerce').dt.year
    year = year.astype('Int64').rename('jahr')
    excluded = df['asset_class_excluded'].astype(str).str.lower() == 'true'
    signals = df['signals_triggered'].fillna('').astype(str)

    xlsx = pd.read_excel(xlsx_path)
    _section('Zeilen', pd.Series({
        'Master': len(df), 'XLSX': len(xlsx), 'Master - XLSX': len(df) - len(xlsx),
        'XLSX identische Zeilen (Wiederholungen)': int(xlsx.duplicated().sum()),
    }))

    for col in RETURN_COLUMNS:
        values = pd.to_numeric(df[col], errors='coerce')
        _section(f'{col} je Jahr (%)', _per_year(pd.DataFrame({
            'null': values == 0, 'nan': values.isna(),
            'gueltig': values.notna() & (values != 0),
        }), year))

    lookup = _build_price_lookup(_load_price_cache(cache_path))
    dates = pd.to_datetime(df['transaction_date'], errors='coerce')
    covered = [pd.notna(d) and _price_at_or_after_fast(lookup, t, d) is not None
               for t, d in zip(df['ticker'], dates)]
    _section('Kurs-Cache: Kurs zum Handelstag je Jahr (%)', _per_year(
        pd.DataFrame({'mit_kurs': pd.Series(covered, index=df.index)}), year))

    _section('Ohne Sektor / ohne Ausschuss je Jahr (%)', _per_year(pd.DataFrame({
        'ohne_sektor': _blank(df['sector_yf']),
        'ohne_ausschuss': _blank(df['Member_Committees_Full']),
    }), year))

    hits = signals.str.get_dummies(sep=';').drop(columns='', errors='ignore')
    _section('Trefferquote je Signal und Jahr (%)', _per_year(hits, year))

    ttype = df['ticker_type'].fillna('').astype(str)
    shareclass = ttype == 'shareclass'
    score = pd.to_numeric(df['trade_score'], errors='coerce').fillna(0)
    _section('Asset-Klassen', pd.Series({
        'etf': int((ttype == 'etf').sum()),
        'ausgeschlossen': int(excluded.sum()),
        'ausgeschlossen mit Signal': int((excluded & (signals != '')).sum()),
        'ausgeschlossen mit Score > 0': int((excluded & (score > 0)).sum()),
        'Share-Class-Zeilen': int(shareclass.sum()),
        'Share-Class-Zeilen mit FIGI': int((shareclass & ~_blank(df['figi'])).sum()),
    }))
    _section('Ausgeschlossen nach ticker_type', ttype[excluded].value_counts())
    _section('Ausgeschlossen: haeufigste Ticker',
             df.loc[excluded, 'ticker'].value_counts().head(10))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--master', default=os.path.join(
        BASE_DIR, settings.DATA_PROCESSED_PATH, settings.TRADES_MASTER_COMPLETE_CSV))
    parser.add_argument('--xlsx', default=os.path.join(
        BASE_DIR, settings.DATA_RAW_PATH, settings.QUIVER_TRADES_FILE))
    parser.add_argument('--cache', default=os.path.join(
        BASE_DIR, settings.POST_TRADE_PRICE_CACHE))
    args = parser.parse_args()
    pd.set_option('display.width', 200)
    pd.set_option('display.max_columns', 30)
    report(args.master, args.xlsx, args.cache)


if __name__ == '__main__':
    main()
