"""Tests fuer den Suspicious-Export (_export_suspicious + Helpers) und --save."""

import json
from dataclasses import replace
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Wir importieren das Modul, um Pfade zu monkey-patchen.
import scripts.import_trades as it
from utils.ticker_classifier import TickerType
from utils.ticker_normalizer import NormalizedTicker
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


def _write_stage_files(tmp_path, branch1_events=None, sequence_events=None,
                       swarm_events=None):
    """Schreibt die drei Event-Stage-CSVs unter tmp_path."""
    b1_path = tmp_path / 'branch1_events_stage.csv'
    seq_path = tmp_path / 'sequence_events_stage.csv'
    for path, events in [(b1_path, branch1_events),
                         (seq_path, sequence_events),
                         (tmp_path / 'swarm_events_stage.csv', swarm_events)]:
        pd.DataFrame(events or [], columns=SIGNAL_EVENT_COLUMNS).to_csv(
            path, index=False)
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

    def test_doppelte_keys_im_eingang_nur_einmal(self, isolated_paths):
        # Zwei identische Trades eines Laufs erzeugen denselben Event-Schluessel.
        importer = TradeImporter()
        events = pd.DataFrame(
            [make_event(trade_id='t1'), make_event(trade_id='t1')],
            columns=SIGNAL_EVENT_COLUMNS,
        )
        assert importer._append_suspicious_events(events) == 1
        assert importer._append_suspicious_events(events) == 0
        df = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(df) == 1

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


# ---------- _export_suspicious (Integration) --------------------------------

def _no_scores():
    return pd.DataFrame(columns=['trade_id', 'trade_score',
                                 'trade_volume_usd', 'trade_warnings'])


class TestExportSuspicious:
    def test_live_schreibt_alle_drei_dateien(self, isolated_paths):
        today = datetime.now().date().isoformat()
        b1_path, seq_path = _write_stage_files(
            isolated_paths,
            branch1_events=[make_event(trade_id='t1', triggering_date=today)],
            sequence_events=[make_event(trade_id='t2',
                                        signal_event_id='sig_bbb',
                                        signal_type='contrarian',
                                        triggering_date=today)],
        )
        importer = TradeImporter()
        importer._export_suspicious(_no_scores())
        assert os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        assert os.path.exists(it.SUSPICIOUS_ASSETS_PATH)
        assert os.path.exists(it.SUSPICIOUS_MEMBERS_PATH)
        events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(events) == 2
        # Aufraeumen ist Sache des Aufrufers, erst nach dem Master-Schreiben.
        assert b1_path.exists()
        assert seq_path.exists()

    def test_idempotenz_voller_zyklus(self, isolated_paths):
        today = datetime.now().date().isoformat()
        importer = TradeImporter()
        for _ in range(2):
            _write_stage_files(
                isolated_paths,
                branch1_events=[make_event(trade_id='t1', triggering_date=today)],
            )
            importer._export_suspicious(_no_scores())
        events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        assert len(events) == 1

    def test_leere_stages_keine_outputs(self, isolated_paths, capsys):
        _write_stage_files(isolated_paths)  # beide leer
        importer = TradeImporter()
        importer._export_suspicious(_no_scores())
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        out = capsys.readouterr().out
        assert '0 neue Events' in out


# ---------- --save (Speicher-Schritt nach --dry_run) -----------------------

SCORE_COLS = ['trade_id', 'trade_score', 'trade_volume_usd', 'trade_warnings']


@pytest.fixture
def save_paths(isolated_paths, monkeypatch):
    """Lenkt zusaetzlich Stage-, Master-, Backup- und Problem-Output-Pfade
    in tmp_path um und stellt den Kurs-Refresh still."""
    tmp = isolated_paths
    for name, fname in [
        ('PROCESSED_DIR', ''),
        ('STAGE_CSV_PATH', 'trades_stage.csv'),
        ('MASTER_CSV_PATH', 'trades_master.csv'),
        ('MANUAL_REVIEW_CSV_PATH', 'manual_review_tickers.csv'),
        ('UNMATCHED_STAGE_PATH', 'unmatched_names_stage.csv'),
        ('UNMATCHED_PATH', 'unmatched_names.csv'),
        ('DELISTED_STAGE_PATH', 'delisted_trades_stage.csv'),
        ('DELISTED_TRADES_PATH', 'delisted_trades.csv'),
        ('ARCHIVE_BACKUP_DIR', 'backup'),
    ]:
        monkeypatch.setattr(it, name, str(tmp / fname) if fname else str(tmp))
    monkeypatch.setattr(TradeImporter, '_compute_recent_paper_returns',
                        lambda self, df: df)
    return tmp


