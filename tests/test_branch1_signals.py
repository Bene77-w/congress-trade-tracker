"""Tests fuer die Branch-1 Per-Trade-Signale."""

import json
from datetime import date

import pandas as pd

from signals.committee_signal import CommitteeSignal
from signals.committee_full_signal import CommitteeFullSignal
from signals.committee_ex_officio_signal import CommitteeExOfficioSignal
from signals.floor_action_signal import FloorActionSignal
from signals.extreme_report_signal import ExtremeReportSignal


# ---------- Test-Fixtures ---------------------------------------------------

def _sectors(*matches):
    """Hilfs-Funktion: baut JSON-String fuer Relevant_Sectors.

    matches: Tupel (sector, industry, role, match_type, [committee, subcommittee])
    """
    out = []
    for m in matches:
        sector, industry, role, mtype = m[0], m[1], m[2], m[3]
        committee = m[4] if len(m) > 4 else 'Ways And Means'
        subcommittee = m[5] if len(m) > 5 else 'Health'
        out.append({
            'sector': sector,
            'industry': industry,
            'role': role,
            'rank': 1,
            'confidence': 'Hoch',
            'committee': committee,
            'subcommittee': subcommittee,
            'match_type': mtype,
        })
    return json.dumps(out)


def make_row(**overrides) -> pd.Series:
    """Erzeugt eine repraesentative Trade-Row mit Defaults (keine Treffer)."""
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
        'Vote_Timing_Hit_Count': 0,
        'Relevant_Sectors': '',
    }
    base.update(overrides)
    return pd.Series(base)


# ---------- CommitteeSignal -------------------------------------------------

class TestCommitteeSignal:
    def test_triggers_when_full_match(self):
        signal = CommitteeSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(
                ('Healthcare', 'Drug Manufacturers', '', 'full')
            )
        ))
        assert event is not None
        assert event['signal_type'] == 'committee'
        assert event['match_count'] == 1

    def test_triggers_when_sector_only_match(self):
        signal = CommitteeSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(
                ('Healthcare', 'Medical Devices', '', 'sector')
            )
        ))
        assert event is not None
        assert event['match_count'] == 1

    def test_does_not_trigger_on_empty_list(self):
        signal = CommitteeSignal()
        assert signal.evaluate_row(make_row(Relevant_Sectors='[]')) is None

    def test_does_not_trigger_on_empty_string(self):
        signal = CommitteeSignal()
        assert signal.evaluate_row(make_row(Relevant_Sectors='')) is None

    def test_does_not_trigger_on_malformed_json(self):
        signal = CommitteeSignal()
        assert signal.evaluate_row(make_row(Relevant_Sectors='not json')) is None

    def test_deterministic_signal_event_id(self):
        signal = CommitteeSignal()
        row = make_row(Relevant_Sectors=_sectors(
            ('Healthcare', 'Drug Manufacturers', '', 'full')
        ))
        a = signal.evaluate_row(row)['signal_event_id']
        b = signal.evaluate_row(row)['signal_event_id']
        assert a == b
        assert len(a) == 16

    def test_direction_buy(self):
        signal = CommitteeSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(('Healthcare', 'Devices', '', 'full')),
            transaction_type='Purchase',
        ))
        assert event['direction'] == 'buy'

    def test_direction_sell(self):
        signal = CommitteeSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(('Healthcare', 'Devices', '', 'full')),
            transaction_type='Sale (Full)',
        ))
        assert event['direction'] == 'sell'


# ---------- CommitteeFullSignal --------------------------------------------

