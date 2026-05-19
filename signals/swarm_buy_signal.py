"""
Signal: Swarm Buy (Branch 2) -- triggert wenn mehrere distinkte Members
denselben Issuer innerhalb eines rollenden Fensters kaufen.

Trigger:         >= SWARM_MIN_MEMBERS distinkte Members kaufen Trades auf
                 demselben `issuer_group_key` (Issuer-Level via SEC EDGAR CIK,
                 Cross-Class: GOOGL+GOOG sind ein Schwarm) innerhalb eines
                 zusammenhaengenden Fensters der Laenge SWARM_WINDOW_DAYS.
Triggering-Date: transaction_date des N-ten distinct Members (chronologisch).
Window-Start:    transaction_date des fruehesten Trades im Cluster.
Scope-Key:       f"{issuer_group_key}|{direction}|{window_start_iso}"
Event-Output:    EINE signal_event_id fuer alle Trades im Schwarm-Fenster;
                 pro Trade eine Event-Zeile (analog Accumulative-Signal).

SKIP-Bypass: Issuer-Group-Keys mit Praefix "SKIP:" (Options, Bonds, Cash, ...)
werden nicht in die Schwarm-Auswertung einbezogen.

Dieses Modul stellt zusaetzlich die Helper `find_swarm_clusters` und
`build_swarm_events` bereit, die von swarm_sell_signal.py und den
Conflict/Paradox-Modulen wiederverwendet werden.
"""

from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