def _write_trade_stage(tmp_path, rows):
    """Schreibt trades_stage.csv, wie --dry_run sie hinterlaesst."""
    df = pd.DataFrame(rows)
    df['transaction_date'] = datetime.now().date().isoformat()
    df.to_csv(tmp_path / 'trades_stage.csv', index=False)


def _run_cli(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, 'argv', ['import_trades.py', *args])
    try:
        it.main()
    except SystemExit as exc:
        return exc.code
    return 0


def _schreibfehler_mitten_in(monkeypatch, filename):
    """to_csv auf ``filename`` (auch auf eine temporaere Datei dazu) schreibt
    nur bis in die Mitte der letzten Zeile und bricht dann ab, wie eine volle
    Platte. Gibt die Original-Methode zurueck, um den Fehler aufzuheben."""
    original = pd.DataFrame.to_csv

    def _to_csv(self, path_or_buf=None, *args, **kwargs):
        if not (isinstance(path_or_buf, str)
                and os.path.basename(path_or_buf).startswith(filename)):
            return original(self, path_or_buf, *args, **kwargs)
        mode = kwargs.pop('mode', 'w')
        kwargs.pop('encoding', None)
        text = original(self, None, *args, **kwargs)
        start = text.rstrip('\n').rfind('\n') + 1
        with open(path_or_buf, mode, encoding='utf-8') as f:
            f.write(text[:start + (len(text) - start) // 2])
        raise OSError('Platte voll')

    monkeypatch.setattr(pd.DataFrame, 'to_csv', _to_csv)
    return original


class TestSave:
    def _provide_stage(self, tmp_path):
        today = datetime.now().date().isoformat()
        _write_trade_stage(tmp_path, [
            {'trade_id': 't1', 'trade_score': 10,
             'trade_volume_usd': 1000, 'trade_warnings': ''},
            {'trade_id': 't2', 'trade_score': 20,
             'trade_volume_usd': 2000, 'trade_warnings': ''},
        ])
        _write_stage_files(
            tmp_path,
            branch1_events=[
                make_event(trade_id='t1', triggering_date=today),
                make_event(trade_id='t2', signal_event_id='sig_bbb',
                           triggering_date=today),
            ],
            sequence_events=[
                make_event(trade_id='t1', signal_event_id='sig_ccc',
                           signal_type='contrarian', triggering_date=today),
            ],
        )

    def test_zweites_save_derselben_stage_erzeugt_keine_doppelten_events(
            self, save_paths, monkeypatch):
        self._provide_stage(save_paths)
        assert _run_cli(monkeypatch, '--save') == 0
        # Der Save raeumt Stage-CSV und Event-Stage-Dateien auf.
        assert not (save_paths / 'trades_stage.csv').exists()
        assert not (save_paths / 'branch1_events_stage.csv').exists()
        assert not (save_paths / 'sequence_events_stage.csv').exists()

        self._provide_stage(save_paths)
        assert _run_cli(monkeypatch, '--save') == 0

        events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        keys = list(zip(events['signal_event_id'].astype(str),
                        events['trade_id'].astype(str)))
        expected = {('sig_aaa_0001', 't1'), ('sig_bbb', 't2'), ('sig_ccc', 't1')}
        assert set(keys) == expected
        assert all(keys.count(k) == 1 for k in expected)
        master = pd.read_csv(it.MASTER_COMPLETE_CSV_PATH)
        assert sorted(master['trade_id']) == ['t1', 't2']

    def test_save_nimmt_scores_aus_stage_nicht_aus_altem_master(
            self, save_paths, monkeypatch):
        # Alter Master mit absichtlich abweichenden Scores.
        _write_trade_scores_csv(save_paths, [
            {'trade_id': 't1', 'trade_score': 1,
             'trade_volume_usd': 1, 'trade_warnings': ''},
            {'trade_id': 't2', 'trade_score': 1,
             'trade_volume_usd': 1, 'trade_warnings': ''},
        ])
        self._provide_stage(save_paths)
        assert _run_cli(monkeypatch, '--save') == 0

        assets = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        assert assets.iloc[0]['score_max'] == 20
        assert assets.iloc[0]['score_total'] == 30
        assert assets.iloc[0]['total_volume_usd'] == 3000
        members = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        assert members.iloc[0]['score_max'] == 20
        assert members.iloc[0]['score_total'] == 30

    def test_save_ohne_stage_bricht_ab(self, save_paths, monkeypatch):
        assert _run_cli(monkeypatch, '--save') == 1

    def test_save_mit_unvollstaendigem_satz_bricht_ab(
            self, save_paths, monkeypatch):
        self._provide_stage(save_paths)
        (save_paths / 'swarm_events_stage.csv').unlink()
        assert _run_cli(monkeypatch, '--save') == 1
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        assert not os.path.exists(it.MASTER_COMPLETE_CSV_PATH)

    def test_leere_event_stage_aktualisiert_scores_der_ansichten(
            self, save_paths, monkeypatch):
        # Event-Historie mit altem Score 11; der neue Lauf liefert keine
        # Events, aber Score 22 fuer denselben Trade.
        today = datetime.now().date().isoformat()
        TradeImporter()._append_suspicious_events(pd.DataFrame(
            [make_event(trade_id='t1', triggering_date=today)],
            columns=SIGNAL_EVENT_COLUMNS))
        _write_trade_scores_csv(save_paths, [
            {'trade_id': 't1', 'trade_score': 11,
             'trade_volume_usd': 1, 'trade_warnings': ''},
        ])
        _write_trade_stage(save_paths, [
            {'trade_id': 't1', 'trade_score': 22,
             'trade_volume_usd': 1, 'trade_warnings': ''},
        ])
        _write_stage_files(save_paths)  # alle leer
        assert _run_cli(monkeypatch, '--save') == 0

        assert pd.read_csv(it.SUSPICIOUS_ASSETS_PATH).iloc[0]['score_max'] == 22
        assert pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH).iloc[0]['score_max'] == 22

    def test_abbruch_im_kurs_refresh_laesst_alles_unveraendert(
            self, save_paths, monkeypatch):
        self._provide_stage(save_paths)

        def _refresh_bricht_ab(self, df):
            raise RuntimeError('Netz ROT')

        monkeypatch.setattr(TradeImporter, '_compute_recent_paper_returns',
                            _refresh_bricht_ab)
        assert _run_cli(monkeypatch, '--save') == 1
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        assert not os.path.exists(it.MASTER_COMPLETE_CSV_PATH)
        for name in ('trades_stage.csv', 'branch1_events_stage.csv',
                     'sequence_events_stage.csv', 'swarm_events_stage.csv'):
            assert (save_paths / name).exists(), name

        # Der liegengebliebene Satz laesst sich danach vollstaendig uebernehmen.
        monkeypatch.setattr(TradeImporter, '_compute_recent_paper_returns',
                            lambda self, df: df)
        assert _run_cli(monkeypatch, '--save') == 0
        assert len(pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)) == 3

    def test_abgebrochener_event_append_laesst_archiv_unveraendert(
            self, save_paths, monkeypatch):
        TradeImporter()._append_suspicious_events(pd.DataFrame(
            [make_event(trade_id='t0', signal_event_id='sig_alt')],
            columns=SIGNAL_EVENT_COLUMNS))
        archiv_vorher = (save_paths / 'suspicious_events.csv').read_bytes()
        self._provide_stage(save_paths)

        original = _schreibfehler_mitten_in(monkeypatch, 'suspicious_events.csv')
        assert _run_cli(monkeypatch, '--save') == 1
        assert (save_paths / 'suspicious_events.csv').read_bytes() == archiv_vorher
        assert not (save_paths / 'suspicious_events.csv.tmp').exists()

        # Der erneute Save uebernimmt den liegengebliebenen Satz vollstaendig.
        monkeypatch.setattr(pd.DataFrame, 'to_csv', original)
        assert _run_cli(monkeypatch, '--save') == 0
        events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
        keys = _event_keys()
        expected = {('sig_alt', 't0'), ('sig_aaa_0001', 't1'),
                    ('sig_bbb', 't2'), ('sig_ccc', 't1')}
        assert set(keys) == expected and len(keys) == len(expected)
        assert events['signal_type'].notna().all()

    def test_abbruch_in_der_promotion_erhaelt_unmatched_bericht(
            self, save_paths, monkeypatch):
        self._provide_stage(save_paths)
        unmatched = pd.DataFrame([{'name': 'Doe, Jane', 'count': 1}])
        unmatched.to_csv(save_paths / 'unmatched_names_stage.csv', index=False)
        pd.DataFrame([{'trade_id': 't1'}]).to_csv(
            save_paths / 'delisted_trades_stage.csv', index=False)

        original = it.shutil.copy2

        def _copy2(src, dst, *args, **kwargs):
            if dst == it.DELISTED_TRADES_PATH:
                raise OSError('Platte voll')
            return original(src, dst, *args, **kwargs)

        monkeypatch.setattr(it.shutil, 'copy2', _copy2)
        assert _run_cli(monkeypatch, '--save') == 1
        monkeypatch.setattr(it.shutil, 'copy2', original)
        assert _run_cli(monkeypatch, '--save') == 0

        pd.testing.assert_frame_equal(pd.read_csv(it.UNMATCHED_PATH), unmatched)
        assert os.path.exists(it.DELISTED_TRADES_PATH)
        for name in ('unmatched_names_stage.csv', 'delisted_trades_stage.csv'):
            assert not (save_paths / name).exists(), name


