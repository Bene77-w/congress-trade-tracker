"""Tests fuer die vier Branch-1 Per-Trade-Signale."""

from datetime import date

import pandas as pd
import pytest

from signals.committee_signal import CommitteeSignal
from signals.committee_ex_officio_signal import CommitteeExOfficioSignal
from signals.floor_action_signal import FloorActionSignal
from signals.extreme_report_signal import ExtremeReportSignal


# ---------- Test-Fixtures ---------------------------------------------------

def make_row(**overrides) -> pd.Series:
    """Erzeugt eine repraesentative Trade-Row mit Defaults."""
    base = {
        'trade_id': 'abc123',
        'name': 'Doe, John',
        'bio_guide_id': 'D000001',
        'chamber': 'House',
        'ticker': 'AAPL',
        'ticker_base': 'AAPL',
        'asset_name': 'Apple Inc',
        'asset_name_figi': 'APPLE INC',
        'transaction_date': date(2026, 3, 1),
        'disclosure_date': date(2026, 3, 10),
        'transaction_type': 'Purchase',
        'amount': '$1,001-$15,000',
        'Member_ExOfficio': '',
        'Member_Committees_Full': 'Armed Services|Chairman',
        'Relevance_Score': 0,
        'Vote_Timing_Hit_Count': 0,
        'Relevant_Sectors': '',
    }
    base.update(overrides)
    return pd.Series(base)


# ---------- CommitteeSignal -------------------------------------------------

class TestCommitteeSignal:
    def test_triggers_above_threshold(self):
        signal = CommitteeSignal(threshold=33)
        event = signal.evaluate_row(make_row(Relevance_Score=50))
        assert event is not None
        assert event['signal_type'] == 'committee'
        assert event['relevance_score'] == 50

    def test_triggers_at_threshold(self):
        signal = CommitteeSignal(threshold=33)
        event = signal.evaluate_row(make_row(Relevance_Score=33))
        assert event is not None

    def test_does_not_trigger_below_threshold(self):
        signal = CommitteeSignal(threshold=33)
        assert signal.evaluate_row(make_row(Relevance_Score=32)) is None

    def test_does_not_trigger_on_nan_score(self):
        signal = CommitteeSignal(threshold=33)
        assert signal.evaluate_row(make_row(Relevance_Score=float('nan'))) is None

    def test_deterministic_signal_event_id(self):
        signal = CommitteeSignal(threshold=33)
        row = make_row(Relevance_Score=50)
        a = signal.evaluate_row(row)['signal_event_id']
        b = signal.evaluate_row(row)['signal_event_id']
        assert a == b
        assert len(a) == 16

    def test_uses_settings_default(self):
        from config import settings
        signal = CommitteeSignal()
        assert signal.threshold == settings.COMMITTEE_THRESHOLD

    def test_direction_buy(self):
        signal = CommitteeSignal(threshold=33)
        event = signal.evaluate_row(make_row(
            Relevance_Score=50, transaction_type='Purchase'
        ))
        assert event['direction'] == 'buy'

    def test_direction_sell(self):
        signal = CommitteeSignal(threshold=33)
        event = signal.evaluate_row(make_row(
            Relevance_Score=50, transaction_type='Sale (Full)'
        ))
        assert event['direction'] == 'sell'


# ---------- CommitteeExOfficioSignal ----------------------------------------

class TestCommitteeExOfficioSignal:
    def test_triggers_when_committee_and_ex_officio(self):
        signal = CommitteeExOfficioSignal(threshold=33)
        event = signal.evaluate_row(make_row(
            Relevance_Score=50, Member_ExOfficio='Chairman'
        ))
        assert event is not None
        assert event['signal_type'] == 'committee_ex_officio'
        assert event['is_ex_officio'] is True

    def test_does_not_trigger_without_ex_officio(self):
        signal = CommitteeExOfficioSignal(threshold=33)
        assert signal.evaluate_row(
            make_row(Relevance_Score=50, Member_ExOfficio='')
        ) is None

    def test_does_not_trigger_without_committee(self):
        signal = CommitteeExOfficioSignal(threshold=33)
        assert signal.evaluate_row(
            make_row(Relevance_Score=20, Member_ExOfficio='Chairman')
        ) is None

    def test_distinct_id_from_committee_signal(self):
        cs = CommitteeSignal(threshold=33)
        es = CommitteeExOfficioSignal(threshold=33)
        row = make_row(Relevance_Score=50, Member_ExOfficio='Chairman')
        assert cs.evaluate_row(row)['signal_event_id'] != \
               es.evaluate_row(row)['signal_event_id']