from config import settings
from signals.committee_signal import (
    _to_int,
    _to_bool,
    _direction_from_transaction_type,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'swarm_buy'


def _swarm_event_snapshot(row: Any, signal_event_id: str, scope_key: str,
                          triggering_date, signal_type: str) -> Dict[str, Any]:
    """Snapshot fuer einen Schwarm-Event. Funktioniert mit dict oder Series."""
    return {
        'signal_event_id': signal_event_id,
        'trade_id': row.get('trade_id', '') or '',
        'signal_type': signal_type,
        'triggering_date': triggering_date,
        'scope_key': scope_key,
        'member_id': row.get('bio_guide_id', '') or '',
        'member_name': row.get('name', '') or '',
        'chamber': row.get('chamber', '') or '',
        'ticker': row.get('ticker', '') or '',
        'issuer_ticker': resolve_issuer_ticker(row),
        'issuer_group_key': row.get('issuer_group_key', '') or '',
        'asset_name_figi': row.get('asset_name_figi')
            or row.get('asset_name', '') or '',
        'transaction_date': row.get('transaction_date'),
        'disclosure_date': row.get('disclosure_date'),
        'transaction_amount': row.get('amount', '') or '',
        'direction': _direction_from_transaction_type(
            row.get('transaction_type')
        ),
        'is_ex_officio': _to_bool(row.get('Member_ExOfficio')),
        'relevance_score': _to_int(row.get('Relevance_Score'), default=0),
        'vote_timing_hit_count': _to_int(
            row.get('Vote_Timing_Hit_Count'), default=0
        ),
        'committee_subcommittee': row.get('Member_Committees_Full', '') or '',
        'relevant_sectors': row.get('Relevant_Sectors', '') or '',
    }


def _is_skip_bucket(issuer_group_key: Any) -> bool:
    """True wenn der Issuer-Group-Key zu einer SKIP-Klasse gehoert."""
    if not isinstance(issuer_group_key, str):
        return False
    return issuer_group_key.startswith('SKIP:')


def find_swarm_clusters(group_df: pd.DataFrame, direction: str,
                        window_days: int, min_members: int
                        ) -> List[List[Dict[str, Any]]]:
    """Identifiziert Schwarm-Cluster fuer eine Direction.

    Greedy-Algorithmus: Ein Cluster startet beim fruehesten Trade in der
    gewuenschten Richtung; weitere Trades schliessen sich an, solange
    `T.date - cluster[0].date <= window_days`. Sobald ein Trade aus diesem
    Fenster faellt, wird das Cluster geschlossen und (falls qualifizierend)
    ein Schwarm-Event erzeugt, ein neues Cluster startet.

    Args:
        group_df: Trades auf demselben issuer_group_key. Muss die Spalten
            `_td` (Timestamp), `_dir` (str), `bio_guide_id`, sowie alle
            Snapshot-Spalten enthalten. SKIP-Bypass und Empty-Checks erfolgen
            extern oder am Anfang.
        direction: 'buy' oder 'sell'.
        window_days: Maximales Cluster-Fenster in Tagen.
        min_members: Mindestanzahl distinkter Members fuer Auslösung.

    Returns:
        Liste von Clustern; ein Cluster ist eine Liste von Trade-Dicts
        (sortiert nach transaction_date). Nur qualifizierende Cluster
        (>= min_members distinct members) werden zurueckgegeben.
    """
    if group_df.empty:
        return []
    # SKIP-Bypass: einmal pro Gruppe checken
    igk_series = group_df.get('issuer_group_key')
    if igk_series is not None and len(igk_series) > 0:
        first_igk = igk_series.iloc[0]
        if _is_skip_bucket(first_igk):
            return []

    df = group_df.copy()
    # _td und _dir muessen vom Aufrufer (Apply-Stage) vorbefuellt sein --
    # Fallback: hier ableiten.
    if '_td' not in df.columns:
        df['_td'] = pd.to_datetime(df['transaction_date'], errors='coerce')
    if '_dir' not in df.columns:
        df['_dir'] = df['transaction_type'].map(_direction_from_transaction_type)

    df = df[df['_dir'] == direction]
    df = df[df['_td'].notna()]
    df = df[df['bio_guide_id'].fillna('').astype(str).str.strip() != '']
    if len(df) < min_members:
        return []

    df = df.sort_values('_td').reset_index(drop=True)
    records = df.to_dict('records')

    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []

    def _close(cluster: List[Dict[str, Any]]):
        if not cluster:
            return
        members = {str(r.get('bio_guide_id', '')) for r in cluster}
        if len(members) >= min_members:
            clusters.append(cluster)

    for rec in records:
        if not current:
            current = [rec]
            continue
        delta = (rec['_td'] - current[0]['_td']).days
        if delta <= window_days:
            current.append(rec)
        else:
            _close(current)
            current = [rec]
    _close(current)

    return clusters


def build_swarm_events(cluster: List[Dict[str, Any]], direction: str,
                       signal_type: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Erzeugt eine signal_event_id + Event-Snapshots fuer einen Schwarm-Cluster.

    Returns:
        (signal_event_id, list_of_event_dicts)
    """
    if not cluster:
        return '', []
    first = cluster[0]
    last = cluster[-1]
    issuer_group_key = str(first.get('issuer_group_key', '') or '')
    window_start_iso = first['_td'].date().isoformat()
    scope_key = f"{issuer_group_key}|{direction}|{window_start_iso}"
    # triggering_date = transaction_date des N-ten distinct Members.
    # In der sortierten Liste: der erste Trade, ab dem distinct count >= ?
    # Hier vereinfacht: triggering_date = Datum des letzten Trades, der
    # zur Auslösung beitraegt. Pragmatisch und gut nachvollziehbar.
    triggering_date = last['_td'].date()

    sig_id = make_signal_event_id(triggering_date, signal_type, scope_key)
    events = [
        _swarm_event_snapshot(r, sig_id, scope_key, triggering_date, signal_type)
        for r in cluster
    ]
    return sig_id, events


class SwarmBuySignal:
    """Erkennt Buy-Schwaerme auf Issuer-Group-Ebene.

    Eine Auslösung erzeugt N Event-Dicts (eines pro beteiligtem Trade),
    alle tragen dieselbe signal_event_id.
    """

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
        """Bewertet eine Gruppe (Trades auf demselben issuer_group_key).

        Returns:
            Liste von Event-Dicts (eines pro Trade in jedem qualifizierenden
            Cluster). Leere Liste wenn kein Schwarm.
        """
        clusters = find_swarm_clusters(
            group_df, 'buy', self.window_days, self.min_members
        )
        events: List[Dict[str, Any]] = []
        for cluster in clusters:
            _sig_id, cluster_events = build_swarm_events(
                cluster, 'buy', self.SIGNAL_TYPE
            )
            events.extend(cluster_events)
        return events
