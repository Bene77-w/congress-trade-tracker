"""
Signal: Committee — triggert bei thematischem Match zwischen Member-Committee
und Ticker-Sektor.

Der Tag CommitteeTag liefert pro Trade die Spalten Relevance_Score,
Insider_Sectors und Relevant_Sectors. Dieses Signal hebt einen Trade als
verdaechtig, sobald Relevance_Score den Schwellwert COMMITTEE_THRESHOLD
erreicht.

Trigger:      Relevance_Score >= settings.COMMITTEE_THRESHOLD
Triggering-Date: transaction_date
Scope-Key:    trade_id
"""

from typing import Optional, Dict, Any

import pandas as pd

from config import settings
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


class CommitteeSignal:
    """Bewertet pro Trade, ob ein Committee-Signal ausgeloest wird."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self, threshold: Optional[int] = None):
        self.threshold = (
            threshold if threshold is not None
            else getattr(settings, 'COMMITTEE_THRESHOLD', 33)
        )

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        """Gibt None zurueck wenn nicht getriggert, sonst Snapshot-Dict."""
        score = _to_int(row.get('Relevance_Score'), default=0)
        if score < self.threshold:
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
            'relevance_score': score,
            'vote_timing_hit_count': _to_int(
                row.get('Vote_Timing_Hit_Count'), default=0
            ),
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
