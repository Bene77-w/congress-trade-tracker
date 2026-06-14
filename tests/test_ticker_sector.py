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
from utils.network_canary import NetworkCanary
from utils.ticker_normalizer import NormalizedTicker
from utils.ticker_sector import SectorInfo, TickerSectorEnricher


def _fixed_canary(live: bool) -> NetworkCanary:
    """Fixierter Canary (GRUEN=live True / ROT=False) ohne echte Probe/Warten."""
    return NetworkCanary(lambda t: live, basket=["X"], min_positive=1,
                         verbose=False, sleep_fn=lambda s: None)


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
    # GRUEN-Canary: Netz gesund -> leeres .info gilt als echtes Delisting/kein
    # Sektor (statt der abgeloesten _network_healthy-Heuristik).
    return TickerSectorEnricher(
        cache_dir=str(cache_dir),
        delisted_output_path=str(tmp_path / "delisted_trades_stage.csv"),
        confirm_callback=lambda n: True,
        canary=_fixed_canary(True),
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
            figi=None, ticker_figi=None, asset_name_figi=None,
            security_type_figi=None, market_sector_figi=None,
            share_class_figi=None,
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


class TestPreferredParentLookup:
    def test_preferred_parent_ticker_used(self, enricher, make_normalized):
        """PREFERRED → yfinance-Call auf parent_ticker (Common-Stamm)."""
        norm = make_normalized(
            raw="EP$C",
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
        )
        # Classifier-Output simulieren: parent_ticker fuer EP$C ist EP.
        from dataclasses import replace
        norm = NormalizedTicker(
            classification=replace(norm.classification, parent_ticker="EP"),
            figi=None, ticker_figi=None, asset_name_figi=None,
            security_type_figi=None, market_sector_figi=None,
            share_class_figi=None,
            normalization_errors=[],
        )
        info = {"EP": {"sector": "Energy", "industry": "Oil & Gas Midstream",
                       "quoteType": "EQUITY"}}

        captured_calls: list[str] = []

        def factory(ticker):
            captured_calls.append(ticker)
            m = MagicMock()
            m.info = info.get(ticker, {})
            return m

        with patch("utils.ticker_sector.yf.Ticker", side_effect=factory):
            result = enricher.enrich_batch([norm])

        assert "EP" in captured_calls, f"yfinance sollte mit EP gecallt werden: {captured_calls}"
        assert result[0].sector_yf == "Energy"
        assert result[0].normalized.classification.raw_ticker == "EP$C"


class TestDelistedTracking:
    def test_delisted_tracking(self, enricher, make_normalized, tmp_path):
        """Echtes Delisting (leerer Sektor bei GESUNDEM Netz) → delisted-stage.csv.

        Ein leeres .info ist nicht von einem Netzfehler unterscheidbar; erst wenn
        das Netz gesund wirkt (mind. ein echter Sektor-Treffer im Lauf), gilt leer
        als echtes Delisting. Daher hier ein Live-Ticker vor GONE.
        """
        items = [
            make_normalized(raw="LIVE", ticker_type=TickerType.STOCK),
            make_normalized(raw="GONE", ticker_type=TickerType.STOCK),
        ]
        info = {
            "LIVE": {"sector": "Tech", "industry": "Software", "quoteType": "EQUITY"},
            "GONE": {},
        }
        with patch("utils.ticker_sector.yf.Ticker",
                   side_effect=_yf_ticker_mock_factory(info)):
            enricher.enrich_batch(items)

        delisted_path = Path(enricher.delisted_output_path)
        assert delisted_path.is_file()
        content = delisted_path.read_text()
        assert "GONE" in content


class TestDelistedWriteGatedByFreshProbe:
    def test_stale_green_suppresses_delisted_write(
        self, cache_dir, tmp_path, make_normalized
    ):
        """Stale-GRUEN beim Sammeln, aber ROT bei frischer Probe -> kein Report.

        Rest-Risiko-Mitigation: ein zu Ausfall-Beginn noch gruenes (gecachtes)
        Verdikt darf keine falschen Delisted-Eintraege verewigen. Der persistente
        Schreibvorgang ist daher an eine FRISCHE force_reprobe()-Probe gekoppelt.
        """
        live = {"v": True}
        canary = NetworkCanary(
            lambda t: live["v"], basket=["X"], min_positive=1,
            verbose=False, sleep_fn=lambda s: None,
        )
        # Verdikt einmal GRUEN cachen (stale), dann Netz auf ROT kippen.
        assert canary.verdict() == "GREEN"
        live["v"] = False  # Netz faellt aus; gecachtes Verdikt bleibt erst GRUEN

        enricher = TickerSectorEnricher(
            cache_dir=str(cache_dir),
            delisted_output_path=str(tmp_path / "delisted.csv"),
            confirm_callback=lambda n: True,
            canary=canary,
        )
        norm = make_normalized(raw="GONE", ticker_type=TickerType.STOCK)
        info = {"GONE": {}}  # leer -> waehrend stale-GRUEN als delisted getrackt
        with patch("utils.ticker_sector.yf.Ticker",
                   side_effect=_yf_ticker_mock_factory(info)):
            enricher.enrich_batch([norm])

        delisted_path = Path(enricher.delisted_output_path)
        assert not delisted_path.is_file(), (
            "Delisted-Report darf bei ROT-Probe am Batch-Ende nicht "
            "geschrieben werden"
        )

    def test_fresh_green_writes_delisted(
        self, cache_dir, tmp_path, make_normalized
    ):
        """Frische Probe GRUEN am Batch-Ende -> Report wird normal geschrieben."""
        canary = NetworkCanary(
            lambda t: True, basket=["X"], min_positive=1,
            verbose=False, sleep_fn=lambda s: None,
        )
        enricher = TickerSectorEnricher(
            cache_dir=str(cache_dir),
            delisted_output_path=str(tmp_path / "delisted.csv"),
            confirm_callback=lambda n: True,
            canary=canary,
        )
        norm = make_normalized(raw="GONE", ticker_type=TickerType.STOCK)
        info = {"GONE": {}}
        with patch("utils.ticker_sector.yf.Ticker",
                   side_effect=_yf_ticker_mock_factory(info)):
            enricher.enrich_batch([norm])

        delisted_path = Path(enricher.delisted_output_path)
        assert delisted_path.is_file()
        assert "GONE" in delisted_path.read_text()


# ---------------------------------------------------------------------------
# _confirm_large_batch — Auto-Confirm / Bypass-Logik
# ---------------------------------------------------------------------------


class TestConfirmLargeBatch:
    def test_auto_confirm_skips_input(self, cache_dir, tmp_path, monkeypatch):
        """auto_confirm=True → True ohne input()-Aufruf."""
        enricher = TickerSectorEnricher(
            cache_dir=str(cache_dir),
            delisted_output_path=str(tmp_path / "delisted.csv"),
            auto_confirm=True,
        )

        def _boom(*_a, **_k):
            raise AssertionError("input() darf bei auto_confirm nicht aufgerufen werden")

        monkeypatch.setattr("builtins.input", _boom)
        assert enricher._confirm_large_batch(500, 12.3) is True

    def test_callback_has_priority_over_auto_confirm(self, cache_dir, tmp_path, monkeypatch):
        """confirm_callback hat Vorrang vor auto_confirm."""
        calls = []
        enricher = TickerSectorEnricher(
            cache_dir=str(cache_dir),
            delisted_output_path=str(tmp_path / "delisted.csv"),
            auto_confirm=True,
            confirm_callback=lambda n: calls.append(n) or False,
        )
        monkeypatch.setattr(
            "builtins.input",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("input() darf nicht aufgerufen werden")
            ),
        )
        assert enricher._confirm_large_batch(500, 1.0) is False
        assert calls == [500]

    def test_non_interactive_stdin_auto_confirms(self, cache_dir, tmp_path, monkeypatch):
        """Kein TTY (z. B. Scheduler) → True ohne input(), trotz auto_confirm=False."""
        enricher = TickerSectorEnricher(
            cache_dir=str(cache_dir),
            delisted_output_path=str(tmp_path / "delisted.csv"),
            auto_confirm=False,
        )
        monkeypatch.setattr("utils.ticker_sector.sys.stdin.isatty", lambda: False)

        def _boom(*_a, **_k):
            raise AssertionError("input() darf ohne TTY nicht aufgerufen werden")

        monkeypatch.setattr("builtins.input", _boom)
        assert enricher._confirm_large_batch(500, 1.0) is True


