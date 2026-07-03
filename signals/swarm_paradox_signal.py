"""
Signal: Swarm Paradox (Branch 2) -- bei aktivem Schwarm tauchen MEHRERE
gegenrichtige Trades im erweiterten Fenster auf. Starke Signal-Daempfung.

Trigger:         Ein swarm_buy oder swarm_sell ist aktiv (>= MIN_MEMBERS),
                 und im erweiterten Fenster
                 [first_swarm_trade - SWARM_CONFLICT_WINDOW_DAYS_BEFORE,
                  last_swarm_trade  + SWARM_CONFLICT_WINDOW_DAYS_AFTER]
                 existieren >= SWARM_PARADOX_MIN_CONFLICTS Gegen-Trades.
                 Mutually exclusive zu swarm_conflict pro Schwarm-Auslösung.

Triggering-Date: transaction_date des letzten Gegen-Trades (= Moment, an dem
                 die Paradox-Schwelle erreicht wurde).
Scope-Key:       f"paradox|{issuer_group_key}|{swarm_direction}|{window_start_iso}"
Event-Output:    EINE signal_event_id mit allen Gegen-Trades als Event-Zeilen
                 (analog Accumulative/Schwarm-Buy). Schwarm-Trades selbst sind
                 via swarm_buy/_sell bereits erfasst.

Semantik: swarm_paradox indiziert, dass das Schwarm-Signal stark abgeschwaecht
wird -- mehrere Insider widersprechen einander. Im Scoring (Modul 8) als
Daempfer beruecksichtigen.

Siehe auch: signals/swarm_conflict_signal.py
"""

from typing import List, Dict, Any, Optional

import pandas as pd

from config import settings
from signals.swarm_buy_signal import (
    find_swarm_clusters,
    _is_skip_bucket,
    _swarm_event_snapshot,
)
from signals.swarm_conflict_signal import find_conflict_trades
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'swarm_paradox'


class SwarmParadoxSignal:
    """Erkennt mehrfache Gegen-Trades zu aktiven Schwaermen.

    Erzeugt swarm_paradox-Events NUR wenn die Anzahl Gegen-Trades
    >= SWARM_PARADOX_MIN_CONFLICTS ist. Bei genau 1 Gegen-Trade uebernimmt
    SwarmConflictSignal.
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
        """Erzeugt swarm_paradox-Events fuer eine issuer_group_key-Gruppe.

        Returns:
            Eine Event-Liste mit signal_type='swarm_paradox'; pro
            Paradox-Auslösung mehrere Event-Zeilen (eine pro Gegen-Trade),
            alle mit derselben signal_event_id.
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
                if len(opposing) < self.paradox_min:
                    continue
                first = cluster[0]
                issuer_group_key = str(first.get('issuer_group_key', '') or '')
                window_start_iso = first['_td'].date().isoformat()
                scope_key = (
                    f"paradox|{issuer_group_key}|{swarm_dir}|{window_start_iso}"
                )
                # triggering_date = Datum des letzten Gegen-Trades (Schwelle erreicht)
                triggering_date = opposing[-1]['_td'].date()
                sig_id = make_signal_event_id(
                    triggering_date, self.SIGNAL_TYPE, scope_key
                )
                # Events fuer Cluster-Trades (die auslösenden Schwarm-Trades,
                # damit der Paradox-Daempfer im Scoring dort greift) UND fuer
                # die Gegen-Trades (dokumentieren das Gegenindiz). Cluster
                # zuerst. Dedup ueber (trade_id, scope_key) gegen doppelte
                # Zeilen, falls ein Trade mehrfach auftaucht.
                seen: set = set()
                for trade in cluster + opposing:
                    tid = str(trade.get('trade_id', '') or '')
                    key = (tid, scope_key)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(_swarm_event_snapshot(
                        trade, sig_id, scope_key, triggering_date,
                        self.SIGNAL_TYPE
                    ))
        return events
