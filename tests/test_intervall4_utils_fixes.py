"""Regressionstests fuer die Debug-Intervall-4-Fixes (utils/ + scripts/).

Ein File fuer alle Intervall-4-Haeppchen; pro Haeppchen eine Testklasse.
"""

import pandas as pd
import pytest

from utils.ticker_classifier import TickerType, classify


# ---------------------------------------------------------------------------
# Haeppchen A — ticker_classifier: T-Bill-Ziffernpflicht + Muni-Wortgrenzen
# ---------------------------------------------------------------------------

class TestTbillDigitRequired:
    def test_alpha_words_are_not_treasury(self):
        """9-10-buchstabige Woerter mit T duerfen NICHT still Treasury werden."""
        for token in ("TRANSPORT", "TELEFONICA", "TREASURIES"):
            cls = classify(token, "Foo Corp")
            assert cls.ticker_type != TickerType.TREASURY, token
            assert cls.manual_review is True, token

    def test_real_tbill_still_treasury(self):
        for token in ("T1234567A8", "T12345678"):
            cls = classify(token, "")
            assert cls.ticker_type == TickerType.TREASURY, token
            assert cls.manual_review is False


class TestMuniWordBoundary:
    def test_authentic_spac_unit_not_muni(self):
        """Substring 'AUTH' in AUTHENTIC darf den Muni-Override nicht triggern."""
        cls = classify("AEAC-U", "AUTHENTIC EQUITY ACQUISITION CORP UNITS")
        assert cls.ticker_type == TickerType.UNIT
        assert cls.classification_reason != "MUNI_DETECTED"

    def test_real_muni_still_bond(self):
        cls = classify("BLOC-U", "HARRIS CNTY TEX REV BDS")
        assert cls.ticker_type == TickerType.BOND
        assert cls.classification_reason == "MUNI_DETECTED"


# ---------------------------------------------------------------------------
# Haeppchen A — ticker_sector: permanenter Fehler vergiftet Null-Cache nicht
# ---------------------------------------------------------------------------

class TestNullCacheNotPoisonedByPermanentError:
    def _enricher(self, tmp_path, monkeypatch, error):
        from utils import ticker_sector as ts

        enricher = ts.TickerSectorEnricher(
            cache_dir=str(tmp_path), auto_confirm=True, pause_seconds=0,
        )
        enricher._loaded = True

        def _raise(_ticker):
            raise error

        # retry_call im Modul so patchen, dass der permanente Fehler direkt
        # durchschlaegt (is_transient=False-Aequivalent).
        monkeypatch.setattr(
            ts, "retry_call",
            lambda fetch, **kw: _raise(None),
        )
        return enricher

    def test_permanent_error_keeps_ticker_out_of_null_cache(
            self, tmp_path, monkeypatch):
        enricher = self._enricher(tmp_path, monkeypatch, ValueError("kaputt"))
        enricher._fetch_misses(["GOODTICKER"])
        assert "GOODTICKER" not in enricher._null_cache
        assert "GOODTICKER" not in enricher._positive_cache

    def test_retry_round_permanent_error_not_null_cached(
            self, tmp_path, monkeypatch):
        enricher = self._enricher(tmp_path, monkeypatch, ValueError("kaputt"))
        enricher._retry_queue.append(
            enricher._fake_normalized_for_retry("GOODTICKER"))
        enricher._run_retry_round()
        assert "GOODTICKER" not in enricher._null_cache
