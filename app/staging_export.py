"""Optionaler CSV-Export markierter Recent Trades.

Das Modul bildet eine neutrale dateibasierte Integrationsgrenze: Congress
Tracker schreibt ein stabiles CSV-Schema in seinen eigenen Exportordner.
Externe Programme koennen die Dateien uebernehmen, sind aber weder importiert
noch fuer den Betrieb dieses Exports erforderlich.

Das Modul ist frei von Streamlit- und Signal-Abhaengigkeiten und offline
testbar. Der Recent-Trades-Tab in ``app/streamlit_app.py`` ruft lediglich
``export_selection`` auf.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config import settings

logger = logging.getLogger(__name__)

# Festes CSV-Spaltenschema (Reihenfolge bindend fuer den Exportvertrag).
# Quellspalten stammen aus dem Master (ungefilterter recent_pool traegt sie);
# 'direction' und 'export_ts' werden beim Export erzeugt.
STAGING_COLUMNS = [
    'trade_id',
    'name', 'chamber', 'Member_Party',
    'ticker', 'issuer_ticker', 'asset_name',
    'transaction_type', 'direction',
    'transaction_date', 'disclosure_date', 'days_since_trade',
    'amount', 'trade_volume_usd', 'trade_amount_max_usd',
    'sector_yf', 'industry_yf', 'relevant_sectors',
    'committee_match', 'Member_Committees_Full',
    'exit_status', 'export_ts',
]

def _normalize_direction(transaction_type: object) -> str:
    """Mappt einen Transaktionstyp lokal auf ``buy``, ``sell`` oder ``other``."""
    if transaction_type is None:
        return 'other'
    try:
        if pd.isna(transaction_type):
            return 'other'
    except (TypeError, ValueError):
        pass

    value = str(transaction_type).strip().lower()
    if 'purchase' in value:
        return 'buy'
    if 'sale' in value or 'sell' in value:
        return 'sell'
    return 'other'


def _derive_direction(df: pd.DataFrame) -> pd.Series:
    """Ableiten der Richtung je Zeile.

    Vorhandene, nicht-leere ``direction``-Werte bleiben erhalten; fehlende
    werden lokal aus ``transaction_type`` berechnet.
    """
    if 'transaction_type' in df.columns:
        derived = df['transaction_type'].map(_normalize_direction)
    else:
        logger.warning(
            "CSV-Export: Spalte 'transaction_type' fehlt — direction "
            "faellt auf 'other' zurueck."
        )
        derived = pd.Series('other', index=df.index)

    if 'direction' not in df.columns:
        return derived

    existing = df['direction']
    is_empty = existing.isna() | (existing.astype(str).str.strip() == '')
    return existing.where(~is_empty, derived)


def build_staging_frame(
    df: pd.DataFrame,
    *,
    export_ts: Optional[str] = None,
) -> pd.DataFrame:
    """Baut aus ``df`` einen Frame mit exakt ``STAGING_COLUMNS`` (Reihenfolge).

    Fehlt eine Quellspalte im Eingabe-Frame, wird sie schema-stabil als leere
    (NaN-)Spalte ergaenzt und eine Warnung geloggt. ``direction`` wird abgeleitet,
    ``export_ts`` auf den Export-Zeitpunkt gesetzt.
    """
    if export_ts is None:
        export_ts = datetime.now().isoformat(timespec='seconds')

    out = pd.DataFrame(index=df.index)
    for col in STAGING_COLUMNS:
        if col == 'direction':
            out[col] = _derive_direction(df)
        elif col == 'export_ts':
            out[col] = export_ts
        elif col in df.columns:
            out[col] = df[col]
        else:
            logger.warning(
                "CSV-Export: Quellspalte '%s' fehlt im DataFrame — als "
                "leere Spalte geschrieben (Schema-Stabilitaet).", col
            )
            out[col] = pd.NA

    # Reihenfolge fixieren (Schutz vor versehentlicher Umsortierung).
    return out[STAGING_COLUMNS]


def export_selection(
    df: pd.DataFrame,
    *,
    export_dir: Optional[Path] = None,
    export_ts: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Schreibt ``df`` als CSV und gibt den Dateipfad zurueck.

    Parameter
    ---------
    df:
        Auszuschreibender Ausschnitt des recent_pool (bereits gefiltert/
        selektiert). Beliebige Zusatzspalten werden ignoriert; fehlende
        Schema-Spalten werden leer ergaenzt.
    export_dir:
        Zielverzeichnis (Default: ``settings.TRADE_EXPORT_DIR``). Wird bei
        Bedarf angelegt.
    export_ts:
        ISO-Zeitpunkt fuer die ``export_ts``-Spalte (Default: jetzt).
    timestamp:
        Dateinamens-Zeitstempel (Default: jetzt, Format aus
        ``settings.TRADE_EXPORT_TIMESTAMP_FORMAT``).

    Rueckgabe
    ---------
    Path der geschriebenen CSV-Datei.
    """
    if export_dir is None:
        export_dir = settings.TRADE_EXPORT_DIR
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    if timestamp is None:
        timestamp = datetime.now().strftime(settings.TRADE_EXPORT_TIMESTAMP_FORMAT)

    frame = build_staging_frame(df, export_ts=export_ts)
    out_path = export_dir / f'{settings.TRADE_EXPORT_FILENAME_PREFIX}_{timestamp}.csv'
    frame.to_csv(out_path, index=False)
    logger.info(
        "CSV-Export: %d Zeilen nach %s geschrieben.", len(frame), out_path
    )
    return out_path
