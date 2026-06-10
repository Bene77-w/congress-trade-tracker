"""
Signal: Committee Ex Officio (v2) — triggert wenn CommitteeSignal aktiv UND
mindestens ein relevanter Committee-Treffer des Trades eine Fuehrungsrolle
traegt. Quelle ist die Master-Spalte ex_officio_role_keys (';'-getrennte
Rollen-Keys, prae-Dedup vom CommitteeTag abgeleitet) — nicht mehr das
member-globale Member_ExOfficio-Flag.

Trigger:         CommitteeSignal triggert UND ex_officio_role_keys nicht leer
Triggering-Date: transaction_date
Scope-Key:       trade_id
"""

from typing import Optional, Dict, Any

import pandas as pd

from signals.committee_signal import (
    CommitteeSignal,
    _to_int,
    _direction_from_transaction_type,
    parse_relevant_sectors,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'committee_ex_officio'


def _role_keys_value(value: Any) -> str:
    """Liest die ex_officio_role_keys-Zelle defensiv (NaN/None -> '')."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


class CommitteeExOfficioSignal:
    """Triggert nur, wenn das Committee-Signal aktiv ist und mindestens ein
    relevanter Treffer eine Fuehrungsrolle traegt (ex_officio_role_keys)."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self):
        self.committee_signal = CommitteeSignal()

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        # Voraussetzung: Committee-Signal ist aktiv
        committee_event = self.committee_signal.evaluate_row(row)
        if committee_event is None:
            return None
        role_keys = _role_keys_value(row.get('ex_officio_role_keys'))
        if not role_keys:
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
            'ex_officio_role_keys': role_keys,
            'match_count': len(parse_relevant_sectors(row.get('Relevant_Sectors'))),
            'vote_timing_hit_count': _to_int(
                row.get('Vote_Timing_Hit_Count'), default=0
            ),
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
