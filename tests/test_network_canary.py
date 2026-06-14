"""Tests fuer utils.network_canary — Out-of-Band-Netz-Verdikt-Orakel.

Vollstaendig deterministisch: probe_fn, time_fn und sleep_fn sind injizierbar,
kein echtes Netzwerk, kein echtes Warten.
"""
from __future__ import annotations

import pytest

from utils.network_canary import GREEN, RED, NetworkCanary

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


# ---------------------------------------------------------------------------
# Test-Doubles
# ---------------------------------------------------------------------------


class FakeClock:
    """Monotone Fake-Uhr; per advance() steuerbar."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def probe_from_live(live_set):
    """probe_fn, das True liefert, wenn der Ticker in live_set ist; zaehlt Calls."""
    calls: list[str] = []

    def _probe(tkr: str) -> bool:
        calls.append(tkr)
        return tkr in live_set

    return _probe, calls


def probe_scripted(script: dict):
    """probe_fn mit pro-Ticker-Skript (Liste von Outcomes: bool/Exception)."""
    calls: list[str] = []

    def _probe(tkr: str):
        calls.append(tkr)
        outcome = script[tkr]
        val = outcome.pop(0) if isinstance(outcome, list) else outcome
        if isinstance(val, BaseException):
            raise val
        return val

    return _probe, calls


def _http_429():
    """Echter requests.HTTPError mit Status 429 (Too Many Requests)."""
    resp = requests.Response()
    resp.status_code = 429
    return requests.HTTPError("429 Client Error: Too Many Requests", response=resp)


def make_canary(probe_fn, clock=None, **kwargs):
    """NetworkCanary mit Test-Defaults (kein echtes Warten, Basket SPY/AAPL/MSFT)."""
    clock = clock or FakeClock()
    defaults = dict(
        basket=["SPY", "AAPL", "MSFT"],
        min_positive=2,
        ttl_seconds=60,
        probe_backoffs=[2, 4, 8],
        verbose=False,
        time_fn=clock,
        sleep_fn=lambda s: None,
    )
    defaults.update(kwargs)
    return NetworkCanary(probe_fn, **defaults), clock


# ---------------------------------------------------------------------------
# Quorum / Verdikt
# ---------------------------------------------------------------------------


class TestQuorum:
    def test_green_when_quorum_met(self):
        probe, _ = probe_from_live({"SPY", "AAPL", "MSFT"})
        canary, _ = make_canary(probe)
        assert canary.verdict() == GREEN

    def test_green_at_exact_quorum(self):
        probe, _ = probe_from_live({"SPY", "AAPL"})  # 2/3
        canary, _ = make_canary(probe)
        assert canary.verdict() == GREEN

    def test_red_below_quorum(self):
        probe, _ = probe_from_live({"SPY"})  # 1/3 < 2
        canary, _ = make_canary(probe)
        assert canary.verdict() == RED

    def test_red_when_all_dead(self):
        probe, _ = probe_from_live(set())
        canary, _ = make_canary(probe)
        assert canary.verdict() == RED


class TestEarlyExit:
    def test_stops_probing_once_quorum_reached(self):
        """SPY+AAPL live -> nach 2 Treffern wird MSFT nicht mehr geprobt."""
        probe, calls = probe_from_live({"SPY", "AAPL", "MSFT"})
        canary, _ = make_canary(probe)
        assert canary.verdict() == GREEN
        assert calls == ["SPY", "AAPL"]  # MSFT uebersprungen

    def test_stops_when_quorum_unreachable(self):
        """SPY+AAPL tot (leer, kein Retry) -> 2/3 unerreichbar -> MSFT skip."""
        probe, calls = probe_from_live(set())
        canary, _ = make_canary(probe)
        assert canary.verdict() == RED
        assert calls == ["SPY", "AAPL"]  # MSFT nicht mehr noetig


# ---------------------------------------------------------------------------
# Caching / TTL / fresh
# ---------------------------------------------------------------------------


class TestVerdictCaching:
    def test_first_verdict_is_fresh(self):
        probe, calls = probe_from_live({"SPY", "AAPL"})
        canary, _ = make_canary(probe)
        canary.verdict()
        assert canary.fresh is True
        assert calls == ["SPY", "AAPL"]

    def test_second_verdict_within_ttl_is_cached(self):
        probe, calls = probe_from_live({"SPY", "AAPL"})
        canary, clock = make_canary(probe)
        assert canary.verdict() == GREEN
        n_after_first = len(calls)
        clock.advance(30)  # < TTL 60
        assert canary.verdict() == GREEN
        assert canary.fresh is False
        assert len(calls) == n_after_first  # KEINE neue Probe

    def test_verdict_reprobes_after_ttl(self):
        probe, calls = probe_from_live({"SPY", "AAPL"})
        canary, clock = make_canary(probe)
        canary.verdict()
        n_after_first = len(calls)
        clock.advance(61)  # > TTL
        canary.verdict()
        assert canary.fresh is True
        assert len(calls) > n_after_first  # neue Probe

    def test_force_reprobe_ignores_ttl(self):
        probe, calls = probe_from_live({"SPY", "AAPL"})
        canary, clock = make_canary(probe)
        canary.verdict()
        n_after_first = len(calls)
        clock.advance(5)  # weit innerhalb TTL
        canary.force_reprobe()
        assert canary.fresh is True
        assert len(calls) > n_after_first


# ---------------------------------------------------------------------------
# Transiente Fehler / Retry / Erschoepfung
# ---------------------------------------------------------------------------


class TestTransientRetry:
    def test_transient_then_success_counts_positive(self):
        # SPY scheitert erst transient, dann live; AAPL live -> Quorum 2/3.
        err = ConnectionError("connection reset by peer")
        probe, _ = probe_scripted({
            "SPY": [err, True],
            "AAPL": [True],
            "MSFT": [True],
        })
        canary, _ = make_canary(probe)
        assert canary.verdict() == GREEN

    def test_persistent_transient_counts_negative(self):
        # SPY+AAPL dauerhaft transient (erschoepft), MSFT live -> 1/3 -> RED.
        err = ConnectionError("connection timed out")
        probe, _ = probe_scripted({
            "SPY": [err, err, err, err, err, err],
            "AAPL": [err, err, err, err, err, err],
            "MSFT": [True],
        })
        canary, _ = make_canary(probe)
        assert canary.verdict() == RED


# ---------------------------------------------------------------------------
# 429 / Throttle-Erkennung
# ---------------------------------------------------------------------------


@pytest.mark.skipif(requests is None, reason="requests nicht verfuegbar")
class TestThrottle:
    def test_429_sets_saw_throttle(self):
        # SPY wirft 429 (erschoepft -> negativ), AAPL+MSFT live -> GREEN,
        # aber saw_throttle muss True sein.
        probe, _ = probe_scripted({
            "SPY": [_http_429()] * 6,
            "AAPL": [True],
            "MSFT": [True],
        })
        canary, _ = make_canary(probe)
        assert canary.verdict() == GREEN
        assert canary.saw_throttle is True

    def test_no_throttle_when_clean(self):
        probe, _ = probe_from_live({"SPY", "AAPL", "MSFT"})
        canary, _ = make_canary(probe)
        canary.verdict()
        assert canary.saw_throttle is False

    def test_throttle_reset_on_clean_reprobe(self):
        # Erst Throttle, dann sauberes Reprobe -> saw_throttle zurueckgesetzt.
        probe, _ = probe_scripted({
            "SPY": [_http_429(), _http_429(), _http_429(),
                    _http_429(), True, True],
            "AAPL": [True, True],
            "MSFT": [True, True],
        })
        canary, clock = make_canary(probe)
        canary.force_reprobe()
        assert canary.saw_throttle is True
        clock.advance(61)
        canary.force_reprobe()
        assert canary.saw_throttle is False


# ---------------------------------------------------------------------------
# wait_then_reprobe (RED-Warteschleife)
# ---------------------------------------------------------------------------


class TestWaitThenReprobe:
    def test_sleeps_ttl_then_probes(self):
        sleeps: list[float] = []
        probe, calls = probe_from_live({"SPY", "AAPL"})
        canary, _ = make_canary(
            probe, sleep_fn=lambda s: sleeps.append(s))
        verdict = canary.wait_then_reprobe()
        assert verdict == GREEN
        assert sleeps and sleeps[0] == 60  # TTL geschlafen
        assert calls  # danach geprobt

    def test_recovery_after_red(self):
        # Erst alles tot (RED), Netz erholt sich waehrend des 60s-Wartens (GREEN).
        # sleep_fn flippt nur beim TTL-Schlaf (s == 60), NICHT bei Probe-Backoffs.
        state = {"live": False}

        def probe(tkr):
            return state["live"]

        def sleep_fn(s):
            if s == 60:
                state["live"] = True

        canary, _ = make_canary(probe, sleep_fn=sleep_fn)
        assert canary.verdict() == RED
        assert canary.wait_then_reprobe() == GREEN
