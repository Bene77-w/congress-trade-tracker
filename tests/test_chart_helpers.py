"""Tests fuer app/chart_helpers.py — Teil-/Vollverkaufs-Marker.

Kernzusicherung: Teilverkauf und Vollverkauf werden mit UNTERSCHIEDLICHEN
Symbolen markiert, Nicht-Verkaeufe gar nicht, und fehlende Datum/Preis-Werte
werden uebersprungen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chart_helpers import (  # noqa: E402
    SALE_KIND_MARKERS,
    build_sale_kind_traces,
    sale_kind_marker,
)


# --- sale_kind_marker ----------------------------------------------------

def test_partial_and_full_use_distinct_symbols():
    """Feature-Kern: Teilverkauf != Vollverkauf im Symbol."""
    partial = sale_kind_marker('Sale (Partial)')
    full = sale_kind_marker('Sale (Full)')
    assert partial is not None and full is not None
    assert partial['symbol'] != full['symbol']
    assert partial['label'] != full['label']


def test_partial_label_and_symbol():
    style = sale_kind_marker('Sale (Partial)')
    assert style['label'] == 'Teilverkauf'
    assert style['symbol'] == 'triangle-down-open'


def test_non_sale_types_return_none():
    for tt in ['Purchase', 'Sale', 'Exchange', 'PURCHASE', None, '', float('nan')]:
        assert sale_kind_marker(tt) is None


def test_returns_fresh_copy_no_aliasing():
    style = sale_kind_marker('Sale (Partial)')
    style['symbol'] = 'MUTATED'
    assert SALE_KIND_MARKERS['Sale (Partial)']['symbol'] != 'MUTATED'
    assert sale_kind_marker('Sale (Partial)')['symbol'] != 'MUTATED'


# --- build_sale_kind_traces ----------------------------------------------

def _rec(date, price, tt, name='X'):
    return {'date': date, 'price': price, 'transaction_type': tt, 'name': name}


def test_empty_input_returns_empty_list():
    assert build_sale_kind_traces([]) == []


def test_only_non_sales_returns_empty():
    recs = [_rec('2024-01-01', 10.0, 'Purchase'),
            _rec('2024-02-01', 11.0, 'Sale')]
    assert build_sale_kind_traces(recs) == []


def test_partial_and_full_produce_two_distinct_traces():
    recs = [
        _rec('2024-03-01', 50.0, 'Sale (Partial)'),
        _rec('2024-04-01', 55.0, 'Sale (Full)'),
    ]
    traces = build_sale_kind_traces(recs)
    assert len(traces) == 2
    assert all(isinstance(t, go.Scatter) for t in traces)
    symbols = {t.marker.symbol for t in traces}
    assert symbols == {'triangle-down-open', 'x'}
    # Deterministische Reihenfolge: Teilverkauf zuerst.
    assert traces[0].name == 'Teilverkauf'
    assert traces[1].name == 'Vollverkauf'


def test_same_kind_grouped_into_single_trace():
    recs = [
        _rec('2024-03-01', 50.0, 'Sale (Partial)', name='A'),
        _rec('2024-03-15', 52.0, 'Sale (Partial)', name='B'),
    ]
    traces = build_sale_kind_traces(recs)
    assert len(traces) == 1
    assert list(traces[0].y) == [50.0, 52.0]
    assert len(traces[0].x) == 2


def test_missing_date_or_price_skipped():
    recs = [
        _rec(pd.NaT, 50.0, 'Sale (Partial)'),
        _rec('2024-03-01', float('nan'), 'Sale (Partial)'),
        _rec(None, 50.0, 'Sale (Full)'),
    ]
    assert build_sale_kind_traces(recs) == []


def test_hover_text_includes_name_and_label():
    traces = build_sale_kind_traces(
        [_rec('2024-03-01', 50.0, 'Sale (Partial)', name='Boozman, John')])
    assert traces[0].text[0] == 'Boozman, John · Teilverkauf'
