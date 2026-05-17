"""Tests für utils.ticker_sector — yfinance-Wrapper.

Alle externen Calls (yf.Ticker, time.sleep) werden gemockt.
Keine echten API-Calls, kein Netzwerk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.ticker_classifier import (
    ApiStrategy,
    Confidence,
    TickerClassification,
    TickerType,
)
from utils.ticker_normalizer import NormalizedTicker
from utils.ticker_sector import SectorInfo, TickerSectorEnricher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("utils.ticker_sector.time.sleep", lambda s: None)


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "mappings"
    d.mkdir()
    return d


@pytest.fixture
def enricher(cache_dir, tmp_path):
    return TickerSectorEnricher(
        cache_dir=str(cache_dir),
        delisted_output_path=str(tmp_path / "delisted_trades_stage.csv"),
        unmatched_preferred_path=str(tmp_path / "unmatched_preferred.csv"),
        confirm_callback=lambda n: True,
    )


@pytest.fixture
def make_normalized():
    """Factory: NormalizedTicker mit minimaler eingebetteter Classification."""
    def _make(
        raw="AAPL",
        ticker_base=None,
        company_name="Apple Inc",
        ticker_type=TickerType.STOCK,
        api_strategy=ApiStrategy.YFINANCE_DIRECT,
        exch_code=None,
    ):
        cls = TickerClassification(
            raw_ticker=raw,
            company_name=company_name,
            ticker_base=ticker_base or raw,
            ticker_type=ticker_type,
            api_strategy=api_strategy,
            exch_code=exch_code,
            confidence=Confidence.HIGH,
            classification_reason="test",
            manual_review=False,
            series_letter=None,
            status_suffix=None,
        )
        return NormalizedTicker(
            classification=cls,
            figi=None, ticker_figi=None, name_figi=None,
            security_type_figi=None, market_sector_figi=None,
            normalization_errors=[],
        )
    return _make


def _yf_ticker_mock_factory(info_by_ticker: dict):
    """Erzeugt eine yf.Ticker-Replacement-Funktion mit definiertem Verhalten."""
    def _factory(ticker):
        m = MagicMock()
        m.info = info_by_ticker.get(ticker, {})
        return m
    return _factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStockTickerEnriched:
    def test_stock_ticker_enriched(self, enricher, make_normalized):
        """STOCK → yfinance-Call → Sektor/Industry im Result."""
        norm = make_normalized(raw="AAPL", ticker_type=TickerType.STOCK)
        info = {"AAPL": {
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "quoteType": "EQUITY",
        }}

        with patch("utils.ticker_sector.yf.Ticker",
                   side_effect=_yf_ticker_mock_factory(info)):
            result = enricher.enrich_batch([norm])

        assert len(result) == 1
        assert result[0].sector_yf == "Technology"
        assert result[0].industry_yf == "Consumer Electronics"
        assert result[0].quote_type_yf == "EQUITY"


class TestSkipTypesNotCalled:
    def test_skip_types_not_called(self, enricher, make_normalized):
        """WARRANT/OPTION/BOND/CRYPTO → kein yfinance-Call."""
        items = [
            make_normalized(raw="WARR", ticker_type=TickerType.WARRANT),
            make_normalized(raw="OPT", ticker_type=TickerType.OPTION),
            make_normalized(raw="BND", ticker_type=TickerType.BOND),
            make_normalized(raw="CRY", ticker_type=TickerType.CRYPTO),
        ]
        with patch("utils.ticker_sector.yf.Ticker") as mock_yf:
            results = enricher.enrich_batch(items)
        mock_yf.assert_not_called()
        for r in results:
            assert r.sector_yf is None


class TestCacheSplit:
    def test_cache_split(self, enricher, make_normalized, cache_dir):
        """Positiv → industry-cache.json, leer → 0_cache.json (getrennte Dateien)."""
        items = [
            make_normalized(raw="OK", ticker_type=TickerType.STOCK),
            make_normalized(raw="DELISTED", ticker_type=TickerType.STOCK),
        ]
        info = {
            "OK": {"sector": "Tech", "industry": "Software", "quoteType": "EQUITY"},
            "DELISTED": {},  # leer → null-Cache
        }
        with patch("utils.ticker_sector.yf.Ticker",
                   side_effect=_yf_ticker_mock_factory(info)):
            enricher.enrich_batch(items)

        positive_path = cache_dir / "ticker_industry_cache.json"
        null_path = cache_dir / "ticker_0_cache.json"

        assert positive_path.is_file()
        assert null_path.is_file()

        positive = json.loads(positive_path.read_text())
        null_list = json.loads(null_path.read_text())

        assert "OK" in positive
        assert "DELISTED" not in positive
        assert "DELISTED" in null_list
        assert "OK" not in null_list


class TestPreferredMapping:
    def test_preferred_mapping(self, cache_dir, tmp_path, make_normalized):
        """EP$C (PREFERRED) → KMI Lookup → SectorInfo mit Energy."""
        # Preferred-Mapping-CSV anlegen.
        pref_csv = tmp_path / "Vorzugsaktien_mapping.csv"
        pref_csv.write_text(
            "stammaktien_ticker,vorzugsaktien_ticker,Company\n"
            "KMI,EP$C,Kinder Morgan\n",
            encoding="utf-8",
        )

        enricher = TickerSectorEnricher(
            cache_dir=str(cache_dir),
            preferred_mapping_path=str(pref_csv),
            confirm_callback=lambda n: True,
        )

        norm = make_normalized(raw="EP$C", ticker_type=TickerType.PREFERRED)
        info = {"KMI": {"sector": "Energy", "industry": "Oil & Gas Midstream",
                         "quoteType": "EQUITY"}}

        captured_calls: list[str] = []

        def factory(ticker):
            captured_calls.append(ticker)
            m = MagicMock()
            m.info = info.get(ticker, {})
            return m

        with patch("utils.ticker_sector.yf.Ticker", side_effect=factory):
            result = enricher.enrich_batch([norm])

        assert "KMI" in captured_calls, f"yfinance sollte mit KMI gecallt werden: {captured_calls}"
        assert result[0].sector_yf == "Energy"
        assert result[0].normalized.classification.raw_ticker == "EP$C"


class TestDelistedTracking:
    def test_delisted_tracking(self, enricher, make_normalized, tmp_path):
        """Leerer Sektor → Eintrag in delisted-stage.csv."""
        norm = make_normalized(raw="GONE", ticker_type=TickerType.STOCK)

        with patch("utils.ticker_sector.yf.Ticker",
                   side_effect=_yf_ticker_mock_factory({"GONE": {}})):
            enricher.enrich_batch([norm])

        delisted_path = Path(enricher.delisted_output_path)
        assert delisted_path.is_file()
        content = delisted_path.read_text()
        assert "GONE" in content


class TestHyphenFallback:
    def test_hyphen_fallback(self, enricher, make_normalized):
        """BRK-B liefert leer → Retry mit BRK/B → Erfolg, kein Delisted."""
        norm = make_normalized(raw="BRK-B", ticker_base="BRK-B",
                                ticker_type=TickerType.SHARECLASS)

        seen_tickers: list[str] = []

        def factory(ticker):
            seen_tickers.append(ticker)
            m = MagicMock()
            if ticker == "BRK-B":
                m.info = {}
            elif ticker == "BRK/B":
                m.info = {"sector": "Financial Services",
                          "industry": "Insurance—Diversified",
                          "quoteType": "EQUITY"}
            else:
                m.info = {}
            return m

        with patch("utils.ticker_sector.yf.Ticker", side_effect=factory):
            result = enricher.enrich_batch([norm])

        assert "BRK-B" in seen_tickers
        assert "BRK/B" in seen_tickers
        assert result[0].sector_yf == "Financial Services"
        # Kein Delisted-Eintrag (Fallback war erfolgreich).
        delisted_path = Path(enricher.delisted_output_path)
        if delisted_path.is_file():
            assert "BRK-B" not in delisted_path.read_text()
