"""Tests fuer die Canary-Netz-Robustheit in scoring.post_trade_returns.

Deckt Variante B ab: vorlaeufige Fenster, rueckwirkende Invalidierung, Restart
ab Fenster-Beginn, fail-closed-Abbruch, 429-Throttle-Veto, last_checked-TTL,
E5-Marker-Prune, Befund-2-Heilung, NaN-Marker-Migration, Timeout-Sichtbarkeit.

Der Canary wird durch eine kontrollierbare ``FakeCanary`` ersetzt; ``time_fn``
ist konstant (keine spuriosen Fenstergrenzen). Kein Netzwerk, kein Warten.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import pytest
import requests

from scoring import post_trade_returns as ptr

NAN = float('nan')


# ---------------------------------------------------------------------------
# Test-Doubles / Helfer
# ---------------------------------------------------------------------------


class FakeCanary:
    """Kontrollierbarer Canary: ``force_reprobe``/``wait_then_reprobe`` poppen das
    naechste Skript-Verdikt; ``verdict()`` liefert den gecachten Wert (TTL-Modell).
    """

    def __init__(self, script=None, throttle=None):
        self._script = list(script) if script else []
        self._throttle = list(throttle) if throttle else []
        self._current = None
        self._fresh = False
        self.saw_throttle = False
        self.calls = {'verdict': 0, 'force': 0, 'wait': 0}

    def _pop(self):
        if self._script:
            self._current = self._script.pop(0)
        elif self._current is None:
            self._current = 'GREEN'
        self.saw_throttle = self._throttle.pop(0) if self._throttle else False
        self._fresh = True
        return self._current

    def verdict(self):
        self.calls['verdict'] += 1
        if self._current is None:
            return self._pop()
        self._fresh = False
        return self._current

    def force_reprobe(self):
        self.calls['force'] += 1
        return self._pop()

    def wait_then_reprobe(self):
        self.calls['wait'] += 1
        return self._pop()

    @property
    def fresh(self):
        return self._fresh


class StepClock:
    """Fortschreitende Fake-Uhr: liefert nacheinander die Werte (letzter haelt).

    Damit laesst sich die periodische Fenstergrenze (``time_fn()-last_boundary >=
    ttl``) deterministisch ausloesen, ohne echte Zeit/Threads.
    """

    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    def __call__(self):
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return v


def make_cache(rows):
    """rows: Liste (ticker, date, close). Leere Liste -> leerer Cache."""
    if not rows:
        idx = pd.MultiIndex.from_arrays([[], []], names=['ticker', 'date'])
        return pd.DataFrame({'close': pd.Series(dtype='float64')}, index=idx)
    idx = pd.MultiIndex.from_tuples(
        [(t, pd.Timestamp(d)) for t, d, _ in rows], names=['ticker', 'date'])
    return pd.DataFrame({'close': [c for *_, c in rows]}, index=idx)


def real_hist():
    return pd.DataFrame(
        {"Close": [10.0, 11.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]))


def make_download(spec):
    """spec: ticker -> 'real' | 'empty' | Liste pro Aufruf. Zaehlt Calls."""
    calls: Counter = Counter()

    def _dl(tkr, **kw):
        calls[tkr] += 1
        s = spec.get(tkr, 'empty')
        if isinstance(s, list):
            s = s[min(calls[tkr] - 1, len(s) - 1)]
        return real_hist() if s == 'real' else pd.DataFrame()

    return _dl, calls


def run_ensure(cache, tickers, canary, monkeypatch, spec, verbose=False):
    dl, calls = make_download(spec)
    monkeypatch.setattr("scoring.post_trade_returns.yf.download", dl)
    out = ptr._ensure_cache_for_tickers(
        cache, tickers, cache_path=None, canary=canary,
        time_fn=lambda: 0.0, verbose_timeouts=verbose)
    return out, calls


# ---------------------------------------------------------------------------
# Variante B: Fenster, rueckwirkende Invalidierung, Restart
# ---------------------------------------------------------------------------


class TestWindowVarianteB:
    def test_many_delistings_green_all_marked(self, monkeypatch):
        """Viele Delistings bei GRUEN -> alle markiert, KEIN Fehlabbruch."""
        tickers = [f"D{i}" for i in range(5)]
        out, _ = run_ensure(make_cache([]), tickers, FakeCanary(['GREEN']),
                            monkeypatch, {t: 'empty' for t in tickers})
        lookup = ptr._build_price_lookup(out)
        for t in tickers:
            assert lookup.get(t) is None  # alle als delisted markiert

    def test_retroactive_invalidation_discards_provisional(self, monkeypatch):
        """Cached-GRUEN-Marker, dann ROT am Fenster-Ende -> Marker verworfen;
        nach Erholung liefert der Ticker echte Preise (kein Marker)."""
        canary = FakeCanary(['GREEN', 'RED', 'GREEN', 'GREEN'])
        out, calls = run_ensure(make_cache([]), ['X'], canary, monkeypatch,
                                {'X': ['empty', 'real']})
        assert ptr._build_price_lookup(out).get('X') is not None  # real, NICHT Marker
        assert calls['X'] == 2  # neu verarbeitet

    def test_restart_refetches_whole_interval(self, monkeypatch):
        """ROT verwirft das GANZE Fenster (Variante B): auch der Ticker mit echten
        Preisen (A) wird ab Fenster-Beginn neu gefetcht."""
        canary = FakeCanary(['GREEN', 'RED', 'GREEN', 'GREEN'])
        out, calls = run_ensure(make_cache([]), ['A', 'B'], canary, monkeypatch,
                                {'A': 'real', 'B': ['empty', 'real']})
        assert calls['A'] == 2  # echte Preise trotzdem neu gefetcht
        assert calls['B'] == 2
        assert ptr._build_price_lookup(out).get('A') is not None
        assert ptr._build_price_lookup(out).get('B') is not None

    def test_persistent_red_aborts_fail_closed(self, monkeypatch):
        """Dauer-ROT -> RuntimeError nach CANARY_MAX_RED_ROUNDS (fail-closed)."""
        monkeypatch.setattr(ptr.settings, 'CANARY_MAX_RED_ROUNDS', 3)
        canary = FakeCanary(['GREEN', 'RED'])  # danach repeat RED
        dl, _ = make_download({'X': 'empty'})
        monkeypatch.setattr("scoring.post_trade_returns.yf.download", dl)
        with pytest.raises(RuntimeError):
            ptr._ensure_cache_for_tickers(
                make_cache([]), ['X'], cache_path=None, canary=canary,
                time_fn=lambda: 0.0, verbose_timeouts=False)


# ---------------------------------------------------------------------------
# 429 / Throttle-Veto (unter Variante B: ganzes Fenster verwerfen)
# ---------------------------------------------------------------------------


class TestThrottleVeto:
    def test_throttle_window_discarded_and_requeued(self, monkeypatch):
        """GRUEN aber saw_throttle am Fenster-Ende -> Fenster wie ROT verworfen +
        re-queued; nach sauberem Reprobe committet."""
        canary = FakeCanary(['GREEN', 'GREEN', 'GREEN'],
                            throttle=[True, False, False])
        out, calls = run_ensure(make_cache([]), ['X'], canary, monkeypatch,
                                {'X': 'real'})
        assert canary.calls['wait'] >= 1     # Throttle loeste Warteschleife aus
        assert calls['X'] == 2               # Fenster neu verarbeitet
        assert ptr._build_price_lookup(out).get('X') is not None


# ---------------------------------------------------------------------------
# last_checked-TTL
# ---------------------------------------------------------------------------


class TestLastCheckedTTL:
    def test_skips_delisted_within_ttl(self, monkeypatch):
        """Delisted-Marker juenger als RECHECK_DAYS -> KEIN Refetch."""
        today = pd.Timestamp.now().normalize()
        cache = make_cache([('X', today - pd.Timedelta(days=10), NAN)])
        out, calls = run_ensure(cache, ['X'], FakeCanary(['GREEN']),
                                monkeypatch, {'X': 'real'})
        assert calls['X'] == 0  # innerhalb TTL nicht gefetcht
        assert ptr._build_price_lookup(out).get('X') is None  # Marker bleibt

    def test_refetches_delisted_after_ttl(self, monkeypatch):
        """Delisted-Marker aelter als RECHECK_DAYS -> Refetch."""
        today = pd.Timestamp.now().normalize()
        cache = make_cache([('X', today - pd.Timedelta(days=40), NAN)])
        out, calls = run_ensure(cache, ['X'], FakeCanary(['GREEN', 'GREEN']),
                                monkeypatch, {'X': 'real'})
        assert calls['X'] == 1
        assert ptr._build_price_lookup(out).get('X') is not None  # jetzt Preise

    def test_fresh_prices_not_refetched(self, monkeypatch):
        """Frische echte Preise (< Preis-TTL) -> KEIN Refetch."""
        today = pd.Timestamp.now().normalize()
        cache = make_cache([('X', today - pd.Timedelta(days=1), 12.0)])
        out, calls = run_ensure(cache, ['X'], FakeCanary(['GREEN']),
                                monkeypatch, {'X': 'real'})
        assert calls['X'] == 0


# ---------------------------------------------------------------------------
# Marker-Schema: E5, Befund-2, Migration, Kollaps (Funktions-Ebene)
# ---------------------------------------------------------------------------


class TestMarkerSchema:
    def test_e5_drops_marker_when_real_prices(self):
        """E5: Ticker mit Marker UND echten Preisen -> Marker physisch entfernt."""
        cache = make_cache([('X', '1900-01-01', NAN)])
        real = make_cache([('X', '2024-01-02', 10.0), ('X', '2024-01-03', 11.0)])
        out = ptr._merge_into_cache(cache, [real])
        sub = out.loc['X']
        assert sub['close'].notna().all()
        assert len(sub) == 2

    def test_marker_collapse_keeps_latest(self):
        """Mehrere Marker (verschiedene Pruef-Daten) -> nur juengster bleibt."""
        cache = make_cache([('X', '2024-01-01', NAN)])
        newer = make_cache([('X', '2024-02-01', NAN)])
        out = ptr._merge_into_cache(cache, [newer])
        sub = out.loc['X']
        assert len(sub) == 1
        assert sub.index[0] == pd.Timestamp('2024-02-01')

    def test_befund2_marker_plus_real_not_delisted(self):
        """Befund-2: Marker + echte Preise -> im Lookup NICHT delisted (None)."""
        cache = make_cache([('X', '1900-01-01', NAN), ('X', '2024-01-02', 10.0)])
        assert ptr._build_price_lookup(cache).get('X') is not None

    def test_befund2_exit_status_not_stuck_delisted(self):
        """Befund-2 end-to-end: _compute_exit_columns mit Marker+Preis -> nicht
        'delisted' (sondern held/closed, je nach Gegentrade)."""
        cache = make_cache([('X', '1900-01-01', NAN),
                            ('X', '2024-01-02', 10.0), ('X', '2024-03-01', 12.0)])
        df = pd.DataFrame({
            'trade_id': ['t1'],
            'name': ['Jane Doe'],
            'issuer_group_key': ['CIK:1'],
            'ticker': ['X'],
            'transaction_date': [pd.Timestamp('2024-01-02')],
            'transaction_type': ['Purchase'],
            'trade_amount_max_usd': [1000.0],
            '_post_trade_applicable': [True],
        })
        cols = ptr._compute_exit_columns(df, cache, None, 0.0)
        assert cols['exit_status'].iloc[0] != 'delisted'

    def test_old_1900_marker_migrates_and_rechecks(self, monkeypatch):
        """Alt-Marker (1900-01-01) -> im Lookup delisted UND faellig zum Refetch."""
        cache = make_cache([('X', '1900-01-01', NAN)])
        assert ptr._build_price_lookup(cache).get('X') is None  # erkannt
        out, calls = run_ensure(cache, ['X'], FakeCanary(['GREEN', 'GREEN']),
                                monkeypatch, {'X': 'real'})
        assert calls['X'] == 1  # Alt-Marker neu geprueft


# ---------------------------------------------------------------------------
# Timeout-Sichtbarkeit
# ---------------------------------------------------------------------------


class TestProductionPaths:
    """Periodische Fenstergrenze + mid-loop-RED — der dominante Produktionspfad
    (in einem echten Lauf das eigentliche Commit- bzw. Ausfall-Erkennungs-Mittel)."""

    def test_midloop_empty_red_invalidates_and_restarts(self, monkeypatch):
        """Leeres Ergebnis trifft MID-LOOP auf ROT -> ganzes Fenster verworfen +
        Restart ab Fenster-Beginn (A trotz echter Preise neu gefetcht)."""
        canary = FakeCanary(['RED', 'GREEN', 'GREEN'])
        out, calls = run_ensure(make_cache([]), ['A', 'B', 'C'], canary,
                                monkeypatch,
                                {'A': 'real', 'B': 'empty', 'C': 'real'})
        assert calls['A'] == 2           # Restart hat A neu gefetcht
        assert canary.calls['wait'] >= 1
        lookup = ptr._build_price_lookup(out)
        assert lookup.get('A') is not None
        assert lookup.get('B') is None   # echtes Delisting (pass2 GRUEN)
        assert lookup.get('C') is not None

    def test_periodic_boundary_commits_on_green(self, monkeypatch):
        """Fenstergrenze (Uhr ueberschreitet TTL) bei GRUEN -> mid-run Commit."""
        clock = StepClock([0, 0, 61, 61])
        canary = FakeCanary(['GREEN', 'GREEN'])
        dl, _ = make_download({'A': 'real', 'B': 'real'})
        monkeypatch.setattr("scoring.post_trade_returns.yf.download", dl)
        out = ptr._ensure_cache_for_tickers(
            make_cache([]), ['A', 'B'], cache_path=None, canary=canary,
            time_fn=clock, verbose_timeouts=False)
        assert canary.calls['force'] == 2  # 1x Fenstergrenze + 1x Lauf-Ende
        assert ptr._build_price_lookup(out).get('A') is not None
        assert ptr._build_price_lookup(out).get('B') is not None

    def test_periodic_boundary_discards_on_red(self, monkeypatch):
        """Fenstergrenze bei ROT (mid-run) -> Fenster verworfen + re-queued +
        Restart nach Erholung."""
        clock = StepClock([0, 0, 61, 61])
        canary = FakeCanary(['RED', 'GREEN', 'GREEN'])
        dl, calls = make_download({'A': 'real', 'B': 'real'})
        monkeypatch.setattr("scoring.post_trade_returns.yf.download", dl)
        out = ptr._ensure_cache_for_tickers(
            make_cache([]), ['A', 'B'], cache_path=None, canary=canary,
            time_fn=clock, verbose_timeouts=False)
        assert canary.calls['force'] == 2  # Fenstergrenze + Lauf-Ende
        assert canary.calls['wait'] >= 1   # ROT loeste Warteschleife aus
        assert calls['A'] == 2             # A neu gefetcht (Restart)
        assert ptr._build_price_lookup(out).get('A') is not None
        assert ptr._build_price_lookup(out).get('B') is not None

    def test_exhausted_exception_red_then_skip(self, monkeypatch):
        """Erschoepfte Exception (Timeout) MID-LOOP + ROT -> Fenster verworfen +
        warten; nach Erholung erneut erschoepft, aber GRUEN -> skip OHNE Marker
        (kein falscher Delisted-Marker bei einem Einzel-Glitch)."""
        monkeypatch.setattr(ptr.settings, 'NETWORK_RETRY_BACKOFFS', [0, 0])
        monkeypatch.setattr("scoring.post_trade_returns.time.sleep", lambda s: None)

        def dl(tkr, **kw):
            raise requests.Timeout("Read timed out")
        monkeypatch.setattr("scoring.post_trade_returns.yf.download", dl)

        canary = FakeCanary(['RED', 'GREEN'])
        out = ptr._ensure_cache_for_tickers(
            make_cache([]), ['X'], cache_path=None, canary=canary,
            time_fn=lambda: 0.0, verbose_timeouts=False)
        assert 'X' not in out.index.get_level_values(0)  # nichts persistiert
        assert canary.calls['wait'] >= 1


class TestTimeoutVisibility:
    def test_net_attempt_printer_format(self, capsys):
        hook = ptr._net_attempt_printer("yf.download[XYZ]")
        hook(0, "transienter Fehler: Read timed out")
        out = capsys.readouterr().out
        assert "[NETZ]" in out
        assert "XYZ" in out
        assert "Versuch 1" in out

    def test_timeout_visible_during_fetch(self, monkeypatch, capsys):
        """Ein transienter Timeout im Fetch erzeugt eine sichtbare [NETZ]-Zeile."""
        monkeypatch.setattr(ptr.settings, 'NETWORK_RETRY_BACKOFFS', [0, 0, 0])
        monkeypatch.setattr("scoring.post_trade_returns.time.sleep", lambda s: None)
        calls = {'n': 0}

        def dl(tkr, **kw):
            calls['n'] += 1
            if calls['n'] < 2:
                raise requests.Timeout("Read timed out")
            return real_hist()
        monkeypatch.setattr("scoring.post_trade_returns.yf.download", dl)

        ptr._ensure_cache_for_tickers(
            make_cache([]), ['X'], cache_path=None,
            canary=FakeCanary(['GREEN', 'GREEN']),
            time_fn=lambda: 0.0, verbose_timeouts=True)
        out = capsys.readouterr().out
        assert "[NETZ]" in out and "Versuch 1" in out
