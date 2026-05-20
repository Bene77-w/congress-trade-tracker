"""Unit-Tests fuer utils/amount_parser.py."""

import math

import pytest

from utils.amount_parser import parse_amount_range, midpoint_usd


class TestParseAmountRange:
    def test_standard_range(self):
        assert parse_amount_range('$1,001 - $15,000') == (1001, 15000)

    def test_large_range(self):
        assert parse_amount_range('$5,000,001 - $25,000,000') == (5_000_001, 25_000_000)

    def test_over_open_ended(self):
        # "Over $50M" konservativ als Punkt-Trade auf Untergrenze
        assert parse_amount_range('Over $50,000,000') == (50_000_000, 50_000_000)

    def test_spouse_text_with_amount(self):
        s = 'Transaction in a Spouse or Dependent Child Asset over $1,000,000'
        assert parse_amount_range(s) == (1_000_000, 1_000_000)

    def test_single_value_no_dash(self):
        assert parse_amount_range('$15,001') == (15001, 15001)

    def test_decimal_truncated(self):
        # Dezimale werden auf int abgeschnitten
        assert parse_amount_range('$94.91') == (94, 94)

    def test_corrupt_double_range(self):
        # Korrupt-Wert: min/max ueber alle gefundenen Werte
        result = parse_amount_range('$250,001-$500,000 - $100,001')
        assert result == (100_001, 500_000)

    def test_low_decimal_high_thousands(self):
        assert parse_amount_range('$1.26 - $50,001') == (1, 50_001)

    def test_empty_string(self):
        assert parse_amount_range('') == (0, 0)

    def test_none(self):
        assert parse_amount_range(None) == (0, 0)

    def test_nan(self):
        assert parse_amount_range(float('nan')) == (0, 0)

    def test_no_dollar_sign_in_string(self):
        # Ohne $-Praefix wird nichts erkannt
        assert parse_amount_range('1001 - 15000') == (0, 0)

    def test_whitespace_only(self):
        assert parse_amount_range('   ') == (0, 0)

    def test_int_passthrough(self):
        assert parse_amount_range(5000) == (5000, 5000)

    def test_float_passthrough(self):
        assert parse_amount_range(5000.75) == (5000, 5000)


class TestMidpointUsd:
    def test_standard_range(self):
        # (1001 + 15000) // 2 = 8000
        assert midpoint_usd('$1,001 - $15,000') == 8000

    def test_single_value(self):
        assert midpoint_usd('$15,001') == 15001

    def test_empty(self):
        assert midpoint_usd('') == 0

    def test_none(self):
        assert midpoint_usd(None) == 0

    def test_over_open_ended(self):
        assert midpoint_usd('Over $50,000,000') == 50_000_000

    def test_large_range(self):
        # (5_000_001 + 25_000_000) // 2 = 15_000_000
        assert midpoint_usd('$5,000,001 - $25,000,000') == 15_000_000
