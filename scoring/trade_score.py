"""Trade-Score-Berechnung (Modul 8, Phase 8.3).

Liefert pro Trade ein Tripel ``(trade_score, trade_score_breakdown,
trade_warnings)`` nach der Formel:

    swarm_part  = Σ WEIGHTS[s] für s in signals_triggered ∩ swarm_signals
    if 'swarm_paradox' in signals_triggered:
        swarm_part *= SCORING_PARADOX_DAMPER

    other_part  = Σ WEIGHTS[s] für s in signals_triggered \\ swarm_signals
                                                        \\ {swarm_paradox}

    volume_part = max(0, asset_tradingvolume_usd - U) * SLOPE
                  (0 wenn SCORING_VOLUME_ENABLED=False)

    bonus       = SCORING_EX_OFFICIO_BONUS, falls Member_ExOfficio truthy UND
                  signals_triggered ∩ SCORING_EX_OFFICIO_TRIGGERS ≠ ∅

    trade_score = round(swarm_part + other_part + volume_part + bonus)

    trade_warnings = SCORING_PARADOX_WARNING_TAG, falls 'swarm_paradox' getriggert.

Breakdown-Format (volle Verbositaet): ``";"``-getrennte ``key:value``-Paare in
fester Reihenfolge. Es erscheinen ausschliesslich Komponenten, deren Signal
auch wirklich in ``signals_triggered`` enthalten ist (sonst Laengen-Explosion);
Volume-Komponente erscheint immer, Ex-Officio-Bonus immer, beide auch mit
Wert 0 wenn deaktiviert oder unter Schwelle.
"""
from __future__ import annotations

from typing import Any, Tuple

import pandas as pd

# Schwarm-Signal-Klassen — fuer Paradox-Daempfung gruppieren wir nur
# swarm_buy/sell/conflict; swarm_paradox ist der Daempfer selbst.
SWARM_SIGNALS = ('swarm_buy', 'swarm_sell', 'swarm_conflict')
PARADOX_SIGNAL = 'swarm_paradox'

# Feste Reihenfolge fuer Breakdown-Strings (deterministische CSV-Diffs).
BRANCH1_ORDER = ('committee', 'floor_action', 'extreme_report')
SEQUENCE_ORDER = ('contrarian', 'accumulative')


def _to_bool(value: Any) -> bool:
    """Truthy-Check fuer DataFrame-Werte. NaN/None/leer -> False."""
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip() != ''
    return bool(value)