# ---------- --dry_run -> --save (ganzer Lauf, externe Schnittstellen isoliert)

class _FakeReference:
    """Ersetzt ReferenceData: alle Trades gematcht, ohne Ausschuesse."""

    def assign(self, df):
        df = df.copy()
        df['Member_Congress'] = 119
        df['Member_Committees_Full'] = ''
        df['Member_Committee_Status'] = it.STATUS_MATCHED
        df['Member_ExOfficio'] = ''
        df['Member_Chamber'] = df['chamber']
        return df


class _FakeNormalizer:
    """Ersetzt OpenFIGI."""

    def __init__(self, **kwargs):
        pass

    def normalize_batch(self, classifications):
        return [NormalizedTicker(
            classification=c, figi=None, ticker_figi=c.ticker_base,
            asset_name_figi='APPLE INC', security_type_figi='Common Stock',
            market_sector_figi='Equity', share_class_figi=None,
            normalization_errors=[]) for c in classifications]


class _FakeIssuerMapper:
    """Ersetzt SEC EDGAR."""

    def resolve_issuer_group_key(self, row):
        return f"TICKER:{row['issuer_ticker']}"


class _FakeSectorEnricher:
    """Ersetzt yfinance-Sektoren."""

    def __init__(self, **kwargs):
        pass

    def enrich_batch(self, normalized, transaction_dates=None):
        return [SimpleNamespace(sector_yf='Technology',
                                industry_yf='Consumer Electronics',
                                quote_type_yf='EQUITY') for _ in normalized]


