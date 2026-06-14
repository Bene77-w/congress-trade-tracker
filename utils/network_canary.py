"""Netzwerk-Canary: Out-of-Band-Gesundheitssignal fuer yfinance-Stages.

Problem: Ein leeres yfinance-Ergebnis (HTTP 200, keine Exception) ist NICHT von
einem echten Delisting unterscheidbar. Aus dem mehrdeutigen Ergebnis selbst auf
den Netz-Zustand zu schliessen, ist zirkulaer (genau das war die abgeloeste
``_network_healthy``-Heuristik in post_trade/ticker_sector).

Loesung: Der Canary probt garantiert-live Ticker (SPY/AAPL/MSFT) als
UNABHAENGIGES Signal. Schlagen ``>= min_positive`` des Baskets an, gilt das Netz
als GRUEN -> ein leeres Fetch-Ergebnis ist ein echtes Delisting. Sonst ROT ->
der Aufrufer schreibt KEINE Marker, verwirft das laufende Fenster und wartet auf
Erholung (fail-closed bei erschoepftem Budget).

Der Canary ist ein reines **Verdikt-Orakel** (kein Wissen ueber Buffer/Queue des
Aufrufers) und in Tests vollstaendig deterministisch: ``probe_fn``, ``time_fn``
und ``sleep_fn`` sind injizierbar.

Verdikt-Caching: ``verdict()`` cached das Ergebnis ``ttl_seconds`` lang (= die
Fensterlaenge). So bleibt die Zahl echter Probe-Requests bei ~ Laufdauer/TTL,
unabhaengig von der Ticker-Zahl. ``force_reprobe()`` erzwingt eine frische Probe
(Fenstergrenze), ``wait_then_reprobe()`` schlaeft erst die TTL ab (RED-Warte-
schleife des Aufrufers).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Sequence

from config import settings
from utils.network_retry import NetworkRetryExhausted, retry_call

logger = logging.getLogger(__name__)

GREEN = "GREEN"
RED = "RED"


class NetworkCanary:
    """Verdikt-Orakel ueber die aktuelle Netz-Gesundheit (GREEN/RED).

    Args:
        probe_fn: ``probe_fn(ticker) -> truthy``, wenn der Ticker live ist
            (z.B. nicht-leere Kurs-Serie / vorhandene Identitaet). Wirft bei
            Netzfehlern (wird per ``retry_call`` mit Backoff wiederholt).
        basket: Liste garantiert-live Ticker (Default ``settings.CANARY_TICKERS``).
        min_positive: Quorum (Default ``settings.CANARY_MIN_POSITIVE``).
        ttl_seconds: Verdikt-Cache- + Fensterlaenge (Default
            ``settings.CANARY_VERDICT_TTL_SECONDS``).
        probe_backoffs: Eigen-Retry-Budget je Probe (Default
            ``settings.CANARY_PROBE_BACKOFFS``).
        verbose: jeden Probe-Fehlversuch im Terminal sichtbar machen.
        on_attempt_failed: optionaler Zusatz-Hook ``(attempt, reason)`` (Tests/
            Counter); wird zusaetzlich zum eigenen Print/Throttle-Tracking gerufen.
        time_fn: Zeitquelle fuer die TTL (Default ``time.monotonic``).
        sleep_fn: Schlaf-Funktion (Default ``time.sleep``); injizierbar fuer Tests.
    """

    def __init__(
        self,
        probe_fn: Callable[[str], object],
        *,
        basket: Optional[Sequence[str]] = None,
        min_positive: Optional[int] = None,
        ttl_seconds: Optional[float] = None,
        probe_backoffs: Optional[Sequence[float]] = None,
        verbose: bool = True,
        on_attempt_failed: Optional[Callable[[int, str], None]] = None,
        time_fn: Optional[Callable[[], float]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._probe_fn = probe_fn
        self._basket = list(
            basket if basket is not None else settings.CANARY_TICKERS)
        self._min_positive = (
            min_positive if min_positive is not None
            else settings.CANARY_MIN_POSITIVE)
        self._ttl = float(
            ttl_seconds if ttl_seconds is not None
            else settings.CANARY_VERDICT_TTL_SECONDS)
        self._probe_backoffs = list(
            probe_backoffs if probe_backoffs is not None
            else settings.CANARY_PROBE_BACKOFFS)
        self._verbose = verbose
        self._on_attempt_failed = on_attempt_failed
        self._time_fn = time_fn if time_fn is not None else time.monotonic
        self._sleep_fn = sleep_fn if sleep_fn is not None else time.sleep

        self._cached_verdict: Optional[str] = None
        self._cached_at: float = 0.0
        self._fresh = False
        self._saw_throttle = False

    # -- Zustands-Properties -------------------------------------------------
    @property
    def fresh(self) -> bool:
        """True, wenn der LETZTE ``verdict()``-Aufruf frisch geprobt hat
        (= Fenstergrenze)."""
        return self._fresh

    @property
    def saw_throttle(self) -> bool:
        """True, wenn in der LETZTEN Probe ein 429/Too-Many-Requests auftrat."""
        return self._saw_throttle

    # -- oeffentliche API ----------------------------------------------------
    def verdict(self) -> str:
        """Aktuelles Verdikt; gecacht innerhalb der TTL, sonst frische Probe."""
        now = self._time_fn()
        if (self._cached_verdict is not None
                and (now - self._cached_at) < self._ttl):
            self._fresh = False
            return self._cached_verdict
        return self.force_reprobe()

    def force_reprobe(self) -> str:
        """Erzwingt eine frische Basket-Probe (ignoriert die TTL)."""
        self._saw_throttle = False
        positive = 0
        n = len(self._basket)
        for i, tkr in enumerate(self._basket):
            if positive >= self._min_positive:
                break  # Quorum bereits erreicht
            if positive + (n - i) < self._min_positive:
                break  # Quorum mathematisch nicht mehr erreichbar
            if self._probe_one(tkr):
                positive += 1
        verdict = GREEN if positive >= self._min_positive else RED
        self._cached_verdict = verdict
        self._cached_at = self._time_fn()
        self._fresh = True
        logger.debug("Canary-Verdikt: %s (%d/%d positiv, throttle=%s)",
                     verdict, positive, self._min_positive, self._saw_throttle)
        return verdict

    def wait_then_reprobe(self) -> str:
        """Schlaeft erst die TTL ab, probt dann frisch (RED-Warteschleife)."""
        self._sleep_fn(self._ttl)
        return self.force_reprobe()

    # -- intern --------------------------------------------------------------
    def _probe_one(self, tkr: str) -> bool:
        """Eine Basket-Probe via ``retry_call``; True bei live, False sonst.

        Nur transiente **Exceptions** (Timeout/ConnErr/5xx/429) werden im Backoff
        wiederholt. Ein leeres (falsy) Ergebnis zaehlt sofort als negativ (kein
        Retry) — der Basket ist garantiert-live, ein Leer-Resultat ist also ein
        Netzsignal, das die aeussere 60-s-Warteschleife behandelt. Das haelt die
        RED-Erkennung schnell.
        """
        try:
            result = retry_call(
                lambda: self._probe_fn(tkr),
                label=f"canary[{tkr}]",
                backoffs=self._probe_backoffs,
                on_attempt_failed=self._make_attempt_hook(tkr),
                _sleep=self._sleep_fn,
            )
        except NetworkRetryExhausted:
            return False
        except Exception as exc:  # noqa: BLE001 — permanenter Fehler EINER Probe
            # Blockiert das Quorum der anderen nicht; zaehlt als negativ.
            logger.debug("canary[%s]: permanenter Fehler: %s", tkr, exc)
            return False
        return bool(result)

    def _make_attempt_hook(self, tkr: str) -> Callable[[int, str], None]:
        def _hook(attempt: int, reason: str) -> None:
            low = reason.lower()
            if "429" in low or "too many requests" in low:
                self._saw_throttle = True
            if self._verbose:
                print(f"  [NETZ] canary[{tkr}]: Versuch {attempt + 1} "
                      f"fehlgeschlagen ({reason}).", flush=True)
            if self._on_attempt_failed is not None:
                self._on_attempt_failed(attempt, reason)
        return _hook
