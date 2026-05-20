"""Tests fuer P4: Suspicious-Export-Stage (_stage_suspicious_export + Helpers)."""

import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Wir importieren das Modul, um Pfade zu monkey-patchen.
import scripts.import_trades as it
from scripts.import_trades import (
    TradeImporter,
    _counter_string,
    SIGNAL_EVENT_COLUMNS,
    ASSETS_VIEW_COLUMNS,
    MEMBERS_VIEW_COLUMNS,
)


# ---------- Fixtures --------------------------------------------------------

def make_event(**overrides) -> dict:
    """Erzeugt ein Event-Dict mit Defaults fuer alle 20 SIGNAL_EVENT_COLUMNS."""
    base = {
        'signal_event_id': 'sig_aaa_0001',
        'trade_id': 'trd_0001',
        'signal_type': 'committee',
        'triggering_date': '2026-05-10',
        'scope_key': 'trd_0001',
        'member_id': 'M000001',
        'member_name': 'Doe, John',
        'chamber': 'House',
        'ticker': 'AAPL',
        'issuer_ticker': 'AAPL',
        'asset_name_figi': 'APPLE INC',
        'transaction_date': '2026-05-10',
        'disclosure_date': '2026-05-12',
        'transaction_amount': '$1,001-$15,000',
        'direction': 'buy',
        'is_ex_officio': False,
        'relevance_score': 80,
        'vote_timing_hit_count': 0,
        'committee_subcommittee': 'Armed Services',
        'relevant_sectors': 'Tech',
    }
    base.update(overrides)
    return base


