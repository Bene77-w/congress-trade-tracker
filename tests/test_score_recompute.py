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
    # committee_full ist Primaersignal -> passiert den Hard-Gate.
    df = _df([_trade_row('committee_full')])
    out = recompute_subset(df, excluded=[],
                           weights_override={'committee_full': 77})
    assert int(out.iloc[0]['trade_score']) == 77
    assert 'committee_full:77' in out.iloc[0]['trade_score_breakdown']


# ---------------------------------------------------------------------------
# 2. params_override (Paradox-Damper) greift durch
# ---------------------------------------------------------------------------
def test_params_override_paradox_damper():
    # swarm_buy ist Primaer -> Gate passt. Default-Damper = 1.0 -> voller
    # Schwarm-Score; Override 0.0 -> swarm_buy wird gedaempft.
    df = _df([_trade_row('swarm_buy;swarm_paradox')])
    base = recompute_subset(df, excluded=[])
    base_score = int(base.iloc[0]['trade_score'])
    assert base_score == _settings.SCORING_WEIGHTS['swarm_buy']

    overridden = recompute_subset(
        df, excluded=[], params_override={'paradox_damper': 0.0},
    )
    over_score = int(overridden.iloc[0]['trade_score'])
    assert over_score < base_score
    assert over_score == 0


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
    # trade_volume ist Hint (per Default deaktiviert + gegated). Mit swarm_buy
    # als Primaersignal (Gate-Pass, Gewicht via Override auf 0 -> isoliert) und
    # aktiviertem Volume testen wir die Tier-Skalierung pur.
    # Override-Tiers: alles ueber 0 -> 50 Punkte (einmaliger Catch-all-Tier).
    # weights[trade_volume]=16 (Default) und tier_max=50 -> scale = 16/50 = 0.32
    # 100k Trade -> 50 * 0.32 = 16 Punkte.
    custom_tiers = [(None, 50)]
    df = _df([_trade_row('swarm_buy;trade_volume', vol=100_000)])
    out = recompute_subset(
        df, excluded=[],
        weights_override={'swarm_buy': 0},
        params_override={'trade_volume_enabled': True,
                         'trade_volume_tiers': custom_tiers,
                         'trade_volume_trigger': 0.0},
    )
    assert int(out.iloc[0]['trade_score']) == 16


def test_volume_excluded_disables_signal():
    # swarm_buy als Primaer (Gate), Gewicht 0 -> isoliert den Volume-Beitrag.
    df = _df([_trade_row('swarm_buy;trade_volume', vol=5_000_000)])
    base = recompute_subset(
        df, excluded=[], weights_override={'swarm_buy': 0},
        params_override={'trade_volume_enabled': True})
    assert int(base.iloc[0]['trade_score']) == 16

    # Mit excluded='trade_volume' -> SCORING_TRADE_VOLUME_ENABLED=False -> 0
    excluded = recompute_subset(
        df, excluded=['trade_volume'], weights_override={'swarm_buy': 0},
        params_override={'trade_volume_enabled': True})
    assert int(excluded.iloc[0]['trade_score']) == 0


def test_ex_officio_role_bonuses_override():
    # Bonus feuert nur bei Trigger {committee, floor_action} (beide Hints) UND
    # benoetigt ein Primaersignal (Gate). Daher swarm_buy (Primaer) + committee
    # (Trigger). Default-Bonus ist jetzt 0 -> Wirkung via Override testen.
    df = _df([_trade_row('swarm_buy;committee',
                         role_keys='committee_ranking_member')])
    base = recompute_subset(df, excluded=[])
    base_score = int(base.iloc[0]['trade_score'])

    out = recompute_subset(
        df, excluded=[],
        params_override={'ex_officio_role_bonuses':
                         {'committee_ranking_member': 50.0}},
    )
    over_score = int(out.iloc[0]['trade_score'])
    # Differenz = neuer Bonus - alter Bonus (Default aus Settings-Tabelle = 0)
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
    # Primaer (swarm_buy) + Trigger (committee); Bonus via Override aktiv setzen,
    # damit das Exclude etwas zu entfernen hat (Default-Bonus = 0).
    df = _df([_trade_row('swarm_buy;committee', role_keys='committee_chairman')])
    bonus_override = {'ex_officio_role_bonuses': {'committee_chairman': 30.0}}
    base = recompute_subset(df, excluded=[], params_override=bonus_override)
    excl = recompute_subset(df, excluded=['committee_ex_officio'],
                            params_override=bonus_override)
    # exclude leert die Bonus-Tabelle -> Bonus 0; base hatte +30
    assert (int(base.iloc[0]['trade_score'])
            - int(excl.iloc[0]['trade_score'])) == 30
    # Das committee-Signal selbst bleibt aktiv, nur der Bonus faellt weg.
    assert 'committee:' in excl.iloc[0]['trade_score_breakdown']
    assert 'ex_officio:+0' in excl.iloc[0]['trade_score_breakdown']


# ---------------------------------------------------------------------------
# 5. Exclude neutralisiert Primaersignale auch im Hard-Gate (R2a)
# ---------------------------------------------------------------------------
def test_exclude_removes_signal_from_primary_signals():
    proxy = build_settings_proxy(excluded=['committee_full'])
    assert 'committee_full' not in proxy.SCORING_PRIMARY_SIGNALS
    assert 'swarm_buy' in proxy.SCORING_PRIMARY_SIGNALS


def test_excluded_primary_signal_closes_gate():
    # committee_full ist Primaersignal, floor_action ist Hint (Gewicht via
    # Override 10). Wird committee_full ausgeschlossen, ist es kein Primaer-
    # signal mehr -> Trade triggert kein Primaersignal -> Gate greift -> 0.
    df = _df([_trade_row('committee_full;floor_action')])
    out = recompute_subset(
        df, excluded=['committee_full'],
        weights_override={'floor_action': 10},
    )
    assert int(out.iloc[0]['trade_score']) == 0


def test_both_primary_excluded_disables_gate():
    # Beide Primaersignale ausgeschlossen -> Tuple leer -> Gate aus (Rueck-
    # waerts-Kompat) -> Hint-Score kommt durch.
    df = _df([_trade_row('committee_full;floor_action')])
    out = recompute_subset(
        df, excluded=['committee_full', 'swarm_buy'],
        weights_override={'floor_action': 10},
    )
    proxy = build_settings_proxy(excluded=['committee_full', 'swarm_buy'])
    assert proxy.SCORING_PRIMARY_SIGNALS == ()
    assert int(out.iloc[0]['trade_score']) == 10


def test_ex_officio_committee_global_override_disables_committee_keys():
    # Primaer (swarm_buy) + Trigger (committee); Bonus via Override aktiv (30),
    # damit das committee_global=False-Filtern eine messbare Wirkung hat
    # (Default-Bonus ist 0).
    df = _df([_trade_row('swarm_buy;committee',
                         role_keys='committee_ranking_member')])
    bonus_override = {'ex_officio_role_bonuses':
                      {'committee_ranking_member': 30.0}}
    base = recompute_subset(df, excluded=[], params_override=bonus_override)
    base_score = int(base.iloc[0]['trade_score'])

    out = recompute_subset(
        df, excluded=[],
        params_override={**bonus_override,
                         'ex_officio_committee_global': False},
    )
    over_score = int(out.iloc[0]['trade_score'])
    # committee_*-Key faellt weg -> Bonus 0 -> Score sinkt um 30
    assert over_score - base_score == -30
