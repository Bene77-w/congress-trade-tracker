"""Tests fuer den Deployment-Pfad: Snapshot-Bau + Session-Presets.

Beides betrifft nur den oeffentlichen Betrieb; die lokalen Pfade
(Master-CSV lesen, Presets in die Datei schreiben) bleiben unveraendert und
sind hier als Gegenprobe mit abgedeckt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app import scoring_presets as sp
from config import settings
from scripts.build_deploy_snapshot import _categorical_columns, build_snapshot


# ---------------------------------------------------------------------------
# Snapshot-Bau
# ---------------------------------------------------------------------------

def _master_frame() -> pd.DataFrame:
    """Mini-Master: eine Wiederholungsspalte, eine hochkardinale, eine Score-Spalte."""
    n = 50
    return pd.DataFrame({
        'trade_id': [f'id-{i}' for i in range(n)],
        'name': ['A. Member' if i % 2 else 'B. Member' for i in range(n)],
        'Insider_Sectors': ['["Finance"]' if i % 2 else '["Energy"]'
                            for i in range(n)],
        'signals_triggered': [f'sig-{i}' for i in range(n)],
        'transaction_date': pd.date_range('2025-01-01', periods=n).astype(str),
        'disclosure_date': pd.date_range('2025-02-01', periods=n).astype(str),
        'trade_score': range(n),
    })


def test_categorical_columns_picks_repeats_and_skips_score_columns():
    df = _master_frame()
    cols = _categorical_columns(df)
    # Wiederholungsspalten -> category
    assert 'name' in cols
    assert 'Insider_Sectors' in cols
    # Vom Score-Recompute ueberschrieben -> muss String bleiben, sonst
    # scheitert das Schreiben neuer Werte
    assert 'signals_triggered' not in cols
    # Numerisch -> kein Kandidat
    assert 'trade_score' not in cols


def test_build_snapshot_roundtrip(tmp_path):
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    target = tmp_path / 'deploy' / 'snap.parquet'
    manifest = tmp_path / 'deploy' / 'manifest.json'

    info = build_snapshot(source, target, manifest)

    assert target.exists() and manifest.exists()
    out = pd.read_parquet(target)
    assert len(out) == 50
    # Wiederholungsspalte als category gespeichert -> das ist der Speicherhebel
    assert isinstance(out['Insider_Sectors'].dtype, pd.CategoricalDtype)
    assert not isinstance(out['signals_triggered'].dtype, pd.CategoricalDtype)
    # Datumsspalten bleiben Zeitstempel (der Loader verlaesst sich darauf)
    assert pd.api.types.is_datetime64_any_dtype(out['transaction_date'])
    # Manifest traegt den Datenstand = juengstes Disclosure-Datum
    meta = json.loads(manifest.read_text(encoding='utf-8'))
    assert meta['rows'] == 50
    assert meta['data_as_of'] == '2025-03-22'
    assert info['memory_bytes_snapshot'] <= info['memory_bytes_plain']


def test_build_snapshot_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_snapshot(tmp_path / 'fehlt.csv', tmp_path / 'a.parquet',
                       tmp_path / 'b.json')


def test_build_snapshot_copies_price_cache(tmp_path):
    """Ohne Kurs-Cache blieben im Deployment alle Verlaufs-Charts leer."""
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    cache = tmp_path / 'price_history.parquet'
    pd.DataFrame({'ticker': ['AAPL'], 'date': ['2025-01-02'],
                  'close': [1.0]}).to_parquet(cache, index=False)

    info = build_snapshot(source, tmp_path / 'deploy' / 'snap.parquet',
                          tmp_path / 'deploy' / 'manifest.json',
                          price_cache=cache)

    assert (tmp_path / 'deploy' / 'price_history.parquet').is_file()
    assert info['price_cache_bytes'] > 0


def _reference_copy(target_dir):
    """Die eingecheckten Referenztabellen (plus members.csv) in einen Ordner."""
    import shutil
    target_dir.mkdir(parents=True)
    for name in ('congresses', 'committees', 'committee_memberships',
                 'committee_industries', 'members'):
        shutil.copy(settings.BASE_DIR / settings.REFERENCE_DATA_PATH / f'{name}.csv',
                    target_dir)
    return target_dir


def test_build_snapshot_copies_reference_tables(tmp_path):
    """Die App liest im Deployment die Ausschuss-Industries aus data/deploy/;
    data/reference/ kommt nicht auf den Server."""
    from utils.reference_data import TABLES, ReferenceData
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    deploy = tmp_path / 'deploy'

    info = build_snapshot(source, deploy / 'snap.parquet', deploy / 'm.json',
                          reference_dir=_reference_copy(tmp_path / 'ref'))

    copied = deploy / Path(settings.DEPLOY_REFERENCE_PATH).name
    assert sorted(p.stem for p in copied.iterdir()) == sorted(TABLES)
    assert info['reference_tables'] == len(TABLES)
    assert ReferenceData(copied).mapper('119').get_all_sectors_for_committee(
        'Senate', 'Committee on the Judiciary')


def test_pipeline_reads_current_reference_tables_despite_snapshot(tmp_path, monkeypatch):
    """Ein Vollauf darf nicht die Tabellen eines alten Snapshots lesen: der
    Snapshot-Ordner gilt nur fuer die App."""
    from scripts.import_trades import TradeImporter
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    current = _reference_copy(tmp_path / settings.REFERENCE_DATA_PATH)
    deploy = tmp_path / Path(settings.DEPLOY_REFERENCE_PATH).parent
    build_snapshot(source, deploy / 'snap.parquet', deploy / 'm.json',
                   reference_dir=current)
    # Snapshot weicht ab: ohne den 118. Kongress
    snap_congresses = tmp_path / settings.DEPLOY_REFERENCE_PATH / 'congresses.csv'
    df = pd.read_csv(snap_congresses, dtype=str)
    df[df.congress != '118'].to_csv(snap_congresses, index=False)
    monkeypatch.setattr(settings, 'BASE_DIR', tmp_path)

    trades = pd.DataFrame({'name': ['Ro Khanna'], 'bio_guide_id': ['K000389'],
                           'chamber': ['House'],
                           'transaction_date': pd.to_datetime(['2023-06-01'])})
    enriched, _ = TradeImporter().enrich_member_data(trades)

    assert list(enriched['Member_Congress']) == ['118']


def test_build_snapshot_failure_keeps_previous_snapshot(tmp_path):
    """Scheitert ein Bau (hier: fehlende Referenztabelle), bleiben Parquet,
    Referenztabellen und Manifest des vorigen Baus zusammen stehen."""
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    reference = _reference_copy(tmp_path / 'ref')
    deploy = tmp_path / 'deploy'
    paths = (deploy / 'snap.parquet', deploy / 'm.json')
    build_snapshot(source, *paths, reference_dir=reference)
    before = {p: p.read_bytes() for p in deploy.rglob('*') if p.is_file()}

    _master_frame().head(10).to_csv(source, index=False)
    (reference / 'committee_industries.csv').unlink()
    with pytest.raises(FileNotFoundError):
        build_snapshot(source, *paths, reference_dir=reference)

    assert {p: p.read_bytes() for p in deploy.rglob('*') if p.is_file()} == before


def _load_trades(root: Path):
    """Die ECHTE ``load_trades`` aus ``app/streamlit_app.py`` (Skript, nicht
    importierbar; Quelle per ``ast``, ohne Cache-Dekorator) mit Pfaden unter
    ``root``."""
    import ast
    from types import SimpleNamespace
    app = Path(__file__).resolve().parent.parent / 'app' / 'streamlit_app.py'
    src = app.read_text(encoding='utf-8')
    node = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == 'load_trades')
    ns = {'pd': pd, 'Path': Path, 'settings': settings, 'ROOT': root,
          'st': SimpleNamespace(warning=lambda _msg: None),
          'TRADES_CSV': root / 'master.csv',
          'DEPLOY_SNAPSHOT': root / settings.DEPLOY_SNAPSHOT_PATH,
          'DEPLOY_REFERENCE': root / settings.DEPLOY_REFERENCE_PATH}
    exec(ast.get_source_segment(src, node), ns)  # noqa: S102 - Testcode
    return ns['load_trades']


def test_app_reads_reference_tables_of_loaded_trades(tmp_path):
    """Liest die App den Master-CSV, gehoeren dazu die aktuellen
    Referenztabellen, auch wenn ein alter Snapshot-Ordner liegt; liest sie den
    Snapshot, die des Snapshots."""
    master = _master_frame()
    for col in set(settings.TRADES_DATE_COLUMNS) - set(master.columns):
        master[col] = '2025-01-01'
    master.to_csv(tmp_path / 'master.csv', index=False)
    (tmp_path / settings.DEPLOY_REFERENCE_PATH).mkdir(parents=True)
    load_trades = _load_trades(tmp_path)

    _, reference = load_trades()
    assert reference == tmp_path / settings.REFERENCE_DATA_PATH

    build_snapshot(tmp_path / 'master.csv',
                   tmp_path / settings.DEPLOY_SNAPSHOT_PATH, tmp_path / 'm.json')
    _, reference = load_trades()
    assert reference == tmp_path / settings.DEPLOY_REFERENCE_PATH


def test_build_snapshot_without_price_cache(tmp_path):
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    info = build_snapshot(source, tmp_path / 'd' / 's.parquet',
                          tmp_path / 'd' / 'm.json', price_cache=None)
    assert info['price_cache_bytes'] is None


def test_build_snapshot_without_disclosure_date(tmp_path):
    """Fehlt die Spalte, bleibt der Datenstand leer statt zu scheitern."""
    df = _master_frame().drop(columns=['disclosure_date'])
    source = tmp_path / 'master.csv'
    df.to_csv(source, index=False)
    info = build_snapshot(source, tmp_path / 'd' / 's.parquet',
                          tmp_path / 'd' / 'm.json')
    assert info['data_as_of'] is None


def test_build_snapshot_leaves_no_temp_files(tmp_path):
    """Atomares Schreiben: keine .tmp-Reste nach einem erfolgreichen Lauf."""
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    out = tmp_path / 'deploy'
    build_snapshot(source, out / 's.parquet', out / 'm.json')
    assert not list(out.glob('*.tmp'))


def test_category_exclude_covers_recompute_columns():
    """Die vom Score-Recompute ersetzten Spalten duerfen nie category werden."""
    from app.score_recompute import recompute_subset
    import inspect
    written = {line.split("out['")[1].split("']")[0]
               for line in inspect.getsource(recompute_subset).splitlines()
               if "out['" in line and '] =' in line}
    excluded = set(settings.DEPLOY_SNAPSHOT_CATEGORY_EXCLUDE)
    # trade_score ist numerisch und damit ohnehin kein category-Kandidat
    assert written - {'trade_score'} <= excluded


def test_snapshot_matches_csv_path(tmp_path):
    """Kernrisiko des Deployments: Snapshot- und CSV-Pfad muessen dieselben
    Werte und Typen liefern. Sonst zeigt die oeffentliche Instanz still
    andere Zahlen als die lokale."""
    source = tmp_path / 'master.csv'
    _master_frame().to_csv(source, index=False)
    target = tmp_path / 'deploy' / 'snap.parquet'
    build_snapshot(source, target, tmp_path / 'deploy' / 'm.json')

    via_csv = pd.read_csv(
        source,
        parse_dates=list(settings.TRADES_DATE_COLUMNS),
        dtype={c: 'string' for c in settings.TRADES_STRING_COLUMNS
               if c in _master_frame().columns},
        low_memory=False,
    )
    via_snapshot = pd.read_parquet(target)

    assert list(via_csv.columns) == list(via_snapshot.columns)
    for col in via_csv.columns:
        left = via_csv[col]
        right = via_snapshot[col]
        if isinstance(right.dtype, pd.CategoricalDtype):
            right = right.astype(left.dtype)
        pd.testing.assert_series_equal(
            left, right, check_dtype=False, check_categorical=False,
        )


# ---------------------------------------------------------------------------
# Presets im Session-Store (oeffentlicher Modus)
# ---------------------------------------------------------------------------

def test_session_store_isolates_presets_from_file(tmp_path, monkeypatch):
    """Mit store schreibt save_preset in das Dict, nicht in die Datei."""
    presets_file = tmp_path / 'scoring_presets.json'
    monkeypatch.setattr(sp, 'PRESETS_PATH', presets_file)

    store: dict = {}
    sp.save_preset('Mein Preset', {'weights': {'committee': 5}}, store)

    assert 'Mein Preset' in store
    assert not presets_file.exists(), 'Datei darf im Session-Modus unberuehrt bleiben'

    loaded = sp.load_presets(store)
    assert loaded['Mein Preset'] == {'weights': {'committee': 5}}
    # Der synthetische Default kommt weiterhin dazu
    assert sp.DEFAULT_PRESET_NAME in loaded

    sp.delete_preset('Mein Preset', store)
    assert 'Mein Preset' not in store


def test_session_stores_do_not_leak_between_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, 'PRESETS_PATH', tmp_path / 'p.json')
    besucher_a: dict = {}
    besucher_b: dict = {}
    sp.save_preset('Nur A', {'weights': {}}, besucher_a)
    assert 'Nur A' not in sp.load_presets(besucher_b)


def test_file_persistence_unchanged_without_store(tmp_path, monkeypatch):
    """Gegenprobe: ohne store bleibt es beim bisherigen Datei-Verhalten."""
    presets_file = tmp_path / 'scoring_presets.json'
    monkeypatch.setattr(sp, 'PRESETS_PATH', presets_file)

    sp.save_preset('Lokal', {'weights': {'swarm_buy': 3}})
    assert presets_file.exists()
    assert 'Lokal' in sp.load_presets()

    sp.delete_preset('Lokal')
    assert 'Lokal' not in sp.load_presets()


def test_default_preset_stays_reserved_in_store_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, 'PRESETS_PATH', tmp_path / 'p.json')
    store: dict = {}
    with pytest.raises(ValueError):
        sp.save_preset(sp.DEFAULT_PRESET_NAME, {}, store)
    with pytest.raises(ValueError):
        sp.delete_preset(sp.DEFAULT_PRESET_NAME, store)


# ---------------------------------------------------------------------------
# Passwort-Gate
# ---------------------------------------------------------------------------
# Diese Tests fahren die echte App per AppTest. Das ist billig, weil das Gate
# vor dem Datenladen greift (``st.stop()``): der teure Snapshot-/CSV-Load wird
# nie erreicht. Deshalb wird hier bewusst nur der gesperrte Zustand geprueft.

def _app(monkeypatch, password: str = 'geheim'):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(settings, 'APP_AUTH_ENABLED', True)
    monkeypatch.setattr(settings, 'APP_PASSWORD', password)
    root = __import__('pathlib').Path(__file__).resolve().parent.parent
    return AppTest.from_file(str(root / 'app' / 'streamlit_app.py'),
                             default_timeout=60)


def test_gate_blocks_without_password(monkeypatch):
    at = _app(monkeypatch)
    at.run()
    assert not at.exception
    assert len(at.text_input) == 1, 'nur das Passwortfeld darf sichtbar sein'
    assert len(at.tabs) == 0, 'ohne Anmeldung duerfen keine Daten erscheinen'


def test_gate_rejects_wrong_password(monkeypatch):
    at = _app(monkeypatch)
    at.run()
    at.text_input[0].input('falsch')
    at.button[0].click().run()
    assert not at.exception
    assert any('Falsches Passwort' in e.value for e in at.error)
    assert at.session_state['auth_attempts'] == 1


def test_gate_handles_non_ascii_password(monkeypatch):
    """Umlaute im Passwort duerfen die Pruefung nicht sprengen.

    ``hmac.compare_digest`` wirft bei str-Argumenten mit Nicht-ASCII einen
    TypeError — mit ``showErrorDetails = "none"`` waere die App dann fuer alle
    unbenutzbar, ohne erkennbaren Grund.
    """
    at = _app(monkeypatch, password='Schlüssel-öäü')
    at.run()
    at.text_input[0].input('Schlüssel-falsch')
    at.button[0].click().run()
    assert not at.exception, 'Nicht-ASCII-Passwort darf keine Exception werfen'
    assert any('Falsches Passwort' in e.value for e in at.error)


def test_gate_locks_after_max_attempts(monkeypatch):
    """Nach dem letzten erlaubten Fehlversuch greift die Sperre beim naechsten
    Aufruf des Formulars — der Nutzer sieht zuerst 'Falsches Passwort', dann
    die Sperre."""
    at = _app(monkeypatch)
    monkeypatch.setattr(settings, 'APP_AUTH_MAX_ATTEMPTS', 2)
    at.run()
    for _ in range(2):
        at.text_input[0].input('falsch')
        at.button[0].click().run()
    assert at.session_state['auth_attempts'] == 2
    at.run()
    assert not at.exception
    assert any('Zu viele Fehlversuche' in e.value for e in at.error)
    assert len(at.text_input) == 0, 'Formular muss verschwinden'


def test_gate_reports_missing_password(monkeypatch):
    at = _app(monkeypatch, password='')
    at.run()
    assert not at.exception
    assert any('kein Passwort gesetzt' in e.value for e in at.error)
    assert len(at.tabs) == 0


# ---------------------------------------------------------------------------
# Kurs-Cache im Deployment
# ---------------------------------------------------------------------------

def _touch_parquet(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({'ticker': ['AAPL'], 'date': ['2025-01-02'],
                  'close': [1.0]}).to_parquet(path, index=False)


def test_price_cache_prefers_normal_location(tmp_path, monkeypatch):
    from app import member_detail as md
    monkeypatch.setattr(md, 'ROOT', tmp_path)
    _touch_parquet(tmp_path / settings.POST_TRADE_PRICE_CACHE)
    _touch_parquet(tmp_path / settings.DEPLOY_PRICE_CACHE_PATH)
    assert md.price_cache_path() == str(tmp_path / settings.POST_TRADE_PRICE_CACHE)


def test_price_cache_falls_back_to_deploy_folder(tmp_path, monkeypatch):
    """Auf dem Server gibt es data/cache/ nicht — ohne Fallback blieben alle
    Kursverlauf-Charts leer, obwohl die Datei mitgeliefert wurde."""
    from app import member_detail as md
    monkeypatch.setattr(md, 'ROOT', tmp_path)
    _touch_parquet(tmp_path / settings.DEPLOY_PRICE_CACHE_PATH)
    assert md.price_cache_path() == str(tmp_path / settings.DEPLOY_PRICE_CACHE_PATH)


def test_price_cache_missing_everywhere(tmp_path, monkeypatch):
    """Fehlt der Cache ueberall, bleibt es beim Normalpfad — der Loader
    liefert dann einen leeren Frame statt zu scheitern."""
    from app import member_detail as md
    monkeypatch.setattr(md, 'ROOT', tmp_path)
    assert md.price_cache_path() == str(tmp_path / settings.POST_TRADE_PRICE_CACHE)