def _write_stage_files(tmp_path, branch1_events=None, sequence_events=None):
    """Schreibt die beiden Stage-CSVs unter tmp_path."""
    b1_path = tmp_path / 'branch1_events_stage.csv'
    seq_path = tmp_path / 'sequence_events_stage.csv'
    pd.DataFrame(
        branch1_events or [], columns=SIGNAL_EVENT_COLUMNS
    ).to_csv(b1_path, index=False)
    pd.DataFrame(
        sequence_events or [], columns=SIGNAL_EVENT_COLUMNS
    ).to_csv(seq_path, index=False)
    return b1_path, seq_path


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Lenkt alle Suspicious-Pfade in tmp_path um."""
    monkeypatch.setattr(it, 'BRANCH1_EVENTS_STAGE_PATH',
                        str(tmp_path / 'branch1_events_stage.csv'))
    monkeypatch.setattr(it, 'SEQUENCE_EVENTS_STAGE_PATH',
                        str(tmp_path / 'sequence_events_stage.csv'))
    monkeypatch.setattr(it, 'SWARM_EVENTS_STAGE_PATH',
                        str(tmp_path / 'swarm_events_stage.csv'))
    monkeypatch.setattr(it, 'SUSPICIOUS_EVENTS_PATH',
                        str(tmp_path / 'suspicious_events.csv'))
    monkeypatch.setattr(it, 'SUSPICIOUS_ASSETS_PATH',
                        str(tmp_path / 'suspicious_assets.csv'))
    monkeypatch.setattr(it, 'SUSPICIOUS_MEMBERS_PATH',
                        str(tmp_path / 'suspicious_members.csv'))
    # Modul 8.4: Score-Lookup-CSV (trades_master_complete) ebenfalls in tmp_path.
    monkeypatch.setattr(it, 'MASTER_COMPLETE_CSV_PATH',
                        str(tmp_path / 'trades_master_complete.csv'))
    # _ensure_directories ruft os.makedirs(PROCESSED_DIR) auf — irrelevant,
    # tmp_path existiert. Wir patchen es trotzdem, damit es ein no-op wird.
    return tmp_path


def _write_trade_scores_csv(tmp_path, rows):
    """Schreibt trades_master_complete.csv mit nur den Score-Spalten (Modul 8.4).

    Args:
        tmp_path: pytest tmp_path
        rows: Liste von dicts mit Keys trade_id, trade_score, trade_volume_usd, trade_warnings.
    """
    cols = ['trade_id', 'trade_score', 'trade_volume_usd', 'trade_warnings']
    pd.DataFrame(rows, columns=cols).to_csv(
        tmp_path / 'trades_master_complete.csv', index=False)


# ---------- _counter_string -------------------------------------------------

class TestCounterString:
    def test_leer_serie(self):
        assert _counter_string(pd.Series([], dtype=str)) == ''

    def test_alle_nan(self):
        assert _counter_string(pd.Series([None, None, None])) == ''

    def test_einzelner_wert(self):
        assert _counter_string(pd.Series(['committee'])) == 'committee:1'

    def test_sortiert_nach_count_desc(self):
        s = pd.Series(['a', 'b', 'a', 'a', 'b', 'c'])
        # a=3, b=2, c=1
        assert _counter_string(s) == 'a:3,b:2,c:1'

    def test_tie_break_alphabetisch(self):
        s = pd.Series(['b', 'a', 'c'])  # alle Count=1
        assert _counter_string(s) == 'a:1,b:1,c:1'

    def test_ignoriert_leere_strings_und_whitespace(self):
        s = pd.Series(['committee', '', '  ', 'committee', None])
        assert _counter_string(s) == 'committee:2'


# ---------- _load_event_stages ---------------------------------------------

class TestLoadEventStages:
    def test_beide_dateien_fehlen(self, isolated_paths):
        importer = TradeImporter()
        result = importer._load_event_stages()
        assert result.empty
        assert list(result.columns) == SIGNAL_EVENT_COLUMNS

    def test_nur_branch1(self, isolated_paths):
        _write_stage_files(isolated_paths, branch1_events=[make_event()])
        # sequence-Datei wird zwar geschrieben, aber leer (0 Datenzeilen)
        importer = TradeImporter()
        result = importer._load_event_stages()
        assert len(result) == 1

    def test_beide_dateien_befuellt(self, isolated_paths):
        _write_stage_files(
            isolated_paths,
            branch1_events=[make_event(trade_id='t1')],
            sequence_events=[make_event(trade_id='t2', signal_type='contrarian')],
        )
        importer = TradeImporter()
        result = importer._load_event_stages()
        assert len(result) == 2


# ---------- _append_suspicious_events --------------------------------------

class TestAppendSuspiciousEvents:
    def test_erstes_anhaengen_schreibt_alle(self, isolated_paths):
        importer = TradeImporter()
        events = pd.DataFrame(
            [make_event(trade_id='t1'),
             make_event(trade_id='t2', signal_event_id='sig_bbb')],
            columns=SIGNAL_EVENT_COLUMNS,
        )
        n = importer._append_suspicious_events(events)
        assert n == 2
        df = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(df) == 2
        assert list(df.columns) == SIGNAL_EVENT_COLUMNS

    def test_idempotenz_zweimaliges_anhaengen(self, isolated_paths):
        importer = TradeImporter()
        events = pd.DataFrame(
            [make_event(trade_id='t1')], columns=SIGNAL_EVENT_COLUMNS,
        )
        importer._append_suspicious_events(events)
        n = importer._append_suspicious_events(events)
        assert n == 0
        df = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(df) == 1

    def test_mix_alte_und_neue_keys(self, isolated_paths):
        importer = TradeImporter()
        first = pd.DataFrame(
            [make_event(trade_id='t1')], columns=SIGNAL_EVENT_COLUMNS,
        )
        importer._append_suspicious_events(first)

        second = pd.DataFrame([
            make_event(trade_id='t1'),  # bereits da
            make_event(trade_id='t2'),  # neu
            make_event(trade_id='t3', signal_event_id='sig_bbb'),  # neu
        ], columns=SIGNAL_EVENT_COLUMNS)
        n = importer._append_suspicious_events(second)
        assert n == 2
        df = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(df) == 3

    def test_leerer_input_keine_aktion(self, isolated_paths):
        importer = TradeImporter()
        n = importer._append_suspicious_events(
            pd.DataFrame(columns=SIGNAL_EVENT_COLUMNS)
        )
        assert n == 0
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)


# ---------- _materialize_suspicious_assets ---------------------------------

class TestMaterializeAssets:
    def test_y_tage_verfall(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date()
        events = pd.DataFrame([
            # alt — sollte aus assets verschwinden
            make_event(trade_id='t1', issuer_ticker='OLD',
                       triggering_date=(today - timedelta(days=31)).isoformat()),
            # frisch — sollte drinbleiben
            make_event(trade_id='t2', issuer_ticker='NEW',
                       triggering_date=(today - timedelta(days=5)).isoformat()),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        n = importer._materialize_suspicious_assets()
        assert n == 1
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        assert df['issuer_ticker'].tolist() == ['NEW']

    def test_aggregation_zwei_members_ein_asset(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', member_id='M1', issuer_ticker='AAPL',
                       triggering_date=today),
            make_event(trade_id='t2', member_id='M2', issuer_ticker='AAPL',
                       signal_event_id='sig_bbb', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        row = df.iloc[0]
        assert row['event_count'] == 2
        assert row['member_count'] == 2
        assert row['asset_name_figi'] == 'APPLE INC'

    def test_signal_types_counter(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', signal_type='committee', triggering_date=today),
            make_event(trade_id='t2', signal_type='committee',
                       signal_event_id='sig_bbb', triggering_date=today),
            make_event(trade_id='t3', signal_type='contrarian',
                       signal_event_id='sig_ccc', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        assert df.iloc[0]['signal_types'] == 'committee:2,contrarian:1'

    def test_keine_events_leere_view_geschrieben(self, isolated_paths):
        # Datei existiert nicht → 0 Zeilen, keine Exception.
        importer = TradeImporter()
        n = importer._materialize_suspicious_assets()
        assert n == 0


# ---------- _materialize_suspicious_members --------------------------------

class TestMaterializeMembers:
    def test_kein_y_tage_filter(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date()
        events = pd.DataFrame([
            make_event(trade_id='t1',
                       triggering_date=(today - timedelta(days=200)).isoformat()),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        n = importer._materialize_suspicious_members()
        assert n == 1
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        assert len(df) == 1

    def test_event_trade_pairs_json_format(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', signal_event_id='sig_aaa',
                       triggering_date=today),
            make_event(trade_id='t2', signal_event_id='sig_aaa',
                       triggering_date=today),
            make_event(trade_id='t3', signal_event_id='sig_bbb',
                       triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        importer._materialize_suspicious_members()
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        pairs = json.loads(df.iloc[0]['event_trade_pairs'])
        assert isinstance(pairs, list)
        assert all(isinstance(p, list) and len(p) == 2 for p in pairs)
        assert sorted(pairs) == sorted(
            [['sig_aaa', 't1'], ['sig_aaa', 't2'], ['sig_bbb', 't3']]
        )

    def test_asset_count_distinct(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', issuer_ticker='AAPL', triggering_date=today),
            make_event(trade_id='t2', issuer_ticker='AAPL',
                       signal_event_id='sig_bbb', triggering_date=today),
            make_event(trade_id='t3', issuer_ticker='MSFT',
                       signal_event_id='sig_ccc', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        importer._materialize_suspicious_members()
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        assert df.iloc[0]['event_count'] == 3
        assert df.iloc[0]['asset_count'] == 2

    def test_is_ex_officio_ever_oder_verknuepfung(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', is_ex_officio=False, triggering_date=today),
            make_event(trade_id='t2', is_ex_officio=True,
                       signal_event_id='sig_bbb', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        importer._materialize_suspicious_members()
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        # CSV-Roundtrip → bool wird zu 'True'/'False'-String
        assert str(df.iloc[0]['is_ex_officio_ever']).lower() in ('true', '1')


# ---------- _stage_suspicious_export (Integration) --------------------------

class TestStageSuspiciousExport:
    def test_dry_run_schreibt_keine_dateien(self, isolated_paths, capsys):
        _write_stage_files(
            isolated_paths,
            branch1_events=[make_event(trade_id='t1')],
        )
        importer = TradeImporter()
        importer._stage_suspicious_export(delta=False, dry_run=True)
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        assert not os.path.exists(it.SUSPICIOUS_ASSETS_PATH)
        assert not os.path.exists(it.SUSPICIOUS_MEMBERS_PATH)
        out = capsys.readouterr().out
        assert 'dry_run' in out

    def test_live_schreibt_alle_drei_dateien(self, isolated_paths):
        today = datetime.now().date().isoformat()
        _write_stage_files(
            isolated_paths,
            branch1_events=[make_event(trade_id='t1', triggering_date=today)],
            sequence_events=[make_event(trade_id='t2',
                                        signal_event_id='sig_bbb',
                                        signal_type='contrarian',
                                        triggering_date=today)],
        )
        importer = TradeImporter()
        importer._stage_suspicious_export(delta=False, dry_run=False)
        assert os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        assert os.path.exists(it.SUSPICIOUS_ASSETS_PATH)
        assert os.path.exists(it.SUSPICIOUS_MEMBERS_PATH)
        events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(events) == 2

    def test_idempotenz_voller_zyklus(self, isolated_paths):
        today = datetime.now().date().isoformat()
        _write_stage_files(
            isolated_paths,
            branch1_events=[make_event(trade_id='t1', triggering_date=today)],
        )
        importer = TradeImporter()
        importer._stage_suspicious_export(delta=False, dry_run=False)
        importer._stage_suspicious_export(delta=False, dry_run=False)
        events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(events) == 1

    def test_leere_stages_keine_outputs(self, isolated_paths, capsys):
        _write_stage_files(isolated_paths)  # beide leer
        importer = TradeImporter()
        importer._stage_suspicious_export(delta=False, dry_run=False)
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        out = capsys.readouterr().out
        assert '0 Events' in out


# ---------- Modul 8.4: Score-Aggregation in Views --------------------------

class TestAssetsViewScoreAggregation:
    """Tests fuer die Score-Spalten in suspicious_assets.csv (Modul 8.4)."""

    def test_two_trades_same_asset(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', issuer_ticker='AAPL', triggering_date=today),
            make_event(trade_id='t2', issuer_ticker='AAPL',
                       signal_event_id='sig_bbb', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 10,
             'trade_volume_usd': 1000, 'trade_warnings': ''},
            {'trade_id': 't2', 'trade_score': 20,
             'trade_volume_usd': 2000, 'trade_warnings': ''},
        ])
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        row = df.iloc[0]
        assert row['score_avg'] == 15.0
        assert row['score_max'] == 20
        assert row['score_total'] == 30
        assert row['total_volume_usd'] == 3000

    def test_distinct_trade_ids(self, isolated_paths):
        """Trade t1 in 2 Events → Score nur einmal aggregiert (distinct trade_id)."""
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', signal_type='committee',
                       triggering_date=today),
            make_event(trade_id='t1', signal_type='contrarian',
                       signal_event_id='sig_bbb', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 50,
             'trade_volume_usd': 5000, 'trade_warnings': ''},
        ])
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        # score nur einmal gezaehlt, nicht 2x
        assert df.iloc[0]['score_total'] == 50
        assert df.iloc[0]['total_volume_usd'] == 5000
        # event_count zaehlt aber alle Events (Status quo)
        assert df.iloc[0]['event_count'] == 2

    def test_warnings_distinct_sorted(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', issuer_ticker='AAPL', triggering_date=today),
            make_event(trade_id='t2', issuer_ticker='AAPL',
                       signal_event_id='sig_bbb', triggering_date=today),
            make_event(trade_id='t3', issuer_ticker='AAPL',
                       signal_event_id='sig_ccc', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 0,
             'trade_volume_usd': 0, 'trade_warnings': 'paradox_swarm'},
            {'trade_id': 't2', 'trade_score': 5,
             'trade_volume_usd': 0, 'trade_warnings': ''},
            {'trade_id': 't3', 'trade_score': 0,
             'trade_volume_usd': 0, 'trade_warnings': 'paradox_swarm'},
        ])
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        # Distinct → nur einmal 'paradox_swarm', kein leerer Token
        assert df.iloc[0]['warnings'] == 'paradox_swarm'

    def test_total_volume_aggregation(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', issuer_ticker='MSFT', triggering_date=today),
            make_event(trade_id='t2', issuer_ticker='MSFT',
                       signal_event_id='sig_bbb', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 1,
             'trade_volume_usd': 12500, 'trade_warnings': ''},
            {'trade_id': 't2', 'trade_score': 1,
             'trade_volume_usd': 7500, 'trade_warnings': ''},
        ])
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        assert df.iloc[0]['total_volume_usd'] == 20000

    def test_fillna_zero_when_trade_missing_in_master(self, isolated_paths):
        """Trade in suspicious_events, aber NICHT in master → score=0 (kein NaN)."""
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t_unknown', issuer_ticker='AAPL',
                       triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        # Master-CSV mit anderem trade_id → t_unknown findet nichts
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't_other', 'trade_score': 99,
             'trade_volume_usd': 1000, 'trade_warnings': 'paradox_swarm'},
        ])
        importer._materialize_suspicious_assets()
        df = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        row = df.iloc[0]
        assert row['score_avg'] == 0.0
        assert row['score_max'] == 0
        assert row['score_total'] == 0
        assert row['total_volume_usd'] == 0
        # Leere warnings → CSV-Roundtrip macht NaN; pd.isna oder '' beides OK.
        assert pd.isna(row['warnings']) or row['warnings'] == ''


class TestMembersViewScoreAggregation:
    """Tests fuer die Score-Spalten in suspicious_members.csv (Modul 8.4)."""

    def test_member_score_aggregation(self, isolated_paths):
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', member_id='M1', triggering_date=today),
            make_event(trade_id='t2', member_id='M1',
                       signal_event_id='sig_bbb', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 5,
             'trade_volume_usd': 0, 'trade_warnings': ''},
            {'trade_id': 't2', 'trade_score': 15,
             'trade_volume_usd': 0, 'trade_warnings': ''},
        ])
        importer._materialize_suspicious_members()
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        row = df.iloc[0]
        assert row['score_avg'] == 10.0
        assert row['score_max'] == 15
        assert row['score_total'] == 20

    def test_inflight_override_uebersteuert_file(self, isolated_paths):
        """Voll-/Delta-Pipeline-Pfad: _inflight_trade_scores hat Vorrang vor
        der trades_master_complete.csv. Simuliert den Fall, dass die Datei
        veraltete (oder gar keine) Scores enthaelt, der laufende Run aber
        frische Scores im DataFrame hat (Bug-Regression-Schutz)."""
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', member_id='M1', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        # Datei haelt ALTE Scores
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 0,
             'trade_volume_usd': 0, 'trade_warnings': ''},
        ])
        # Override durchreichen: frische Scores aus der Pipeline
        importer._inflight_trade_scores = pd.DataFrame([
            {'trade_id': 't1', 'trade_score': 99,
             'trade_volume_usd': 12345, 'trade_warnings': 'paradox_swarm'},
        ])
        importer._materialize_suspicious_members()
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        assert df.iloc[0]['score_max'] == 99, \
            "Override muss File-Wert (0) ueberschreiben"
        assert df.iloc[0]['score_total'] == 99

    def test_members_view_no_volume_no_warnings_columns(self, isolated_paths):
        """Members-View hat NICHT total_volume_usd und NICHT warnings."""
        importer = TradeImporter()
        today = datetime.now().date().isoformat()
        events = pd.DataFrame([
            make_event(trade_id='t1', triggering_date=today),
        ], columns=SIGNAL_EVENT_COLUMNS)
        importer._append_suspicious_events(events)
        _write_trade_scores_csv(isolated_paths, [
            {'trade_id': 't1', 'trade_score': 1,
             'trade_volume_usd': 9999, 'trade_warnings': 'paradox_swarm'},
        ])
        importer._materialize_suspicious_members()
        df = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        assert 'total_volume_usd' not in df.columns
        assert 'warnings' not in df.columns
        assert 'score_avg' in df.columns
        assert 'score_max' in df.columns
        assert 'score_total' in df.columns
