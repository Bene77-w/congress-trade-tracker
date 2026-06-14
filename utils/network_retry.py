"""Einheitliches Netzwerk-Retry-Schema mit exponentiell wachsender Abklingzeit.

Ein einziger Standardablauf fuer alle Netzwerk-Operationen eines
``import_trades.py``-Laufs. Ziel: bei transienten Netzwerkproblemen robust
wiederholen, bei dauerhaften Problemen sauber **fail-closed** abbrechen, damit
durch Netzwerkfehler keine falschen oder veralteten Daten ins System gelangen.

Backoff-Schema (Sekunden ZWISCHEN den Versuchen):
``config.settings.NETWORK_RETRY_BACKOFFS`` (Default ``[2,4,8,16,32,64,128]``)
= 8 Versuche, ~254s Gesamt-Wartezeit pro Operation. Bewusst KEINE Max-Gesamtzeit
— die Einzelzeiten geben das Budget bereits vor.

Klassifikation (fail-closed):
- **Transient** (Timeout, ConnectionError, DNS, HTTP 5xx/429) -> Abklingzeit + Retry.
- **Leere Antwort** (call-site-spezifisch via ``is_empty``) -> wie transient gewertet -> Retry.
- **Permanent** (HTTP 4xx ausser 429, Parse-/Schema-Fehler) -> sofort weiterreichen, KEIN Retry.
- **Alle Versuche erschoepft** -> :class:`NetworkRetryExhausted` (der Aufrufer
  entscheidet das fail-closed-Verhalten: ueberspringen ohne Persistenz, oder
  eine domaenenspezifische Exception werfen).

Die Funktion ist seiteneffektfrei bzgl. Persistenz und in Tests vollstaendig
deterministisch: ``_sleep`` ist injizierbar, echte Wartezeiten entfallen so.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Sequence

from config import settings

logger = logging.getLogger(__name__)

try:  # requests ist im Projekt vorhanden; Fallback haelt das Util importierbar.
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


class NetworkRetryExhausted(Exception):
    """Alle Retry-Versuche einer Netzwerk-Operation erschoepft (transient/leer).

    Signalisiert dem Aufrufer: Operation konnte nicht erfolgreich abgeschlossen
    werden, obwohl der volle Backoff-Budget ausgeschoepft wurde. Der Aufrufer
    MUSS fail-closed reagieren (nichts persistieren / Stage abbrechen).
    """


# Fallback-Textmuster fuer Bibliotheken ohne saubere Exception-Typen
# (z.B. yfinance, das HTTP-Fehler intern in generische Exceptions kapselt).
# Bewusst breiter als die historische 6er-Liste in ticker_normalizer/ticker_sector,
# damit Timeout-/DNS-/SSL-Varianten nicht durchrutschen (R2/R6).
_TRANSIENT_TEXT_PATTERNS = (
    "could not resolve host",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "tls connect error",
    "ssl",
    "certificate verify failed",
    "connection refused",
    "connection reset",
    "connection aborted",
    "connection timed out",
    "read timed out",
    "timed out",
    "timeout",
    "network is unreachable",
    "max retries exceeded",
    "proxyerror",
    "incompleteread",
    "remotedisconnected",
    "remote end closed",
    "502 ",
    "503 ",
    "504 ",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
)


def is_transient_exception(exc: BaseException) -> bool:
    """Klassifiziert eine Exception als transient (retrybar) oder permanent.

    Typ-basiert (robust) wo moeglich, Text-Fallback fuer untypisierte Libs:

    - ``requests.Timeout`` / ``requests.ConnectionError`` -> transient.
    - ``requests.HTTPError`` -> transient nur bei HTTP 429 oder 5xx; 4xx permanent.
    - sonst -> Text-Pattern-Match (``_TRANSIENT_TEXT_PATTERNS``).

    Permanent sind insbesondere HTTP 4xx (ausser 429) sowie Parse-/Schema-
    Fehler (``ValueError``/``KeyError`` matchen kein Pattern).
    """
    if requests is not None:
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError):
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", None)
            if code is not None:
                return code == 429 or 500 <= code < 600
            return False  # HTTPError ohne Status -> konservativ permanent
    msg = str(exc).lower()
    return any(p in msg for p in _TRANSIENT_TEXT_PATTERNS)


def retry_call(
    func: Callable[[], object],
    *,
    is_empty: Optional[Callable[[object], bool]] = None,
    label: str = "",
    backoffs: Optional[Sequence[float]] = None,
    on_attempt_failed: Optional[Callable[[int, str], None]] = None,
    _sleep: Optional[Callable[[float], None]] = None,
) -> object:
    """Fuehrt ``func`` mit dem Standard-Retry/Backoff aus.

    Args:
        func: parameterlose Operation; liefert ein Ergebnis oder wirft.
        is_empty: optionaler Praedikat-Callback. Gibt er fuer das Ergebnis
            ``True`` zurueck, gilt das Ergebnis als verdaechtig-leer und wird wie
            ein transienter Fehler behandelt (-> Retry). call-site-spezifisch.
            Der Callback kann veraenderlichen Lauf-Zustand lesen (z.B. eine
            "Netz-gesund"-Heuristik), um echtes Leer-Ergebnis von einem
            netzbedingt leeren zu trennen.
        label: Bezeichner fuer Log-/Exception-Texte.
        backoffs: Abklingzeiten (Sek.) zwischen den Versuchen; Default
            ``settings.NETWORK_RETRY_BACKOFFS``. Ist ``NETWORK_RETRY_ENABLED``
            False, wird ohne Retry genau ein Versuch ausgefuehrt.
        on_attempt_failed: optionaler Hook ``(attempt_idx, reason)`` — z.B. um
            einen aeusseren Consecutive-Error-Counter EINMAL pro endgueltig
            gescheiterter Operation zu fuehren (nicht pro Einzelversuch).
        _sleep: injizierbar fuer Tests (kein echtes Warten).

    Returns:
        Das Ergebnis von ``func`` beim ersten erfolgreichen, nicht-leeren Versuch.

    Raises:
        NetworkRetryExhausted: alle Versuche transient/leer gescheitert.
        Exception: ein permanenter (nicht-transienter) Fehler wird unveraendert
            sofort weitergereicht (kein Retry) — fail-closed.
    """
    if backoffs is None:
        backoffs = settings.NETWORK_RETRY_BACKOFFS
    if not getattr(settings, "NETWORK_RETRY_ENABLED", True):
        backoffs = ()
    # time.sleep dynamisch (nicht als Default-Arg eingefroren), damit ein
    # globales Monkeypatch von time.sleep in Tests auch hier greift.
    sleep_fn = _sleep if _sleep is not None else time.sleep
    waits = (0.0, *backoffs)  # erster Versuch sofort, danach die Abklingzeiten
    last_reason = "kein Versuch"
    for attempt, wait in enumerate(waits):
        if wait:
            sleep_fn(wait)
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 — bewusste zentrale Klassifikation
            if not is_transient_exception(exc):
                raise  # permanent -> sofort fail-closed
            last_reason = f"transienter Fehler: {exc}"
            logger.debug("%s: Versuch %d/%d gescheitert (%s)",
                         label or "retry_call", attempt + 1, len(waits), last_reason)
            if on_attempt_failed is not None:
                on_attempt_failed(attempt, last_reason)
            continue
        if is_empty is not None and is_empty(result):
            last_reason = "leere/verdaechtige Antwort"
            logger.debug("%s: Versuch %d/%d leer", label or "retry_call",
                         attempt + 1, len(waits))
            if on_attempt_failed is not None:
                on_attempt_failed(attempt, last_reason)
            continue
        return result
    raise NetworkRetryExhausted(
        f"{label or 'Netzwerk-Operation'}: nach {len(waits)} Versuchen "
        f"erschoepft ({last_reason})."
    )
