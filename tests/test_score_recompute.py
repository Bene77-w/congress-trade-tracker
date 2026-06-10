"""Tests fuer app/score_recompute.py — Settings-Proxy + Recompute.

Deckt insbesondere den weights_override / params_override-Pfad ab, der vom
Streamlit-Dashboard (Sidebar-Form) genutzt wird.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.score_recompute import (  # noqa: E402
    build_settings_proxy,
    recompute_subset,
)
from config import settings as _settings  # noqa: E402


def _trade_row(signals: str, vol: int = 0, role_keys: str = '') -> dict:
    return {
        'signals_triggered': signals,
        'ex_officio_role_keys': role_keys,
        'trade_amount_max_usd': vol,
        'trade_score': -1,  # platzhalter, wird ueberschrieben
        'trade_score_breakdown': '',
        'trade_warnings': '',
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. weights_override greift durch
# ---------------------------------------------------------------------------
def test_weights_override_takes_effect():
    proxy = build_settings_proxy(
        excluded=[],
        weights_override={'committee': 99},
    )
    assert proxy.SCORING_WEIGHTS['committee'] == 99
    # andere Keys unveraendert
    assert proxy.SCORING_WEIGHTS['swarm_buy'] == \
        _settings.SCORING_WEIGHTS['swarm_buy']


def test_recompute_uses_weights_override():
    df = _df([_trade_row('committee')])
    out = recompute_subset(df, excluded=[],
                           weights_override={'committee': 77})
    assert int(out.iloc[0]['trade_score']) == 77
    assert 'committee:77' in out.iloc[0]['trade_score_breakdown']


# ---------------------------------------------------------------------------
# 2. params_override (Paradox-Damper) greift durch
# ---------------------------------------------------------------------------
def test_params_override_paradox_damper():
    # Default-Damper = 0.0  -> Schwarm wird auf 0 gedaempft.
    df = _df([_trade_row('swarm_buy;swarm_paradox')])
    base = recompute_subset(df, excluded=[])
    base_score = int(base.iloc[0]['trade_score'])

    # Damper = 1.0 -> keine Daempfung, voller Schwarm-Score.
    overridden = recompute_subset(
        df, excluded=[], params_override={'paradox_damper': 1.0},
    )
    over_score = int(overridden.iloc[0]['trade_score'])
    assert over_score > base_score
    assert over_score == _settings.SCORING_WEIGHTS['swarm_buy']


# ---------------------------------------------------------------------------
# 3. Exclude gewinnt ueber Override (Reihenfolge im Proxy)
# ---------------------------------------------------------------------------
def test_exclude_overrides_weights_override():
    proxy = build_settings_proxy(
        excluded=['committee'],
        weights_override={'committee': 999},
    )
    # Exclude wird zuletzt angewendet -> 0 gewinnt.
    assert proxy.SCORING_WEIGHTS['committee'] == 0


def test_recompute_exclude_wins_over_override():
    df = _df([_trade_row('committee')])
    out = recompute_subset(
        df, excluded=['committee'], weights_override={'committee': 50},
    )
    assert int(out.iloc[0]['trade_score']) == 0


# ---------------------------------------------------------------------------
# 4. Leere Overrides == Settings-Defaults
# ---------------------------------------------------------------------------
def test_no_override_matches_settings_defaults():
    proxy = build_settings_proxy(excluded=[])
    assert proxy.SCORING_WEIGHTS == _settings.SCORING_WEIGHTS
    assert proxy.SCORING_PARADOX_DAMPER == _settings.SCORING_PARADOX_DAMPER
    assert proxy.SCORING_TRADE_VOLUME_ENABLED == \
        _settings.SCORING_TRADE_VOLUME_ENABLED
    assert proxy.SCORING_TRADE_VOLUME_TIERS == \
        _settings.SCORING_TRADE_VOLUME_TIERS
    assert proxy.SCORING_TRADE_VOLUME_TRIGGER_FLOOR == \
        _settings.SCORING_TRADE_VOLUME_TRIGGER_FLOOR
    assert proxy.SCORING_EX_OFFICIO_ROLE_BONUSES == \
        _settings.SCORING_EX_OFFICIO_ROLE_BONUSES
    assert proxy.SCORING_EX_OFFICIO_COMMITTEE_GLOBAL == \
        _settings.SCORING_EX_OFFICIO_COMMITTEE_GLOBAL


def test_volume_tiers_override():
    # Override-Tiers: alles ueber 0 -> 50 Punkte (einmaliger Catch-all-Tier).
    # weights[trade_volume]=16 (Default) und tier_max=50 -> scale = 16/50 = 0.32
    # 100k Trade -> 50 * 0.32 = 16 Punkte.
    custom_tiers = [(None, 50)]
    df = _df([_trade_row('trade_volume', vol=100_000)])
    out = recompute_subset(
        df, excluded=[],
        params_override={'trade_volume_tiers': custom_tiers,
                         'trade_volume_trigger': 0.0},
    )
    assert int(out.iloc[0]['trade_score']) == 16


def test_volume_excluded_disables_signal():
    # Trade ueber Floor + trade_volume getriggert -> normal +16 (Top-Tier)
    df = _df([_trade_row('trade_volume', vol=5_000_000)])
    base = recompute_subset(df, excluded=[])
    assert int(base.iloc[0]['trade_score']) == 16

    # Mit excluded='trade_volume' -> Signal-Beitrag faellt weg
    excluded = recompute_subset(df, excluded=['trade_volume'])
    assert int(excluded.iloc[0]['trade_score']) == 0


def test_ex_officio_role_bonuses_override():
    df = _df([_trade_row('committee', role_keys='committee_ranking_member')])
    base = recompute_subset(df, excluded=[])
    base_score = int(base.iloc[0]['trade_score'])

    out = recompute_subset(
        df, excluded=[],
        params_override={'ex_officio_role_bonuses':
                         {'committee_ranking_member': 50.0}},
    )
    over_score = int(out.iloc[0]['trade_score'])
    # Differenz = neuer Bonus - alter Bonus (Default aus Settings-Tabelle)
    expected_delta = 50 - int(
        _settings.SCORING_EX_OFFICIO_ROLE_BONUSES['committee_ranking_member']
    )
    assert over_score - base_score == expected_delta


def test_exclude_committee_ex_officio_empties_bonus_table():
    proxy = build_settings_proxy(excluded=['committee_ex_officio'])
    assert proxy.SCORING_EX_OFFICIO_ROLE_BONUSES == {}


def test_exclude_committee_ex_officio_wins_over_override():
    proxy = build_settings_proxy(
        excluded=['committee_ex_officio'],
        params_override={'ex_officio_role_bonuses':
                         {'committee_chairman': 99.0}},
    )
    assert proxy.SCORING_EX_OFFICIO_ROLE_BONUSES == {}


def test_recompute_exclude_committee_ex_officio_drops_bonus():
    df = _df([_trade_row('committee', role_keys='committee_chairman')])
    base = recompute_subset(df, excluded=[])
    excl = recompute_subset(df, excluded=['committee_ex_officio'])
    expected_delta = -int(
        _settings.SCORING_EX_OFFICIO_ROLE_BONUSES['committee_chairman']
    )
    assert (int(excl.iloc[0]['trade_score'])
            - int(base.iloc[0]['trade_score'])) == expected_delta
    # Das committee-Signal selbst bleibt aktiv, nur der Bonus faellt weg.
    assert 'committee:' in excl.iloc[0]['trade_score_breakdown']
    assert 'ex_officio:+0' in excl.iloc[0]['trade_score_breakdown']


def test_ex_officio_committee_global_override_disables_committee_keys():
    df = _df([_trade_row('committee', role_keys='committee_ranking_member')])
    base = recompute_subset(df, excluded=[])
    base_score = int(base.iloc[0]['trade_score'])

    out = recompute_subset(
        df, excluded=[],
        params_override={'ex_officio_committee_global': False},
    )
    over_score = int(out.iloc[0]['trade_score'])
    # committee_*-Key faellt weg -> Score sinkt um den Default-Bonus
    expected_delta = -int(
        _settings.SCORING_EX_OFFICIO_ROLE_BONUSES['committee_ranking_member']
    )
    assert over_score - base_score == expected_delta
