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


# ---------------------------------------------------------------------------
# Haeppchen B — ticker_normalizer: Cache-Key, classification_type, http-Fehler
# ---------------------------------------------------------------------------

def _make_cls(raw, ticker_type, company_name="Foo Corp"):
    from utils.ticker_classifier import (
        ApiStrategy, Confidence, TickerClassification,
    )
    return TickerClassification(
        raw_ticker=raw, company_name=company_name, ticker_base="ABC",
        ticker_type=ticker_type, api_strategy=ApiStrategy.FIGI_SEARCH,
        exch_code=None, confidence=Confidence.HIGH,
        classification_reason="test", manual_review=False,
        series_letter=None, status_suffix=None,
    )


@pytest.fixture
def normalizer(tmp_path, monkeypatch):
    from utils.ticker_normalizer import TickerNormalizer
    monkeypatch.setattr("utils.ticker_normalizer.time.sleep", lambda s: None)
    return TickerNormalizer(cache_db_path=str(tmp_path / "figi.db"),
                            api_key="fake")


class TestSearchCacheKeyPerTickerType:
    def test_warrant_and_unit_get_distinct_search_keys(self, normalizer):
        """SPAC-Paar: Unit darf den Warrant-FIGI nicht aus dem Cache erben."""
        warrant = _make_cls("ABC-W", TickerType.WARRANT)
        unit = _make_cls("ABC.U", TickerType.UNIT)
        key_w = normalizer._cache_key(warrant, "SEARCH")
        key_u = normalizer._cache_key(unit, "SEARCH")
        assert key_w != key_u

    def test_mapping_key_format_unchanged(self, normalizer):
        """TICKER-Keys behalten das alte Format (kein Cache-Invalidieren)."""
        cls = _make_cls("ABC", TickerType.STOCK)
        assert normalizer._cache_key(cls, "TICKER") == "ABC:TICKER:*"


class TestHttpErrorNotCached:
    def test_search_http_error_no_negative_cache(self, normalizer, monkeypatch):
        cls = _make_cls("ABC.U", TickerType.UNIT)
        monkeypatch.setattr(normalizer, "_api_post",
                            lambda url, body: (None, "http"))
        results = normalizer._process_search([cls])
        assert results[0].figi is None
        assert "http_error" in results[0].normalization_errors
        # Kein Cache-Eintrag geschrieben:
        assert normalizer._cache_lookup(
            normalizer._cache_key(cls, "SEARCH")) is None

    def test_mapping_http_error_no_search_fallback(
            self, normalizer, monkeypatch):
        cls = _make_cls("ABC", TickerType.STOCK)
        monkeypatch.setattr(normalizer, "_api_post",
                            lambda url, body: (None, "http"))

        def _fail(items):
            raise AssertionError("Search-Fallback darf bei http nicht laufen")

        monkeypatch.setattr(normalizer, "_process_search", _fail)
        results = normalizer._process_mapping([cls])
        assert results[0].figi is None
        assert "http_error" in results[0].normalization_errors
        assert normalizer._cache_lookup(
            normalizer._cache_key(cls, "TICKER")) is None


# ---------------------------------------------------------------------------
# Haeppchen C — misc utils
# ---------------------------------------------------------------------------

class TestTradeIdCsvRoundtrip:
    def test_normalized_dates_reproduce_pipeline_ids(self, tmp_path):
        """CSV-Roundtrip: Hash nach pd.to_datetime == Pipeline-Hash (Timestamps)."""
        from utils.trade_id import TRADE_ID_SOURCE_COLUMNS, compute_trade_ids
        df = pd.DataFrame({
            'name': ['Doe, John'], 'ticker': ['AAPL'],
            'asset_name': ['Apple Inc.'], 'transaction_type': ['Purchase'],
            'amount': ['$1,001 - $15,000'],
            'transaction_date': pd.to_datetime(['2026-06-16']),
            'subholding': [''], 'comment': [''],
        })
        pipeline_ids = compute_trade_ids(df)
        p = tmp_path / 'master.csv'
        df.to_csv(p, index=False)
        loaded = pd.read_csv(p).fillna('')
        raw_ids = compute_trade_ids(loaded)
        for col in ('transaction_date',):
            if col in TRADE_ID_SOURCE_COLUMNS:
                loaded[col] = pd.to_datetime(loaded[col], errors='coerce')
        fixed_ids = compute_trade_ids(loaded)
        assert list(fixed_ids) == list(pipeline_ids)
        assert list(raw_ids) != list(pipeline_ids)  # fixiert die Fehlerursache


class TestPdNaHandling:
    def test_issuer_ticker_is_empty_catches_pd_na(self):
        from utils.issuer_ticker import _is_empty
        assert _is_empty(pd.NA) is True
        assert _is_empty(pd.NaT) is True
        assert _is_empty(float('nan')) is True
        assert _is_empty('AAPL') is False

    def test_issuer_group_mapper_get_str_catches_pd_na(self):
        from utils.issuer_group_mapper import _get_str
        assert _get_str({'k': pd.NA}, 'k') == ''
        assert _get_str({'k': pd.NaT}, 'k') == ''
        assert _get_str({'k': ' X '}, 'k') == 'X'


class TestTransientClassificationEdges:
    def test_http_error_without_code_falls_back_to_text(self):
        import requests
        from utils.network_retry import is_transient_exception
        exc = requests.HTTPError('503 Service Unavailable')
        assert getattr(exc, 'response', None) is None
        assert is_transient_exception(exc) is True

    def test_ssl_config_error_is_permanent(self):
        from utils.network_retry import is_transient_exception
        assert is_transient_exception(
            ValueError('ssl certificate path invalid')) is False

    def test_sslerror_still_transient(self):
        from utils.network_retry import is_transient_exception
        assert is_transient_exception(
            Exception('SSLError: handshake failed')) is True


class TestClassificationTypeColumn:
    def test_cache_write_stores_real_type(self, normalizer):
        import sqlite3
        payload = {"figi": "BBG000TEST", "ticker_figi": "ABC",
                   "asset_name_figi": None, "security_type_figi": None,
                   "raw_security_type": None, "raw_security_type2": None,
                   "market_sector_figi": None, "share_class_figi": "BBG001SC",
                   "source": "mapping"}
        normalizer._cache_write("ABC:TICKER:*", payload, "stock")
        with sqlite3.connect(normalizer.cache_db_path) as conn:
            row = conn.execute(
                "SELECT classification_type, source FROM ticker_normalizer "
                "WHERE cache_key = 'ABC:TICKER:*'").fetchone()
        assert row == ("stock", "mapping")
