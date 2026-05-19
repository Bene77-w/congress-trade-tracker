"""Tests fuer utils/issuer_group_mapper.py (P5.6)."""

import json
import os
import sys
from datetime import datetime, timedelta
from unittest import mock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.issuer_group_mapper import (  # noqa: E402
    IssuerGroupMapper,
    IssuerGroupMapperUnreachableError,
    normalize_ticker_for_edgar,
    _parse_sec_payload,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PAYLOAD = {
    'fields': ['cik', 'name', 'ticker', 'exchange'],
    'data': [
        [1652044, 'Alphabet Inc.',          'GOOGL', 'Nasdaq'],
        [1652044, 'Alphabet Inc.',          'GOOG',  'Nasdaq'],
        [1067983, 'Berkshire Hathaway Inc', 'BRK-A', 'NYSE'],
        [1067983, 'Berkshire Hathaway Inc', 'BRK-B', 'NYSE'],
        [1754301, 'Fox Corp',               'FOXA',  'Nasdaq'],
        [1754301, 'Fox Corp',               'FOX',   'Nasdaq'],
        [320193,  'Apple Inc.',             'AAPL',  'Nasdaq'],
    ],
}


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def mapper_with_loaded_cache(cache_dir):
    """Mapper mit Cache schon im Speicher (= kein Disk-Write, kein API-Call)."""
    m = IssuerGroupMapper(cache_dir=cache_dir,
                          url='http://test.invalid',
                          user_agent='test',
                          ttl_days=7)
    # Cache manuell injizieren statt fetchen.
    from utils.issuer_group_mapper import _CacheState  # noqa: WPS433
    m._cache = _CacheState(
        loaded_at=datetime.now(),
        ticker_to_cik=_parse_sec_payload(SAMPLE_PAYLOAD),
    )
    return m


# ---------------------------------------------------------------------------
# Lookup-Logik
# ---------------------------------------------------------------------------

class TestMultiClassResolution:
    """GOOGL/GOOG, BRK-A/BRK-B, FOXA/FOX bekommen denselben CIK."""

    def test_googl_goog_same_cik(self, mapper_with_loaded_cache):
        googl = mapper_with_loaded_cache.resolve_issuer_group_key(
            {'ticker': 'GOOGL', 'ticker_base': 'GOOGL', 'ticker_type': 'stock'})
        goog = mapper_with_loaded_cache.resolve_issuer_group_key(
            {'ticker': 'GOOG', 'ticker_base': 'GOOG', 'ticker_type': 'stock'})
        assert googl == 'CIK:1652044'
        assert goog  == 'CIK:1652044'

    def test_brk_variants_same_cik(self, mapper_with_loaded_cache):
        for ticker in ('BRK.A', 'BRK/A', 'BRK A', 'BRK-A'):
            result = mapper_with_loaded_cache.resolve_issuer_group_key(
                {'ticker': ticker, 'ticker_base': 'BRK', 'ticker_type': 'stock'})
            assert result == 'CIK:1067983', f'failed for {ticker!r}'

    def test_foxa_fox_same_cik(self, mapper_with_loaded_cache):
        foxa = mapper_with_loaded_cache.resolve_issuer_group_key(
            {'ticker': 'FOXA', 'ticker_base': 'FOXA', 'ticker_type': 'stock'})
        fox = mapper_with_loaded_cache.resolve_issuer_group_key(
            {'ticker': 'FOX', 'ticker_base': 'FOX', 'ticker_type': 'stock'})
        assert foxa == fox == 'CIK:1754301'


class TestNormalization:
    def test_dot_slash_space_unified(self):
        assert normalize_ticker_for_edgar('BRK.A') == 'BRK-A'
        assert normalize_ticker_for_edgar('BRK/A') == 'BRK-A'
        assert normalize_ticker_for_edgar('BRK A') == 'BRK-A'
        assert normalize_ticker_for_edgar('BRK-A') == 'BRK-A'

    def test_uppercase_and_strip(self):
        assert normalize_ticker_for_edgar('  googl  ') == 'GOOGL'
        assert normalize_ticker_for_edgar('') == ''
        assert normalize_ticker_for_edgar(None) == ''


class TestEtfBypass:
    def test_etf_does_not_use_cik(self, mapper_with_loaded_cache):
        """SPY hat keinen Eintrag in unserem Test-Payload, wuerde aber ohnehin
        nicht ueber EDGAR aufgeloest werden — ETF-Bypass setzt direkt
        TICKER:<issuer_ticker>."""
        result = mapper_with_loaded_cache.resolve_issuer_group_key({
            'ticker': 'SPY', 'ticker_base': 'SPY', 'issuer_ticker': 'BBG_SPY',
            'ticker_type': 'etf',
        })
        assert result == 'TICKER:BBG_SPY'

    def test_etf_bypass_does_not_consult_cache(self, mapper_with_loaded_cache):
        """Auch wenn der Ticker zufaellig in EDGAR ist, ETF -> TICKER-Fallback."""
        # Wir nutzen einen Ticker aus dem Payload und markieren ihn als ETF.
        result = mapper_with_loaded_cache.resolve_issuer_group_key({
            'ticker': 'GOOGL', 'ticker_base': 'GOOGL',
            'issuer_ticker': 'BBG_GOOGL', 'ticker_type': 'etf',
        })
        assert result == 'TICKER:BBG_GOOGL'


class TestForeignBypass:
    def test_foreign_stock_uses_ticker_fallback(self, mapper_with_loaded_cache):
        result = mapper_with_loaded_cache.resolve_issuer_group_key({
            'ticker': 'NESN.SG', 'ticker_base': 'NESN', 'issuer_ticker': 'NESN',
            'ticker_type': 'foreign_stock',
        })
        assert result == 'TICKER:NESN'


class TestSkipBranches:
    @pytest.mark.parametrize('ttype', [
        'option', 'bond', 'treasury', 'crypto', 'when_issued',
        'invalid', 'misc', 'warrant', 'unit', 'ignored',
    ])
    def test_skip_types(self, mapper_with_loaded_cache, ttype):
        result = mapper_with_loaded_cache.resolve_issuer_group_key({
            'ticker': 'XYZ', 'ticker_base': 'XYZ', 'ticker_type': ttype,
        })
        assert result == f'SKIP:{ttype}'


class TestFallback:
    def test_unknown_stock_falls_back_to_ticker_base(self, mapper_with_loaded_cache):
        """Stock-Ticker nicht in EDGAR -> TICKER:<ticker_base>."""
        result = mapper_with_loaded_cache.resolve_issuer_group_key({
            'ticker': 'XYZUNKNOWN', 'ticker_base': 'XYZUNKNOWN',
            'ticker_type': 'stock',
        })
        assert result == 'TICKER:XYZUNKNOWN'

    def test_unknown_stock_uses_ticker_when_base_missing(
            self, mapper_with_loaded_cache):
        result = mapper_with_loaded_cache.resolve_issuer_group_key({
            'ticker': 'XYZUNKNOWN', 'ticker_base': '', 'ticker_type': 'stock',
        })
        assert result == 'TICKER:XYZUNKNOWN'


# ---------------------------------------------------------------------------
# Cache- und Bootstrap-Verhalten
# ---------------------------------------------------------------------------

class TestCacheFreshness:
    def test_fresh_cache_skips_api_call(self, cache_dir):
        """Cache <=7 Tage -> kein requests-Aufruf."""
        m = IssuerGroupMapper(cache_dir=cache_dir, ttl_days=7,
                              url='http://test.invalid', user_agent='test')
        # Disk-Cache mit aktuellem Datum schreiben.
        payload = {
            'loaded_at': datetime.now().isoformat(timespec='seconds'),
            'ticker_to_cik': _parse_sec_payload(SAMPLE_PAYLOAD),
        }
        with open(os.path.join(cache_dir, 'edgar_cik_map.json'), 'w') as fh:
            json.dump(payload, fh)
        with mock.patch('utils.issuer_group_mapper.requests') as mock_req:
            result = m.resolve_issuer_group_key({
                'ticker': 'AAPL', 'ticker_base': 'AAPL', 'ticker_type': 'stock',
            })
            mock_req.get.assert_not_called()
        assert result == 'CIK:320193'

    def test_expired_cache_triggers_refresh(self, cache_dir):
        """Cache >7 Tage -> requests.get muss aufgerufen werden."""
        m = IssuerGroupMapper(cache_dir=cache_dir, ttl_days=7,
                              url='http://test.invalid', user_agent='test')
        # Disk-Cache mit altem Datum schreiben (10 Tage alt).
        old_payload = {
            'loaded_at': (datetime.now() - timedelta(days=10)).isoformat(timespec='seconds'),
            'ticker_to_cik': {'STALE': '999'},
        }
        with open(os.path.join(cache_dir, 'edgar_cik_map.json'), 'w') as fh:
            json.dump(old_payload, fh)

        fake_resp = mock.Mock()
        fake_resp.json.return_value = SAMPLE_PAYLOAD
        fake_resp.raise_for_status = mock.Mock()

        with mock.patch('utils.issuer_group_mapper.requests') as mock_req:
            mock_req.get.return_value = fake_resp
            result = m.resolve_issuer_group_key({
                'ticker': 'AAPL', 'ticker_base': 'AAPL', 'ticker_type': 'stock',
            })
            mock_req.get.assert_called_once()
        assert result == 'CIK:320193'


class TestBootstrap:
    def test_bootstrap_writes_cache(self, cache_dir):
        m = IssuerGroupMapper(cache_dir=cache_dir, ttl_days=7,
                              url='http://test.invalid', user_agent='test')
        fake_resp = mock.Mock()
        fake_resp.json.return_value = SAMPLE_PAYLOAD
        fake_resp.raise_for_status = mock.Mock()
        with mock.patch('utils.issuer_group_mapper.requests') as mock_req:
            mock_req.get.return_value = fake_resp
            m.resolve_issuer_group_key({
                'ticker': 'AAPL', 'ticker_base': 'AAPL', 'ticker_type': 'stock',
            })
        assert os.path.exists(os.path.join(cache_dir, 'edgar_cik_map.json'))


class TestApiFailureStrictAbort:
    def test_bootstrap_failure_raises(self, cache_dir):
        """Kein Cache, SEC down -> Abbruch."""
        m = IssuerGroupMapper(cache_dir=cache_dir, ttl_days=7,
                              url='http://test.invalid', user_agent='test')
        with mock.patch('utils.issuer_group_mapper.requests') as mock_req:
            mock_req.get.side_effect = Exception('connection refused')
            with pytest.raises(IssuerGroupMapperUnreachableError):
                m.resolve_issuer_group_key({
                    'ticker': 'AAPL', 'ticker_base': 'AAPL', 'ticker_type': 'stock',
                })

    def test_ttl_refresh_failure_raises_no_silent_fallback(self, cache_dir):
        """Alter Cache vorhanden, SEC down -> trotzdem Abbruch (kein
        silent Fallback auf veralteten Cache)."""
        m = IssuerGroupMapper(cache_dir=cache_dir, ttl_days=7,
                              url='http://test.invalid', user_agent='test')
        old_payload = {
            'loaded_at': (datetime.now() - timedelta(days=10)).isoformat(timespec='seconds'),
            'ticker_to_cik': {'STALE': '999'},
        }
        with open(os.path.join(cache_dir, 'edgar_cik_map.json'), 'w') as fh:
            json.dump(old_payload, fh)
        with mock.patch('utils.issuer_group_mapper.requests') as mock_req:
            mock_req.get.side_effect = Exception('connection refused')
            with pytest.raises(IssuerGroupMapperUnreachableError):
                m.resolve_issuer_group_key({
                    'ticker': 'AAPL', 'ticker_base': 'AAPL', 'ticker_type': 'stock',
                })
