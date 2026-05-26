"""Score-Recompute fuer das Streamlit-Dashboard.

Erzeugt einen Settings-Proxy, in dem die Gewichte ausgeschlossener Signale
auf 0 gesetzt sind, und ruft ``scoring.trade_score.compute_trade_scores`` damit
auf. So bleibt die volle Score-Logik (Paradox-Daempfung, Ex-Officio-Bonus,
Volume) erhalten, nur der direkte Beitrag der vom User ausgeklammerten Signale
faellt weg.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable

import pandas as pd

from config import settings as _settings
from scoring.trade_score import compute_trade_scores


SCORING_ATTRS = (
    'SCORING_WEIGHTS',
    'SCORING_PARADOX_DAMPER',
    'SCORING_PARADOX_WARNING_TAG',
    'SCORING_VOLUME_ENABLED',
    'SCORING_VOLUME_THRESHOLD_U',
    'SCORING_VOLUME_SLOPE',
    'SCORING_EX_OFFICIO_BONUS',
    'SCORING_EX_OFFICIO_TRIGGERS',
)


def available_signals() -> list[str]:
    """Liste aller Signale/Komponenten, die der User ausschliessen kann.

    Quelle: Keys aus SCORING_WEIGHTS plus zwei Pseudo-Eintraege:
      - 'swarm_paradox' (kein eigenes Gewicht, wirkt als Daempfer)
      - 'volume' (kein eigenes Gewicht, schaltet SCORING_VOLUME_ENABLED)
    """
    weights = getattr(_settings, 'SCORING_WEIGHTS', {}) or {}
    sigs = list(weights.keys())
    if 'swarm_paradox' not in sigs:
        sigs.append('swarm_paradox')
    if 'volume' not in sigs:
        sigs.append('volume')
    return sigs


def build_settings_proxy(excluded: Iterable[str]) -> SimpleNamespace:
    """Kopiert die SCORING_*-Konstanten und nullt ausgeschlossene Gewichte."""
    excluded_set = set(excluded or [])
    proxy_kwargs: dict[str, object] = {}
    for attr in SCORING_ATTRS:
        proxy_kwargs[attr] = getattr(_settings, attr, None)

    weights = dict(proxy_kwargs.get('SCORING_WEIGHTS') or {})
    for sig in excluded_set:
        if sig in weights:
            weights[sig] = 0
    proxy_kwargs['SCORING_WEIGHTS'] = weights

    if 'swarm_paradox' in excluded_set:
        proxy_kwargs['SCORING_PARADOX_DAMPER'] = 1.0

    if 'volume' in excluded_set:
        proxy_kwargs['SCORING_VOLUME_ENABLED'] = False

    triggers = list(proxy_kwargs.get('SCORING_EX_OFFICIO_TRIGGERS') or [])
    triggers = [t for t in triggers if t not in excluded_set]
    proxy_kwargs['SCORING_EX_OFFICIO_TRIGGERS'] = triggers

    return SimpleNamespace(**proxy_kwargs)


def recompute_subset(df: pd.DataFrame,
                     excluded: Iterable[str]) -> pd.DataFrame:
    """Berechnet trade_score / trade_score_breakdown / trade_warnings neu.

    Liefert eine Kopie von ``df`` mit ueberschriebenen Score-Spalten. Bei
    leerem ``excluded`` werden die Original-Spalten (sofern vorhanden)
    beibehalten, sonst neu berechnet.
    """
    if df.empty:
        return df.copy()

    proxy = build_settings_proxy(excluded)
    scores = compute_trade_scores(df, proxy)

    out = df.copy()
    out['trade_score'] = scores['trade_score'].values
    out['trade_score_breakdown'] = scores['trade_score_breakdown'].values
    out['trade_warnings'] = scores['trade_warnings'].values
    return out