class _FakeVoteTag:
    """Ersetzt die Vote-Daten (bill_actions.json)."""

    def load_votes(self):
        return pd.DataFrame()


@pytest.fixture
def pipeline_paths(save_paths, monkeypatch):
    """Synthetische Quiver-Datei; OpenFIGI, EDGAR, yfinance, Kursreihen,
    Referenz- und Vote-Daten sind ersetzt. Zwei Abgeordnete kaufen AAPL
    innerhalb weniger Tage (Schwarm)."""
    tmp = save_paths
    today = datetime.now().date()
    pd.DataFrame([
        {'Name': f'Member {m}', 'Ticker': 'AAPL', 'Company': 'Apple Inc',
         'Traded': (today - timedelta(days=d)).isoformat(),
         'Filed': today.isoformat(), 'Transaction': 'Purchase',
         'Trade_Size_USD': '$1,001 - $15,000', 'Chamber': 'House',
         'BioGuideID': m, 'Party': 'D', 'State': 'CA'}
        for m, d in [('A000001', 3), ('B000002', 1)]
    ]).to_csv(tmp / 'quiver.csv', index=False)
    monkeypatch.setattr(it, 'QUIVER_TRADES_PATH', str(tmp / 'quiver.csv'))
    monkeypatch.setattr(it, 'IGNORED_TICKERS_PATH', str(tmp / 'ignored.csv'))
    monkeypatch.setattr(it, 'ReferenceData', _FakeReference)
    monkeypatch.setattr(it, 'TickerNormalizer', _FakeNormalizer)
    monkeypatch.setattr(it, 'IssuerGroupMapper', _FakeIssuerMapper)
    monkeypatch.setattr(it, 'TickerSectorEnricher', _FakeSectorEnricher)
    monkeypatch.setattr(it, 'VoteTimingTag', _FakeVoteTag)
    monkeypatch.setattr(it, 'compute_post_trade_returns', lambda df, **kw: df)
    monkeypatch.setattr(TradeImporter, '_apply_abnormal_returns',
                        lambda self, df: df)
    monkeypatch.setattr(TradeImporter, '_ensure_fresh_vote_data',
                        lambda self: None)
    return tmp


