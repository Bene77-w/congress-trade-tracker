"""
Hash-basierte Trade-ID für den Congress Trade Tracker.

Berechnet eine stabile 16-Zeichen-Hex-ID aus definierten Trade-Inhalten.
Deterministisch und reihenfolge-invariant innerhalb eines Laufs.

WICHTIG:
- TRADE_ID_SOURCE_COLUMNS und ihre Reihenfolge sind Teil der ID-Semantik.
  Jede Änderung bricht bestehende IDs und lässt alle Trades als "neu"
  erscheinen. Änderungen nur über bewusste Migration.
- None/NaN werden vor dem Hashing in leeren String konvertiert. Das ist
  äquivalent zum alten _create_dup_key-Mechanismus
  (pandas.Series.str.cat mit na_rep='').
"""

import hashlib
from typing import Optional

import pandas as pd

from config import settings


# Spalten, aus denen die ID gebildet wird.
# Muss in der Reihenfolge STABIL bleiben — Änderung bricht bestehende IDs.
# Bewusst hardcoded: Spalten-Reihenfolge ist Teil der ID-Semantik
# (siehe Modul-Docstring) — nicht in settings.py auslagern.
TRADE_ID_SOURCE_COLUMNS = [
    'name',
    'ticker',
    'asset_name',
    'transaction_type',
    'amount',
    'transaction_date',
    'subholding',
    'comment',
]

# Hex-Zeichen der abgeschnittenen SHA256-Summe (Default 16 = 64 Bit Entropie,
# Kollisionsrisiko ~10^-19 bei ~10^5 Zeilen). Konfigurierbar via settings —
# WARNUNG: Aenderung bricht ALLE bestehenden trade_ids in trades_master.csv.
TRADE_ID_LENGTH = getattr(settings, 'TRADE_ID_HASH_LENGTH', 16)


def _to_part(val) -> str:
    """Wandelt einen Zellwert in den String um, der in die Hash-Eingabe geht.

    None/NaN → ''. Alles andere via str(). Äquivalent zum Verhalten von
    pd.Series.astype(str).str.cat(..., na_rep='') im alten _create_dup_key.
    """
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except (TypeError, ValueError):
        # pd.isna reagiert allergisch auf manche Container-Typen.
        pass
    return str(val)


def compute_trade_id(row: pd.Series) -> str:
    """Berechnet stabile trade_id aus Trade-Inhalten.

    Deterministisch: Derselbe Trade-Inhalt liefert immer dieselbe ID.
    SHA256 über '|'.join(parts), davon die ersten TRADE_ID_LENGTH Hex-Zeichen.
    """
    parts = [_to_part(row.get(col, '')) for col in TRADE_ID_SOURCE_COLUMNS]
    combined = '|'.join(parts)
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()[:TRADE_ID_LENGTH]


def compute_trade_ids(df: pd.DataFrame) -> pd.Series:
    """Vektorisiert: Berechnet trade_id für alle Zeilen. Reihenfolge stabil."""
    if df.empty:
        return pd.Series([], dtype='object')
    return df.apply(compute_trade_id, axis=1)


def detect_new_trades(input_df: pd.DataFrame,
                      master_df: pd.DataFrame) -> pd.DataFrame:
    """Delta-Erkennung über trade_id — Multiset-bewusst.

    Liefert die Input-Zeilen, die im Master noch nicht oder noch nicht oft
    genug vorhanden sind. Hat ein trade_id im Master m Kopien und im Input
    i Kopien, werden die (i - m) überzähligen Input-Zeilen als neu gemeldet
    (Multiset-Differenz Input − Master). Pro trade_id gelten die ersten m
    Vorkommen (in Input-Reihenfolge) als bereits im Master abgebildet; die
    späteren werden behalten.

    Das respektiert DEDUP_STRATEGY='keep_all': zusätzliche echte Doppel-
    Trades gehen nicht mehr verloren. Frühere reine Set-Membership-Logik
    (`~input_df['trade_id'].isin(set(master))`) verwarf jede weitere Kopie
    eines bereits vorhandenen trade_id fälschlich als Duplikat. Für den
    Normalfall (komplett neue trade_ids bzw. unveränderte) bleibt das
    Verhalten identisch.

    Args:
        input_df: Neue Trades (müssen trade_id-Spalte haben)
        master_df: Bestehende trades_master_complete.csv

    Returns:
        DataFrame mit den (multiset-)neuen Zeilen aus input_df.
    """
    if master_df is None or master_df.empty or 'trade_id' not in master_df.columns:
        return input_df.copy()
    if input_df.empty or 'trade_id' not in input_df.columns:
        return input_df.copy()
    master_counts = master_df['trade_id'].value_counts()
    # 0-basierter laufender Index je trade_id in Input-Reihenfolge.
    occ = input_df.groupby('trade_id', sort=False).cumcount().to_numpy()
    already = input_df['trade_id'].map(master_counts).fillna(0).astype('int64').to_numpy()
    keep = occ >= already
    return input_df[keep].copy()
