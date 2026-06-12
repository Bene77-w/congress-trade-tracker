"""Trade-Score-Berechnung (Modul 8).

Liefert pro Trade ein Tripel ``(trade_score, trade_score_breakdown,
trade_warnings)`` nach der Formel:

    swarm_part  = Σ WEIGHTS[s] für s in signals_triggered ∩ swarm_signals
    if 'swarm_paradox' in signals_triggered:
        swarm_part *= SCORING_PARADOX_DAMPER

    other_part  = Σ WEIGHTS[s] für s in signals_triggered \\ swarm_signals
                                                        \\ {swarm_paradox}
                                                        \\ {trade_volume}

    volume_part = tier_score(trade_amount_max_usd, SCORING_TRADE_VOLUME_TIERS)
                  (0 wenn SCORING_TRADE_VOLUME_ENABLED=False oder
                   'trade_volume' nicht in signals_triggered)

    bonus       = max(SCORING_EX_OFFICIO_ROLE_BONUSES[k] fuer k in
                  ex_officio_role_keys), falls mindestens ein Key in der
                  Bonus-Tabelle steht UND signals_triggered ∩
                  SCORING_EX_OFFICIO_TRIGGERS ≠ ∅ (MAX, nicht Summe).
                  committee_*-Keys zaehlen nur, wenn
                  SCORING_EX_OFFICIO_COMMITTEE_GLOBAL=True.

    trade_score = round(swarm_part + other_part + volume_part + bonus)

    trade_warnings = SCORING_PARADOX_WARNING_TAG, falls 'swarm_paradox' getriggert.

Breakdown-Format (volle Verbositaet): ``";"``-getrennte ``key:value``-Paare in
fester Reihenfolge. Es erscheinen ausschliesslich Komponenten, deren Signal
auch wirklich in ``signals_triggered`` enthalten ist (sonst Laengen-Explosion);
Ex-Officio-Bonus erscheint immer (auch mit Wert 0). ``trade_volume`` erscheint
nur, wenn das Signal getriggert hat — der Beitrag kommt dann aus der
Tier-Tabelle, nicht aus ``SCORING_WEIGHTS``.
"""
from __future__ import annotations

from typing import Any, Tuple

import pandas as pd

from signals.trade_volume_signal import (
    resolve_tier as _resolve_volume_tier,
    _normalize_tiers as _normalize_volume_tiers,
)

# Schwarm-Signal-Klassen — fuer Paradox-Daempfung gruppieren wir nur
# swarm_buy/sell/conflict; swarm_paradox ist der Daempfer selbst.
SWARM_SIGNALS = ('swarm_buy', 'swarm_sell', 'swarm_conflict')
PARADOX_SIGNAL = 'swarm_paradox'
TRADE_VOLUME_SIGNAL = 'trade_volume'

# Feste Reihenfolge fuer Breakdown-Strings (deterministische CSV-Diffs).
BRANCH1_ORDER = ('committee', 'committee_full', 'floor_action', 'extreme_report')
SEQUENCE_ORDER = ('contrarian', 'accumulative')


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
                        ex_officio_role_keys: Any,
                        trade_amount_max_usd: Any,
                        settings_module: Any) -> Tuple[int, str, str]:
    """Berechnet ``(score, breakdown, warnings)`` fuer einen einzelnen Trade.

    Parameters
    ----------
    signals_value:
        Zellwert aus ``signals_triggered`` (``;``-getrennter String oder NaN).
    ex_officio_role_keys:
        Zellwert aus ``ex_officio_role_keys`` (``;``-getrennte Fuehrungs-
        rollen-Keys, z.B. ``"committee_chairman;subcommittee_chair"``;
        leer/NaN wenn kein relevanter Treffer eine Fuehrungsrolle traegt).
    trade_amount_max_usd:
        Zellwert aus ``trade_amount_max_usd`` (int, kann 0/NaN sein). Wird
        nur ausgewertet, wenn ``'trade_volume'`` in ``signals_value`` ist.
    settings_module:
        Modul mit den ``SCORING_*``-Konstanten (typischerweise
        ``config.settings``).
    """
    weights = getattr(settings_module, 'SCORING_WEIGHTS', {}) or {}
    paradox_damper = float(getattr(settings_module, 'SCORING_PARADOX_DAMPER', 0.0))
    paradox_tag = getattr(settings_module, 'SCORING_PARADOX_WARNING_TAG',
                          'paradox_swarm')
    volume_enabled = bool(getattr(
        settings_module, 'SCORING_TRADE_VOLUME_ENABLED', True
    ))
    volume_tiers = getattr(
        settings_module, 'SCORING_TRADE_VOLUME_TIERS', []
    ) or []
    volume_trigger_floor = float(getattr(
        settings_module, 'SCORING_TRADE_VOLUME_TRIGGER_FLOOR', 0.0
    ))
    role_bonuses = getattr(settings_module,
                           'SCORING_EX_OFFICIO_ROLE_BONUSES', {}) or {}
    committee_global = getattr(settings_module,
                               'SCORING_EX_OFFICIO_COMMITTEE_GLOBAL', None)
    if committee_global is None:
        committee_global = True
    committee_global = bool(committee_global)
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

    # --- Trade-Volume: Tier-basiert, nur wenn Signal getriggert hat ---
    volume_part = 0.0
    if volume_enabled and TRADE_VOLUME_SIGNAL in triggered:
        tiers_norm = _normalize_volume_tiers(volume_tiers)
        vol = _to_float(trade_amount_max_usd, 0.0)
        _tier_idx, tier_score = _resolve_volume_tier(
            vol, tiers_norm, volume_trigger_floor
        )
        # SCORING_WEIGHTS['trade_volume'] dient nur als UI-Skalierungsfaktor:
        # tier_score wird relativ zum nominellen Max-Wert skaliert, wenn der
        # User das Gewicht im UI ueberschreibt. Default-Faktor = 1.0 (das
        # Gewicht entspricht dem hoechsten Tier-Score).
        nominal_max = float(weights.get(TRADE_VOLUME_SIGNAL, 0))
        if tiers_norm:
            tier_max = max(int(s) for _, s in tiers_norm)
        else:
            tier_max = 0
        if tier_max > 0:
            scale = nominal_max / tier_max
        else:
            scale = 1.0
        volume_part = tier_score * scale
        breakdown_parts.append(f'trade_volume:{int(round(volume_part))}')

    # --- Ex-Officio-Bonus v2: hoechster Einzelbonus der Rollen-Keys ---
    role_keys = _parse_signals(ex_officio_role_keys)
    if not committee_global:
        role_keys = {k for k in role_keys if not k.startswith('committee_')}
    eligible = [_to_float(role_bonuses.get(k), 0.0)
                for k in role_keys if k in role_bonuses]
    bonus_active = bool(eligible) and bool(triggered & bonus_triggers)
    bonus = max(eligible) if bonus_active else 0.0
    breakdown_parts.append(f'ex_officio:+{int(round(bonus))}')

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
    ex_col = df['ex_officio_role_keys'] if 'ex_officio_role_keys' in df.columns \
        else pd.Series([''] * len(df), index=df.index)
    vol_col = df['trade_amount_max_usd'] \
        if 'trade_amount_max_usd' in df.columns \
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
