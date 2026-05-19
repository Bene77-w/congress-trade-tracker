"""
Signal: Contrarian — triggert wenn ein Member innerhalb eines kurzen
Zeitfensters seine Richtung auf demselben Asset umkehrt (Buy gefolgt von
Sell oder umgekehrt).

Trigger:         Zwei aufeinanderfolgende Trades desselben Members auf
                 demselben issuer_ticker mit unterschiedlicher Direction
                 (buy/sell) und Datums-Differenz <= CONTRARIAN_WINDOW_DAYS.
Triggering-Date: transaction_date des spaeteren Trades (der die Wende vollzieht)
Scope-Key:       f"{member_id}|{issuer_ticker}|{window_start_iso}"
                 wobei window_start_iso = transaction_date des ersten Trades.
"""

from typing import List, Dict, Any, Optional

import pandas as pd

from config import settings
from signals.committee_signal import (
    _to_int,
    _to_bool,
    _direction_from_transaction_type,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'contrarian'


def _event_snapshot(row: pd.Series, signal_event_id: str, scope_key: str,
                    triggering_date) -> Dict[str, Any]:
    """Erzeugt einen Event-Snapshot fuer eine Sequenz-Auslösung."""
    return {
        'signal_event_id': signal_event_id,
        'trade_id': row.get('trade_id', ''),
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
        'vote_timing_hit_count': _to_int(
            row.get('Vote_Timing_Hit_Count'), default=0
        ),
        'committee_subcommittee': row.get('Member_Committees_Full', ''),
        'relevant_sectors': row.get('Relevant_Sectors', ''),
    }


class ContrarianSignal:
    """Bewertet eine Gruppe (Member x Asset) auf Richtungswenden im Fenster.

    Eine Auslösung erzeugt ZWEI Event-Dicts (eines pro beteiligtem Trade),
    beide tragen dieselbe signal_event_id, um die Gruppierung in
    suspicious_events.csv zu ermoeglichen.
    """

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self, window_days: Optional[int] = None):
        self.window_days = (
            window_days if window_days is not None
            else getattr(settings, 'CONTRARIAN_WINDOW_DAYS', 30)
        )

    def evaluate_group(self, group_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Wertet alle Trades eines Members auf einem Asset aus.

        Args:
            group_df: DataFrame mit allen Trades eines (Member x issuer_ticker)-
                Paars. Beliebige Direction, beliebige Reihenfolge. Wenn die
                Spalten `_td` (Timestamp) und `_dir` (str) bereits vorhanden
                sind (vom Aufrufer pre-computed), werden sie wiederverwendet
                — sonst werden sie hier abgeleitet.

        Returns:
            Liste von Event-Dicts (zwei pro Wende-Treffer).
        """
        if len(group_df) < 2:
            return []

        if '_td' not in group_df.columns or '_dir' not in group_df.columns:
            group_df = group_df.copy()
            if '_td' not in group_df.columns:
                group_df['_td'] = pd.to_datetime(
                    group_df['transaction_date'], errors='coerce'
                )
            if '_dir' not in group_df.columns:
                group_df['_dir'] = group_df['transaction_type'].map(
                    _direction_from_transaction_type
                )

        # In dicts arbeiten — vermeidet pandas-Overhead pro Gruppe.
        records = group_df.to_dict('records')
        valid = []
        for r in records:
            td = r.get('_td')
            if td is None:
                continue
            if isinstance(td, float) and pd.isna(td):
                continue
            if r.get('_dir') not in ('buy', 'sell'):
                continue
            valid.append(r)
        if len(valid) < 2:
            return []
        valid.sort(key=lambda r: r['_td'])

        events: List[Dict[str, Any]] = []
        for i in range(1, len(valid)):
            prev = valid[i - 1]
            curr = valid[i]
            if prev['_dir'] == curr['_dir']:
                continue
            delta_days = (curr['_td'] - prev['_td']).days
            if delta_days < 0 or delta_days > self.window_days:
                continue

            member_id = curr.get('bio_guide_id', '') or ''
            issuer_ticker = resolve_issuer_ticker(curr)
            window_start_iso = prev['_td'].date().isoformat()
            scope_key = f"{member_id}|{issuer_ticker}|{window_start_iso}"
            triggering_date = curr['_td'].date()

            sig_id = make_signal_event_id(
                triggering_date, SIGNAL_TYPE, scope_key
            )
            events.append(
                _event_snapshot(prev, sig_id, scope_key, triggering_date)
            )
            events.append(
                _event_snapshot(curr, sig_id, scope_key, triggering_date)
            )

        return events
