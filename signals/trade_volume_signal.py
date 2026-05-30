"""
Signal: Trade Volume — triggert bei Trades mit hohem Disclosure-Amount.

Score-Basis ist die Obergrenze der Quiver-Amount-Range
(Spalte ``trade_amount_max_usd``, befuellt von Stage ``_stage_trade_volume``).
Pro Trade zaehlt nur die hoechste erreichte Stufe; der Score-Beitrag kommt
aus ``settings.SCORING_TRADE_VOLUME_TIERS`` (nicht aus ``SCORING_WEIGHTS``).

Trigger:         trade_amount_max_usd > SCORING_TRADE_VOLUME_TRIGGER_FLOOR
                 (Default 15.000 USD).
Triggering-Date: transaction_date
Scope-Key:       trade_id
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config import settings
from signals.committee_signal import (
    _to_bool,
    _to_int,
    _direction_from_transaction_type,
    parse_relevant_sectors,
)
from utils.issuer_ticker import resolve_issuer_ticker
from utils.signal_event_id import make_signal_event_id


SIGNAL_TYPE = 'trade_volume'


def _normalize_tiers(raw: Any) -> List[Tuple[float, int]]:
    """Validiert + sortiert die Tier-Liste defensiv.

    Akzeptiert eine Liste von ``(max_inclusive, score)``-Paaren in beliebiger
    Reihenfolge. ``max_inclusive=None`` wird als ``float('inf')`` interpretiert
    (Catch-all-Eintrag, kommt aus dem JSON-Preset-Format und dem
    Streamlit-Tier-Editor). Liefert eine aufsteigend nach Obergrenze sortierte
    Liste. Eintraege mit ungueltigen Typen werden aussortiert.
    """
    if not raw:
        return []
    cleaned: List[Tuple[float, int]] = []
    for item in raw:
        try:
            upper, score = item
        except (TypeError, ValueError):
            continue
        if upper is None:
            upper_val = float('inf')
        else:
            try:
                upper_val = float(upper)
            except (TypeError, ValueError):
                continue
            # NaN -> ebenfalls als Catch-all behandeln
            if upper_val != upper_val:
                upper_val = float('inf')
        try:
            score_val = int(score)
        except (TypeError, ValueError):
            continue
        cleaned.append((upper_val, score_val))
    cleaned.sort(key=lambda t: t[0])
    return cleaned


def resolve_tier(amount_max_usd: float,
                 tiers: List[Tuple[float, int]],
                 trigger_floor: float) -> Tuple[int, int]:
    """Liefert ``(tier_index, score)`` fuer einen Trade.

    ``tier_index`` 0 = unter ``trigger_floor`` (kein Trigger, kein Score).
    ``tier_index`` 1..N = Index in ``tiers`` (1-basiert, damit Tier 0 frei
    fuer den Nicht-Treffer-Fall bleibt).
    """
    if amount_max_usd is None:
        return (0, 0)
    try:
        if pd.isna(amount_max_usd):
            return (0, 0)
    except (TypeError, ValueError):
        pass
    try:
        amount = float(amount_max_usd)
    except (TypeError, ValueError):
        return (0, 0)

    if amount <= trigger_floor:
        return (0, 0)

    for idx, (upper, score) in enumerate(tiers, start=1):
        if amount <= upper:
            return (idx, int(score))
    # Sollte nicht erreichbar sein wenn der letzte Tier-Eintrag inf ist —
    # aber defensiv: nimm den letzten Eintrag.
    if tiers:
        return (len(tiers), int(tiers[-1][1]))
    return (0, 0)


class TradeVolumeSignal:
    """Triggert ab Tier-1 auf ``trade_amount_max_usd``."""

    SIGNAL_TYPE = SIGNAL_TYPE

    def __init__(self,
                 tiers: Optional[List[Tuple[float, int]]] = None,
                 trigger_floor: Optional[float] = None):
        if tiers is None:
            tiers = getattr(settings, 'SCORING_TRADE_VOLUME_TIERS', [])
        self.tiers = _normalize_tiers(tiers)
        if trigger_floor is None:
            trigger_floor = getattr(
                settings, 'SCORING_TRADE_VOLUME_TRIGGER_FLOOR', 0.0
            )
        self.trigger_floor = float(trigger_floor)

    def evaluate_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        amount_max = row.get('trade_amount_max_usd', 0)
        tier_index, _score = resolve_tier(
            amount_max, self.tiers, self.trigger_floor
        )
        if tier_index <= 0:
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
            'match_count': tier_index,  # Tier-Index reused als match_count
            'vote_timing_hit_count': _to_int(
                row.get('Vote_Timing_Hit_Count'), default=0
            ),
            'committee_subcommittee': row.get('Member_Committees_Full', ''),
            'relevant_sectors': row.get('Relevant_Sectors', ''),
        }
