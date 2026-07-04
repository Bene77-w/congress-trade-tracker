"""Tests fuer app/score_correlation.py (Modul 9.x — Score-Validierung)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.score_correlation import (
    _ex_officio_bonus,
    correlation_stats,
    per_signal_return_table,
    return_col,
    score_bucket_returns,
)


# --- return_col -----------------------------------------------------------

def test_return_col_formats_horizon():
    assert return_col(180) == 'post_return_180d'
    assert return_col('30') == 'post_return_30d'


# --- correlation_stats ----------------------------------------------------

def test_correlation_stats_perfect_monotone():
    scores = pd.Series([1, 2, 3, 4, 5])
    returns = pd.Series([10, 20, 30, 40, 50])
    res = correlation_stats(scores, returns)
    assert res['n'] == 5
    assert math.isclose(res['spearman'], 1.0, abs_tol=1e-9)
    assert math.isclose(res['pearson'], 1.0, abs_tol=1e-9)


def test_correlation_stats_monotone_nonlinear_spearman_one():
    # Monoton, aber nicht-linear: Spearman = 1, Pearson < 1.
    scores = pd.Series([1, 2, 3, 4, 5])
    returns = pd.Series([1, 4, 9, 16, 25])
    res = correlation_stats(scores, returns)
    assert math.isclose(res['spearman'], 1.0, abs_tol=1e-9)
    assert res['pearson'] < 1.0


def test_correlation_stats_drops_nan_pairs():
    scores = pd.Series([1, 2, 3, np.nan, 5])
    returns = pd.Series([10, np.nan, 30, 40, 50])
    res = correlation_stats(scores, returns)
    # Nur Paare ohne NaN: (1,10), (3,30), (5,50) -> n=3.
    assert res['n'] == 3


def test_correlation_stats_too_few_points_is_nan():
    res = correlation_stats(pd.Series([1.0]), pd.Series([2.0]))
    assert res['n'] == 1
    assert math.isnan(res['spearman'])
    assert math.isnan(res['pearson'])


# --- score_bucket_returns -------------------------------------------------

def test_score_bucket_returns_zero_is_own_bucket_then_5steps():
    # Score 0 muss ein EIGENES Bucket sein (nicht mit 1–5 vermischt). Danach
    # feste 5er-Schritte bis zum Maximum; leere Zwischen-Buckets bleiben als
    # Balken erhalten. Max=13 -> kmax=3 -> Buckets 0 / 1–5 / 6–10 / 11–15.
    df = pd.DataFrame({
        'trade_score': [0, 0, 0, 5, 3, 10, 13],
        'post_return_180d': [1, -2, 3, 5, 4, 8, 12],
    })
    out = score_bucket_returns(df, 'trade_score', 'post_return_180d')
    assert list(out['bucket']) == ['0', '1–5', '6–10', '11–15']
    assert out['n'].sum() == 7
    # Bucket 0: nur die drei Nuller-Trades.
    z = out.iloc[0]
    assert z['n'] == 3
    assert math.isclose(z['mean_return'], (1 - 2 + 3) / 3)
    # Bucket 1–5 enthaelt Score 5 und 3, aber NICHT die Nuller.
    b15 = out.iloc[1]
    assert b15['n'] == 2
    assert b15['score_min'] == 3 and b15['score_max'] == 5
    assert set(['bucket', 'n', 'mean_return', 'median_return',
                'score_min', 'score_max']).issubset(out.columns)


def test_score_bucket_returns_step_boundaries():
    # 5 gehoert zu 1–5, 6 zu 6–10 (Intervall ((k-1)*5, k*5]).
    df = pd.DataFrame({
        'trade_score': [5, 6],
        'post_return_180d': [1, 2],
    })
    out = score_bucket_returns(df, 'trade_score', 'post_return_180d')
    by = dict(zip(out['bucket'], out['n']))
    assert by['1–5'] == 1
    assert by['6–10'] == 1


def test_score_bucket_returns_all_zero():
    df = pd.DataFrame({
        'trade_score': [0, 0, 0, 0],
        'post_return_180d': [1, 2, 3, 4],
    })
    out = score_bucket_returns(df, 'trade_score', 'post_return_180d')
    assert list(out['bucket']) == ['0']
    assert out.iloc[0]['n'] == 4
    assert math.isclose(out.iloc[0]['mean_return'], 2.5)


def test_score_bucket_returns_empty_input():
    df = pd.DataFrame({'trade_score': [np.nan], 'post_return_180d': [np.nan]})
    out = score_bucket_returns(df, 'trade_score', 'post_return_180d')
    assert out.empty


# --- _ex_officio_bonus ----------------------------------------------------

def test_ex_officio_bonus_parsing():
    assert _ex_officio_bonus('committee:10;swarm_buy:20;ex_officio:+15') == 15.0
    assert _ex_officio_bonus('committee:10;ex_officio:+0') == 0.0
    assert _ex_officio_bonus('committee:10') == 0.0
    assert _ex_officio_bonus(None) == 0.0
    assert _ex_officio_bonus(float('nan')) == 0.0


# --- per_signal_return_table ----------------------------------------------

def test_per_signal_return_table_means_and_delta():
    df = pd.DataFrame({
        'signals_triggered': ['committee;swarm_buy', 'committee',
                              'swarm_buy', ''],
        'post_return_180d': [10.0, 8.0, 6.0, 2.0],
        'trade_score_breakdown': ['committee:10;swarm_buy:20;ex_officio:+15',
                                  'committee:10;ex_officio:+0',
                                  'swarm_buy:20;ex_officio:+0',
                                  'ex_officio:+0'],
    })
    out = per_signal_return_table(df, 'post_return_180d')
    by_sig = out.set_index('signal')

    # committee: gefeuert rows 0,1 -> mean 9; nicht rows 2,3 -> mean 4; delta 5.
    assert by_sig.loc['committee', 'n_fired'] == 2
    assert math.isclose(by_sig.loc['committee', 'mean_fired'], 9.0)
    assert math.isclose(by_sig.loc['committee', 'mean_not'], 4.0)
    assert math.isclose(by_sig.loc['committee', 'delta'], 5.0)

    # swarm_buy: gefeuert rows 0,2 -> mean 8; nicht rows 1,3 -> mean 5; delta 3.
    assert by_sig.loc['swarm_buy', 'n_fired'] == 2
    assert math.isclose(by_sig.loc['swarm_buy', 'mean_fired'], 8.0)
    assert math.isclose(by_sig.loc['swarm_buy', 'delta'], 3.0)


def test_per_signal_return_table_pseudo_ex_officio():
    df = pd.DataFrame({
        'signals_triggered': ['committee', 'committee', 'committee',
                              'committee'],
        'post_return_180d': [10.0, 8.0, 6.0, 2.0],
        'trade_score_breakdown': ['committee:10;ex_officio:+15',
                                  'committee:10;ex_officio:+0',
                                  'committee:10;ex_officio:+0',
                                  'committee:10;ex_officio:+0'],
    })
    out = per_signal_return_table(df, 'post_return_180d')
    sigs = set(out['signal'])
    # committee_ex_officio steht NICHT in signals_triggered, muss aber aus dem
    # Breakdown abgeleitet sein.
    assert 'committee_ex_officio' in sigs
    row = out.set_index('signal').loc['committee_ex_officio']
    assert row['n_fired'] == 1
    assert math.isclose(row['mean_fired'], 10.0)


def test_per_signal_return_table_negative_signal():
    # Signal feuert nur auf niedrigen Returns -> negatives delta + negative Korrel.
    df = pd.DataFrame({
        'signals_triggered': ['neg', 'neg', '', ''],
        'post_return_180d': [1.0, 2.0, 10.0, 12.0],
        'trade_score_breakdown': ['', '', '', ''],
    })
    out = per_signal_return_table(df, 'post_return_180d')
    row = out.set_index('signal').loc['neg']
    assert row['delta'] < 0
    assert row['spearman'] < 0
    assert row['pearson'] < 0


def test_per_signal_return_table_empty_input():
    out = per_signal_return_table(pd.DataFrame(), 'post_return_180d')
    assert out.empty
    assert list(out.columns) == ['signal', 'n_fired', 'mean_fired', 'mean_not',
                                 'delta', 'spearman', 'pearson']
