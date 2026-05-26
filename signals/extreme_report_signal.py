"""
Signal: Extreme Report — triggert wenn die Meldefrist deutlich ueberschritten
wurde (disclosure_date liegt > EXTREME_REPORT_DAYS nach transaction_date).
Die regulaere Stock-Act-Frist betraegt 45 Tage; Verzoegerungen daruber hinaus
sind ein eigenstaendiges Verdachtsindiz.

Trigger:         (disclosure_date - transaction_date).days > settings.EXTREME_REPORT_DAYS
Triggering-Date: disclosure_date  (das ist das spaeter eintretende Ereignis)
Scope-Key:       trade_id
"""

from typing import Optional, Dict, Any

import pandas as pd

from config import settings
from signals.committee_signal import (
    _to_int,
    _to_bool,
    _direction_from_transaction_type,
    parse_relevant_sectors,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'extreme_report'


def _to_timestamp(value: Any) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return pd.to_datetime(value)
    except (ValueError, TypeError):
        return None


class ExtremeReportSignal:
    """Triggert bei stark verzoegerter Meldung."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self, threshold_days: Optional[int] = None):
        self.threshold_days = (
            threshold_days if threshold_days is not None
            else getattr(settings, 'EXTREME_REPORT_DAYS', 60)
        )

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        td = _to_timestamp(row.get('transaction_date'))
        dd = _to_timestamp(row.get('disclosure_date'))
        if td is None or dd is None:
            return None

        delay_days = (dd - td).days
        if delay_days <= self.threshold_days:
            return None

        trade_id = row.get('trade_id', '')
        triggering_date = row.get('disclosure_date')
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
            'match_count': len(parse_relevant_sectors(row.get('Relevant_Sectors'))),
            'vote_timing_hit_count': _to_int(
                row.get('Vote_Timing_Hit_Count'), default=0
            ),
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
