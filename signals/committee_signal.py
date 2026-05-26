"""
Signal: Committee — triggert bei thematischem Match zwischen Member-Committee
und Ticker-Sektor.

Der Tag CommitteeTag liefert pro Trade die Spalten Insider_Sectors und
Relevant_Sectors. Dieses Signal hebt einen Trade als verdaechtig, sobald
Relevant_Sectors mindestens einen Eintrag enthaelt — egal mit welchem
match_type. Die Match-Auswahl steuert settings.RELEVANT_SECTORS_MATCH_MODE
im CommitteeTag (sector_and_industry / sector_or_industry / hybrid).

Trigger:         len(Relevant_Sectors) > 0
Triggering-Date: transaction_date
Scope-Key:       trade_id
"""

import json
from typing import Optional, Dict, Any, List

import pandas as pd

from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'committee'


def _to_int(value: Any, default: int = 0) -> int:
    """Wandelt einen Wert defensiv in int (NaN/None -> default)."""
    try:
        if value is None or pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    """Truthy-Check fuer DataFrame-Werte. NaN/None/leer -> False."""
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip() != ''
    return bool(value)


def _direction_from_transaction_type(transaction_type: Any) -> str:
    """Mappt Quiver-Transaction-Type auf eine kanonische Richtung.

    'Purchase' -> 'buy', 'Sale*' -> 'sell', sonst 'other'."""
    if transaction_type is None:
        return 'other'
    try:
        if pd.isna(transaction_type):
            return 'other'
    except (TypeError, ValueError):
        pass
    text = str(transaction_type).strip().lower()
    if 'purchase' in text:
        return 'buy'
    if 'sale' in text or 'sell' in text:
        return 'sell'
    return 'other'


def parse_relevant_sectors(value: Any) -> List[Dict[str, Any]]:
    """Liest die Relevant_Sectors-Zelle defensiv in eine Liste von Dicts.

    Akzeptiert JSON-String, Python-Liste, NaN/None/leer. Returnt immer eine
    (ggf. leere) Liste.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [e for e in value if isinstance(e, dict)]
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [e for e in parsed if isinstance(e, dict)]


class CommitteeSignal:
    """Bewertet pro Trade, ob ein Committee-Signal ausgeloest wird."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self):
        pass

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        """Gibt None zurueck wenn nicht getriggert, sonst Snapshot-Dict."""
        relevant = parse_relevant_sectors(row.get('Relevant_Sectors'))
        if not relevant:
            return None

        trade_id = row.get('trade_id', '')
        triggering_date = row.get('transaction_date')
        scope_key = str(trade_id) if trade_id is not None else ''

        return {
            'signal_event_id': make_signal_event_id(
                triggering_date, SIGNAL_TYPE, scope_key
            ),
            'trade_id': trade_id,
            'signal_type': SIGNAL_TYPE,
            'triggering_date': triggering_date,
            'scope_key': scope_key,
            'member_id': row.get('bio_guide_id', ''),
            'member_name': row.get('name', ''),
            'chamber': row.get('chamber', ''),
            'ticker': row.get('ticker', ''),
            'issuer_ticker': resolve_issuer_ticker(row),
            'issuer_group_key': row.get('issuer_group_key', '') or '',
            'asset_name_figi': row.get('asset_name_figi')
                or row.get('asset_name', ''),
            'transaction_date': row.get('transaction_date'),
            'disclosure_date': row.get('disclosure_date'),
            'transaction_amount': row.get('amount', ''),
            'direction': _direction_from_transaction_type(
                row.get('transaction_type')
            ),
            'is_ex_officio': _to_bool(row.get('Member_ExOfficio')),
            'match_count': len(relevant),
            'vote_timing_hit_count': _to_int(
                row.get('Vote_Timing_Hit_Count'), default=0
            ),
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
