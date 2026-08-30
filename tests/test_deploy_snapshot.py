"""Tests fuer den Deployment-Pfad: Snapshot-Bau + Session-Presets.

Beides betrifft nur den oeffentlichen Betrieb; die lokalen Pfade
(Master-CSV lesen, Presets in die Datei schreiben) bleiben unveraendert und
sind hier als Gegenprobe mit abgedeckt.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from app import scoring_presets as sp
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
    with pytest.raises(SystemExit):
        build_snapshot(tmp_path / 'fehlt.csv', tmp_path / 'a.parquet',
                       tmp_path / 'b.json')


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
