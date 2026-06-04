"""Tests fuer scrapers/quiver_scraper.py — Validierung + Exit-Code-Mapping.

Kernzusicherung: Die zwei Fehlerklassen werden sauber getrennt und auf die
richtigen Exit-Codes abgebildet, ohne dass ein echter Browser gestartet wird.
Die Selektoren/Credentials sind reine Settings-Konstanten und werden hier
ueber monkeypatch gesetzt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
import scrapers.quiver_scraper as q  # noqa: E402


def test_require_credentials_raises_when_missing(monkeypatch):
    monkeypatch.setattr(settings, "QUIVER_EMAIL", "")
    monkeypatch.setattr(settings, "QUIVER_PASSWORD", "")
    with pytest.raises(q.QuiverLoginError):
        q._require_credentials()


def test_require_credentials_ok_when_set(monkeypatch):
    monkeypatch.setattr(settings, "QUIVER_EMAIL", "user@example.com")
    monkeypatch.setattr(settings, "QUIVER_PASSWORD", "geheim")
    q._require_credentials()  # darf nicht werfen


_REQUIRED_SELECTORS = (
    "QUIVER_EMAIL_SELECTOR", "QUIVER_PASSWORD_SELECTOR",
    "QUIVER_SUBMIT_SELECTOR", "QUIVER_POST_LOGIN_SELECTOR",
    "QUIVER_DOWNLOAD_SELECTOR",
)


def test_require_selectors_lists_missing(monkeypatch):
    # Alle Pflicht-Selektoren leer -> Fehlermeldung nennt jeden fehlenden Namen.
    for name in _REQUIRED_SELECTORS:
        monkeypatch.setattr(settings, name, "")
    with pytest.raises(q.QuiverLoginError) as exc:
        q._require_selectors()
    assert "QUIVER_DOWNLOAD_SELECTOR" in str(exc.value)


def test_require_selectors_ok_when_all_set(monkeypatch):
    for name in _REQUIRED_SELECTORS:
        monkeypatch.setattr(settings, name, "#dummy")
    q._require_selectors()  # darf nicht werfen


def test_try_dismiss_cookie_swallows_errors(monkeypatch):
    # Kein Cookie-Banner -> best-effort, darf NICHT werfen.
    class _Loc:
        def first(self):  # pragma: no cover - via property ersetzt
            return self
    class _FakePage:
        def locator(self, sel):
            raise RuntimeError("kein Banner")
    monkeypatch.setattr(settings, "QUIVER_COOKIE_REJECT_SELECTOR", 'role=button[name="Reject All"]')
    q._try_dismiss_cookie(_FakePage())  # darf nicht werfen


def test_try_dismiss_cookie_noop_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "QUIVER_COOKIE_REJECT_SELECTOR", "")
    q._try_dismiss_cookie(object())  # kein page-Zugriff, da Selektor leer


@pytest.mark.parametrize("fehler,exit_code", [
    (q.QuiverLoginError("login"), q.EXIT_LOGIN),
    (q.QuiverDownloadError("download"), q.EXIT_DOWNLOAD),
    (RuntimeError("unerwartet"), q.EXIT_GENERIC),
])
def test_main_maps_errors_to_exit_codes(monkeypatch, fehler, exit_code):
    # download_quiver durch Stub ersetzen, der die jeweilige Exception wirft.
    def _stub(*args, **kwargs):
        raise fehler
    monkeypatch.setattr(q, "download_quiver", _stub)
    monkeypatch.setattr(q, "_setup_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["quiver_scraper.py"])
    assert q.main() == exit_code


def test_main_returns_ok_on_success(monkeypatch):
    monkeypatch.setattr(q, "download_quiver", lambda *a, **k: q.QUIVER_TRADES_PATH)
    monkeypatch.setattr(q, "_setup_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["quiver_scraper.py"])
    assert q.main() == q.EXIT_OK
