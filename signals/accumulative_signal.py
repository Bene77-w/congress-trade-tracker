"""
Signal: Accumulative — triggert wenn ein Member innerhalb eines kurzen
Zeitfensters mehrfach dasselbe Asset in dieselbe Richtung handelt
(wiederholtes Aufbauen oder Abbauen einer Position).

Trigger:         >= ACCUMULATIVE_MIN_COUNT Handelstage, an denen derselbe
                 Member auf demselben issuer_ticker in gleicher Direction
                 handelt, innerhalb eines rollenden Fensters von
                 ACCUMULATIVE_WINDOW_DAYS. Mehrere Lots desselben
                 Handelstags zaehlen als ein Ereignis (H6).
Triggering-Date: Handelstag, der die Schwelle erreicht
Scope-Key:       f"{member_id}|{issuer_ticker}|{window_start_iso}|{direction}"
                 wobei window_start_iso = aeltester Handelstag im
                 qualifizierenden Fenster.
"""

from typing import List, Dict, Any, Optional

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


SIGNAL_TYPE = 'accumulative'


def _event_snapshot(row: pd.Series, signal_event_id: str, scope_key: str,
                    triggering_date) -> Dict[str, Any]:
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
        'match_count': len(parse_relevant_sectors(row.get('Relevant_Sectors'))),
        'vote_timing_hit_count': _to_int(
            row.get('Vote_Timing_Hit_Count'), default=0
        ),
        'committee_subcommittee': row.get('Member_Committees_Full', ''),
        'relevant_sectors': row.get('Relevant_Sectors', ''),
    }


class AccumulativeSignal:
    """Bewertet eine (Member x Asset)-Gruppe auf wiederholte Trades in
    derselben Richtung innerhalb eines rollenden Fensters.

    Implementierung als Sliding-Window pro Direction ueber Handelstage:
      * Fasse die Trades je Handelstag zusammen (Lots = ein Ereignis).
      * Maintain einen Pointer auf den aeltesten Handelstag, der noch
        innerhalb des Fensters liegt.
      * Sobald das Fenster >= MIN_COUNT Handelstage enthaelt, wird es
        eingefroren und ein einziges Event-Set fuer alle Trades dieser
        Tage emittiert. Anschliessend startet die naechste Suche nach dem
        ersten Handelstag jenseits des Fensters.
    """

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self,
                 window_days: Optional[int] = None,
                 min_count: Optional[int] = None):
        self.window_days = (
            window_days if window_days is not None
            else getattr(settings, 'ACCUMULATIVE_WINDOW_DAYS', 30)
        )
        self.min_count = (
            min_count if min_count is not None
            else getattr(settings, 'ACCUMULATIVE_MIN_COUNT', 2)
        )

    def evaluate_group(self, group_df: pd.DataFrame) -> List[Dict[str, Any]]:
        if len(group_df) < self.min_count:
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
        records = [r for r in records
                   if r.get('_td') is not None
                   and not (isinstance(r.get('_td'), float)
                            and pd.isna(r.get('_td')))]

        events: List[Dict[str, Any]] = []
        for direction in ('buy', 'sell'):
            sub = [r for r in records if r.get('_dir') == direction]
            if len(sub) < self.min_count:
                continue
            sub.sort(key=lambda r: r['_td'])
            events.extend(self._scan_direction(sub, direction))
        return events

    def _scan_direction(self, sub: List[Dict[str, Any]],
                        direction: str) -> List[Dict[str, Any]]:
        days: Dict[pd.Timestamp, List[Dict[str, Any]]] = {}
        for row in sub:
            days.setdefault(row['_td'].normalize(), []).append(row)
        dates = sorted(days)

        events: List[Dict[str, Any]] = []
        i = 0
        n = len(dates)
        while i <= n - self.min_count:
            window_end = i + self.min_count - 1
            if (dates[window_end] - dates[i]).days > self.window_days:
                i += 1
                continue

            # Fenster qualifiziert — erweitere window_end weiter, solange
            # alle weiteren Handelstage noch innerhalb des Fensters liegen
            j = window_end
            while j + 1 < n and \
                    (dates[j + 1] - dates[i]).days <= self.window_days:
                j += 1

            window = [row for day in dates[i:j + 1] for row in days[day]]
            triggering_date = dates[window_end].date()
            last = window[-1]
            member_id = last.get('bio_guide_id', '') or ''
            issuer_ticker = resolve_issuer_ticker(last)
            window_start_iso = dates[i].date().isoformat()
            scope_key = f"{member_id}|{issuer_ticker}|{window_start_iso}|{direction}"
            sig_id = make_signal_event_id(
                triggering_date, SIGNAL_TYPE, scope_key
            )

            for row in window:
                events.append(_event_snapshot(row, sig_id, scope_key,
                                              triggering_date))

            i = j + 1
        return events
