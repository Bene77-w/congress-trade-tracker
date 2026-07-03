"""Score-Recompute fuer das Streamlit-Dashboard.

Erzeugt einen Settings-Proxy, in dem die Gewichte ausgeschlossener Signale
auf 0 gesetzt sind (und optional weitere Scoring-Parameter ueberschrieben
werden), und ruft ``scoring.trade_score.compute_trade_scores`` damit auf. So
bleibt die volle Score-Logik (Paradox-Daempfung, Ex-Officio-Bonus, Volume)
erhalten, nur Gewichte/Parameter werden ad-hoc fuer die UI-Sitzung getauscht.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable, Mapping

import pandas as pd

from config import settings as _settings
from scoring.trade_score import compute_trade_scores


SCORING_ATTRS = (
    'SCORING_WEIGHTS',
    'SCORING_PRIMARY_SIGNALS',
    'SCORING_PARADOX_DAMPER',
    'SCORING_PARADOX_WARNING_TAG',
    'SCORING_TRADE_VOLUME_ENABLED',
    'SCORING_TRADE_VOLUME_TIERS',
    'SCORING_TRADE_VOLUME_TRIGGER_FLOOR',
    'SCORING_EX_OFFICIO_ROLE_BONUSES',
    'SCORING_EX_OFFICIO_COMMITTEE_GLOBAL',
    'SCORING_EX_OFFICIO_TRIGGERS',
)

# Erlaubte Keys in params_override -> Mapping auf den jeweiligen Settings-Attr.
# Halten wir bewusst explizit, damit Tippfehler im UI nicht stillschweigend
# unwirksame Overrides erzeugen.
PARAMS_OVERRIDE_KEYS = {
    'paradox_damper':         'SCORING_PARADOX_DAMPER',
    'trade_volume_enabled':   'SCORING_TRADE_VOLUME_ENABLED',
    'trade_volume_tiers':     'SCORING_TRADE_VOLUME_TIERS',
    'trade_volume_trigger':   'SCORING_TRADE_VOLUME_TRIGGER_FLOOR',
    'ex_officio_role_bonuses':     'SCORING_EX_OFFICIO_ROLE_BONUSES',
    'ex_officio_committee_global': 'SCORING_EX_OFFICIO_COMMITTEE_GLOBAL',
}


def available_signals() -> list[str]:
    """Liste aller Signale/Komponenten, die der User ausschliessen kann.

    Quelle: Keys aus SCORING_WEIGHTS plus Pseudo-Eintraege:
      - 'swarm_paradox' (kein eigenes Gewicht, wirkt als Daempfer)
      - 'committee_ex_officio' (kein eigenes Gewicht, Ex-Officio-Bonus)
    """
    weights = getattr(_settings, 'SCORING_WEIGHTS', {}) or {}
    sigs = list(weights.keys())
    if 'swarm_paradox' not in sigs:
        sigs.append('swarm_paradox')
    if 'committee_ex_officio' not in sigs:
        sigs.append('committee_ex_officio')
    return sigs


def build_settings_proxy(
    excluded: Iterable[str],
    weights_override: Mapping[str, float] | None = None,
    params_override: Mapping[str, object] | None = None,
) -> SimpleNamespace:
    """Kopiert die SCORING_*-Konstanten und wendet Overrides + Exclude an.

    Reihenfolge:
      1. Defaults aus ``config.settings`` kopieren.
      2. ``weights_override`` auf ``SCORING_WEIGHTS`` anwenden.
      3. ``params_override`` auf die Skalar-Parameter anwenden
         (Keys siehe ``PARAMS_OVERRIDE_KEYS``).
      4. ``excluded`` nullt Gewichte / deaktiviert Volume / 1.0-Damper /
         leert die Ex-Officio-Bonus-Tabelle — Exclude gewinnt also immer
         ueber einen positiven Override.
    """
    excluded_set = set(excluded or [])
    proxy_kwargs: dict[str, object] = {}
    for attr in SCORING_ATTRS:
        proxy_kwargs[attr] = getattr(_settings, attr, None)

    # Ausgeschlossene Signale fliegen AUCH aus SCORING_PRIMARY_SIGNALS — ein
    # ausgeschlossenes Primaersignal (committee_full/swarm_buy) darf das
    # Hard-Gate nicht mehr oeffnen. Bewusste Konsequenz: schliesst der User
    # BEIDE Primaersignale aus, ist das Tuple leer -> Gate deaktiviert
    # (Rueckwaerts-Kompat-Semantik in compute_trade_score) -> alle Trades
    # werden nach Hint-Gewichten gescort.
    primary = getattr(_settings, 'SCORING_PRIMARY_SIGNALS', ()) or ()
    proxy_kwargs['SCORING_PRIMARY_SIGNALS'] = tuple(
        s for s in primary if s not in excluded_set
    )

    weights = dict(proxy_kwargs.get('SCORING_WEIGHTS') or {})
    if weights_override:
        for sig, value in weights_override.items():
            if sig in weights:
                weights[sig] = value

    if params_override:
        for key, value in params_override.items():
            attr = PARAMS_OVERRIDE_KEYS.get(key)
            if attr is not None:
                proxy_kwargs[attr] = value

    for sig in excluded_set:
        if sig in weights:
            weights[sig] = 0
    proxy_kwargs['SCORING_WEIGHTS'] = weights

    if 'swarm_paradox' in excluded_set:
        proxy_kwargs['SCORING_PARADOX_DAMPER'] = 1.0

    if 'trade_volume' in excluded_set:
        proxy_kwargs['SCORING_TRADE_VOLUME_ENABLED'] = False

    if 'committee_ex_officio' in excluded_set:
        proxy_kwargs['SCORING_EX_OFFICIO_ROLE_BONUSES'] = {}

    triggers = list(proxy_kwargs.get('SCORING_EX_OFFICIO_TRIGGERS') or [])
    triggers = [t for t in triggers if t not in excluded_set]
    proxy_kwargs['SCORING_EX_OFFICIO_TRIGGERS'] = triggers

    return SimpleNamespace(**proxy_kwargs)


def recompute_subset(
    df: pd.DataFrame,
    excluded: Iterable[str],
    weights_override: Mapping[str, float] | None = None,
    params_override: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    """Berechnet trade_score / trade_score_breakdown / trade_warnings neu.

    Rekomputet immer (auch bei leerem ``excluded`` und ohne Overrides) und
    liefert eine Kopie von ``df`` mit ueberschriebenen Score-Spalten. Ein
    Pass-Through-Schnellpfad existiert hier bewusst nicht; ob ueberhaupt neu
    gerechnet werden muss, entscheiden die Aufrufer (needs_recompute).
    """
    if df.empty:
        return df.copy()

    proxy = build_settings_proxy(excluded, weights_override, params_override)
    scores = compute_trade_scores(df, proxy)

    out = df.copy()
    out['trade_score'] = scores['trade_score'].values
    out['trade_score_breakdown'] = scores['trade_score_breakdown'].values
    out['trade_warnings'] = scores['trade_warnings'].values
    return out
