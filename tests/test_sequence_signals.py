"""Tests fuer die Sequenz-Signale (Contrarian, Accumulative)."""

from datetime import date

import pandas as pd
import pytest

from signals.contrarian_signal import ContrarianSignal
from signals.accumulative_signal import AccumulativeSignal


def make_trade(td, tx_type='Purchase', trade_id=None, ticker='AAPL',
               ticker_base=None, member_id='D000001') -> dict:
    return {
        'trade_id': trade_id or f'trd_{td.isoformat()}_{tx_type}',
        'name': 'Doe, John',
        'bio_guide_id': member_id,
        'chamber': 'House',
        'ticker': ticker,
        'ticker_base': ticker_base if ticker_base is not None else ticker,
        'asset_name': 'Apple Inc',
        'asset_name_figi': 'APPLE INC',
        'transaction_date': td,
        'disclosure_date': td,
        'transaction_type': tx_type,
        'amount': '$1,001-$15,000',
        'Member_ExOfficio': '',
        'Member_Committees_Full': '',
        'Vote_Timing_Hit_Count': 0,
        'Relevant_Sectors': '',
    }


def make_group(trades) -> pd.DataFrame:
    return pd.DataFrame(trades)


# ---------- ContrarianSignal ------------------------------------------------

class TestContrarianSignal:
    def test_triggers_on_reversal_within_window(self):
        sig = ContrarianSignal(window_days=30)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 10), 'Sale (Full)', trade_id='t2'),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 2
        assert events[0]['signal_event_id'] == events[1]['signal_event_id']
        assert {e['trade_id'] for e in events} == {'t1', 't2'}
        assert events[0]['triggering_date'] == date(2026, 3, 10)

    def test_does_not_trigger_outside_window(self):
        sig = ContrarianSignal(window_days=30)
        group = make_group([
            make_trade(date(2026, 1, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 10), 'Sale (Full)', trade_id='t2'),
        ])
        assert sig.evaluate_group(group) == []

    def test_does_not_trigger_without_reversal(self):
        sig = ContrarianSignal(window_days=30)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 10), 'Purchase', trade_id='t2'),
        ])
        assert sig.evaluate_group(group) == []

    def test_multiple_reversals_emit_multiple_events(self):
        sig = ContrarianSignal(window_days=30)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 10), 'Sale (Full)', trade_id='t2'),
            make_trade(date(2026, 3, 20), 'Purchase', trade_id='t3'),
        ])
        events = sig.evaluate_group(group)
        # 2 Wenden -> 2 Event-Paare = 4 Dicts
        assert len(events) == 4
        # 2 unterschiedliche signal_event_ids
        assert len({e['signal_event_id'] for e in events}) == 2

    def test_single_trade_no_event(self):
        sig = ContrarianSignal(window_days=30)
        group = make_group([make_trade(date(2026, 3, 1), 'Purchase')])
        assert sig.evaluate_group(group) == []

    def test_ignores_other_direction(self):
        """Trades mit transaction_type='Exchange' werden ignoriert."""
        sig = ContrarianSignal(window_days=30)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 5), 'Exchange', trade_id='t2'),
            make_trade(date(2026, 3, 10), 'Sale (Full)', trade_id='t3'),
        ])
        events = sig.evaluate_group(group)
        # Wende ist t1 (buy) -> t3 (sell), 9 Tage
        assert len(events) == 2
        assert {e['trade_id'] for e in events} == {'t1', 't3'}

    def test_deterministic_signal_event_id(self):
        sig = ContrarianSignal(window_days=30)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 10), 'Sale (Full)', trade_id='t2'),
        ])
        a = sig.evaluate_group(group)
        b = sig.evaluate_group(group)
        assert a[0]['signal_event_id'] == b[0]['signal_event_id']
        assert len(a[0]['signal_event_id']) == 16

    def test_uses_settings_default(self):
        from config import settings
        sig = ContrarianSignal()
        assert sig.window_days == settings.CONTRARIAN_WINDOW_DAYS


# ---------- AccumulativeSignal ----------------------------------------------

