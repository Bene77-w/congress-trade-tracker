"""B5: Export markierter Recent-Trades ins variantenuebergreifende Agent-Staging.

Schreibt eine Auswahl aus dem Recent-Trades-Pool der Streamlit-Haupt-App nach
``data/agent_staging/trade_selection_<timestamp>.csv`` mit dem festen
Spaltenschema aus dem Suspicious-Trades-Basisplan (§5b). Das Zwischenlager ist
bewusst variantenuebergreifend (ausserhalb der modul-eigenen DATA_DIR der
Agent-Klone): die Haupt-App schreibt hier, die Control-UIs der Varianten lesen.

Dieses Modul ist absichtlich frei von Streamlit-Abhaengigkeiten und daher
offline testbar. Der Recent-Trades-Tab in ``app/streamlit_app.py`` ruft nur
``export_selection``.

Kontrakt-Hinweis: Der Staging-Reader der Agent-Varianten
(``suspicious_trades*/trades_access``) liest exakt dieses Schema und berechnet
``direction`` bei Fehlen ebenfalls via
``signals.committee_signal._direction_from_transaction_type`` nach. Deshalb wird
``direction`` hier mit genau derselben Funktion abgeleitet (Rueckgabe
'buy'/'sell'/'other') — nur so bleiben Export und Reader konsistent. (Die
"+1/-1"-Annotation der Plan-Tabelle beschreibt die Semantik der Richtung, nicht
das tatsaechliche Rueckgabeformat dieser Funktion.)
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config import settings
from signals.committee_signal import _direction_from_transaction_type

logger = logging.getLogger(__name__)

# Festes §5b-Spaltenschema (Reihenfolge bindend fuer den Staging-Reader-Kontrakt).
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

# Spalten, die beim Export erzeugt und daher nicht als "fehlende Quellspalte"
# gewarnt werden.
_GENERATED_COLUMNS = {'direction', 'export_ts'}

# Dateinamens-Zeitstempel (kompakt, sortierbar).
_FILENAME_TS_FMT = '%Y%m%d-%H%M%S'


def _derive_direction(df: pd.DataFrame) -> pd.Series:
    """Ableiten der Richtung je Zeile.

    Vorhandene, nicht-leere ``direction``-Werte bleiben erhalten; fehlende
    werden via ``_direction_from_transaction_type`` aus ``transaction_type``
    berechnet (identisch zum Staging-Reader-Kontrakt).
    """
    if 'transaction_type' in df.columns:
        derived = df['transaction_type'].map(_direction_from_transaction_type)
    else:
        logger.warning(
            "Staging-Export: Spalte 'transaction_type' fehlt — direction "
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
                "Staging-Export: Quellspalte '%s' fehlt im DataFrame — als "
                "leere Spalte geschrieben (Schema-Stabilitaet).", col
            )
            out[col] = pd.NA

    # Reihenfolge fixieren (Schutz vor versehentlicher Umsortierung).
    return out[STAGING_COLUMNS]


def export_selection(
    df: pd.DataFrame,
    *,
    staging_dir: Optional[Path] = None,
    export_ts: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Schreibt ``df`` als Staging-CSV und gibt den Dateipfad zurueck.

    Parameter
    ---------
    df:
        Auszuschreibender Ausschnitt des recent_pool (bereits gefiltert/
        selektiert). Beliebige Zusatzspalten werden ignoriert; fehlende
        Schema-Spalten werden leer ergaenzt.
    staging_dir:
        Zielverzeichnis (Default: ``settings.AGENT_STAGING_DIR``). Wird bei
        Bedarf angelegt.
    export_ts:
        ISO-Zeitpunkt fuer die ``export_ts``-Spalte (Default: jetzt).
    timestamp:
        Dateinamens-Zeitstempel (Default: jetzt, Format ``%Y%m%d-%H%M%S``).

    Rueckgabe
    ---------
    Path der geschriebenen CSV-Datei.
    """
    if staging_dir is None:
        staging_dir = settings.AGENT_STAGING_DIR
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    if timestamp is None:
        timestamp = datetime.now().strftime(_FILENAME_TS_FMT)

    frame = build_staging_frame(df, export_ts=export_ts)
    out_path = staging_dir / f'trade_selection_{timestamp}.csv'
    frame.to_csv(out_path, index=False)
    logger.info(
        "Staging-Export: %d Zeilen nach %s geschrieben.", len(frame), out_path
    )
    return out_path
