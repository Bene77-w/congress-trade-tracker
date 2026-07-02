"""Regressionstests: Sequenz-/Schwarm-Signale im Delta-Modus mit
Master-Historie als Gruppierungs-KONTEXT.

Bug (behoben): In run_delta() liefen _apply_sequence_signals /
_apply_swarm_signals nur auf der Delta-Charge. Ein neuer Trade, der erst mit
einem HISTORISCHEN Master-Trade ein Paar/einen Schwarm bildet, war allein in
der Charge -> Gruppe < 2 -> Signal loeste nicht aus. Fix: optionaler
Parameter history_df; die Historie liefert nur den Gruppierungs-Kontext,
Events + Aggregate bleiben strikt auf die Delta-Zeilen beschraenkt.
"""

from datetime import date

import pandas as pd

import scripts.import_trades as it
from scripts.import_trades import TradeImporter

from tests.test_swarm_signals import make_swarm_trade


def _read_events(path):
    return pd.read_csv(path, low_memory=False)


class TestSwarmDeltaHistoryContext:
    def test_delta_trade_pairs_with_history(self, tmp_path, monkeypatch):
        """1 historischer + 1 Delta-Trade bilden einen swarm_buy (7d-Fenster,
        gleicher issuer_group_key). Der Delta-Trade bekommt das Signal, und die
        Event-CSV enthaelt KEIN Event fuer den historischen Trade."""
        swarm_events = tmp_path / 'swarm_events_stage.csv'
        monkeypatch.setattr(it, 'SWARM_EVENTS_STAGE_PATH', str(swarm_events))
        monkeypatch.setattr(TradeImporter, '_ensure_directories', lambda self: None)

        history = pd.DataFrame([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1),
                             issuer_group_key='CIK:APPLE', trade_id='hist_A'),
        ])
        delta = pd.DataFrame([
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 3),
                             issuer_group_key='CIK:APPLE', trade_id='delta_B'),
        ])

        importer = TradeImporter()
        out = importer._apply_swarm_signals(delta, history_df=history)

        # Delta-Zeile bekommt das Signal.
        assert len(out) == 1
        assert out.iloc[0]['trade_id'] == 'delta_B'
        assert 'swarm_buy' in str(out.iloc[0]['signals_triggered']).split(';')
        assert int(out.iloc[0]['signal_count']) >= 1

        # Event-CSV: nur der Delta-Trade, KEIN historischer Event (kein Dup).
        events = _read_events(swarm_events)
        assert set(events['trade_id'].astype(str)) == {'delta_B'}
        assert 'hist_A' not in set(events['trade_id'].astype(str))

    def test_no_history_single_delta_does_not_trigger(self, tmp_path, monkeypatch):
        """Ohne history_df loest ein einzelner Delta-Trade allein NICHT aus
        (Alt-Verhalten: Gruppe < 2)."""
        swarm_events = tmp_path / 'swarm_events_stage.csv'
        monkeypatch.setattr(it, 'SWARM_EVENTS_STAGE_PATH', str(swarm_events))
        monkeypatch.setattr(TradeImporter, '_ensure_directories', lambda self: None)

        delta = pd.DataFrame([
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 3),
                             issuer_group_key='CIK:APPLE', trade_id='delta_B'),
        ])

        importer = TradeImporter()
        out = importer._apply_swarm_signals(delta)

        assert 'swarm_buy' not in str(out.iloc[0]['signals_triggered']).split(';')
        events = _read_events(swarm_events)
        assert events.empty

    def test_full_run_behavior_unchanged(self, tmp_path, monkeypatch):
        """Full-Run (beide Trades in einem df, kein history_df) loest den
        swarm_buy aus und emittiert Events fuer BEIDE Trades — unveraendert."""
        swarm_events = tmp_path / 'swarm_events_stage.csv'
        monkeypatch.setattr(it, 'SWARM_EVENTS_STAGE_PATH', str(swarm_events))
        monkeypatch.setattr(TradeImporter, '_ensure_directories', lambda self: None)

        both = pd.DataFrame([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1),
                             issuer_group_key='CIK:APPLE', trade_id='hist_A'),
            make_swarm_trade('B', 'AAPL', 'buy', date(2026, 3, 3),
                             issuer_group_key='CIK:APPLE', trade_id='delta_B'),
        ])

        importer = TradeImporter()
        out = importer._apply_swarm_signals(both)

        triggered = {
            row['trade_id']: str(row['signals_triggered']).split(';')
            for _, row in out.iterrows()
        }
        assert 'swarm_buy' in triggered['hist_A']
        assert 'swarm_buy' in triggered['delta_B']
        events = _read_events(swarm_events)
        assert set(events['trade_id'].astype(str)) == {'hist_A', 'delta_B'}


class TestSequenceDeltaHistoryContext:
    def test_contrarian_delta_pairs_with_history(self, tmp_path, monkeypatch):
        """Historischer Buy + Delta-Sell desselben Members auf demselben Asset
        im 30d-Fenster -> contrarian. Delta-Zeile bekommt das Signal, Event-CSV
        enthaelt kein historisches Duplikat."""
        seq_events = tmp_path / 'sequence_events_stage.csv'
        monkeypatch.setattr(it, 'SEQUENCE_EVENTS_STAGE_PATH', str(seq_events))
        monkeypatch.setattr(TradeImporter, '_ensure_directories', lambda self: None)

        history = pd.DataFrame([
            make_swarm_trade('A', 'AAPL', 'buy', date(2026, 3, 1),
                             issuer_group_key='CIK:APPLE', trade_id='hist_buy'),
        ])
        delta = pd.DataFrame([
            make_swarm_trade('A', 'AAPL', 'sell', date(2026, 3, 10),
                             issuer_group_key='CIK:APPLE', trade_id='delta_sell'),
        ])

        importer = TradeImporter()
        out = importer._apply_sequence_signals(delta, history_df=history)

        assert len(out) == 1
        assert out.iloc[0]['trade_id'] == 'delta_sell'
        assert 'contrarian' in str(out.iloc[0]['signals_triggered']).split(';')

        events = _read_events(seq_events)
        assert set(events['trade_id'].astype(str)) == {'delta_sell'}
        assert 'hist_buy' not in set(events['trade_id'].astype(str))
