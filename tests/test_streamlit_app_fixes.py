"""Tests fuer die Streamlit-App-Fixes O1 (Recompute-Ableitung) + Z1b (Schwelle).

O1: Der Trades-/Assets-/Members-Basis-Frame ``prefilt`` wird nicht mehr separat
rekomputet, sondern aus der horizont-filter-freien ``corr_base`` per
Horizont-Maske ABGELEITET. Weil ``recompute_subset`` pro Zeile deterministisch
ist und ``first_close_offset_days`` unberuehrt laesst, muss das zeilenidentisch
zum frueheren zweiten Recompute auf der vorgefilterten Teilmenge sein. Genau das
prueft ``test_o1_derivation_equivalence`` mit der ECHTEN ``recompute_subset``.

Z1b: ``_related_return_style`` liest die Faerbungs-Schwelle aus
``settings.DETAIL_RELATED_RETURN_COLOR_PCT`` statt hartkodiert ``10``. Die
Funktion lebt im Modul-Top-Level von ``app/streamlit_app.py`` (das beim Import
die gesamte App rendert und daher nicht standalone importierbar ist — vgl.
``tests/test_member_detail_recompute_apptest.py``). Deshalb wird die ECHTE
Funktionsquelle per ``ast`` aus der Datei extrahiert und mit einem kontrollierten
``settings``-Stub ausgefuehrt (monkeypatch der Schwelle).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.score_recompute import recompute_subset  # noqa: E402
from config import settings as _settings  # noqa: E402

APP_PATH = ROOT / 'app' / 'streamlit_app.py'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _trade_row(signals: str, offset, vol: int = 0) -> dict:
    """Minimal-Zeile fuer recompute_subset + Horizont-Maske."""
    return {
        'signals_triggered': signals,
        'ex_officio_role_keys': '',
        'trade_amount_max_usd': vol,
        'first_close_offset_days': offset,
        'trade_score': -1,          # Platzhalter, wird ueberschrieben
        'trade_score_breakdown': '',
        'trade_warnings': '',
    }


def _horizon_mask(df: pd.DataFrame, primary_horizon: int) -> pd.Series:
    """Repliziert exakt den Horizont-Schritt aus ``apply_prefilter``:
    ``first_close_offset_days`` notna & <= primary_horizon.
    """
    off = df['first_close_offset_days']
    return off.notna() & (off <= primary_horizon)


def _load_related_return_style():
    """Extrahiert die ECHTE ``_related_return_style``-Quelle aus streamlit_app.py
    und liefert eine ausfuehrbare Funktion, deren Globals ``pd`` + ein
    injizierbarer ``settings``-Stub sind (damit die Schwelle monkeypatchbar ist,
    ohne die volle App zu importieren)."""
    src = APP_PATH.read_text(encoding='utf-8')
    tree = ast.parse(src)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == '_related_return_style'
    )
    func_src = ast.get_source_segment(src, node)

    def _make(threshold: float):
        ns: dict = {'pd': pd,
                    'settings': SimpleNamespace(
                        DETAIL_RELATED_RETURN_COLOR_PCT=threshold)}
        exec(func_src, ns)  # noqa: S102 - kontrollierter Namespace, Testcode
        return ns['_related_return_style']

    return _make


# ---------------------------------------------------------------------------
# O1 — Ableitungs-Aequivalenz
# ---------------------------------------------------------------------------
def test_o1_derivation_equivalence():
    """recompute(corr_base)[horizont] == recompute(corr_base[horizont]).

    Bildet die O1-Umstellung nach: statt zweimal zu rekomputen, wird prefilt aus
    dem rekomputeten corr_base per Horizont-Maske abgeleitet. Ergebnis muss
    zeilenidentisch (Index + Score-Spalten) zum separaten Recompute sein.
    """
    primary_horizon = 90
    df = pd.DataFrame([
        _trade_row('committee_full', offset=10),    # <= 90 -> sichtbar
        _trade_row('swarm_buy', offset=90),         # == 90 -> sichtbar (Grenze)
        _trade_row('committee_full', offset=200),   # > 90  -> ausgeblendet
        _trade_row('swarm_buy', offset=None),       # held  -> ausgeblendet
        _trade_row('committee_full', offset=45),    # <= 90 -> sichtbar
    ])

    overrides = {'committee_full': 77, 'swarm_buy': 55}

    # Weg A (neu): einmal auf voller Basis rekomputen, dann Horizont ableiten.
    recomputed_base = recompute_subset(df, excluded=[],
                                       weights_override=overrides)
    mask = _horizon_mask(recomputed_base, primary_horizon)
    derived = recomputed_base[mask]

    # Weg B (alt): erst Horizont filtern, dann separat rekomputen.
    separate = recompute_subset(df[_horizon_mask(df, primary_horizon)],
                                excluded=[], weights_override=overrides)

    # Zeilenidentitaet: gleicher Index, gleiche Score-Spalten.
    assert list(derived.index) == list(separate.index)
    score_cols = ['trade_score', 'trade_score_breakdown', 'trade_warnings']
    pd.testing.assert_frame_equal(
        derived[score_cols].reset_index(drop=True),
        separate[score_cols].reset_index(drop=True),
    )
    # Sanity: Horizont-Filter hat tatsaechlich Zeilen entfernt (3 sichtbar).
    assert len(derived) == 3
    assert set(derived.index) == {0, 1, 4}


def test_o1_derivation_equivalence_no_override():
    """Auch ohne Overrides (needs_recompute nur via excluded / leer) muss die
    Ableitung zeilenidentisch bleiben."""
    primary_horizon = 60
    df = pd.DataFrame([
        _trade_row('committee_full', offset=30),
        _trade_row('swarm_buy', offset=120),
        _trade_row('committee_full', offset=60),
    ])
    recomputed_base = recompute_subset(df, excluded=[])
    mask = _horizon_mask(recomputed_base, primary_horizon)
    derived = recomputed_base[mask]
    separate = recompute_subset(df[_horizon_mask(df, primary_horizon)],
                                excluded=[])
    assert list(derived.index) == list(separate.index)
    pd.testing.assert_frame_equal(
        derived[['trade_score', 'trade_score_breakdown']]
        .reset_index(drop=True),
        separate[['trade_score', 'trade_score_breakdown']]
        .reset_index(drop=True),
    )


# ---------------------------------------------------------------------------
# Z1b — Faerbungs-Schwelle aus settings
# ---------------------------------------------------------------------------
def test_z1b_default_threshold_matches_settings():
    """Bei Default-Schwelle identisches Verhalten wie zuvor hartkodiert (10 %)."""
    make = _load_related_return_style()
    style = make(_settings.DETAIL_RELATED_RETURN_COLOR_PCT)
    assert _settings.DETAIL_RELATED_RETURN_COLOR_PCT == 10.0
    assert style(15) != ''            # > 10 -> gruen
    assert 'color: #dcfce7' in style(15)
    assert style(-15) != ''           # < -10 -> rot
    assert 'color: #fee2e2' in style(-15)
    assert style(5) == ''             # innerhalb Band -> neutral
    assert style(-5) == ''
    assert style(10) == ''            # Grenze exklusiv (> / <)
    assert style(float('nan')) == ''


def test_z1b_respects_monkeypatched_threshold():
    """Andere Schwelle -> Faerbungsgrenzen verschieben sich entsprechend.

    Mit Schwelle 5 wird +7 gruen (bei 10 waere es neutral) -> beweist, dass die
    Funktion die settings-Schwelle liest statt der alten Konstante 10.
    """
    make = _load_related_return_style()
    style5 = make(5.0)
    assert style5(7) != ''            # 7 > 5 -> gruen (bei 10 neutral gewesen)
    assert style5(-7) != ''           # -7 < -5 -> rot
    assert style5(3) == ''            # innerhalb 5er-Band -> neutral

    style20 = make(20.0)
    assert style20(15) == ''          # 15 < 20 -> neutral (bei 10 gruen gewesen)
    assert style20(-15) == ''
    assert style20(25) != ''
