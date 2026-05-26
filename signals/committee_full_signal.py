"""
Signal: Committee Full — triggert wenn der Trade einen Voll-Treffer
(Sector + Industry) im Insider-Committee-Mapping hat.

Voll-Treffer heisst: mindestens ein Eintrag in Relevant_Sectors hat
``match_type == 'full'`` (d.h. der Insider-Eintrag deckt sowohl den
Sector als auch die Industry des gehandelten Assets ab). Halb-Treffer
(``match_type == 'sector'``, nur Sector trifft) loesen dieses Signal
NICHT aus — sie loesen weiterhin das schwaechere `committee`-Signal aus.

Im Modus `sector_and_industry` enthaelt Relevant_Sectors per Definition
nur full-Eintraege, daher feuern `committee` und `committee_full` dort
immer gemeinsam. Im Modus `sector_or_industry` differenziert
`committee_full` zwischen den beiden Stufen.

Trigger:         any(e.match_type == 'full' for e in Relevant_Sectors)
Triggering-Date: transaction_date
Scope-Key:       trade_id
"""

from typing import Optional, Dict, Any

import pandas as pd

from signals.committee_signal import (
    _to_bool,
    _to_int,
    _direction_from_transaction_type,
    parse_relevant_sectors,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'committee_full'


class CommitteeFullSignal:
    """Triggert wenn mindestens ein Eintrag in Relevant_Sectors
    match_type='full' hat."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self):
        pass

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        relevant = parse_relevant_sectors(row.get('Relevant_Sectors'))
        if not any(e.get('match_type') == 'full' for e in relevant):
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