class TestAccumulativeSignal:
    def test_triggers_on_two_buys_within_window(self):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 15), 'Purchase', trade_id='t2'),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 2
        assert events[0]['signal_event_id'] == events[1]['signal_event_id']

    def test_does_not_trigger_outside_window(self):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        group = make_group([
            make_trade(date(2026, 1, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 15), 'Purchase', trade_id='t2'),
        ])
        assert sig.evaluate_group(group) == []

    def test_does_not_trigger_for_single_trade(self):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        group = make_group([make_trade(date(2026, 3, 1), 'Purchase')])
        assert sig.evaluate_group(group) == []

    def test_buy_and_sell_groups_separately(self):
        """Buy- und Sell-Trades duerfen sich nicht gegenseitig zaehlen."""
        sig = AccumulativeSignal(window_days=30, min_count=2)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 2), 'Sale (Full)', trade_id='t2'),
            make_trade(date(2026, 3, 3), 'Purchase', trade_id='t3'),
        ])
        events = sig.evaluate_group(group)
        # Erwartung: ein Buy-Event-Set mit t1+t3 (kein Sell-Event, da nur ein Sell)
        assert len(events) == 2
        assert {e['trade_id'] for e in events} == {'t1', 't3'}
        assert all(e['direction'] == 'buy' for e in events)

    def test_three_buys_in_window_emit_single_event_set(self):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 10), 'Purchase', trade_id='t2'),
            make_trade(date(2026, 3, 20), 'Purchase', trade_id='t3'),
        ])
        events = sig.evaluate_group(group)
        # Alle 3 sind in einem 30-Tage-Fenster -> ein gemeinsames Event-Set
        assert len(events) == 3
        assert len({e['signal_event_id'] for e in events}) == 1

    def test_buys_split_across_windows(self):
        """Wenn das Fenster bricht, entstehen mehrere unabhaengige
        Event-Sets."""
        sig = AccumulativeSignal(window_days=10, min_count=2)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 5), 'Purchase', trade_id='t2'),
            # Luecke > 10 Tage
            make_trade(date(2026, 4, 1), 'Purchase', trade_id='t3'),
            make_trade(date(2026, 4, 8), 'Purchase', trade_id='t4'),
        ])
        events = sig.evaluate_group(group)
        # 2 separate Event-Sets, je 2 Trades
        sig_ids = {e['signal_event_id'] for e in events}
        assert len(sig_ids) == 2
        assert len(events) == 4

    def test_deterministic_signal_event_id(self):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        group = make_group([
            make_trade(date(2026, 3, 1), 'Purchase', trade_id='t1'),
            make_trade(date(2026, 3, 15), 'Purchase', trade_id='t2'),
        ])
        a = sig.evaluate_group(group)
        b = sig.evaluate_group(group)
        assert a[0]['signal_event_id'] == b[0]['signal_event_id']
        assert len(a[0]['signal_event_id']) == 16

    def test_uses_settings_defaults(self):
        from config import settings
        sig = AccumulativeSignal()
        assert sig.window_days == settings.ACCUMULATIVE_WINDOW_DAYS
        assert sig.min_count == settings.ACCUMULATIVE_MIN_COUNT


# ---------- H6: Lots desselben Handelstags sind ein Ereignis -----------------
# Audit-Fall: McCaul verkauft EOG am 2016-05-18 in 44 Lots (Quiver-XLSX).

def _mccaul_eog_lots(tx_type):
    return [make_trade(date(2016, 5, 18), tx_type, trade_id=f'lot{i}',
                       ticker='EOG', member_id='M001157')
            for i in range(44)]


@pytest.mark.parametrize('tx_type', ['Sale', 'Purchase'])
class TestAccumulativeSameDayLots:
    def test_lots_eines_handelstags_loesen_nicht_aus(self, tx_type):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        assert sig.evaluate_group(make_group(_mccaul_eog_lots(tx_type))) == []

    def test_zweiter_handelstag_im_fenster_loest_aus(self, tx_type):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        trades = _mccaul_eog_lots(tx_type) + [
            make_trade(date(2016, 5, 25), tx_type, trade_id='tag2',
                       ticker='EOG', member_id='M001157')]
        events = sig.evaluate_group(make_group(trades))
        # Ein Event-Set ueber beide Handelstage, alle 45 Zeilen beteiligt.
        assert sorted(e['trade_id'] for e in events) == sorted(
            [f'lot{i}' for i in range(44)] + ['tag2'])
        assert len({e['signal_event_id'] for e in events}) == 1
        assert {e['triggering_date'] for e in events} == {date(2016, 5, 25)}

    def test_undatierte_zeile_wird_uebersprungen(self, tx_type):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        undatiert = make_trade(date(2016, 5, 20), tx_type, trade_id='ohne_datum',
                               ticker='EOG', member_id='M001157')
        undatiert['transaction_date'] = pd.NaT
        trades = _mccaul_eog_lots(tx_type)[:2] + [undatiert] + [
            make_trade(date(2016, 5, 25), tx_type, trade_id='tag2',
                       ticker='EOG', member_id='M001157')]
        events = sig.evaluate_group(make_group(trades))
        assert 'ohne_datum' not in {e['trade_id'] for e in events}
        assert len(events) == 3

    def test_zweiter_handelstag_ausserhalb_des_fensters_loest_nicht_aus(
            self, tx_type):
        sig = AccumulativeSignal(window_days=30, min_count=2)
        trades = _mccaul_eog_lots(tx_type) + [
            make_trade(date(2016, 7, 1), tx_type, trade_id='spaet',
                       ticker='EOG', member_id='M001157')]
        assert sig.evaluate_group(make_group(trades)) == []
