"""
issuer_ticker-Resolver: liefert den besten verfuegbaren Issuer-Identifier
fuer Cross-Share-Class-Gruppierung (Schwarm- und Sequenz-Signale).

Fallback-Kette:
    share_class_figi -> ticker_base -> ticker

Hintergrund:
    Ein Issuer (z.B. Alphabet) kann mehrere Securities mit
    unterschiedlichen Tickern + FIGIs haben (GOOGL Class A, GOOG Class C).
    OpenFIGI's `shareClassFIGI` ist der einzige eindeutige
    Issuer x Aktienklassen-Identifier, der GOOGL und GOOG zusammenfuehrt.
    Wenn share_class_figi vorhanden ist (Erstkandidat), gruppiert
    issuer_ticker also alle Aktienklassen desselben Issuers korrekt.
    Bei FIGI-Miss greift ticker_base (strippt Suffixe wie .WS, .UN, .A,
    vereinigt aber keine getrennten Symbole), und als Last-Resort der
    raw ticker. Damit ist die Spalte garantiert nicht-leer, solange
    mindestens einer der drei Werte gesetzt ist.
"""

from typing import Any, Union

import pandas as pd


def _is_empty(val: Any) -> bool:
    """True wenn val None, NaN/pd.NA/NaT oder leerer/whitespace-String ist."""
    if val is None:
        return True
    try:
        # pd.isna statt isinstance(float): faengt auch pd.NA/NaT (nullable/
        # Arrow-Dtypes ab pandas 3.0), die sonst als '<NA>'/'NaT' als
        # Issuer-Key durchgingen.
        if pd.isna(val):
            return True
    except (TypeError, ValueError):
        pass  # Container-Typen: pd.isna liefert Array/wirft -> kein NaN
    if isinstance(val, str) and not val.strip():
        return True
    return False


def _get(row: Any, key: str) -> Any:
    """Robuster Getter fuer dict, pd.Series, oder Objekte mit .get()."""
    if hasattr(row, 'get'):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def resolve_issuer_ticker(row: Union[dict, pd.Series]) -> str:
    """Aufloesen des issuer_ticker fuer eine Trade-Zeile.

    Args:
        row: dict, pandas Series oder vergleichbares Mapping. Erwartet
            mindestens `ticker`; `share_class_figi` und `ticker_base`
            optional.

    Returns:
        Trimmed Issuer-Identifier als string. Erstkandidat
        share_class_figi (Bloomberg-FIGI), Fallback ticker_base, dann
        ticker. Leerer string nur wenn alle drei leer/whitespace sind.
    """
    scf = _get(row, 'share_class_figi')
    if not _is_empty(scf):
        return str(scf).strip()

    base = _get(row, 'ticker_base')
    if not _is_empty(base):
        return str(base).strip()

    ticker = _get(row, 'ticker')
    if not _is_empty(ticker):
        return str(ticker).strip()

    return ''
