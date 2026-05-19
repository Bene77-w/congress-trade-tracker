"""Tests fuer utils/issuer_ticker.py — issuer_ticker-Aufloesung mit Fallback-Kette."""

import math

import numpy as np
import pandas as pd
import pytest

from utils.issuer_ticker import resolve_issuer_ticker


class TestResolveIssuerTicker:
    def test_ticker_base_bevorzugt(self):
        row = {'ticker_base': 'AAPL', 'ticker': 'AAPL.WS'}
        assert resolve_issuer_ticker(row) == 'AAPL'

    def test_fallback_zu_ticker_wenn_base_fehlt(self):
        row = {'ticker_base': None, 'ticker': 'MSFT'}
        assert resolve_issuer_ticker(row) == 'MSFT'

    def test_fallback_wenn_base_nan(self):
        row = {'ticker_base': float('nan'), 'ticker': 'NVDA'}
        assert resolve_issuer_ticker(row) == 'NVDA'

    def test_fallback_wenn_base_leer_string(self):
        row = {'ticker_base': '', 'ticker': 'TSLA'}
        assert resolve_issuer_ticker(row) == 'TSLA'

    def test_fallback_wenn_base_whitespace(self):
        row = {'ticker_base': '   ', 'ticker': 'AMZN'}
        assert resolve_issuer_ticker(row) == 'AMZN'

    def test_leer_wenn_beide_fehlen(self):
        assert resolve_issuer_ticker({'ticker_base': None, 'ticker': None}) == ''
        assert resolve_issuer_ticker({}) == ''
        assert resolve_issuer_ticker({'ticker_base': '', 'ticker': ''}) == ''

    def test_trimmt_whitespace(self):
        row = {'ticker_base': '  AAPL  '}
        assert resolve_issuer_ticker(row) == 'AAPL'

    def test_funktioniert_mit_pandas_series(self):
        row = pd.Series({'ticker_base': 'GOOGL', 'ticker': 'GOOG'})
        assert resolve_issuer_ticker(row) == 'GOOGL'

    def test_funktioniert_mit_pandas_series_nan(self):
        row = pd.Series({'ticker_base': np.nan, 'ticker': 'BRK.B'})
        assert resolve_issuer_ticker(row) == 'BRK.B'

    def test_deterministisch(self):
        row = {'ticker_base': 'AAPL', 'ticker': 'AAPL.WS'}
        assert resolve_issuer_ticker(row) == resolve_issuer_ticker(row)

    def test_unbekannter_typ_robust(self):
        # Objekt ohne .get() und ohne __getitem__ → Exception-frei = ''
        class Dummy:
            pass
        assert resolve_issuer_ticker(Dummy()) == ''


class TestShareClassFigiPrecedence:
    """Neue Fallback-Kette: share_class_figi -> ticker_base -> ticker."""

    def test_share_class_figi_prevails_over_ticker_base(self):
        """share_class_figi schlaegt ticker_base (Erstkandidat in Kette)."""
        row = {
            'share_class_figi': 'BBG009S39JX6',
            'ticker_base': 'GOOGL',
            'ticker': 'GOOGL',
        }
        assert resolve_issuer_ticker(row) == 'BBG009S39JX6'

    def test_falls_back_to_ticker_base_when_share_class_figi_empty(self):
        """share_class_figi=None -> ticker_base bleibt der naechste Kandidat."""
        row = {
            'share_class_figi': None,
            'ticker_base': 'AAPL',
            'ticker': 'AAPL',
        }
        assert resolve_issuer_ticker(row) == 'AAPL'

    def test_share_class_figi_whitespace_treated_as_empty(self):
        """Whitespace-only share_class_figi -> Fallback zu ticker_base."""
        row = {
            'share_class_figi': '   ',
            'ticker_base': 'MSFT',
            'ticker': 'MSFT',
        }
        assert resolve_issuer_ticker(row) == 'MSFT'

    def test_share_class_figi_nan_treated_as_empty(self):
        """NaN share_class_figi -> Fallback zu ticker_base."""
        row = {
            'share_class_figi': np.nan,
            'ticker_base': 'NVDA',
            'ticker': 'NVDA',
        }
        assert resolve_issuer_ticker(row) == 'NVDA'

    def test_share_class_figi_alone_sufficient(self):
        """share_class_figi gesetzt, beide andere leer -> FIGI."""
        row = {
            'share_class_figi': 'BBG000B9XRY4',
            'ticker_base': None,
            'ticker': None,
        }
        assert resolve_issuer_ticker(row) == 'BBG000B9XRY4'

    def test_full_chain_works_with_pandas_series(self):
        """Realer Fall: GOOGL+GOOG teilen denselben share_class_figi."""
        googl = pd.Series({
            'share_class_figi': 'BBG009S39JX6',
            'ticker_base': 'GOOGL',
            'ticker': 'GOOGL',
        })
        goog = pd.Series({
            'share_class_figi': 'BBG009S39JX6',
            'ticker_base': 'GOOG',
            'ticker': 'GOOG',
        })
        # Trotz unterschiedlicher Ticker -> selber issuer_ticker
        assert resolve_issuer_ticker(googl) == resolve_issuer_ticker(goog)