# ---------- FloorActionSignal -----------------------------------------------

class TestFloorActionSignal:
    def test_triggers_at_threshold(self):
        signal = FloorActionSignal(threshold=1)
        event = signal.evaluate_row(make_row(Vote_Timing_Hit_Count=1))
        assert event is not None
        assert event['signal_type'] == 'floor_action'
        assert event['vote_timing_hit_count'] == 1

    def test_does_not_trigger_at_zero(self):
        signal = FloorActionSignal(threshold=1)
        assert signal.evaluate_row(make_row(Vote_Timing_Hit_Count=0)) is None

    def test_triggers_with_high_count(self):
        signal = FloorActionSignal(threshold=1)
        event = signal.evaluate_row(make_row(Vote_Timing_Hit_Count=5))
        assert event is not None

    def test_deterministic_signal_event_id(self):
        signal = FloorActionSignal(threshold=1)
        row = make_row(Vote_Timing_Hit_Count=3)
        a = signal.evaluate_row(row)['signal_event_id']
        b = signal.evaluate_row(row)['signal_event_id']
        assert a == b


# ---------- ExtremeReportSignal ---------------------------------------------

class TestExtremeReportSignal:
    def test_triggers_when_delay_exceeds_threshold(self):
        signal = ExtremeReportSignal(threshold_days=60)
        event = signal.evaluate_row(make_row(
            transaction_date=date(2026, 1, 1),
            disclosure_date=date(2026, 3, 15),  # 73 Tage spaeter
        ))
        assert event is not None
        assert event['signal_type'] == 'extreme_report'

    def test_does_not_trigger_at_threshold(self):
        # > threshold (strikt), nicht >=
        signal = ExtremeReportSignal(threshold_days=60)
        event = signal.evaluate_row(make_row(
            transaction_date=date(2026, 1, 1),
            disclosure_date=date(2026, 3, 2),  # genau 60 Tage
        ))
        assert event is None

    def test_does_not_trigger_within_normal_window(self):
        signal = ExtremeReportSignal(threshold_days=60)
        event = signal.evaluate_row(make_row(
            transaction_date=date(2026, 3, 1),
            disclosure_date=date(2026, 3, 10),  # 9 Tage
        ))
        assert event is None

    def test_triggering_date_is_disclosure_date(self):
        signal = ExtremeReportSignal(threshold_days=60)
        event = signal.evaluate_row(make_row(
            transaction_date=date(2026, 1, 1),
            disclosure_date=date(2026, 4, 15),
        ))
        assert event['triggering_date'] == date(2026, 4, 15)

    def test_handles_missing_dates(self):
        signal = ExtremeReportSignal(threshold_days=60)
        assert signal.evaluate_row(make_row(transaction_date=None)) is None
        assert signal.evaluate_row(make_row(disclosure_date=None)) is None


# ---------- Integration: alle 4 Signale auf einem Mini-DataFrame ------------

class TestBranch1Integration:
    def test_combined_signal_count_and_list(self):
        """Ein Trade triggert mehrere Signale gleichzeitig — der Aggregator
        muss alle einsammeln und korrekt zaehlen."""
        signals = [
            CommitteeSignal(threshold=33),
            CommitteeExOfficioSignal(threshold=33),
            FloorActionSignal(threshold=1),
            ExtremeReportSignal(threshold_days=60),
        ]
        # Volltreffer: Committee + ExOfficio + FloorAction + ExtremeReport
        row = make_row(
            Relevance_Score=75,
            Member_ExOfficio='Chairman',
            Vote_Timing_Hit_Count=2,
            transaction_date=date(2026, 1, 1),
            disclosure_date=date(2026, 4, 1),  # 90 Tage Verzug
        )
        triggered = [s.SIGNAL_TYPE for s in signals if s.evaluate_row(row)]
        assert triggered == [
            'committee', 'committee_ex_officio', 'floor_action', 'extreme_report'
        ]

    def test_no_signals_on_clean_row(self):
        signals = [
            CommitteeSignal(threshold=33),
            CommitteeExOfficioSignal(threshold=33),
            FloorActionSignal(threshold=1),
            ExtremeReportSignal(threshold_days=60),
        ]
        row = make_row()  # alle Defaults sauber
        assert [s.SIGNAL_TYPE for s in signals if s.evaluate_row(row)] == []
