"""Integrationstests: Netzwerk-Robustheit der import_trades-Stages (R1-R6).

Pro Stelle die vier Kern-Szenarien:
  (1) kurzer Netzausfall -> Recovery via Retry
  (2) API-Timeout       -> Standard-Backoff statt Totlauf
  (3) Dauerausfall/4xx  -> fail-closed (kein falscher/veralteter Datensatz)
  (4) echtes Delisting  -> korrekt als delisted erkannt

Alle externen Calls (yfinance, requests) sind gemockt; es wird nie real
gewartet (NETWORK_RETRY_BACKOFFS -> [0,...] + time.sleep gepatcht).
"""

import os
import sys
import types

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402

from config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Keine echten Wartezeiten: Backoffs auf 0, Modul-Sleeps stummschalten."""
    monkeypatch.setattr(settings, "NETWORK_RETRY_BACKOFFS", [0, 0])
    for mod in ("utils.ticker_sector", "utils.ticker_normalizer",
                "scoring.post_trade_returns"):
        try:
            monkeypatch.setattr(f"{mod}.time.sleep", lambda s: None)
        except AttributeError:
            pass


# ===========================================================================
# R1/R2 — ticker_sector (yfinance .info): Netzfehler != delisted/null
# ===========================================================================

from utils.ticker_sector import TickerSectorEnricher  # noqa: E402


@pytest.fixture
def enricher(tmp_path):
    d = tmp_path / "mappings"
    d.mkdir()
    # ROT-Canary: Netz down -> leeres .info ist VERDAECHTIG (-> Retry/Retry-Queue),
    # wird NICHT als null/delisted gecacht. Bei .info MIT Identitaet greift der
    # Canary gar nicht (Identitaets-Check zuerst), daher fuer alle R1/R2-Faelle ok.
    from utils.network_canary import NetworkCanary
    canary = NetworkCanary(lambda t: False, basket=["X"], min_positive=1,
                           verbose=False, sleep_fn=lambda s: None)
    return TickerSectorEnricher(
        cache_dir=str(d),
        delisted_output_path=str(tmp_path / "delisted_trades_stage.csv"),
        confirm_callback=lambda n: True,
        canary=canary,
    )


def _patch_yf_ticker(monkeypatch, info_sequence):
    seq = iter(info_sequence)
    monkeypatch.setattr(
        "utils.ticker_sector.yf.Ticker",
        lambda t: types.SimpleNamespace(info=next(seq)),
    )


def test_sector_network_empty_not_null_cached(enricher, monkeypatch):
    """(3) Leeres .info (Netzfehler ohne Exception) -> NICHT als null/delisted."""
    _patch_yf_ticker(monkeypatch, [{}] * 10)
    enricher._fetch_misses(["AAPL"])
    assert "AAPL" not in enricher._null_cache  # kein vergifteter 365-Tage-Null-Cache
    assert any(n.classification.raw_ticker == "AAPL"
               for n in enricher._retry_queue)


def test_sector_identity_without_sector_is_null_cached(enricher, monkeypatch):
    """(4) Echtes 'kein Sektor' (mit Identitaet) -> legitim als null gecacht."""
    _patch_yf_ticker(monkeypatch, [{"quoteType": "EQUITY", "symbol": "XYZ"}])
    enricher._fetch_misses(["XYZ"])
    assert "XYZ" in enricher._null_cache


def test_sector_recovers_after_transient_empty(enricher, monkeypatch):
    """(1) Erst leer (Netz), dann echtes Ergebnis -> Sektor uebernommen."""
    monkeypatch.setattr(settings, "NETWORK_RETRY_BACKOFFS", [0, 0, 0])
    _patch_yf_ticker(monkeypatch, [
        {}, {},
        {"sector": "Technology", "industry": "Software", "quoteType": "EQUITY"},
    ])
    info, kind = enricher._fetch_yf_info("MSFT")
    assert kind is None
    assert info["sector"] == "Technology"


def test_sector_network_exhausted_marks_fetch_failed(enricher, monkeypatch):
    """(3) Dauerleer -> error_kind network_exhausted, kein Cache-Eintrag."""
    _patch_yf_ticker(monkeypatch, [{}] * 10)
    info, kind = enricher._fetch_yf_info("AAPL")
    assert kind == "network_exhausted"
    assert info is None


# ===========================================================================
# R3 — post_trade_returns (yfinance download): Netzausfall != Delisting
# ===========================================================================

from scoring import post_trade_returns as ptr  # noqa: E402


def _empty_cache(tmp_path):
    return ptr._load_price_cache(str(tmp_path / "px.parquet"))


def _real_hist():
    return pd.DataFrame(
        {"Close": [10.0, 11.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )


def _green_canary():
    """Fixierter GRUEN-Canary (Netz gesund) fuer deterministische Tests."""
    from utils.network_canary import NetworkCanary
    return NetworkCanary(lambda t: True, basket=["X"], min_positive=1,
                         verbose=False, sleep_fn=lambda s: None)


def _red_canary():
    """Fixierter ROT-Canary (Netz down)."""
    from utils.network_canary import NetworkCanary
    return NetworkCanary(lambda t: False, basket=["X"], min_positive=1,
                         verbose=False, sleep_fn=lambda s: None)


def test_ptr_genuine_delisting_writes_marker(monkeypatch, tmp_path):
    """(4) Canary GRUEN + leeres Ergebnis -> echtes Delisting (NaN-Marker)."""
    def fake_download(tkr, **kw):
        return _real_hist() if tkr == "AAA" else pd.DataFrame()
    monkeypatch.setattr("scoring.post_trade_returns.yf.download", fake_download)

    out = ptr._ensure_cache_for_tickers(
        _empty_cache(tmp_path), ["AAA", "ZZZ"], cache_path=None,
        canary=_green_canary(), verbose_timeouts=False)
    # ZZZ -> NaN-Marker (im Lookup None = delisted), AAA -> echte Preise.
    assert ptr._build_price_lookup(out).get("ZZZ") is None
    assert "ZZZ" in out.index.get_level_values(0)
    assert "AAA" in out.index.get_level_values(0)


def test_ptr_network_down_aborts_fail_closed(monkeypatch, tmp_path):
    """(3) Netz down von Anfang an -> Canary ROT -> fail-closed (RuntimeError);
    KEIN falscher Delisted-Marker, nichts persistiert."""
    monkeypatch.setattr(settings, "CANARY_MAX_RED_ROUNDS", 3)
    monkeypatch.setattr("scoring.post_trade_returns.yf.download",
                        lambda tkr, **kw: pd.DataFrame())
    with pytest.raises(RuntimeError):
        ptr._ensure_cache_for_tickers(
            _empty_cache(tmp_path), ["AAA", "BBB"], cache_path=None,
            canary=_red_canary(), verbose_timeouts=False)


def test_ptr_recovers_from_transient_exception(monkeypatch, tmp_path):
    """(1) Download wirft Timeout, dann echte Daten -> uebernommen, kein Marker."""
    monkeypatch.setattr(settings, "NETWORK_RETRY_BACKOFFS", [0, 0, 0])
    calls = {"n": 0}

    def fake_download(tkr, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.Timeout("Read timed out")
        return _real_hist()
    monkeypatch.setattr("scoring.post_trade_returns.yf.download", fake_download)

    out = ptr._ensure_cache_for_tickers(
        _empty_cache(tmp_path), ["AAA"], cache_path=None,
        canary=_green_canary(), verbose_timeouts=False)
    assert "AAA" in out.index.get_level_values(0)
    assert ptr._build_price_lookup(out).get("AAA") is not None


# ===========================================================================
# R4 — issuer_group_mapper (SEC EDGAR): Retry, sonst fail-closed
# ===========================================================================

from utils import issuer_group_mapper as igm  # noqa: E402

_SEC_SAMPLE = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]],
}


class _SecResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = requests.HTTPError(str(self.status_code))
            exc.response = self
            raise exc

    def json(self):
        return self._p


def _mapper(tmp_path):
    return igm.IssuerGroupMapper(cache_dir=str(tmp_path), url="http://sec.invalid",
                                 user_agent="test", ttl_days=7)


def test_edgar_transient_then_success(monkeypatch, tmp_path):
    """(1) Timeout x2, dann Erfolg -> CIK aufgeloest."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.Timeout("Read timed out")
        return _SecResp(_SEC_SAMPLE)
    monkeypatch.setattr("utils.issuer_group_mapper.requests.get", fake_get)

    key = _mapper(tmp_path).resolve_issuer_group_key(
        {"ticker_type": "stock", "ticker": "AAPL"})
    assert key == "CIK:320193"
    assert calls["n"] == 3


