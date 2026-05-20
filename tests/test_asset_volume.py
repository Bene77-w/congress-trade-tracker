"""Tests fuer scoring/asset_volume.py (Modul 8, Phase 8.2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoring.asset_volume import compute_asset_tradingvolume


def _df(rows):
    """Helper: DataFrame mit Standard-Index aus Tuple-Liste."""
    return pd.DataFrame(rows, columns=[
        'trade_volume_usd', 'issuer_group_key', 'transaction_date'
    ])


def test_single_trade_window_returns_own_volume():
    df = _df([(8000, 'CIK:0000320193', '2025-03-10')])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.tolist() == [8000]


def test_same_day_two_trades_share_volume():
    df = _df([
        (8000,  'CIK:0000320193', '2025-03-10'),
        (32500, 'CIK:0000320193', '2025-03-10'),
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    # rolling('90D', closed='both') summiert beide Tag-0-Trades je Zeile.
    assert result.tolist() == [40500, 40500]


def test_cross_class_via_issuer_group_key():
    # GOOG + GOOGL haben identischen issuer_group_key CIK:1652044.
    df = _df([
        (8000,  'CIK:1652044', '2025-03-10'),  # GOOGL
        (15000, 'CIK:1652044', '2025-03-15'),  # GOOG
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.tolist() == [8000, 23000]


def test_window_edge_inside():
    # T2 ist genau 90 Tage nach T1 -> closed='both' -> T1 enthalten.
    df = _df([
        (1000, 'CIK:X', '2025-01-01'),
        (2000, 'CIK:X', '2025-04-01'),  # 1.1. + 90d = 1.4.
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.tolist() == [1000, 3000]


def test_window_edge_outside():
    # T2 ist 91 Tage nach T1 -> T1 nicht mehr im Fenster.
    df = _df([
        (1000, 'CIK:X', '2025-01-01'),
        (2000, 'CIK:X', '2025-04-02'),
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.tolist() == [1000, 2000]


def test_skip_bucket_returns_zero():
    df = _df([
        (8000,    'CIK:0000320193', '2025-03-10'),
        (250_000, 'SKIP:OPTION',    '2025-03-10'),
        (100_000, 'SKIP:BOND',      '2025-03-10'),
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.tolist() == [8000, 0, 0]


def test_different_groups_isolated():
    df = _df([
        (1000, 'CIK:A', '2025-03-10'),
        (2000, 'CIK:B', '2025-03-10'),
        (3000, 'CIK:A', '2025-03-15'),
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    # Gruppe A: 1000 -> 1000; A+3000 -> 4000. Gruppe B: 2000.
    assert result.tolist() == [1000, 2000, 4000]


def test_empty_df():
    df = _df([])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.empty
    assert result.dtype == 'int64'


def test_nan_date_returns_zero():
    df = _df([
        (8000, 'CIK:X', None),
        (5000, 'CIK:X', '2025-03-10'),
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.iloc[0] == 0
    assert result.iloc[1] == 5000


def test_empty_issuer_group_key_returns_zero():
    df = _df([
        (8000, '',  '2025-03-10'),
        (5000, None, '2025-03-10'),
    ])
    result = compute_asset_tradingvolume(df, window_days_z=90)
    assert result.tolist() == [0, 0]
