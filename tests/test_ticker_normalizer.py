"""Tests für utils.ticker_normalizer — OpenFIGI-Wrapper.

Alle externen Calls (requests.post, time.sleep) werden gemockt.
Keine echten API-Calls.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from utils.ticker_classifier import (
    ApiStrategy,
    Confidence,
    TickerClassification,
    TickerType,
    classify,
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
        assert result[0].asset_name_figi == "Obscure Co"


# Echte /v3/mapping-Antworten (2026-11-19 live aufgezeichnet, ohne API-Key),
# je Job-idValue. Enthaelt BRK.B als Gegenprobe fuer das alte Format.
_RECORDED_MAPPING = json.loads(
    (Path(__file__).parent / "fixtures" / "openfigi" / "mapping_responses.json")
    .read_text(encoding="utf-8")
)


def _recorded_post(url, json=None, headers=None, timeout=None):
    """requests.post-Ersatz: beantwortet jeden Job mit der aufgezeichneten Antwort."""
    return _http_response(200, [_RECORDED_MAPPING[job["idValue"]] for job in json])


class TestRecordedOpenFigiResponses:
    """H1/H3 gegen aufgezeichnete echte OpenFIGI-Antworten statt Mock-Payloads."""

    def _normalize(self, normalizer, raw_tickers):
        with patch("utils.ticker_normalizer.requests.post", side_effect=_recorded_post):
            return {r.classification.raw_ticker: r
                    for r in normalizer.normalize_batch([classify(t) for t in raw_tickers])}

    def test_etp_becomes_etf(self, normalizer):
        """H1: securityType='ETP' (securityType2='Mutual Fund') → STOCK wird ETF."""
        result = self._normalize(normalizer, ["SPY", "QQQ"])
        for tkr in ("SPY", "QQQ"):
            assert result[tkr].classification.ticker_type == TickerType.ETF
            assert result[tkr].classification.classification_reason.endswith(
                "+openfigi_corrected")
        assert result["SPY"].figi == "BBG000BDTBL9"

    def test_common_stock_stays_stock(self, normalizer):
        result = self._normalize(normalizer, ["AAPL"])
        assert result["AAPL"].classification.ticker_type == TickerType.STOCK
        assert result["AAPL"].figi == "BBG000B9XRY4"

    def test_share_class_gets_figi(self, normalizer):
        """H3: BRK.B/BF.B gehen als BRK/B bzw. BF/B an OpenFIGI und bekommen FIGIs."""
        result = self._normalize(normalizer, ["BRK.B", "BF.B"])
        assert result["BRK.B"].figi == "BBG000DWG505"
        assert result["BRK.B"].share_class_figi == "BBG001S90346"
        assert result["BF.B"].figi == "BBG000BD2NY8"
        assert result["BRK.B"].classification.ticker_type == TickerType.SHARECLASS


# ---------------------------------------------------------------------------
# P5: share_class_figi + Router-Erweiterung (YFINANCE_DIRECT, MANUAL_REVIEW)
# ---------------------------------------------------------------------------


def _mapping_response_with_share_class(ticker, figi, share_class_figi,
                                       name="Test Co", security_type="Common Stock"):
    """Mapping-Response-Payload mit shareClassFIGI-Feld."""
    return [{"data": [{
        "ticker": ticker, "figi": figi,
        "name": name, "securityType": security_type,
        "marketSector": "Equity",
        "shareClassFIGI": share_class_figi,
    }]}]


class TestShareClassFigiSchema:
    def test_normalized_ticker_has_share_class_figi(self):
        """Dataclass-Feld share_class_figi vorhanden."""
        assert "share_class_figi" in NormalizedTicker.__dataclass_fields__

    def test_parse_data_item_extracts_share_class_figi(self):
        """Response mit shareClassFIGI wird korrekt geparst."""
        from utils.ticker_normalizer import TickerNormalizer as TN
        item = {
            "ticker": "GOOGL", "figi": "BBG009S39JX6",
            "name": "Alphabet Inc.", "securityType": "Common Stock",
            "marketSector": "Equity",
            "shareClassFIGI": "BBG009S3NB30",
        }
        parsed = TN._parse_data_item(item, source="mapping")
        assert parsed["share_class_figi"] == "BBG009S3NB30"
        assert parsed["figi"] == "BBG009S39JX6"

    def test_parse_data_item_without_share_class_figi(self):
        """Response ohne shareClassFIGI → None."""
        from utils.ticker_normalizer import TickerNormalizer as TN
        item = {"ticker": "X", "figi": "BBG-X", "name": "X Co"}
        parsed = TN._parse_data_item(item, source="mapping")
        assert parsed["share_class_figi"] is None


class TestShareClassFigiCacheRoundtrip:
    def test_cache_roundtrip_share_class_figi(self, normalizer, make_classification):
        """Hit -> Cache schreibt share_class_figi -> Lookup gibt es zurueck."""
        cls = make_classification(raw="GOOGL", api_strategy=ApiStrategy.FIGI_MAPPING)
        payload = _mapping_response_with_share_class(
            "GOOGL", "BBG009S39JX6", "BBG009S3NB30", "Alphabet Inc."
        )

        with patch("utils.ticker_normalizer.requests.post",
                   return_value=_http_response(200, payload)):
            normalizer.normalize_batch([cls])

        cached = normalizer._cache_lookup(TickerNormalizer._cache_key(cls, "TICKER"))
        assert cached is not None
        assert cached["share_class_figi"] == "BBG009S3NB30"
        assert cached["figi"] == "BBG009S39JX6"

    def test_cache_migration_adds_column(self, tmp_path):
        """Alte DB ohne share_class_figi-Spalte → _ensure_db macht ALTER TABLE."""
        db_path = tmp_path / "openfigi.db"
        # Alte Schema-Variante simulieren (ohne share_class_figi)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                CREATE TABLE ticker_normalizer (
                    cache_key           TEXT PRIMARY KEY,
                    classification_type TEXT NOT NULL,
                    figi                TEXT,
                    ticker_figi         TEXT,
                    asset_name_figi     TEXT,
                    security_type_figi  TEXT,
                    raw_security_type   TEXT,
                    raw_security_type2  TEXT,
                    market_sector_figi  TEXT,
                    source              TEXT NOT NULL,
                    checked_date        TEXT NOT NULL
                )
            """)

        n = TickerNormalizer(cache_db_path=str(db_path), api_key="fake")
        n._ensure_db()

        with sqlite3.connect(str(db_path)) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(ticker_normalizer)")}
        assert "share_class_figi" in cols

    def test_cache_invalidation_for_missing_share_class_figi(
        self, tmp_path, make_classification
    ):
        """Alter Hit mit figi NOT NULL und share_class_figi NULL → Cache-Miss → API-Call."""
        db_path = tmp_path / "openfigi.db"
        n = TickerNormalizer(cache_db_path=str(db_path), api_key="fake")
        # Schema initialisieren
        n._ensure_db()

        cls = make_classification(raw="AAPL", api_strategy=ApiStrategy.FIGI_MAPPING)
        key = TickerNormalizer._cache_key(cls, "TICKER")
        # Alten Hit ohne share_class_figi simulieren
        from datetime import datetime as _dt
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                INSERT INTO ticker_normalizer
                    (cache_key, classification_type, figi, ticker_figi, asset_name_figi,
                     security_type_figi, raw_security_type, raw_security_type2,
                     market_sector_figi, share_class_figi, source, checked_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, "mapping", "BBG000B9XRY4", "AAPL", "Apple Inc",
                "Common Stock", "Common Stock", "Common Stock",
                "Equity", None, "mapping", _dt.now().isoformat(),
            ))

        # Cache-Lookup soll None liefern (Miss) → API wird gerufen
        payload = _mapping_response_with_share_class(
            "AAPL", "BBG000B9XRY4", "BBG001S5N8V8", "Apple Inc"
        )
        with patch("utils.ticker_normalizer.requests.post",
                   return_value=_http_response(200, payload)) as mock_post:
            result = n.normalize_batch([cls])

        mock_post.assert_called_once()
        assert result[0].share_class_figi == "BBG001S5N8V8"

    def test_cache_invalidation_skips_negative_hits(self, tmp_path, make_classification):
        """Negativ-Cache-Eintraege (source='empty') bleiben gueltig trotz NULL share_class_figi."""
        db_path = tmp_path / "openfigi.db"
        n = TickerNormalizer(cache_db_path=str(db_path), api_key="fake")
        n._ensure_db()

        cls = make_classification(raw="WEIRD", api_strategy=ApiStrategy.FIGI_MAPPING)
        key = TickerNormalizer._cache_key(cls, "TICKER")
        from datetime import datetime as _dt
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                INSERT INTO ticker_normalizer
                    (cache_key, classification_type, figi, ticker_figi, asset_name_figi,
                     security_type_figi, raw_security_type, raw_security_type2,
                     market_sector_figi, share_class_figi, source, checked_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, "empty", None, None, None, None, None, None, None, None,
                "empty", _dt.now().isoformat(),
            ))

        cached = n._cache_lookup(key)
        # Negativ-Cache greift weiter, kein API-Call noetig.
        assert cached is not None
        assert cached["source"] == "empty"
        assert cached["figi"] is None


