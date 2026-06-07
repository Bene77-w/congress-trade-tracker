"""Tests fuer app/member_metrics.py — pure Logik des Member-Detail-Moduls.

Deckt ab: Committee-Parser (an realen Roster-Strings gepinnt inkl. der
verlustbehafteten Grenze), Horizont-Attributierung (Mittelpunkt-Rundung),
paper_return_terminal, Linienende und Trajektorien-Aufbau. Der Kohaerenz-Test
pinnt die bewusste Divergenz Attributierung (first_close) vs. Linienende
(full_close).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.member_metrics import (  # noqa: E402
    DEFAULT_HORIZONS,
    compute_member_trajectories,
    nearest_horizon,
    paper_return_terminal,
    parse_committees_full,
    qualifies_for_horizon,
    qualifying_horizons,
    trajectory_end_offset,
)

H = [30, 60, 90, 120, 180]


# ---------------------------------------------------------------------------
# Committee-Parser — an realen Roster-Strings gepinnt
# ---------------------------------------------------------------------------

WALBERG = ('Education And Workforce|Chairman:Early Childhood, Elementary, And '
           'Secondary Education|Ex Officio;Health, Employment, Labor, And '
           'Pensions|Ex Officio;Higher Education And Workforce Development|Ex '
           'Officio;Workforce Protections|Ex Officio;Natural Resources|:Indian '
           'And Insular Affairs|;Water, Wildlife And Fisheries|')

AUCHINCLOSS = ('Energy And Commerce|:Energy|;Environment|;Health|')

MOORE = ('Budget|;Financial Services|:Digital Assets, Financial Technology, '
         'And Artificial Intelligence|;Financial Institutions|;Oversight And '
         'Investigations|Vice Chairman')

GOTTHEIMER = ('Financial Services|:Capital Markets|;Digital Assets, Financial '
              'Technology, And Artificial Intelligence|;National Security, '
              'Illicit Finance, And International Financial Institutions|;'
              'Permanent Select Committee On Intelligence|')


def test_parse_walberg_two_committees_nested_subs():
    """Walberg: 2 Committees (NICHT 6) mit korrekt verschachtelten Subs."""
    res = parse_committees_full(WALBERG)
    assert [c['committee'] for c in res] == [
        'Education And Workforce', 'Natural Resources']

    edu = res[0]
    assert edu['role'] == 'Chairman'
    assert [s['name'] for s in edu['subcommittees']] == [
        'Early Childhood, Elementary, And Secondary Education',
        'Health, Employment, Labor, And Pensions',
        'Higher Education And Workforce Development',
        'Workforce Protections',
    ]
    assert all(s['role'] == 'Ex Officio' for s in edu['subcommittees'])

    nat = res[1]
    assert nat['role'] == ''
    assert [s['name'] for s in nat['subcommittees']] == [
        'Indian And Insular Affairs', 'Water, Wildlife And Fisheries']
    assert all(s['role'] == '' for s in nat['subcommittees'])


def test_parse_auchincloss_single_committee_three_subs():
    res = parse_committees_full(AUCHINCLOSS)
    assert len(res) == 1
    assert res[0]['committee'] == 'Energy And Commerce'
    assert res[0]['role'] == ''
    assert [s['name'] for s in res[0]['subcommittees']] == [
        'Energy', 'Environment', 'Health']


def test_parse_moore_committee_without_subs_then_with():
    """Budget (keine Subs) gefolgt von Financial Services (mit Subs + Rolle)."""
    res = parse_committees_full(MOORE)
    assert [c['committee'] for c in res] == ['Budget', 'Financial Services']
    assert res[0]['subcommittees'] == []
    fs = res[1]
    assert [s['name'] for s in fs['subcommittees']] == [
        'Digital Assets, Financial Technology, And Artificial Intelligence',
        'Financial Institutions',
        'Oversight And Investigations',
    ]
    assert fs['subcommittees'][-1]['role'] == 'Vice Chairman'


def test_parse_gottheimer_lossy_limitation_documented():
    """Dokumentierte Grenze: subcommittee-loses Folge-Committee landet als Sub.

    'Permanent Select Committee On Intelligence' ist eigentlich ein eigenes
    Committee, im Quellformat aber nicht von einem Subcommittee unterscheidbar.
    Wir pinnen das bewusste Verhalten (nicht "falsch", sondern format-bedingt).
    """
    res = parse_committees_full(GOTTHEIMER)
    assert len(res) == 1
    assert res[0]['committee'] == 'Financial Services'
    sub_names = [s['name'] for s in res[0]['subcommittees']]
    assert sub_names[-1] == 'Permanent Select Committee On Intelligence'


def test_parse_empty_and_invalid():
    assert parse_committees_full('') == []
    assert parse_committees_full(None) == []
    assert parse_committees_full(np.nan) == []
    assert parse_committees_full(123) == []


# ---------------------------------------------------------------------------
# Horizont-Attributierung
# ---------------------------------------------------------------------------

def test_nearest_horizon_midpoints():
    assert nearest_horizon(20, H) == 30
    assert nearest_horizon(45, H) == 60     # exakt Mittelpunkt -> hoeher
    assert nearest_horizon(69, H) == 60
    assert nearest_horizon(75, H) == 90     # exakt Mittelpunkt -> hoeher
    assert nearest_horizon(76, H) == 90
    assert nearest_horizon(150, H) == 180
    assert nearest_horizon(400, H) == 180
    assert nearest_horizon(0, H) == 30


def test_nearest_horizon_default_horizons():
    assert DEFAULT_HORIZONS == H
    assert nearest_horizon(69) == 60


def test_qualifying_horizons_user_examples():
    assert qualifying_horizons(69, H) == [30, 60]        # B@69
    assert qualifying_horizons(76, H) == [30, 60, 90]    # ss@76
    assert qualifying_horizons(20, H) == [30]
    assert qualifying_horizons(400, H) == [30, 60, 90, 120, 180]


def test_qualifying_horizons_open_trade_all():
    assert qualifying_horizons(np.nan, H) == H
    assert qualifying_horizons(None, H) == H
    assert qualifying_horizons(pd.NA, H) == H


def test_qualifies_for_horizon_vectorized():
    df = pd.DataFrame({'first_close_offset_days': [69.0, 76.0, np.nan, 20.0]})
    # B@69 -> {30,60}; ss@76 -> {30,60,90}; offen -> alle; 20 -> {30}
    assert qualifies_for_horizon(df, 30, H).tolist() == [True, True, True, True]
    assert qualifies_for_horizon(df, 60, H).tolist() == [True, True, True, False]
    assert qualifies_for_horizon(df, 90, H).tolist() == [False, True, True, False]
    assert qualifies_for_horizon(df, 180, H).tolist() == [False, False, True, False]


# ---------------------------------------------------------------------------
# paper_return_terminal
# ---------------------------------------------------------------------------

def test_paper_return_terminal_picks_nearest_horizon_column():
    df = pd.DataFrame({
        'first_close_offset_days': [20.0, 69.0, 76.0, np.nan],
        'post_return_30d':  [1.0, 2.0, 3.0, 4.0],
        'post_return_60d':  [11.0, 12.0, 13.0, 14.0],
        'post_return_90d':  [21.0, 22.0, 23.0, 24.0],
        'post_return_120d': [31.0, 32.0, 33.0, 34.0],
        'post_return_180d': [41.0, 42.0, 43.0, 44.0],
    })
    out = paper_return_terminal(df, H)
    # 20->30d=1.0 ; 69->60d=12.0 ; 76->90d=23.0 ; offen->180d=44.0
    assert out.tolist() == [1.0, 12.0, 23.0, 44.0]


def test_paper_return_terminal_nan_when_future():
    df = pd.DataFrame({
        'first_close_offset_days': [np.nan],     # offen -> 180d
        'post_return_180d': [np.nan],            # junge Position < 180d
    })
    out = paper_return_terminal(df, H)
    assert out.isna().all()


# ---------------------------------------------------------------------------
# Linienende
# ---------------------------------------------------------------------------

def test_trajectory_end_offset_states():
    t0 = pd.Timestamp('2020-01-01')
    assert trajectory_end_offset('closed_full', t0, t0 + pd.Timedelta(days=100)) == 100
    assert trajectory_end_offset('closed_full', t0, t0 + pd.Timedelta(days=250)) == 180
    assert trajectory_end_offset('delisted', t0, t0 + pd.Timedelta(days=45)) == 45
    assert trajectory_end_offset('held', t0, pd.NaT) == 180
    assert trajectory_end_offset('closed_partial', t0, t0 + pd.Timedelta(days=30)) == 180
    assert trajectory_end_offset('not_applicable', t0, pd.NaT) == 180
    assert trajectory_end_offset('closed_full', t0, pd.NaT) == 180     # kein exit_date


def test_trajectory_end_offset_custom_cap():
    t0 = pd.Timestamp('2020-01-01')
    assert trajectory_end_offset('held', t0, pd.NaT, cap=90) == 90
    assert trajectory_end_offset('closed_full', t0, t0 + pd.Timedelta(days=200), cap=90) == 90


# ---------------------------------------------------------------------------
# Kohaerenz: Attributierung (first_close) vs. Linienende (full_close)
# ---------------------------------------------------------------------------

def test_coherence_partial_at_40_full_at_100():
    """Teil-Close 40 / Voll-Close 100:

    Attributierung (first_close=40) -> nearest 30 -> zaehlt NUR fuer Horizont 30
    (D1/D2). Linienende (closed_full, exit @100) -> Linie laeuft bis 100
    (Spaghetti). Diese Divergenz ist gewollt.
    """
    first_close = 40.0
    assert qualifying_horizons(first_close, H) == [30]

    t0 = pd.Timestamp('2020-01-01')
    assert trajectory_end_offset('closed_full', t0,
                                 t0 + pd.Timedelta(days=100)) == 100

    # Vektorisiert: zaehlt fuer 30, nicht fuer 60+.
    df = pd.DataFrame({'first_close_offset_days': [first_close]})
    assert qualifies_for_horizon(df, 30, H).iloc[0]
    assert not qualifies_for_horizon(df, 60, H).iloc[0]


# ---------------------------------------------------------------------------
# Trajektorien-Aufbau aus synthetischem Cache
# ---------------------------------------------------------------------------

def _synthetic_cache():
    idx = pd.MultiIndex.from_tuples(
        [
            ('AAA', pd.Timestamp('2020-01-01')),
            ('AAA', pd.Timestamp('2020-01-15')),
            ('AAA', pd.Timestamp('2020-02-01')),
            ('AAA', pd.Timestamp('2020-08-01')),   # 213d > cap 180 -> ausserhalb
            ('BBB', pd.Timestamp('2020-01-01')),
            ('BBB', pd.Timestamp('2020-01-15')),
        ],
        names=['ticker', 'date'])
    return pd.DataFrame(
        {'close': [100.0, 110.0, 90.0, 200.0, 50.0, 55.0]}, index=idx)


def test_compute_member_trajectories_buy_and_sell_sign():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 't1', 'ticker': 'AAA', 'asset_name': 'Alpha',
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT,
         'post_return_180d': -10.0},
        {'trade_id': 't2', 'ticker': 'BBB', 'asset_name': 'Beta',
         'transaction_type': 'Sale', 'post_trade_baseline_price': 50.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT,
         'post_return_180d': -10.0},
    ])
    traj = compute_member_trajectories(df_member, cache, H)
    assert len(traj) == 2

    t1 = next(t for t in traj if t['trade_id'] == 't1')
    # held -> cap 180; Tag 213 (2020-08-01) liegt ausserhalb -> 3 Punkte.
    assert t1['days'] == [0, 14, 31]
    # Buy: (close/100 -1)*100: 0%, +10%, -10%
    assert t1['returns'] == pytest.approx([0.0, 10.0, -10.0])
    assert t1['direction'] == 'buy'

    t2 = next(t for t in traj if t['trade_id'] == 't2')
    # Sell: Vorzeichen invertiert. close 50->55 = +10% Kurs -> -10% (schlechtes
    # Timing fuer einen Verkauf vor Anstieg).
    assert t2['days'] == [0, 14]
    assert t2['returns'] == pytest.approx([0.0, -10.0])
    assert t2['direction'] == 'sell'


def test_compute_member_trajectories_line_ends_at_full_close():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 't3', 'ticker': 'AAA', 'asset_name': 'Alpha',
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'closed_full',
         'exit_date': pd.Timestamp('2020-01-20'),   # 19 Tage -> endet @ Tag 15
         'post_return_180d': 5.0},
    ])
    traj = compute_member_trajectories(df_member, cache, H)
    assert len(traj) == 1
    # end_offset 19 -> nur Punkte bis Tag 15 (2020-01-15), Tag 31 faellt raus.
    assert traj[0]['end_offset'] == 19
    assert traj[0]['days'] == [0, 14]


def test_compute_member_trajectories_skips_missing_data():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 'x1', 'ticker': 'ZZZ', 'asset_name': 'Z',  # nicht im Cache
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT, 'post_return_180d': 1.0},
        {'trade_id': 'x2', 'ticker': 'AAA', 'asset_name': 'A',  # keine Baseline
         'transaction_type': 'Purchase', 'post_trade_baseline_price': np.nan,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT, 'post_return_180d': 1.0},
    ])
    assert compute_member_trajectories(df_member, cache, H) == []
