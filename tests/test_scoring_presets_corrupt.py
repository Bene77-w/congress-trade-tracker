"""Tests fuer app/scoring_presets.py — Umgang mit korrupter Presets-Datei (R1).

Eine nicht parsebare JSON-Datei darf beim Laden NICHT still zum Totalverlust
fuehren: das Original wird nach ``<pfad>.bak`` gesichert, ein Warn-Flag gesetzt
(abholbar via ``consume_corrupt_warning``), und ein nachfolgendes save_preset
darf die ``.bak`` nicht zerstoeren.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.scoring_presets as sp  # noqa: E402


def _point_to(tmp_path: Path, monkeypatch) -> Path:
    """Lenkt PRESETS_PATH auf eine Datei in tmp_path und resettet das Flag."""
    p = tmp_path / 'scoring_presets.json'
    monkeypatch.setattr(sp, 'PRESETS_PATH', p)
    monkeypatch.setattr(sp, '_last_load_corrupt', False)
    return p


def test_corrupt_json_backed_up_on_load(tmp_path, monkeypatch):
    p = _point_to(tmp_path, monkeypatch)
    corrupt = '{ this is not valid json ,,, '
    p.write_text(corrupt, encoding='utf-8')

    presets = sp.load_presets()

    # Load liefert nur den synthetischen Default (korrupte Datei -> {}).
    assert set(presets.keys()) == {sp.DEFAULT_PRESET_NAME}

    # .bak existiert mit dem Originalinhalt.
    bak = p.with_suffix(p.suffix + '.bak')
    assert bak.exists()
    assert bak.read_text(encoding='utf-8') == corrupt


def test_corrupt_warning_is_consumable(tmp_path, monkeypatch):
    p = _point_to(tmp_path, monkeypatch)
    p.write_text('{ broken', encoding='utf-8')

    sp.load_presets()
    msg = sp.consume_corrupt_warning()
    assert msg is not None
    # Nur einmal abholbar.
    assert sp.consume_corrupt_warning() is None


def test_no_warning_on_valid_file(tmp_path, monkeypatch):
    p = _point_to(tmp_path, monkeypatch)
    p.write_text(json.dumps({'MyPreset': {'weights': {}}}), encoding='utf-8')

    sp.load_presets()
    assert sp.consume_corrupt_warning() is None


def test_save_after_corrupt_preserves_bak(tmp_path, monkeypatch):
    p = _point_to(tmp_path, monkeypatch)
    corrupt = '{ irreparabel ,,, '
    p.write_text(corrupt, encoding='utf-8')

    # Erster Load sichert die .bak.
    sp.load_presets()
    bak = p.with_suffix(p.suffix + '.bak')
    assert bak.exists()

    # Nachfolgendes save_preset ueberschreibt die Hauptdatei (jetzt valides
    # JSON), darf aber die .bak nicht zerstoeren.
    sp.save_preset('Neu', sp.default_payload())

    assert bak.exists()
    assert bak.read_text(encoding='utf-8') == corrupt
    # Hauptdatei ist wieder valide und enthaelt das neue Preset.
    saved = json.loads(p.read_text(encoding='utf-8'))
    assert 'Neu' in saved
