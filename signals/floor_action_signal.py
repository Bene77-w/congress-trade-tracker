"""
Signal: Floor Action — triggert wenn der Trade einen oder mehrere Treffer im
Vote-Timing-Tag aufweist (Trade nahe an einer thematisch passenden
Kongress-Abstimmung).

Trigger:         Vote_Timing_Hit_Count >= settings.VOTE_TIMING_HIT_THRESHOLD
Triggering-Date: transaction_date
Scope-Key:       trade_id
"""

from typing import Optional, Dict, Any

import pandas as pd

from config import settings
from signals.committee_signal import (
    _to_int,
    _to_bool,
    _direction_from_transaction_type,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'floor_action'


class FloorActionSignal:
    """Triggert bei Treffern aus dem VoteTimingTag."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self, threshold: Optional[int] = None):
        self.threshold = (
            threshold if threshold is not None
            else getattr(settings, 'VOTE_TIMING_HIT_THRESHOLD', 1)
        )

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        hit_count = _to_int(row.get('Vote_Timing_Hit_Count'), default=0)
        if hit_count < self.threshold:
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
            'relevance_score': _to_int(row.get('Relevance_Score'), default=0),
            'vote_timing_hit_count': hit_count,
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
