"""Persistenz benannter Scoring-Konstellationen fuer das Streamlit-Dashboard.

Ein Preset speichert alle vom User editierbaren Scoring-Parameter (Gewichte +
Paradox-Damper + Trade-Volume-Tiers + Ex-Officio-Bonus). Format: JSON in
``data/config/scoring_presets.json``. Ein Preset namens ``Default`` wird beim
ersten Laden synthetisch aus ``config.settings`` erzeugt, damit der User auch
ohne vorhandene Datei umschalten kann.

Payload-Schema pro Preset::

    {
        "weights": {"committee": 10, "swarm_buy": 20, "trade_volume": 16, ...},
        "paradox_damper": 0.0,
        "trade_volume_enabled": true,
        "trade_volume_tiers": [[50000, 2], [100000, 4], ..., [null, 16]],
        "trade_volume_trigger": 15000,
        "ex_officio_bonus": 10
    }

JSON erlaubt kein ``float('inf')``; daher wird die Obergrenze des letzten
Tiers als ``null`` serialisiert und beim Laden zurueck zu ``float('inf')``
konvertiert.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from config import settings as _settings


ROOT = Path(__file__).resolve().parent.parent
PRESETS_PATH = ROOT / 'data' / 'config' / 'scoring_presets.json'

DEFAULT_PRESET_NAME = 'Default'


def _tiers_to_json(tiers: Any) -> list[list]:
    """Serialisiert Tier-Liste: float('inf') -> None (JSON-kompatibel)."""
    out = []
    for item in (tiers or []):
        try:
            upper, score = item
        except (TypeError, ValueError):
            continue
        upper_f = float(upper)
        upper_serial = None if math.isinf(upper_f) else upper_f
        out.append([upper_serial, int(score)])
    return out


def _tiers_from_json(raw: Any) -> list[tuple[float, int]]:
    """Liest Tier-Liste aus JSON: None -> float('inf')."""
    out: list[tuple[float, int]] = []
    for item in (raw or []):
        try:
            upper, score = item
        except (TypeError, ValueError):
            continue
        upper_val = float('inf') if upper is None else float(upper)
        out.append((upper_val, int(score)))
    return out


def default_payload() -> dict[str, Any]:
    """Snapshot der aktuellen Defaults aus ``config.settings``."""
    return {
        'weights': dict(getattr(_settings, 'SCORING_WEIGHTS', {}) or {}),
        'paradox_damper': float(getattr(_settings, 'SCORING_PARADOX_DAMPER', 0.0)),
        'trade_volume_enabled': bool(getattr(
            _settings, 'SCORING_TRADE_VOLUME_ENABLED', True
        )),
        'trade_volume_tiers': _tiers_to_json(getattr(
            _settings, 'SCORING_TRADE_VOLUME_TIERS', []
        )),
        'trade_volume_trigger': float(getattr(
            _settings, 'SCORING_TRADE_VOLUME_TRIGGER_FLOOR', 0.0
        )),
        'ex_officio_role_bonuses': {
            str(k): float(v)
            for k, v in (getattr(
                _settings, 'SCORING_EX_OFFICIO_ROLE_BONUSES', {}
            ) or {}).items()
        },
        'ex_officio_committee_global': bool(getattr(
            _settings, 'SCORING_EX_OFFICIO_COMMITTEE_GLOBAL', True
        )),
    }


def _read_file() -> dict[str, dict[str, Any]]:
    if not PRESETS_PATH.exists():
        return {}
    try:
        with PRESETS_PATH.open('r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _write_file(data: dict[str, dict[str, Any]]) -> None:
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PRESETS_PATH.open('w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def load_presets() -> dict[str, dict[str, Any]]:
    """Liefert alle Presets inkl. synthetischem ``Default``-Eintrag."""
    presets = _read_file()
    presets.setdefault(DEFAULT_PRESET_NAME, default_payload())
    return presets


def save_preset(name: str, payload: dict[str, Any]) -> None:
    """Speichert ``payload`` unter ``name``. ``Default`` ist reserviert."""
    name = (name or '').strip()
    if not name:
        raise ValueError('Preset-Name darf nicht leer sein.')
    if name == DEFAULT_PRESET_NAME:
        raise ValueError(
            f"Preset-Name '{DEFAULT_PRESET_NAME}' ist reserviert."
        )
    data = _read_file()
    data[name] = payload
    _write_file(data)


def delete_preset(name: str) -> None:
    """Loescht ein Preset. ``Default`` ist reserviert und nicht loeschbar."""
    if name == DEFAULT_PRESET_NAME:
        raise ValueError(
            f"Preset '{DEFAULT_PRESET_NAME}' kann nicht geloescht werden."
        )
    data = _read_file()
    if name in data:
        del data[name]
        _write_file(data)