class TestOptimisticMappingRouter:
    def test_yfinance_direct_invokes_openfigi(self, normalizer, make_classification):
        """YFINANCE_DIRECT triggert /v3/mapping, share_class_figi wird gesetzt."""
        cls = make_classification(
            raw="SPY", ticker_type=TickerType.ETF,
            api_strategy=ApiStrategy.YFINANCE_DIRECT,
        )
        payload = _mapping_response_with_share_class(
            "SPY", "BBG000BDTBL9", "BBG001S6DZG3", "SPDR S&P 500 ETF Trust", "ETP"
        )

        urls = []
        def fake_post(url, json=None, headers=None, timeout=None):
            urls.append(url)
            return _http_response(200, payload)

        with patch("utils.ticker_normalizer.requests.post", side_effect=fake_post):
            result = normalizer.normalize_batch([cls])

        assert any("/mapping" in u for u in urls)
        assert result[0].share_class_figi == "BBG001S6DZG3"
        assert result[0].figi == "BBG000BDTBL9"

    def test_yfinance_direct_negative_cache(self, normalizer, make_classification):
        """YFINANCE_DIRECT Miss → Negativ-Cache, zweiter Aufruf macht KEINEN API-Call."""
        cls = make_classification(
            raw="XYZ123", ticker_type=TickerType.NUMERIC_SUFFIX,
            api_strategy=ApiStrategy.YFINANCE_DIRECT,
        )
        # Mapping antwortet mit 'warning' → empty
        miss_payload = [{"warning": "No identifier found"}]

        with patch("utils.ticker_normalizer.requests.post",
                   return_value=_http_response(200, miss_payload)) as mock_post:
            normalizer.normalize_batch([cls])

        first_calls = mock_post.call_count
        assert first_calls >= 1

        # Zweiter Lauf: Cache-Hit auf 'empty', kein neuer API-Call
        with patch("utils.ticker_normalizer.requests.post",
                   return_value=_http_response(200, miss_payload)) as mock_post2:
            result = normalizer.normalize_batch([cls])

        mock_post2.assert_not_called()
        assert result[0].figi is None
        assert result[0].share_class_figi is None

    def test_manual_review_invokes_openfigi(self, normalizer, make_classification):
        """MANUAL_REVIEW triggert /v3/mapping (sofern ticker_base nicht leer)."""
        cls = make_classification(
            raw="OBSCUREX", ticker_base="OBSCUREX",
            ticker_type=TickerType.UNKNOWN,
            api_strategy=ApiStrategy.MANUAL_REVIEW,
        )
        payload = _mapping_response_with_share_class(
            "OBSCUREX", "BBG-OBSCUREX", "BBG-OBSCUREX-CLASS", "Obscure Inc"
        )

        with patch("utils.ticker_normalizer.requests.post",
                   return_value=_http_response(200, payload)) as mock_post:
            result = normalizer.normalize_batch([cls])

        mock_post.assert_called()
        assert result[0].share_class_figi == "BBG-OBSCUREX-CLASS"

    def test_manual_review_empty_ticker_base_no_call(self, normalizer, make_classification):
        """MANUAL_REVIEW mit leerem ticker_base macht KEINEN API-Call."""
        cls = make_classification(
            raw="", ticker_base="",
            ticker_type=TickerType.INVALID,
            api_strategy=ApiStrategy.MANUAL_REVIEW,
        )

        with patch("utils.ticker_normalizer.requests.post") as mock_post:
            result = normalizer.normalize_batch([cls])

        mock_post.assert_not_called()
        assert result[0].figi is None
        assert "manual_review_no_ticker" in result[0].normalization_errors

    def test_skip_strategy_still_no_api_call(self, normalizer, make_classification):
        """Regression: SKIP loest weiterhin KEINEN API-Call aus."""
        cls = make_classification(
            raw="AAPL250101C00100000",
            ticker_type=TickerType.OPTION,
            api_strategy=ApiStrategy.SKIP,
        )

        with patch("utils.ticker_normalizer.requests.post") as mock_post:
            result = normalizer.normalize_batch([cls])

        mock_post.assert_not_called()
        assert result[0].share_class_figi is None


