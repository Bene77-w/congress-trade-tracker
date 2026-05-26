"""Tests fuer die Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6).

Setup: SwarmBuy/SwarmSell laufen auf Issuer-Group-Buckets (gruppiert nach
issuer_group_key) und erzeugen pro Cluster eine signal_event_id mit allen
beteiligten Trades als Event-Zeilen.

Conflict + Paradox sind mutually exclusive pro Schwarm-Auslösung:
- 1 Gegen-Trade im erweiterten Fenster -> swarm_conflict
- >= SWARM_PARADOX_MIN_CONFLICTS Gegen-Trades -> swarm_paradox
"""

from datetime import date

import pandas as pd
import pytest

from signals.swarm_buy_signal import SwarmBuySignal
from signals.swarm_sell_signal import SwarmSellSignal
from signals.swarm_conflict_signal import SwarmConflictSignal
from signals.swarm_paradox_signal import SwarmParadoxSignal


def make_swarm_trade(member_id, ticker, direction, td,
                    issuer_group_key=None,
                    ticker_type='stock',
                    trade_id=None,
                    asset_name='Apple Inc') -> dict:
    """Erzeugt einen Trade-Dict fuer Schwarm-Tests.

    direction: 'buy' oder 'sell' (wird zu Purchase/Sale (Full) konvertiert).
    issuer_group_key: Default = f'TICKER:{ticker}'.
    """
    if issuer_group_key is None:
        issuer_group_key = f'TICKER:{ticker}'
    tx_type = 'Purchase' if direction == 'buy' else 'Sale (Full)'
    return {
        'trade_id': trade_id or f'trd_{member_id}_{td.isoformat()}_{direction}',
        'name': f'Member, {member_id}',
        'bio_guide_id': member_id,
        'chamber': 'House',
        'ticker': ticker,
        'ticker_base': ticker,
        'ticker_type': ticker_type,
        'issuer_ticker': ticker,
        'issuer_group_key': issuer_group_key,
        'asset_name': asset_name,
        'asset_name_figi': asset_name,
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


# ===========================================================================
# SwarmBuySignal / SwarmSellSignal
# ===========================================================================

class TestSwarmBuy:
    def test_basic_swarm_two_members(self):
        """A+B kaufen AAPL Tag 0/1 -> 2 Events, gleiche signal_event_id."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1),
                            trade_id='tA'),
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 2),
                            trade_id='tB'),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 2
        assert events[0]['signal_event_id'] == events[1]['signal_event_id']
        assert {e['trade_id'] for e in events} == {'tA', 'tB'}
        assert all(e['signal_type'] == 'swarm_buy' for e in events)

    def test_re_trigger_third_member_same_window(self):
        """A+B+C kaufen Tag 0/1/3 (alle innerhalb 7d ab Tag 0) -> 3 Events."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 2)),
            make_swarm_trade('C', 'AAPL', 'buy', date(2026, 3, 4)),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 3
        # Alle teilen sich eine signal_event_id (selber window_start)
        ids = {e['signal_event_id'] for e in events}
        assert len(ids) == 1

    def test_min_members_not_reached(self):
        """Nur A kauft -> 0 Events."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1)),
        ])
        assert sig.evaluate_group(group) == []

    def test_cross_class_via_issuer_group_key(self):
        """A kauft GOOGL, B kauft GOOG, beide mit CIK:1652044 -> 1 Schwarm."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'GOOGL', 'buy', date(2026, 3, 1),
                            issuer_group_key='CIK:1652044'),
            make_swarm_trade('B', 'GOOG',  'buy', date(2026, 3, 2),
                            issuer_group_key='CIK:1652044'),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 2
        # Verschiedene tickers, gleiche signal_event_id
        assert {e['ticker'] for e in events} == {'GOOGL', 'GOOG'}
        assert events[0]['signal_event_id'] == events[1]['signal_event_id']

    def test_skip_bucket_bypass(self):
        """SKIP:option-Bucket -> kein Schwarm, auch wenn Bedingungen erfuellt."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1),
                            issuer_group_key='SKIP:option',
                            ticker_type='option'),
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 2),
                            issuer_group_key='SKIP:option',
                            ticker_type='option'),
        ])
        assert sig.evaluate_group(group) == []

    def test_window_split(self):
        """Tag 0 + Tag 10 mit Window=7 -> Cluster splittet, kein Schwarm."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 11)),
        ])
        assert sig.evaluate_group(group) == []

    def test_sell_trades_ignored_in_buy_swarm(self):
        """SwarmBuy ignoriert Sell-Trades."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'sell', date(2026, 3, 2)),
        ])
        assert sig.evaluate_group(group) == []


class TestSwarmSell:
    def test_basic_sell_swarm(self):
        sig = SwarmSellSignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'sell', date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'sell', date(2026, 3, 2)),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 2
        assert all(e['signal_type'] == 'swarm_sell' for e in events)


# ===========================================================================
# SwarmConflictSignal
# ===========================================================================

class TestSwarmConflict:
    def test_external_conflict_single_opposing(self):
        """A+B kaufen Tag 0/1, C verkauft Tag 3 -> 1 swarm_conflict (C)."""
        sig = SwarmConflictSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=3, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2)),
            make_swarm_trade('C', 'AAPL', 'sell', date(2026, 3, 4),
                            trade_id='conflict_C'),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 1
        ev = events[0]
        assert ev['signal_type'] == 'swarm_conflict'
        assert ev['trade_id'] == 'conflict_C'
        assert ev['scope_key'].startswith('conflict|TICKER:AAPL|buy|')

    def test_self_reversal_counts_as_conflict(self):
        """A+B+C kaufen, B verkauft spaeter -> swarm_conflict fuer B-Sell."""
        sig = SwarmConflictSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=5, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1),
                            trade_id='A_buy'),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2),
                            trade_id='B_buy'),
            make_swarm_trade('C', 'AAPL', 'buy',  date(2026, 3, 3),
                            trade_id='C_buy'),
            make_swarm_trade('B', 'AAPL', 'sell', date(2026, 3, 6),
                            trade_id='B_sell'),
        ])
        events = sig.evaluate_group(group)
        # Selbst-Umkehr triggert Conflict (1 Gegen-Trade)
        assert len(events) == 1
        assert events[0]['trade_id'] == 'B_sell'
        assert events[0]['signal_type'] == 'swarm_conflict'

    def test_before_window_within(self):
        """X verkauft 2 Tage VOR dem Schwarm (BEFORE=3) -> Konflikt."""
        sig = SwarmConflictSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=3, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('X', 'AAPL', 'sell', date(2026, 3, 1),
                            trade_id='X_sell'),
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 3)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 4)),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 1
        assert events[0]['trade_id'] == 'X_sell'

    def test_before_window_outside(self):
        """X verkauft 5 Tage VOR dem Schwarm (BEFORE=3) -> KEIN Konflikt."""
        sig = SwarmConflictSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=3, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('X', 'AAPL', 'sell', date(2026, 3, 1)),
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 6)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 7)),
        ])
        events = sig.evaluate_group(group)
        assert events == []

    def test_after_window_outside(self):
        """Y verkauft 5 Tage NACH letztem Schwarm-Trade (AFTER=3) -> KEIN."""
        sig = SwarmConflictSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=3, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2)),
            make_swarm_trade('Y', 'AAPL', 'sell', date(2026, 3, 7)),
        ])
        events = sig.evaluate_group(group)
        assert events == []

    def test_no_conflict_when_paradox(self):
        """Bei 2 Gegen-Trades -> swarm_conflict NICHT erzeugt (Paradox uebernimmt)."""
        sig = SwarmConflictSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=5, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2)),
            make_swarm_trade('C', 'AAPL', 'sell', date(2026, 3, 4)),
            make_swarm_trade('D', 'AAPL', 'sell', date(2026, 3, 5)),
        ])
        events = sig.evaluate_group(group)
        assert events == []


# ===========================================================================
# SwarmParadoxSignal
# ===========================================================================

class TestSwarmParadox:
    def test_paradox_two_opposing(self):
        """A+B kaufen, C+D verkaufen -> 2 swarm_paradox (symmetrisch).

        Beide Richtungen bilden Schwaerme, beide Schwaerme haben den
        jeweils anderen als Gegen-Trades. Resultat: 2 paradox-Events
        (buy-perspektive, sell-perspektive) mit je 2 Trade-Zeilen = 4 Zeilen.
        Bewusste Design-Entscheidung (User-Wunsch: 'existieren 2 paradox_schwarm').
        """
        sig = SwarmParadoxSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=5, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1),
                            trade_id='A_buy'),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2),
                            trade_id='B_buy'),
            make_swarm_trade('C', 'AAPL', 'sell', date(2026, 3, 4),
                            trade_id='C_sell'),
            make_swarm_trade('D', 'AAPL', 'sell', date(2026, 3, 5),
                            trade_id='D_sell'),
        ])
        events = sig.evaluate_group(group)
        assert len(events) == 4
        assert all(e['signal_type'] == 'swarm_paradox' for e in events)
        # 2 distinkte signal_event_ids (buy-paradox, sell-paradox)
        ids = {e['signal_event_id'] for e in events}
        assert len(ids) == 2
        # Buy-Perspektive: paradox|TICKER:AAPL|buy|... mit Gegen-Trades C,D
        # Sell-Perspektive: paradox|TICKER:AAPL|sell|... mit Gegen-Trades A,B
        scope_keys = {e['scope_key'] for e in events}
        assert any(sk.startswith('paradox|TICKER:AAPL|buy|') for sk in scope_keys)
        assert any(sk.startswith('paradox|TICKER:AAPL|sell|') for sk in scope_keys)

    def test_paradox_no_trigger_below_threshold(self):
        """Nur 1 Gegen-Trade -> kein Paradox (Conflict-Bereich)."""
        sig = SwarmParadoxSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=3, paradox_min=2,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2)),
            make_swarm_trade('C', 'AAPL', 'sell', date(2026, 3, 4)),
        ])
        assert sig.evaluate_group(group) == []

    def test_paradox_threshold_configurable(self):
        """Mit paradox_min=3 ergibt 2 Gegen-Trades KEIN Paradox."""
        sig = SwarmParadoxSignal(
            window_days=7, min_members=2,
            before_days=3, after_days=5, paradox_min=3,
        )
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy',  date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy',  date(2026, 3, 2)),
            make_swarm_trade('C', 'AAPL', 'sell', date(2026, 3, 4)),
            make_swarm_trade('D', 'AAPL', 'sell', date(2026, 3, 5)),
        ])
        assert sig.evaluate_group(group) == []


# ===========================================================================
# Idempotenz
# ===========================================================================

class TestIdempotency:
    def test_repeated_evaluation_identical_events(self):
        """Zweite Evaluation auf identischer Gruppe -> identische Events."""
        sig = SwarmBuySignal(window_days=7, min_members=2)
        group = make_group([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1)),
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 2)),
        ])
        events1 = sig.evaluate_group(group)
        events2 = sig.evaluate_group(group)
        # Identische signal_event_ids (deterministischer Hash)
        ids1 = sorted(e['signal_event_id'] for e in events1)
        ids2 = sorted(e['signal_event_id'] for e in events2)
        assert ids1 == ids2
