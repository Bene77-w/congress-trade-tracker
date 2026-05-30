"""Tests fuer signals/trade_volume_signal.py (Tier-basiertes Volume-Signal)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signals.trade_volume_signal import (  # noqa: E402
    TradeVolumeSignal,
    _normalize_tiers,
    resolve_tier,
)


# Default-Tiers aus dem Plan (Vorschlag C, konservativ)
DEFAULT_TIERS = [
    (50_000,        2),
    (100_000,       4),
    (250_000,       7),
    (500_000,      10),
    (1_000_000,    13),
    (float('inf'), 16),
]
DEFAULT_FLOOR = 15_000


# ---------------------------------------------------------------------------
# resolve_tier — pro Stufe ein Sample + Edge-Cases
# ---------------------------------------------------------------------------
class TestResolveTier:
    def test_below_trigger_floor(self):
        assert resolve_tier(15_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (0, 0)

    def test_zero(self):
        assert resolve_tier(0, DEFAULT_TIERS, DEFAULT_FLOOR) == (0, 0)

    def test_tier_1_low(self):
        # Knapp ueber Floor -> Tier 1
        assert resolve_tier(15_001, DEFAULT_TIERS, DEFAULT_FLOOR) == (1, 2)

    def test_tier_1_high(self):
        # Genau Obergrenze von Tier 1
        assert resolve_tier(50_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (1, 2)

    def test_tier_2(self):
        assert resolve_tier(100_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (2, 4)

    def test_tier_3(self):
        assert resolve_tier(250_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (3, 7)

    def test_tier_4(self):
        assert resolve_tier(500_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (4, 10)

    def test_tier_5(self):
        assert resolve_tier(1_000_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (5, 13)

    def test_tier_6_catchall(self):
        # 50M -> letzter Tier (Catch-all)
        assert resolve_tier(50_000_000, DEFAULT_TIERS, DEFAULT_FLOOR) == (6, 16)

    def test_nan(self):
        assert resolve_tier(float('nan'), DEFAULT_TIERS, DEFAULT_FLOOR) == (0, 0)

    def test_none(self):
        assert resolve_tier(None, DEFAULT_TIERS, DEFAULT_FLOOR) == (0, 0)


# ---------------------------------------------------------------------------
# _normalize_tiers — None / NaN als Catch-all
# ---------------------------------------------------------------------------
class TestNormalizeTiers:
    def test_pass_through(self):
        tiers = [(50_000, 2), (100_000, 4)]
        assert _normalize_tiers(tiers) == [(50_000.0, 2), (100_000.0, 4)]

    def test_none_upper_becomes_inf(self):
        tiers = [(50_000, 2), (None, 16)]
        out = _normalize_tiers(tiers)
        assert out[-1][0] == float('inf')
        assert out[-1][1] == 16

    def test_unsorted_input_gets_sorted(self):
        tiers = [(100_000, 4), (50_000, 2)]
        out = _normalize_tiers(tiers)
        assert out == [(50_000.0, 2), (100_000.0, 4)]

    def test_empty(self):
        assert _normalize_tiers([]) == []
        assert _normalize_tiers(None) == []

    def test_invalid_entries_dropped(self):
        tiers = [(50_000, 2), 'broken', (100_000, 'X'), (250_000, 7)]
        out = _normalize_tiers(tiers)
        assert out == [(50_000.0, 2), (250_000.0, 7)]


# ---------------------------------------------------------------------------
# TradeVolumeSignal.evaluate_row
# ---------------------------------------------------------------------------
def _trade_row(amount_max: int, **extra) -> pd.Series:
    base = {
        'trade_id': 'T123',
        'trade_amount_max_usd': amount_max,
        'transaction_date': '2025-01-15',
        'disclosure_date': '2025-02-01',
        'transaction_type': 'Purchase',
        'amount': '$15,001 - $50,000',
        'bio_guide_id': 'A000123',
        'name': 'Jane Doe',
        'chamber': 'House',
        'ticker': 'AAPL',
        'asset_name': 'Apple Inc.',
        'asset_name_figi': 'Apple Inc.',
        'issuer_group_key': 'CIK:0000320193',
        'Member_ExOfficio': '',
        'Member_Committees_Full': '',
        'Vote_Timing_Hit_Count': 0,
        'Relevant_Sectors': '',
    }
    base.update(extra)
    return pd.Series(base)


class TestSignalEvaluate:
    def test_no_trigger_at_floor(self):
        sig = TradeVolumeSignal(tiers=DEFAULT_TIERS, trigger_floor=15_000)
        row = _trade_row(amount_max=15_000)
        assert sig.evaluate_row(row) is None

    def test_tier_1_triggers(self):
        sig = TradeVolumeSignal(tiers=DEFAULT_TIERS, trigger_floor=15_000)
        row = _trade_row(amount_max=50_000)
        event = sig.evaluate_row(row)
        assert event is not None
        assert event['signal_type'] == 'trade_volume'
        assert event['match_count'] == 1   # Tier-Index
        assert event['trade_id'] == 'T123'
        assert event['direction'] == 'buy'

    def test_top_tier(self):
        sig = TradeVolumeSignal(tiers=DEFAULT_TIERS, trigger_floor=15_000)
        row = _trade_row(amount_max=50_000_000)
        event = sig.evaluate_row(row)
        assert event is not None
        assert event['match_count'] == 6   # letzter Tier-Index

    def test_uses_settings_defaults_when_none_passed(self):
        # Ohne Args -> liest aus config.settings
        sig = TradeVolumeSignal()
        assert sig.tiers  # nicht leer
        assert sig.trigger_floor >= 0

    def test_amount_max_zero_no_trigger(self):
        sig = TradeVolumeSignal(tiers=DEFAULT_TIERS, trigger_floor=15_000)
        row = _trade_row(amount_max=0)
        assert sig.evaluate_row(row) is None