# ---------------------------------------------------------------------------
# Dedup + Negativ-Cache: keine doppelten API-Calls und Log-Zeilen bei
# wiederkehrenden Tickern (Fixes A+B).
# ---------------------------------------------------------------------------


class TestDedupIdenticalClassifications:
    def test_identical_classifications_call_api_once(
        self, normalizer, make_classification
    ):
        """8 identische Classifications → genau 1 Mapping-Request, 8 Outputs."""
        cls = make_classification(
            raw="AAM-PA", ticker_base="AAM",
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
        )

        post_calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            post_calls.append((url, json))
            if "/mapping" in url:
                return _http_response(200, [{"warning": "No identifier found"}])
            return _http_response(200, {"data": []})

        with patch("utils.ticker_normalizer.requests.post", side_effect=fake_post):
            results = normalizer.normalize_batch([cls] * 8)

        # 8 Resultate in Eingabe-Reihenfolge
        assert len(results) == 8
        # Genau 1 Mapping-Call (Dedup) + 1 Search-Fallback-Call
        mapping_calls = [c for c in post_calls if "/mapping" in c[0]]
        search_calls = [c for c in post_calls if "/search" in c[0]]
        assert len(mapping_calls) == 1
        assert len(mapping_calls[0][1]) == 1  # genau 1 Job im Batch
        assert len(search_calls) == 1


class TestMappingMissNegativeCache:
    def test_mapping_miss_writes_negative_cache(
        self, normalizer, make_classification
    ):
        """Zweiter normalize_batch-Lauf nutzt Negativ-Cache → kein neuer Mapping-Call."""
        cls = make_classification(
            raw="SEAL-PB", ticker_base="SEAL",
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
        )

        post_calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            post_calls.append(url)
            if "/mapping" in url:
                return _http_response(200, [{"warning": "No identifier found"}])
            return _http_response(200, {"data": []})

        with patch("utils.ticker_normalizer.requests.post", side_effect=fake_post):
            normalizer.normalize_batch([cls])
            first_round_calls = list(post_calls)
            post_calls.clear()
            normalizer.normalize_batch([cls])

        # Erster Lauf: Mapping + Search-Fallback
        assert any("/mapping" in u for u in first_round_calls)
        assert any("/search" in u for u in first_round_calls)
        # Zweiter Lauf: weder Mapping noch Search (beide Negativ-Caches greifen)
        assert post_calls == []
