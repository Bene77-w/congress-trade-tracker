"""
Signal: Committee Ex Officio — triggert wenn CommitteeSignal aktiv UND der
Member zum Trade-Zeitpunkt eine ex-officio-Rolle innehat (Chairman, Ranking
Member, Vice Chairman, ...).

Trigger:         CommitteeSignal triggert UND Member_ExOfficio truthy
Triggering-Date: transaction_date
Scope-Key:       trade_id
"""

from typing import Optional, Dict, Any

import pandas as pd

from signals.committee_signal import (
    CommitteeSignal,
    _to_bool,
    _to_int,
    _direction_from_transaction_type,
    parse_relevant_sectors,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'committee_ex_officio'


class CommitteeExOfficioSignal:
    """Triggert nur, wenn das Committee-Signal aktiv ist und der Member
    zum Trade-Zeitpunkt eine ex-officio-Rolle hat."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self):
        self.committee_signal = CommitteeSignal()

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        # Voraussetzung: Committee-Signal ist aktiv
        committee_event = self.committee_signal.evaluate_row(row)
        if committee_event is None:
            return None
        if not _to_bool(row.get('Member_ExOfficio')):
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
            'is_ex_officio': True,
            'match_count': len(parse_relevant_sectors(row.get('Relevant_Sectors'))),
            'vote_timing_hit_count': _to_int(
                row.get('Vote_Timing_Hit_Count'), default=0
            ),
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
