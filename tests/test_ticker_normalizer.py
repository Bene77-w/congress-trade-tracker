"""Tests für utils.ticker_normalizer — OpenFIGI-Wrapper.

Alle externen Calls (requests.post, time.sleep) werden gemockt.
Keine echten API-Calls.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import requests

from utils.ticker_classifier import (
    ApiStrategy,
    Confidence,
    TickerClassification,
    TickerType,
)
from utils.ticker_normalizer import NormalizedTicker, TickerNormalizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Sleep aushebeln — Tests dürfen nicht warten."""
    monkeypatch.setattr("utils.ticker_normalizer.time.sleep", lambda s: None)


@pytest.fixture
def normalizer(tmp_path):
    """Normalizer mit temporärer SQLite-DB."""
    db_path = tmp_path / "openfigi.db"
    return TickerNormalizer(cache_db_path=str(db_path), api_key="fake-api-key")


@pytest.fixture
def make_classification():
    """Factory: TickerClassification mit sinnvollen Defaults."""
    def _make(
        raw="AAPL",
        ticker_base=None,
        company_name="Apple Inc",
        ticker_type=TickerType.STOCK,
        api_strategy=ApiStrategy.FIGI_MAPPING,
        exch_code="US",
        confidence=Confidence.HIGH,
        classification_reason="default_test",
    ):
        return TickerClassification(
            raw_ticker=raw,
            company_name=company_name,
            ticker_base=ticker_base or raw,
            ticker_type=ticker_type,
            api_strategy=api_strategy,
            exch_code=exch_code,
            confidence=confidence,
            classification_reason=classification_reason,
            manual_review=False,
            series_letter=None,
            status_suffix=None,
        )
    return _make


def _http_response(status_code=200, json_data=None):
    """Baut ein Mock-Response-Objekt im requests-Stil."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = "" if json_data is None else str(json_data)
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkipStrategy:
    def test_skip_strategy_returns_empty(self, normalizer, make_classification):
        """SKIP → kein API-Call, leere NormalizedTicker."""
        cls = make_classification(
            raw="SPY160219P00180000",
            ticker_type=TickerType.OPTION,
            api_strategy=ApiStrategy.SKIP,
        )
        with patch("utils.ticker_normalizer.requests.post") as mock_post:
            result = normalizer.normalize_batch([cls])

        mock_post.assert_not_called()
        assert len(result) == 1
        assert result[0].figi is None
        assert result[0].ticker_figi is None
        assert result[0].classification == cls


class TestNetworkErrorNotCached:
    def test_network_error_not_cached(self, normalizer, make_classification):
        """Network-Error: Cache bleibt leer, Item in Retry-Queue."""
        cls = make_classification(raw="AAPL", api_strategy=ApiStrategy.FIGI_MAPPING)

        with patch(
            "utils.ticker_normalizer.requests.post",
            side_effect=requests.ConnectionError("Could not resolve host"),
        ):
            normalizer.normalize_batch([cls])

        # Cache: kein Eintrag für AAPL.
        with sqlite3.connect(normalizer.cache_db_path) as conn:
            row = conn.execute(
                "SELECT cache_key FROM ticker_normalizer WHERE cache_key = ?",
                (TickerNormalizer._cache_key(cls, "TICKER"),),
            ).fetchone()
        assert row is None


class TestConsecutiveErrorsAbort:
    def test_consecutive_errors_trigger_abort(self, normalizer, make_classification):
        """5 consecutive Network-Errors → RuntimeError."""
        # FIGI_SEARCH macht pro Item einen API-Call → 5 Items = 5 Calls.
        items = [
            make_classification(
                raw=f"T{i}", company_name=f"Company {i}",
                api_strategy=ApiStrategy.FIGI_SEARCH,
            )
            for i in range(10)
        ]

        with patch(
            "utils.ticker_normalizer.requests.post",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            with pytest.raises(RuntimeError, match="network errors"):
                normalizer.normalize_batch(items)


class TestBatchSizeLimit:
    def test_batch_size_limit(self, normalizer, make_classification):
        """150 Items → mehrere Batches, alle ≤100."""
        items = [
            make_classification(raw=f"T{i}", api_strategy=ApiStrategy.FIGI_MAPPING)
            for i in range(150)
        ]

        captured_batches: list[int] = []

        def fake_post(url, json=None, headers=None, timeout=None):
            # Mapping-Call: json ist eine Liste mit jobs.
            if isinstance(json, list):
                captured_batches.append(len(json))
                # Leeres data für jedes Job → routet zum Search-Fallback.
                # Search-Calls (dict-body) zählen wir nicht für Batch-Limit.
                return _http_response(200, [{"data": [{"ticker": j["idValue"], "figi": "BBG-X"}]}
                                            for j in json])
            return _http_response(200, {"data": []})

        with patch("utils.ticker_normalizer.requests.post", side_effect=fake_post):
            normalizer.normalize_batch(items)

        # Mit api_key sind Batches max 100.
        assert captured_batches, "Es sollten Mapping-Batches gemacht werden"
        assert all(size <= 100 for size in captured_batches), \
            f"Mind. ein Batch zu groß: {captured_batches}"
        assert sum(captured_batches) == 150


class TestFallbackMappingToSearch:
    def test_fallback_mapping_to_search(self, normalizer, make_classification):
        """Mapping liefert leer → Fallback /v3/search wird gerufen."""
        cls = make_classification(
            raw="OBSCURE", company_name="Obscure Co",
            api_strategy=ApiStrategy.FIGI_MAPPING,
        )

        urls_called: list[str] = []

        def fake_post(url, json=None, headers=None, timeout=None):
            urls_called.append(url)
            if "/mapping" in url:
                # Mapping → 'warning' → leeres Result.
                return _http_response(200, [{"warning": "No identifier found"}])
            # Search → erfolgreich.
            return _http_response(200, {
                "data": [{"ticker": "OBSCURE", "figi": "BBG-OBSCURE",
                          "name": "Obscure Co", "securityType": "Common Stock"}],
            })

        with patch("utils.ticker_normalizer.requests.post", side_effect=fake_post):
            result = normalizer.normalize_batch([cls])

        assert any("/mapping" in u for u in urls_called)
        assert any("/search" in u for u in urls_called)
        assert result[0].figi == "BBG-OBSCURE"
        assert result[0].name_figi == "Obscure Co"


class TestTypeCorrectionStockToEtf:
    def test_type_correction_stock_to_etf(self, normalizer, make_classification):
        """OpenFIGI securityType2='ETP' bei STOCK → ticker_type wird auf ETF korrigiert."""
        cls = make_classification(
            raw="SPY", ticker_type=TickerType.STOCK,
            api_strategy=ApiStrategy.FIGI_MAPPING,
            classification_reason="entry_v1",
        )

        payload = [{
            "data": [{
                "ticker": "SPY", "figi": "BBG000BDTBL9",
                "securityType": "ETP",
                "securityType2": "ETP",
                "name": "SPDR S&P 500 ETF Trust",
                "marketSector": "Equity",
            }],
        }]

        with patch(
            "utils.ticker_normalizer.requests.post",
            return_value=_http_response(200, payload),
        ):
            result = normalizer.normalize_batch([cls])

        assert result[0].classification.ticker_type == TickerType.ETF
        assert "+openfigi_corrected" in result[0].classification.classification_reason
        # Original-Reason bleibt erhalten.
        assert result[0].classification.classification_reason.startswith("entry_v1")
        assert result[0].figi == "BBG000BDTBL9"