def _parse_signals(value: Any) -> set[str]:
    """Parst ``signals_triggered``-Zelle in Set. Defensiv gegen NaN/None/''."""
    if value is None:
        return set()
    try:
        if pd.isna(value):
            return set()
    except (TypeError, ValueError):
        pass
    if not isinstance(value, str):
        value = str(value)
    return {tok for tok in value.split(';') if tok}


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_trade_score(signals_value: Any,
                        ex_officio_value: Any,
                        asset_tradingvolume_usd: Any,
                        settings_module: Any) -> Tuple[int, str, str]:
    """Berechnet ``(score, breakdown, warnings)`` fuer einen einzelnen Trade.

    Parameters
    ----------
    signals_value:
        Zellwert aus ``signals_triggered`` (``;``-getrennter String oder NaN).
    ex_officio_value:
        Zellwert aus ``Member_ExOfficio`` (z.B. ``"Chairman"`` oder leer/NaN).
    asset_tradingvolume_usd:
        Zellwert aus ``asset_tradingvolume_usd`` (int, kann 0/NaN sein).
    settings_module:
        Modul mit den ``SCORING_*``-Konstanten (typischerweise
        ``config.settings``).
    """
    weights = getattr(settings_module, 'SCORING_WEIGHTS', {}) or {}
    paradox_damper = float(getattr(settings_module, 'SCORING_PARADOX_DAMPER', 0.0))
    paradox_tag = getattr(settings_module, 'SCORING_PARADOX_WARNING_TAG',
                          'paradox_swarm')
    volume_enabled = bool(getattr(settings_module, 'SCORING_VOLUME_ENABLED',
                                  True))
    volume_threshold = float(getattr(settings_module,
                                     'SCORING_VOLUME_THRESHOLD_U', 0.0))
    volume_slope = float(getattr(settings_module, 'SCORING_VOLUME_SLOPE', 0.0))
    bonus_amount = float(getattr(settings_module, 'SCORING_EX_OFFICIO_BONUS',
                                 0.0))
    bonus_triggers = set(getattr(settings_module,
                                 'SCORING_EX_OFFICIO_TRIGGERS', []) or [])

    triggered = _parse_signals(signals_value)
    is_paradox = PARADOX_SIGNAL in triggered

    breakdown_parts: list[str] = []

    # --- Branch-1 + Sequenz: einfache Additive ---
    other_part = 0.0
    for sig in BRANCH1_ORDER + SEQUENCE_ORDER:
        if sig not in triggered:
            continue
        w = float(weights.get(sig, 0))
        other_part += w
        breakdown_parts.append(f'{sig}:{int(round(w))}')

    # --- Schwarm-Signale: erst Roh-Summe, dann ggf. daempfen ---
    swarm_raw_parts: list[Tuple[str, float]] = []
    for sig in SWARM_SIGNALS:
        if sig not in triggered:
            continue
        w = float(weights.get(sig, 0))
        swarm_raw_parts.append((sig, w))
    swarm_raw_sum = sum(w for _, w in swarm_raw_parts)
    if is_paradox:
        swarm_factor = paradox_damper
    else:
        swarm_factor = 1.0
    swarm_part = swarm_raw_sum * swarm_factor

    for sig, w in swarm_raw_parts:
        damped = w * swarm_factor
        if is_paradox:
            breakdown_parts.append(
                f'{sig}(x{paradox_damper:g}):{int(round(damped))}'
            )
        else:
            breakdown_parts.append(f'{sig}:{int(round(damped))}')

    # --- Ex-Officio-Bonus: einmalig, falls truthy + Trigger getroffen ---
    ex_officio_truthy = _to_bool(ex_officio_value)
    bonus_active = ex_officio_truthy and bool(triggered & bonus_triggers)
    bonus = bonus_amount if bonus_active else 0.0
    breakdown_parts.append(f'ex_officio:+{int(round(bonus))}')

    # --- Volume: nichtlinear ab Schwelle U ---
    if volume_enabled:
        vol = _to_float(asset_tradingvolume_usd, 0.0)
        volume_part = max(0.0, vol - volume_threshold) * volume_slope
    else:
        volume_part = 0.0
    breakdown_parts.append(f'volume:{int(round(volume_part))}')

    # --- Aggregation ---
    total = swarm_part + other_part + volume_part + bonus
    score = int(round(total))
    breakdown = ';'.join(breakdown_parts)
    warnings = paradox_tag if is_paradox else ''
    return score, breakdown, warnings


def compute_trade_scores(df: pd.DataFrame, settings_module: Any) -> pd.DataFrame:
    """Vektorisierter Wrapper: liefert 3-Spalten-DataFrame fuer alle Trades.

    Liefert ``DataFrame`` mit Spalten
    ``['trade_score', 'trade_score_breakdown', 'trade_warnings']`` in der
    Reihenfolge von ``df.index``. Fehlende Spalten werden als leer behandelt.
    """
    if df.empty:
        return pd.DataFrame(
            {'trade_score': pd.Series(dtype='int64'),
             'trade_score_breakdown': pd.Series(dtype='object'),
             'trade_warnings': pd.Series(dtype='object')},
            index=df.index,
        )

    sig_col = df['signals_triggered'] if 'signals_triggered' in df.columns \
        else pd.Series([''] * len(df), index=df.index)
    ex_col = df['Member_ExOfficio'] if 'Member_ExOfficio' in df.columns \
        else pd.Series([''] * len(df), index=df.index)
    vol_col = df['asset_tradingvolume_usd'] \
        if 'asset_tradingvolume_usd' in df.columns \
        else pd.Series([0] * len(df), index=df.index)

    scores = []
    breakdowns = []
    warnings = []
    for sig_val, ex_val, vol_val in zip(sig_col, ex_col, vol_col):
        s, b, w = compute_trade_score(sig_val, ex_val, vol_val, settings_module)
        scores.append(s)
        breakdowns.append(b)
        warnings.append(w)

    return pd.DataFrame(
        {'trade_score': pd.Series(scores, index=df.index, dtype='int64'),
         'trade_score_breakdown': pd.Series(breakdowns, index=df.index,
                                            dtype='object'),
         'trade_warnings': pd.Series(warnings, index=df.index, dtype='object')},
    )