def _event_keys():
    events = pd.read_csv(it.SUSPICIOUS_EVENTS_PATH)
    return list(zip(events['signal_event_id'].astype(str),
                    events['trade_id'].astype(str)))


class TestDryRunSave:
    def test_dry_run_dann_save_uebernimmt_den_lauf(
            self, pipeline_paths, monkeypatch):
        tmp = pipeline_paths
        assert _run_cli(monkeypatch, '--dry_run') == 0

        # Der Dry-Run schreibt nur Stage-Dateien.
        for name in ('trades_stage.csv', 'branch1_events_stage.csv',
                     'sequence_events_stage.csv', 'swarm_events_stage.csv',
                     'unmatched_names_stage.csv'):
            assert (tmp / name).exists(), name
        assert not os.path.exists(it.MASTER_COMPLETE_CSV_PATH)
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)
        stage = pd.read_csv(tmp / 'trades_stage.csv')
        assert len(stage) == 2
        assert (stage['trade_score'] > 1).all()
        swarm = pd.read_csv(tmp / 'swarm_events_stage.csv')
        assert set(swarm['trade_id']) == set(stage['trade_id'])

        # Alter Master mit absichtlich abweichenden Scores.
        _write_trade_scores_csv(tmp, [
            {'trade_id': t, 'trade_score': 1, 'trade_volume_usd': 1,
             'trade_warnings': ''} for t in stage['trade_id']])

        assert _run_cli(monkeypatch, '--save') == 0
        master = pd.read_csv(it.MASTER_COMPLETE_CSV_PATH)
        assert sorted(master['trade_id']) == sorted(stage['trade_id'])
        assert set(swarm['trade_id']) <= {t for _, t in _event_keys()}
        assets = pd.read_csv(it.SUSPICIOUS_ASSETS_PATH)
        assert assets.iloc[0]['score_max'] == stage['trade_score'].max()
        members = pd.read_csv(it.SUSPICIOUS_MEMBERS_PATH)
        assert set(members['score_max']) == set(stage['trade_score'])
        for name in ('trades_stage.csv', 'branch1_events_stage.csv',
                     'sequence_events_stage.csv', 'swarm_events_stage.csv'):
            assert not (tmp / name).exists(), name

    def test_abgebrochener_dry_run_hinterlaesst_keinen_uebernehmbaren_satz(
            self, pipeline_paths, monkeypatch):
        # Lauf A vollstaendig, Lauf B bricht nach den Branch-1-Events ab.
        assert _run_cli(monkeypatch, '--dry_run') == 0

        def _bricht_ab(self, df):
            raise RuntimeError('Abbruch in Lauf B')

        monkeypatch.setattr(TradeImporter, '_apply_sequence_signals',
                            _bricht_ab)
        assert _run_cli(monkeypatch, '--dry_run') == 1
        assert (pipeline_paths / 'branch1_events_stage.csv').exists()
        assert not (pipeline_paths / 'trades_stage.csv').exists()

        assert _run_cli(monkeypatch, '--save') == 1
        assert not os.path.exists(it.MASTER_COMPLETE_CSV_PATH)
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)

    def test_schreibfehler_in_trades_stage_hinterlaesst_keinen_uebernehmbaren_satz(
            self, pipeline_paths, monkeypatch):
        original = _schreibfehler_mitten_in(monkeypatch, 'trades_stage.csv')
        assert _run_cli(monkeypatch, '--dry_run') == 1
        assert not (pipeline_paths / 'trades_stage.csv').exists()

        monkeypatch.setattr(pd.DataFrame, 'to_csv', original)
        assert _run_cli(monkeypatch, '--save') == 1
        assert not os.path.exists(it.MASTER_COMPLETE_CSV_PATH)
        assert not os.path.exists(it.SUSPICIOUS_EVENTS_PATH)


