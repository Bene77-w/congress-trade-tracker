"""
Signal: Swarm Sell (Branch 2) -- triggert wenn mehrere distinkte Members
denselben Issuer innerhalb eines rollenden Fensters verkaufen.

Analog zu SwarmBuySignal, nur direction='sell'. Algorithmus, Scope-Key-
Konvention und Event-Form sind identisch. Doku: siehe swarm_buy_signal.py.
"""

from typing import List, Dict, Any, Optional

import pandas as pd

from config import settings
from signals.swarm_buy_signal import find_swarm_clusters, build_swarm_events


SIGNAL_TYPE = 'swarm_sell'


class SwarmSellSignal:
    """Erkennt Sell-Schwaerme auf Issuer-Group-Ebene."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self, window_days: Optional[int] = None,
                 min_members: Optional[int] = None):
        self.window_days = (
            window_days if window_days is not None
            else getattr(settings, 'SWARM_WINDOW_DAYS', 7)
        )
        self.min_members = (
            min_members if min_members is not None
            else getattr(settings, 'SWARM_MIN_MEMBERS', 2)
        )

    def evaluate_group(self, group_df: pd.DataFrame) -> List[Dict[str, Any]]:
        clusters = find_swarm_clusters(
            group_df, 'sell', self.window_days, self.min_members
        )
        events: List[Dict[str, Any]] = []
        for cluster in clusters:
            _sig_id, cluster_events = build_swarm_events(
                cluster, 'sell', self.SIGNAL_TYPE
            )
            events.extend(cluster_events)
        return events
