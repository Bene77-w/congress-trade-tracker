"""Kohaerenz-Tests D3b/D4-Trajektorie <-> post_return-Spalte (Phase A).

Pinnt die im Forensik-Befund 2 dokumentierte bewusste Divergenz:
  - Trajektorien-Linie (D3b/D4) endet am VOLLEN Close (``exit_date`` bei
    ``closed_full``),
  - ``post_return_{h}d`` misst den Kurs h Tage NACH der Transaktion — auch nach
    Voll-Close.
Beide Groessen muessen ueber die gemeinsame Tage-Achse vorzeichen-kohaerent
sein (gleiches Richtungs-Sign), und der Sell-Flip muss greifen.
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

from app.member_metrics import compute_member_trajectories  # noqa: E402

H = [30, 60, 90, 120, 180]


def _price_cache():
    """Mini-Cache: AAA steigt monoton (100->130), BBB faellt (100->70).

    Tage relativ zum 2020-01-01 in 30-Tage-Schritten, damit Horizont-Snapshots
    direkt ablesbar sind.
    """
    rows = []
    base = pd.Timestamp('2020-01-01')
    aaa = {0: 100.0, 30: 110.0, 60: 115.0, 90: 120.0, 120: 125.0, 180: 130.0}
    bbb = {0: 100.0, 30: 95.0, 60: 90.0, 90: 85.0, 120: 80.0, 180: 70.0}
    for off, px in aaa.items():
        rows.append(('AAA', base + pd.Timedelta(days=off), px))
    for off, px in bbb.items():
        rows.append(('BBB', base + pd.Timedelta(days=off), px))
    idx = pd.MultiIndex.from_tuples([(t, d) for t, d, _ in rows],
                                    names=['ticker', 'date'])
    return pd.DataFrame({'close': [p for _, _, p in rows]}, index=idx)


def _post_returns(baseline, prices_by_off, sign):
    """post_return_{h}d-Spalten (richtungsbereinigt) aus Preis-Snapshots."""
    return {f'post_return_{h}d': (prices_by_off[h] / baseline - 1.0) * 100.0
            * sign for h in H}


def test_buy_trajectory_sign_matches_post_return_and_ends_at_close():
    """Buy AAA, Voll-Close @ Tag 90. Linie endet bei 90; Vorzeichen der
    Trajektorienpunkte = Vorzeichen der post_return-Spalten (beide positiv)."""
    base = pd.Timestamp('2020-01-01')
    pr = _post_returns(100.0,
                       {0: 100.0, 30: 110.0, 60: 115.0, 90: 120.0,
                        120: 125.0, 180: 130.0}, sign=1.0)
    row = {'trade_id': 'buy1', 'ticker': 'AAA', 'asset_name': 'Alpha',
           'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
           'transaction_date': base, 'exit_status': 'closed_full',
           'exit_date': base + pd.Timedelta(days=90), **pr}
    traj = compute_member_trajectories(pd.DataFrame([row]), _price_cache(), H,
                                       sample_days=1)
    assert len(traj) == 1
    t = traj[0]
    assert t['direction'] == 'buy'
    # (b) Linienende = Voll-Close-Tag 90 (Tag 120/180 fallen raus).
    assert t['end_offset'] == 90
    assert max(t['days']) == 90
    # (a) Vorzeichen-Kohaerenz: Trajektorienpunkt bei Tag 30 == post_return_30d
    day30 = t['returns'][t['days'].index(30)]
    assert day30 == pytest.approx(pr['post_return_30d'])     # +10%
    assert day30 > 0 and pr['post_return_30d'] > 0


def test_sell_flip_coherent_with_post_return():
    """Sell BBB (Kurs faellt nach Verkauf -> gutes Timing -> post_return positiv).
    Trajektorie ist sign-invertiert und stimmt mit post_return ueberein."""
    base = pd.Timestamp('2020-01-01')
    pr = _post_returns(100.0,
                       {0: 100.0, 30: 95.0, 60: 90.0, 90: 85.0,
                        120: 80.0, 180: 70.0}, sign=-1.0)   # sell -> invert
    row = {'trade_id': 'sell1', 'ticker': 'BBB', 'asset_name': 'Beta',
           'transaction_type': 'Sale', 'post_trade_baseline_price': 100.0,
           'transaction_date': base, 'exit_status': 'held', 'exit_date': pd.NaT,
           **pr}
    traj = compute_member_trajectories(pd.DataFrame([row]), _price_cache(), H,
                                       sample_days=1)
    t = traj[0]
    assert t['direction'] == 'sell'
    # (c) Sell-Flip: Kurs faellt -> richtungsbereinigt POSITIV (gutes Timing).
    day30 = t['returns'][t['days'].index(30)]
    assert day30 == pytest.approx(pr['post_return_30d'])     # +5%
    assert day30 > 0
    # held -> Linie laeuft bis cap 180.
    assert t['end_offset'] == 180
    assert max(t['days']) == 180


def test_post_return_below_minus_100_after_close_is_real():
    """Forensik-Befund 2: post_return_180d kann < −100 % sein (Sell vor Anstieg).
    Die Trajektorie endet am Close, post_return laeuft weiter — die Divergenz
    ist gewollt und darf nicht 'korrigiert' werden."""
    base = pd.Timestamp('2020-01-01')
    # Sell AAA @100, Kurs steigt auf 130 (Tag 180) -> sign -1 -> −30 %.
    pr = _post_returns(100.0,
                       {0: 100.0, 30: 110.0, 60: 115.0, 90: 120.0,
                        120: 125.0, 180: 130.0}, sign=-1.0)
    assert pr['post_return_180d'] == pytest.approx(-30.0)
    row = {'trade_id': 's2', 'ticker': 'AAA', 'asset_name': 'A',
           'transaction_type': 'Sale', 'post_trade_baseline_price': 100.0,
           'transaction_date': base, 'exit_status': 'closed_full',
           'exit_date': base + pd.Timedelta(days=30), **pr}
    traj = compute_member_trajectories(pd.DataFrame([row]), _price_cache(), H,
                                       sample_days=1)
    t = traj[0]
    # Linie endet Tag 30 (Voll-Close), post_return_180d (−30%) liegt DAHINTER.
    assert t['end_offset'] == 30
    assert max(t['days']) == 30
    assert not np.isnan(t['paper_return_180'])
    assert t['paper_return_180'] == pytest.approx(-30.0)
