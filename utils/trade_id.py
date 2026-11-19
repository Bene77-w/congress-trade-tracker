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
- Die ID hängt von der str()-REPRÄSENTATION der Werte ab. Die Pipeline hasht
  NACH normalize_dates(), d.h. transaction_date ist dort ein Timestamp
  (str -> 'YYYY-MM-DD 00:00:00'). Wer den Master aus der CSV lädt (dort steht
  'YYYY-MM-DD') und hasht, MUSS die Datums-Spalten vorher via pd.to_datetime
  konvertieren, sonst entstehen inkompatible IDs.
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

