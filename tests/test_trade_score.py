"""Tests fuer scoring/trade_score.py (Modul 8, Phase 8.3)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoring.trade_score import compute_trade_score, compute_trade_scores


def _settings(**overrides):
    """Erstellt ein synthetisches Settings-Modul mit Default-Werten + Overrides."""
    mod = types.SimpleNamespace(
        SCORING_WEIGHTS={
            'committee': 15,
            'floor_action': 15,
            'extreme_report': 5,
            'contrarian': 8,
            'accumulative': 10,
            'swarm_buy': 20,
            'swarm_sell': 20,
            'swarm_conflict': 5,
            'trade_volume': 16,
        },
        SCORING_EX_OFFICIO_BONUS=10,
        SCORING_EX_OFFICIO_TRIGGERS=['committee', 'floor_action'],
        SCORING_PARADOX_DAMPER=0.0,
        SCORING_PARADOX_WARNING_TAG='paradox_swarm',
        SCORING_TRADE_VOLUME_ENABLED=True,
        SCORING_TRADE_VOLUME_TIERS=[
            (50_000,        2),
            (100_000,       4),
            (250_000,       7),
            (500_000,      10),
            (1_000_000,    13),
            (float('inf'), 16),
        ],
        SCORING_TRADE_VOLUME_TRIGGER_FLOOR=15_000,
    )
    for k, v in overrides.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# 1. Branch-1 alleine
# ---------------------------------------------------------------------------
def test_committee_only():
    s, b, w = compute_trade_score('committee', '', 0, _settings())
    assert s == 15
    assert 'committee:15' in b
    assert 'ex_officio:+0' in b
    # trade_volume nicht in triggered -> keine trade_volume-Komponente
    assert 'trade_volume' not in b
    assert w == ''


# ---------------------------------------------------------------------------
# 2. Ex-Officio-Bonus aktiv
# ---------------------------------------------------------------------------
def test_committee_with_ex_officio_bonus():
    s, b, w = compute_trade_score('committee', 'Chairman', 0, _settings())
    assert s == 15 + 10
    assert 'ex_officio:+10' in b


# ---------------------------------------------------------------------------
# 3. Ex-Officio truthy aber kein Trigger-Signal -> kein Bonus
# ---------------------------------------------------------------------------
def test_ex_officio_without_trigger_signal_no_bonus():
    # extreme_report ist nicht in SCORING_EX_OFFICIO_TRIGGERS
    s, b, w = compute_trade_score('extreme_report', 'Chairman', 0, _settings())
    assert s == 5
    assert 'ex_officio:+0' in b


# ---------------------------------------------------------------------------
# 4. Schwarm-Buy ohne Paradox
# ---------------------------------------------------------------------------
def test_swarm_buy_only():
    s, b, w = compute_trade_score('swarm_buy', '', 0, _settings())
    assert s == 20
    assert 'swarm_buy:20' in b
    assert w == ''


# ---------------------------------------------------------------------------
# 5. Schwarm-Buy + Paradox (DAMPER=0.0) -> Schwarm-Anteil wegfaellt, Warning
# ---------------------------------------------------------------------------
def test_swarm_buy_with_paradox_full_damping():
    s, b, w = compute_trade_score('swarm_buy;swarm_paradox', '', 0, _settings())
    assert s == 0
    assert 'swarm_buy(x0):0' in b
    assert w == 'paradox_swarm'


# ---------------------------------------------------------------------------
# 6. Schwarm-Buy + Paradox (DAMPER=0.5) -> halbiert
# ---------------------------------------------------------------------------
def test_swarm_buy_with_paradox_half_damping():
    settings = _settings(SCORING_PARADOX_DAMPER=0.5)
    s, b, w = compute_trade_score('swarm_buy;swarm_paradox', '', 0, settings)
    assert s == 10
    assert 'swarm_buy(x0.5):10' in b
    assert w == 'paradox_swarm'


# ---------------------------------------------------------------------------
# 7. Volume-Signal nicht in signals_triggered -> keine Volume-Komponente
# ---------------------------------------------------------------------------
def test_volume_not_triggered_no_contribution():
    # auch wenn amount hoch ist: ohne 'trade_volume' in signals_triggered -> 0
    s, b, w = compute_trade_score('', '', 50_000_000, _settings())
    assert s == 0
    assert 'trade_volume' not in b


# ---------------------------------------------------------------------------
# 8. Volume-Signal getriggert ab Tier 1 (knapp ueber Floor)
# ---------------------------------------------------------------------------
def test_volume_tier_1():
    # 50_000 -> Tier 1 -> 2 Punkte (scale=1.0 weil weights[trade_volume]=16=tier_max)
    s, b, w = compute_trade_score('trade_volume', '', 50_000, _settings())
    assert s == 2
    assert 'trade_volume:2' in b


# ---------------------------------------------------------------------------
# 9. Volume-Signal Top-Tier
# ---------------------------------------------------------------------------
def test_volume_top_tier():
    s, b, w = compute_trade_score('trade_volume', '', 50_000_000, _settings())
    assert s == 16
    assert 'trade_volume:16' in b


# ---------------------------------------------------------------------------
# 10. SCORING_TRADE_VOLUME_ENABLED=False -> Beitrag 0 unabh. vom Trigger
# ---------------------------------------------------------------------------
def test_volume_disabled():
    settings = _settings(SCORING_TRADE_VOLUME_ENABLED=False)
    s, b, w = compute_trade_score('trade_volume', '', 50_000_000, settings)
    assert s == 0
    # volume_part wurde nicht berechnet -> keine Breakdown-Zeile
    assert 'trade_volume' not in b


# ---------------------------------------------------------------------------
# 10b. UI-Override des nominellen Max-Gewichts skaliert den Tier-Beitrag
# ---------------------------------------------------------------------------
def test_volume_weight_override_scales_contribution():
    # weights[trade_volume]=8 (halbiert) -> Top-Tier-Beitrag halbiert auf 8
    settings = _settings(SCORING_WEIGHTS={
        'committee': 15, 'floor_action': 15, 'extreme_report': 5,
        'contrarian': 8, 'accumulative': 10,
        'swarm_buy': 20, 'swarm_sell': 20, 'swarm_conflict': 5,
        'trade_volume': 8,
    })
    s, b, w = compute_trade_score('trade_volume', '', 50_000_000, settings)
    assert s == 8
    assert 'trade_volume:8' in b


# ---------------------------------------------------------------------------
# 10c. trade_volume-Weight=0 -> kein Beitrag, selbst bei getriggertem Signal
# ---------------------------------------------------------------------------
def test_volume_weight_zero_no_contribution():
    settings = _settings(SCORING_WEIGHTS={
        'committee': 15, 'floor_action': 15, 'extreme_report': 5,
        'contrarian': 8, 'accumulative': 10,
        'swarm_buy': 20, 'swarm_sell': 20, 'swarm_conflict': 5,
        'trade_volume': 0,
    })
    s, b, w = compute_trade_score('trade_volume', '', 50_000_000, settings)
    assert s == 0
    assert 'trade_volume:0' in b


# ---------------------------------------------------------------------------
# 11. WEIGHTS['committee']=0 -> Signal traegt 0 bei
# ---------------------------------------------------------------------------
def test_signal_weight_zero():
    settings = _settings(SCORING_WEIGHTS={
        'committee': 0, 'floor_action': 15, 'extreme_report': 5,
        'contrarian': 8, 'accumulative': 10,
        'swarm_buy': 20, 'swarm_sell': 20, 'swarm_conflict': 5,
        'trade_volume': 16,
    })
    s, b, w = compute_trade_score('committee', 'Chairman', 0, settings)
    # committee trifft (Bonus-Trigger), Bonus addiert sich aber Weight ist 0.
    assert s == 10
    assert 'committee:0' in b
    assert 'ex_officio:+10' in b


# ---------------------------------------------------------------------------
# 12. Alle Signale aktiv -> grosser Score, kompletter Breakdown
# ---------------------------------------------------------------------------
def test_all_signals_active():
    sig = ('committee;floor_action;extreme_report;contrarian;accumulative;'
           'swarm_buy;swarm_sell;trade_volume')
    # 1.1M -> Tier 6 -> 16 Punkte
    s, b, w = compute_trade_score(sig, 'Chairman', 1_100_000, _settings())
    # 15 + 15 + 5 + 8 + 10 + 20 + 20 + 16 (volume) + 10 (bonus) = 119
    assert s == 119
    for part in ['committee:15', 'floor_action:15', 'extreme_report:5',
                 'contrarian:8', 'accumulative:10',
                 'swarm_buy:20', 'swarm_sell:20',
                 'trade_volume:16', 'ex_officio:+10']:
        assert part in b
    assert w == ''


# ---------------------------------------------------------------------------
# 13. Leerer Signal-String -> nur volume + bonus moeglich, bonus=0
# ---------------------------------------------------------------------------
def test_empty_signals_string():
    s, b, w = compute_trade_score('', '', 0, _settings())
    assert s == 0
    # Ohne Signale: nur ex_officio-Komponente sichtbar (Bonus inaktiv -> +0).
    # trade_volume-Komponente erscheint nur wenn Signal getriggert hat.
    assert b == 'ex_officio:+0'
    assert w == ''


# ---------------------------------------------------------------------------
# 14. NaN-signals -> score=0, leere warnings
# ---------------------------------------------------------------------------
def test_nan_signals():
    s, b, w = compute_trade_score(float('nan'), '', 0, _settings())
    assert s == 0
    assert w == ''


# ---------------------------------------------------------------------------
# 15. NaN-asset_tradingvolume_usd -> volume_part=0
# ---------------------------------------------------------------------------
def test_nan_volume():
    # NaN als amount_max -> Signal nicht getriggert (Aufrufer entscheidet),
    # hier signal_string ohne trade_volume -> keine volume-Komponente.
    s, b, w = compute_trade_score('committee', '', float('nan'), _settings())
    assert s == 15
    assert 'trade_volume' not in b


def test_nan_volume_with_signal_triggered_no_score():
    # Defensiv: wenn Caller trotz NaN amount das Signal triggert (Edge-Case),
    # liefert resolve_tier (0, 0) -> volume_part 0
    s, b, w = compute_trade_score(
        'committee;trade_volume', '', float('nan'), _settings(),
    )
    assert s == 15
    assert 'trade_volume:0' in b


# ---------------------------------------------------------------------------
# 16. ex_officio als String 'Chairman' -> truthy
# ---------------------------------------------------------------------------
def test_ex_officio_string_chairman_is_truthy():
    s, b, w = compute_trade_score('committee', 'Chairman', 0, _settings())
    assert 'ex_officio:+10' in b


def test_ex_officio_nan_is_falsy():
    s, b, w = compute_trade_score('committee', float('nan'), 0, _settings())
    assert 'ex_officio:+0' in b


# ---------------------------------------------------------------------------
# 17. Score wird int zurueckgegeben (round)
# ---------------------------------------------------------------------------
def test_score_is_int():
    # Skalierungsfaktor 0.5 (weight=8, tier_max=16) erzwingt Bruchteil-Rundung
    settings = _settings(SCORING_WEIGHTS={
        'committee': 15, 'floor_action': 15, 'extreme_report': 5,
        'contrarian': 8, 'accumulative': 10,
        'swarm_buy': 20, 'swarm_sell': 20, 'swarm_conflict': 5,
        'trade_volume': 8,
    })
    # 250k -> Tier 3 -> 7 Punkte * 0.5 = 3.5 -> round -> 4 (banker's: ties to even -> 4)
    s, b, w = compute_trade_score('trade_volume', '', 250_000, settings)
    assert isinstance(s, int)
    assert s == 4


# ---------------------------------------------------------------------------
# 18. Breakdown-Reihenfolge ist deterministisch
# ---------------------------------------------------------------------------
def test_breakdown_order_deterministic():
    # Reihenfolge: committee, floor_action, extreme_report (Branch-1)
    #              -> contrarian (Sequenz)
    #              -> swarm_buy, swarm_sell, swarm_conflict (Schwarm)
    #              -> trade_volume -> ex_officio
    sig = ('swarm_buy;extreme_report;contrarian;floor_action;committee;'
           'trade_volume')
    _, b, _ = compute_trade_score(sig, '', 50_000, _settings())
    parts = b.split(';')
    expected_order = ['committee:15', 'floor_action:15', 'extreme_report:5',
                      'contrarian:8', 'swarm_buy:20',
                      'trade_volume:2', 'ex_officio:+0']
    assert parts == expected_order


# ---------------------------------------------------------------------------
# 19. Paradox alleine (ohne Schwarm-Buy/Sell/Conflict) -> swarm_part=0, Warning
# ---------------------------------------------------------------------------
def test_paradox_alone():
    s, b, w = compute_trade_score('swarm_paradox', '', 0, _settings())
    assert s == 0
    assert w == 'paradox_swarm'


# ---------------------------------------------------------------------------
# 20. Vektor-Wrapper compute_trade_scores
# ---------------------------------------------------------------------------
def test_compute_trade_scores_vectorized():
    df = pd.DataFrame({
        'signals_triggered': ['committee', 'swarm_buy;swarm_paradox',
                              'trade_volume'],
        'Member_ExOfficio': ['Chairman', '', ''],
        'trade_amount_max_usd': [0, 0, 1_100_000],
    })
    out = compute_trade_scores(df, _settings())
    assert list(out.columns) == ['trade_score', 'trade_score_breakdown',
                                 'trade_warnings']
    # Row 1: committee(15) + ex_officio(10) = 25
    # Row 2: swarm_buy * paradox(0) = 0
    # Row 3: trade_volume Tier 6 (1.1M -> 16)
    assert out['trade_score'].tolist() == [25, 0, 16]
    assert out['trade_warnings'].tolist() == ['', 'paradox_swarm', '']


def test_compute_trade_scores_empty_df():
    df = pd.DataFrame(columns=['signals_triggered', 'Member_ExOfficio',
                               'trade_amount_max_usd'])
    out = compute_trade_scores(df, _settings())
    assert out.empty
    assert list(out.columns) == ['trade_score', 'trade_score_breakdown',
                                 'trade_warnings']


def test_compute_trade_scores_missing_columns():
    # Defensiv: fehlende Eingangsspalten werden tolerant behandelt.
    df = pd.DataFrame({'signals_triggered': ['committee', 'swarm_buy']})
    out = compute_trade_scores(df, _settings())
    assert out['trade_score'].tolist() == [15, 20]