class TestCommitteeFullSignal:
    def test_triggers_on_full_match(self):
        signal = CommitteeFullSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(
                ('Healthcare', 'Drug Manufacturers', '', 'full')
            )
        ))
        assert event is not None
        assert event['signal_type'] == 'committee_full'

    def test_does_not_trigger_on_sector_only(self):
        signal = CommitteeFullSignal()
        assert signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(
                ('Healthcare', 'Other', '', 'sector')
            )
        )) is None

    def test_triggers_on_mixed_with_at_least_one_full(self):
        signal = CommitteeFullSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(
                ('Healthcare', 'Other', '', 'sector'),
                ('Tech', 'Software', '', 'full'),
            )
        ))
        assert event is not None

    def test_does_not_trigger_on_empty(self):
        signal = CommitteeFullSignal()
        assert signal.evaluate_row(make_row(Relevant_Sectors='[]')) is None

    def test_distinct_id_from_committee_signal(self):
        cs = CommitteeSignal()
        fs = CommitteeFullSignal()
        row = make_row(Relevant_Sectors=_sectors(
            ('Healthcare', 'Drug Manufacturers', '', 'full')
        ))
        assert cs.evaluate_row(row)['signal_event_id'] != \
               fs.evaluate_row(row)['signal_event_id']


# ---------- CommitteeExOfficioSignal ----------------------------------------

class TestCommitteeExOfficioSignal:
    def test_triggers_when_committee_and_ex_officio(self):
        signal = CommitteeExOfficioSignal()
        event = signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(('Healthcare', 'Devices', '', 'full')),
            Member_ExOfficio='Chairman',
        ))
        assert event is not None
        assert event['signal_type'] == 'committee_ex_officio'
        assert event['is_ex_officio'] is True

    def test_does_not_trigger_without_ex_officio(self):
        signal = CommitteeExOfficioSignal()
        assert signal.evaluate_row(make_row(
            Relevant_Sectors=_sectors(('Healthcare', 'Devices', '', 'full')),
            Member_ExOfficio='',
        )) is None

    def test_does_not_trigger_without_committee(self):
        signal = CommitteeExOfficioSignal()
        assert signal.evaluate_row(make_row(
            Relevant_Sectors='[]', Member_ExOfficio='Chairman',
        )) is None

    def test_distinct_id_from_committee_signal(self):
        cs = CommitteeSignal()
        es = CommitteeExOfficioSignal()
        row = make_row(
            Relevant_Sectors=_sectors(('Healthcare', 'Devices', '', 'full')),
            Member_ExOfficio='Chairman',
        )
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


# ---------- Integration: alle Signale auf einem Mini-DataFrame --------------

class TestBranch1Integration:
    def test_combined_signal_count_and_list(self):
        """Voll-Treffer + Ex-Officio + Vote-Timing + Late-Disclosure feuert
        committee, committee_full, committee_ex_officio, floor_action,
        extreme_report."""
        signals = [
            CommitteeSignal(),
            CommitteeFullSignal(),
            CommitteeExOfficioSignal(),
            FloorActionSignal(threshold=1),
            ExtremeReportSignal(threshold_days=60),
        ]
        row = make_row(
            Relevant_Sectors=_sectors(
                ('Healthcare', 'Drug Manufacturers', 'Chairman', 'full')
            ),
            Member_ExOfficio='Chairman',
            Vote_Timing_Hit_Count=2,
            transaction_date=date(2026, 1, 1),
            disclosure_date=date(2026, 4, 1),
        )
        triggered = [s.SIGNAL_TYPE for s in signals if s.evaluate_row(row)]
        assert triggered == [
            'committee', 'committee_full', 'committee_ex_officio',
            'floor_action', 'extreme_report',
        ]

    def test_sector_only_match_skips_committee_full(self):
        """Halb-Treffer triggert committee, aber NICHT committee_full."""
        signals = [
            CommitteeSignal(),
            CommitteeFullSignal(),
        ]
        row = make_row(Relevant_Sectors=_sectors(
            ('Healthcare', 'Other', '', 'sector')
        ))
        triggered = [s.SIGNAL_TYPE for s in signals if s.evaluate_row(row)]
        assert triggered == ['committee']

    def test_no_signals_on_clean_row(self):
        signals = [
            CommitteeSignal(),
            CommitteeFullSignal(),
            CommitteeExOfficioSignal(),
            FloorActionSignal(threshold=1),
            ExtremeReportSignal(threshold_days=60),
        ]
        row = make_row()
        assert [s.SIGNAL_TYPE for s in signals if s.evaluate_row(row)] == []