# ---------- H2: Asset-Gate schliesst hart aus (ganzer Lauf) ----------------

class _FakeNormalizerBonds(_FakeNormalizer):
    """Wie _FakeNormalizer; Anleihen (Name mit '%') bekommen den Typ bond."""

    def normalize_batch(self, classifications):
        return super().normalize_batch([
            replace(c, ticker_type=TickerType.BOND) if '%' in c.company_name
            else c for c in classifications])


class TestAssetGateHarterAusschluss:
    def test_ausgeschlossene_zeile_ohne_signal_score_und_wirkung(
            self, pipeline_paths, monkeypatch):
        """Apple-Anleihen teilen den Issuer mit der Apple-Aktie. Ohne Gate
        bildeten sie mit dem Aktienkauf von A einen Schwarm (B) und eine
        Sequenz (A) und bekaemen selbst Signale und Score."""
        tmp = pipeline_paths
        today = datetime.now().date()
        notes = 'Apple Inc 3.85% Notes due 2043'
        pd.DataFrame([
            {'Name': f'Member {m}', 'Ticker': 'AAPL', 'Company': company,
             'Traded': (today - timedelta(days=d)).isoformat(),
             'Filed': today.isoformat(), 'Transaction': 'Purchase',
             'Trade_Size_USD': size, 'Chamber': 'House',
             'BioGuideID': m, 'Party': 'D', 'State': 'CA'}
            for m, d, company, size in [
                ('A000001', 5, 'Apple Inc', '$1,001 - $15,000'),
                ('B000002', 4, notes, '$1,000,001 - $5,000,000'),
                ('A000001', 2, notes, '$1,000,001 - $5,000,000'),
            ]
        ]).to_csv(tmp / 'quiver.csv', index=False)
        monkeypatch.setattr(it, 'TickerNormalizer', _FakeNormalizerBonds)

        assert _run_cli(monkeypatch, '--dry_run') == 0

        stage = pd.read_csv(tmp / 'trades_stage.csv')
        assert len(stage) == 3
        excluded = stage[stage['asset_class_excluded']]
        assert set(excluded['ticker_type']) == {'bond'} and len(excluded) == 2
        assert (excluded['signal_count'] == 0).all()
        assert excluded['signals_triggered'].isna().all()
        assert (excluded['trade_score'] == 0).all()
        assert excluded['trade_score_breakdown'].isna().all()

        stock = stage[~stage['asset_class_excluded']].iloc[0]
        stock_signals = str(stock['signals_triggered']).split(';')
        for sig in ('swarm_buy', 'accumulative', 'contrarian'):
            assert sig not in stock_signals
        assert stock['trade_score'] == 0

        events = pd.concat(
            [pd.read_csv(tmp / name) for name in (
                'branch1_events_stage.csv', 'sequence_events_stage.csv',
                'swarm_events_stage.csv')], ignore_index=True)
        assert not set(events['trade_id']) & set(excluded['trade_id'])
        assert not pd.read_csv(tmp / 'sequence_events_stage.csv').shape[0]
        assert not pd.read_csv(tmp / 'swarm_events_stage.csv').shape[0]


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
        """Pipeline-Pfad: _inflight_trade_scores hat Vorrang vor
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
