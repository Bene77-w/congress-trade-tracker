"""
Signal: Swarm Conflict (Branch 2) -- bei aktivem Schwarm taucht GENAU 1
gegenrichtiger Trade im erweiterten Fenster auf.

Trigger:         Ein swarm_buy oder swarm_sell ist aktiv (>= MIN_MEMBERS),
                 und im erweiterten Fenster
                 [first_swarm_trade - SWARM_CONFLICT_WINDOW_DAYS_BEFORE,
                  last_swarm_trade  + SWARM_CONFLICT_WINDOW_DAYS_AFTER]
                 existiert GENAU 1 Trade in der Gegenrichtung.

                 Wenn >= SWARM_PARADOX_MIN_CONFLICTS Gegen-Trades existieren,
                 wird KEIN swarm_conflict erzeugt; stattdessen kuemmert sich
                 SwarmParadoxSignal um den Paradox-Fall (mutually exclusive
                 pro Schwarm-Auslösung).

                 Selbst-Umkehr zaehlt: Wenn ein Member aus dem Schwarm
                 spaeter in der Gegenrichtung handelt, gilt das als Konflikt.
                 contrarian-Signal erfasst den Member-Aspekt orthogonal.

Triggering-Date: transaction_date des Konflikt-Trades.
Scope-Key:       f"conflict|{issuer_group_key}|{swarm_direction}|{window_start_iso}"
Event-Output:    EINE Event-Zeile pro Konflikt-Trade (hier: genau 1 Zeile).
                 Schwarm-Trades selbst sind via swarm_buy/_sell bereits erfasst.

Siehe auch: signals/swarm_paradox_signal.py
"""

from typing import List, Dict, Any, Optional

import pandas as pd

from config import settings
from signals.committee_signal import _direction_from_transaction_type
from signals.swarm_buy_signal import (
    find_swarm_clusters,
    _is_skip_bucket,
    _swarm_event_snapshot,
)
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'swarm_conflict'


def _opposite_direction(direction: str) -> str:
    return 'sell' if direction == 'buy' else 'buy'


def find_conflict_trades(group_df: pd.DataFrame,
                         cluster: List[Dict[str, Any]],
                         swarm_direction: str,
                         before_days: int,
                         after_days: int) -> List[Dict[str, Any]]:
    """Findet alle Gegen-Trades im erweiterten Fenster eines Schwarm-Clusters.

    Fenster: [cluster[0].date - before_days, cluster[-1].date + after_days].
    Selbst-Umkehr (Member aus dem Schwarm) ist explizit erlaubt.

    Returns:
        Liste von Trade-Dicts (sortiert nach transaction_date).
    """
    if not cluster:
        return []
    df = group_df
    if '_td' not in df.columns:
        df = df.copy()
        df['_td'] = pd.to_datetime(df['transaction_date'], errors='coerce')
    if '_dir' not in df.columns:
        df = df.copy()
        df['_dir'] = df['transaction_type'].map(_direction_from_transaction_type)

    first_date = cluster[0]['_td']
    last_date = cluster[-1]['_td']
    window_start = first_date - pd.Timedelta(days=before_days)
    window_end = last_date + pd.Timedelta(days=after_days)
    opp = _opposite_direction(swarm_direction)

    mask = (
        (df['_dir'] == opp) &
        df['_td'].notna() &
        (df['_td'] >= window_start) &
        (df['_td'] <= window_end)
    )
    candidates = df[mask].sort_values('_td')
    return candidates.to_dict('records')


class SwarmConflictSignal:
    """Erkennt einzelne Gegen-Trades zu aktiven Schwaermen.

    Erzeugt swarm_conflict-Events NUR wenn die Anzahl Gegen-Trades
    1 <= n < SWARM_PARADOX_MIN_CONFLICTS ist. Andernfalls uebernimmt
    SwarmParadoxSignal die Erzeugung (mutually exclusive).
    """

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self,
                 window_days: Optional[int] = None,
                 min_members: Optional[int] = None,
                 before_days: Optional[int] = None,
                 after_days: Optional[int] = None,
                 paradox_min: Optional[int] = None):
        self.window_days = (
            window_days if window_days is not None
            else getattr(settings, 'SWARM_WINDOW_DAYS', 7)
        )
        self.min_members = (
            min_members if min_members is not None
            else getattr(settings, 'SWARM_MIN_MEMBERS', 2)
        )
        self.before_days = (
            before_days if before_days is not None
            else getattr(settings, 'SWARM_CONFLICT_WINDOW_DAYS_BEFORE', 3)
        )
        self.after_days = (
            after_days if after_days is not None
            else getattr(settings, 'SWARM_CONFLICT_WINDOW_DAYS_AFTER', 3)
        )
        self.paradox_min = (
            paradox_min if paradox_min is not None
            else getattr(settings, 'SWARM_PARADOX_MIN_CONFLICTS', 2)
        )

    def evaluate_group(self, group_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Erzeugt swarm_conflict-Events fuer eine issuer_group_key-Gruppe.

        Returns:
            Eine Event-Liste mit signal_type='swarm_conflict'. Paradox-Faelle
            werden hier NICHT erzeugt -- dafuer ist SwarmParadoxSignal
            zustaendig.
        """
        if group_df.empty:
            return []
        igk_series = group_df.get('issuer_group_key')
        if igk_series is not None and len(igk_series) > 0:
            if _is_skip_bucket(igk_series.iloc[0]):
                return []

        events: List[Dict[str, Any]] = []
        for swarm_dir in ('buy', 'sell'):
            clusters = find_swarm_clusters(
                group_df, swarm_dir, self.window_days, self.min_members
            )
            for cluster in clusters:
                opposing = find_conflict_trades(
                    group_df, cluster, swarm_dir,
                    self.before_days, self.after_days
                )
                # Mutually exclusive: nur 1 <= n < paradox_min erzeugt conflict
                if not opposing or len(opposing) >= self.paradox_min:
                    continue
                first = cluster[0]
                issuer_group_key = str(first.get('issuer_group_key', '') or '')
                window_start_iso = first['_td'].date().isoformat()
                scope_key = (
                    f"conflict|{issuer_group_key}|{swarm_dir}|{window_start_iso}"
                )
                # 1 Event pro Konflikt-Trade (hier genau 1, da n < paradox_min == 2)
                for trade in opposing:
                    triggering_date = trade['_td'].date()
                    sig_id = make_signal_event_id(
                        triggering_date, self.SIGNAL_TYPE, scope_key
                    )
                    events.append(_swarm_event_snapshot(
                        trade, sig_id, scope_key, triggering_date,
                        self.SIGNAL_TYPE
                    ))
        return events
