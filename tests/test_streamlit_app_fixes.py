"""Tests fuer die Streamlit-App-Fixes O1 (Recompute-Ableitung), Z1b (Schwelle)
und W1 (Zeitfenster-Anker = gemeinsamer Datenstand).

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


# ---------------------------------------------------------------------------
# W1 — Zeitfenster-Anker: gemeinsamer Datenstand (max disclosure_date)
# ---------------------------------------------------------------------------
def _load_apply_prefilter():
    """Extrahiert die ECHTEN Quellen von ``_window_anchor`` + ``apply_prefilter``
    aus streamlit_app.py und liefert eine Factory, die die Sidebar-Globals
    (date_basis, window_days, …) kontrolliert injiziert — gleiches Muster wie
    ``_load_related_return_style``.
    """
    src = APP_PATH.read_text(encoding='utf-8')
    tree = ast.parse(src)
    wanted = ('_window_anchor', 'apply_prefilter')
    sources = {n.name: ast.get_source_segment(src, n) for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name in wanted}
    assert set(sources) == set(wanted)

    def _make(date_basis: str, window_days):
        ns: dict = {'pd': pd, 'date_basis': date_basis,
                    'window_days': window_days, 'search_query': '',
                    'horizon_filter_active': False, 'primary_horizon': 90}
        for name in wanted:  # _window_anchor zuerst (Abhaengigkeit)
            exec(sources[name], ns)  # noqa: S102 - kontrollierter Namespace
        return ns['apply_prefilter']

    return _make


def test_w1_common_anchor_is_data_stand():
    """Der Fenster-Anker ist fuer BEIDE Datumsbasen max(disclosure_date)
    (= Datenstand; User-Entscheid 2026-07-07).

    Kurzfenster auf transaction-Basis duerfen dadurch leer sein (Meldeverzug
    bis 45 Tage) — die Trefferzahlen der Basen bleiben dafuer vergleichbar.
    """
    make = _load_apply_prefilter()
    # Juengste Meldung 2026-07-01, juengste Transaktion 2026-06-16
    # (Meldeverzug 15 Tage — realistisches Muster). 7d-Cutoff = 06-24.
    df = pd.DataFrame({
        'transaction_date': ['2026-06-10', '2026-06-14', '2026-06-16',
                             '2026-05-01'],
        'disclosure_date':  ['2026-06-17', '2026-07-01', '2026-06-20',
                             '2026-06-30'],
    })
    # Transaction-Basis 7d: keine Transaktion nach dem 06-24 bekannt -> leer
    # (bewusst; NICHT erneut auf Basis-Spalten-Anker "fixen").
    assert make('transaction_date', 7)(df).empty
    # Disclosure-Basis 7d: die 2 juengsten Meldungen (auch die alte
    # Mai-Transaktion, frisch gemeldet).
    assert list(make('disclosure_date', 7)(df).index) == [1, 3]
    # 30d (Cutoff 06-01): Transaction sieht die 3 Juni-Transaktionen,
    # Disclosure alle 4 Meldungen.
    assert list(make('transaction_date', 30)(df).index) == [0, 1, 2]
    assert len(make('disclosure_date', 30)(df)) == 4
    # window_days=None ('Alles'): kein Cutoff.
    assert len(make('transaction_date', None)(df)) == len(df)


def test_w1_transaction_window_is_subset_of_disclosure_window():
    """Invariante des gemeinsamen Ankers: da disclosure_date >=
    transaction_date, ist die Transaction-Treffermenge stets Teilmenge der
    Disclosure-Treffermenge (nie MEHR Trades auf transaction-Basis)."""
    make = _load_apply_prefilter()
    df = pd.DataFrame({
        'transaction_date': ['2026-05-01', '2026-06-05', '2026-06-27',
                             '2026-06-30', '2026-04-10'],
        'disclosure_date':  ['2026-06-10', '2026-06-26', '2026-06-29',
                             '2026-07-01', '2026-05-20'],
    })
    for window in (7, 30, 365):
        txn_idx = set(make('transaction_date', window)(df).index)
        disc_idx = set(make('disclosure_date', window)(df).index)
        assert txn_idx <= disc_idx, (window, txn_idx, disc_idx)


def test_w1_fallback_anchor_and_nat_robustness():
    """Fehlt disclosure_date (oder ist komplett NaT), faellt der Anker auf das
    Max der Basis-Spalte zurueck; NaT-/kaputte Datumszeilen fallen aus dem
    Fenster; komplett datumslose Frames bleiben ungefiltert (kein Crash)."""
    make = _load_apply_prefilter()
    df = pd.DataFrame({
        'transaction_date': ['2026-06-10', None, '2026-06-16', 'kaputt',
                             '2026-05-01'],
    })
    out = make('transaction_date', 7)(df)
    # Fallback-Anker = max(transaction_date) = 06-16, Cutoff 06-09.
    assert list(out.index) == [0, 2]
    # disclosure_date vorhanden, aber komplett NaT -> gleicher Fallback.
    df_nat = df.assign(disclosure_date=pd.NaT)
    assert list(make('transaction_date', 7)(df_nat).index) == [0, 2]
    # Gar kein gueltiges Datum -> kein Anker -> Filter uebersprungen.
    df_empty = pd.DataFrame({'transaction_date': [None, None]})
    assert len(make('transaction_date', 7)(df_empty)) == 2
