"""
Hash-basierte Signal-Event-ID fuer den Congress Trade Tracker.

Berechnet eine stabile 16-Zeichen-Hex-ID fuer jede Signal-Auslösung. Die ID
gruppiert mehrere Trades, die zur selben Auslösung gehören (z.B. ein
Schwarm-Signal trifft drei Trades gleichzeitig — alle drei bekommen dieselbe
signal_event_id).

WICHTIG:
- Die Hash-Inputs (triggering_date, signal_type, scope_key) sind Teil der
  ID-Semantik. Aenderung der Konstruktionslogik oder der scope_key-Konvention
  bricht bestehende IDs in suspicious_events.csv.
- None/NaN-Werte in den Inputs werden vor dem Hashing in leeren String
  konvertiert (defensiv).
- Die Konvention fuer scope_key pro Signal-Familie ist in
  organisation/tool_specific_md/signal_info.md dokumentiert.
"""

import hashlib
from datetime import date, datetime
from typing import Union

import pandas as pd

from config import settings


# Hex-Zeichen der abgeschnittenen SHA256-Summe. Standardmaessig identisch zur
# trade_id-Laenge — beide IDs teilen das gleiche Hash-Schema im Projekt.
SIGNAL_EVENT_ID_LENGTH = getattr(settings, 'TRADE_ID_HASH_LENGTH', 16)


def _to_iso(value: Union[date, datetime, str, None]) -> str:
    """Normalisiert ein Datum auf ISO-Format (YYYY-MM-DD).

    Akzeptiert date, datetime, str oder None. None/NaN wird zu leerem String.
    """
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    # String — versuch pd.to_datetime fuer robuste Parsing-Behandlung
    try:
        parsed = pd.to_datetime(value)
        return parsed.date().isoformat()
    except (ValueError, TypeError):
        return str(value)


def _to_part(value) -> str:
    """Wandelt einen Input in den String, der in die Hash-Eingabe geht."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value)


def make_signal_event_id(triggering_date: Union[date, datetime, str],
                         signal_type: str,
                         scope_key: str) -> str:
    """Berechnet die stabile signal_event_id fuer eine Signal-Auslösung.

    Deterministisch: gleiche Inputs liefern immer dieselbe ID. Ein Re-Run
    derselben Pipeline produziert daher denselben Hash, was zusammen mit dem
    (signal_event_id, trade_id)-Duplikat-Schutz in suspicious_events.csv
    Idempotenz garantiert.

    Args:
        triggering_date: Auslösedatum (siehe signal_info.md, Spalte
            "Triggering-Date" pro Signal).
        signal_type: z.B. 'committee', 'swarm_buy', 'contrarian', 'swarm_paradox'.
        scope_key: Variabel je nach Signal-Familie. Konvention:
            - per-Trade-Signale: trade_id
            - Sequenz (member×asset): f"{member_id}|{issuer_ticker}|{window_start}"
            - Schwarm Buy/Sell: f"{issuer_group_key}|{direction}|{window_start}"
            - Schwarm Konflikt: f"conflict|{issuer_group_key}|{swarm_direction}|{window_start}"
            - Schwarm Paradox: f"paradox|{issuer_group_key}|{swarm_direction}|{window_start}"

    Returns:
        16-Zeichen-Hex-String (SHA256, abgeschnitten).
    """
    parts = [
        _to_iso(triggering_date),
        _to_part(signal_type),
        _to_part(scope_key),
    ]
    combined = '|'.join(parts)
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()[:SIGNAL_EVENT_ID_LENGTH]