def test_edgar_exhausted_raises_unreachable(monkeypatch, tmp_path):
    """(3) Dauerausfall -> IssuerGroupMapperUnreachableError (kein Stale-Cache)."""
    def fake_get(url, **kw):
        raise requests.Timeout("Read timed out")
    monkeypatch.setattr("utils.issuer_group_mapper.requests.get", fake_get)

    with pytest.raises(igm.IssuerGroupMapperUnreachableError):
        _mapper(tmp_path).resolve_issuer_group_key(
            {"ticker_type": "stock", "ticker": "AAPL"})


def test_edgar_4xx_not_retried(monkeypatch, tmp_path):
    """(3) HTTP 404 ist permanent -> sofort fail-closed, KEIN Retry."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        return _SecResp(None, status=404)
    monkeypatch.setattr("utils.issuer_group_mapper.requests.get", fake_get)

    with pytest.raises(igm.IssuerGroupMapperUnreachableError):
        _mapper(tmp_path).resolve_issuer_group_key(
            {"ticker_type": "stock", "ticker": "AAPL"})
    assert calls["n"] == 1


# ===========================================================================
# R5/R6 — ticker_normalizer (OpenFIGI): Timeout-Retry + Retry-After-Vorrang
# ===========================================================================

from utils.ticker_normalizer import TickerNormalizer  # noqa: E402


class _FigiResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._p = payload
        self.headers = headers or {}
        self.text = "error-body"

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def test_openfigi_timeout_is_retried(monkeypatch, tmp_path):
    """(2) Read-Timeout wird jetzt mit Backoff wiederholt (frueher: tot in 'http')."""
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='api.openfigi.com'): Read timed out. (read timeout=30)")
    monkeypatch.setattr("utils.ticker_normalizer.requests.post", fake_post)

    n = TickerNormalizer(cache_db_path=str(tmp_path / "c.db"), api_key="fake")
    result, kind = n._api_post(n.api_url, {"x": 1})
    assert result is None
    assert kind == "network"          # nach erschoepftem Budget, NICHT 'http'
    assert calls["n"] == 3            # 1 Versuch + 2 Backoff-Retries (R6)


def test_openfigi_429_honors_retry_after(monkeypatch, tmp_path):
    """(2/R5) Retry-After-Header hat Vorrang vor dem 2^n-Backoff."""
    sleeps = []
    monkeypatch.setattr("utils.ticker_normalizer.time.sleep",
                        lambda s: sleeps.append(s))
    seq = iter([
        _FigiResp(429, headers={"Retry-After": "7"}),
        _FigiResp(200, payload=[{"data": []}]),
    ])
    monkeypatch.setattr("utils.ticker_normalizer.requests.post",
                        lambda url, **kw: next(seq))

    n = TickerNormalizer(cache_db_path=str(tmp_path / "c.db"), api_key="fake")
    result, kind = n._api_post(n.api_url, {"x": 1})
    assert kind is None
    assert 7 in sleeps  # API-Vorgabe respektiert (nicht 2.0**1)


def test_openfigi_4xx_permanent_no_retry(monkeypatch, tmp_path):
    """(3) HTTP 400 ist permanent -> error_kind 'http', genau 1 Call."""
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return _FigiResp(400, payload=None)
    monkeypatch.setattr("utils.ticker_normalizer.requests.post", fake_post)

    n = TickerNormalizer(cache_db_path=str(tmp_path / "c.db"), api_key="fake")
    result, kind = n._api_post(n.api_url, {"x": 1})
    assert result is None
    assert kind == "http"
    assert calls["n"] == 1


@pytest.mark.parametrize("header, expected", [
    ({"Retry-After": "42"}, 42.0),
    ({"Retry-After": "0"}, 0.0),
    ({}, None),
])
def test_parse_retry_after(header, expected):
    resp = types.SimpleNamespace(headers=header)
    assert TickerNormalizer._parse_retry_after(resp) == expected


def test_parse_retry_after_none():
    assert TickerNormalizer._parse_retry_after(None) is None
